"""Split one message into the independent requests it actually makes, so all of them get answered.

Why this exists
---------------
`live_info_mode()` returns a single mode for a whole turn, and every lane below it then selects a
single subject (`return target` on the first alias match, one location, one topic). One turn -> one
mode -> one subject -> one answer. A message that asks several things cannot survive that by
construction, and four separate instances of the same shape have already been fixed one at a time
(the crypto single-coin fetch, the crypto alias table, the market-quote target, the weather swallow).
Measured on the shipped build, 2026-08-05:

    "what is the euro to usd today and will it rain and where i can change my tyres in vilnius"
      -> "Weather in euro to usd and will it rain and where i can change my tyres in vilnius:
          Vilnius, Lithuania: Sunny, 21 C ..."

One mode claimed the turn, the location extractor swallowed the entire sentence as the "place name",
and two of the three questions were never answered or even mentioned.

Handing such a turn to the model+tool lane instead is NOT the fix; that was measured too. A
two-question message with no weather marker already reaches that lane, and it spent 154.86s and
made zero tool calls, replying "Check XE.com for EUR/USD rate. For tyre services in Vilnius, search
Google Maps". Wrong-but-fast became useless-and-slow.

So the split happens here, ahead of mode selection, and each planned request is then run as its own
ordinary turn through the existing lanes. No lane needs to learn about multi-part messages.

The safety property
-------------------
The model chooses the split -- a separator split is the hard-coded phrase matching Section 2 bans,
and it shreds real requests ("gold and silver price" is not "gold" + "silver price"; note that
`core/task_decomposer.py`, on the mesh lane, splits exactly that way and is not reused here).

But the model is never trusted to introduce content. `verify_plan` rejects any plan containing a
content word that is not already in the user's message. That is what makes context resolution safe:
the planner MAY rewrite "will it rain" as "will it rain in vilnius", because `vilnius` is the user's
own word from a sibling clause -- and it MAY NOT turn it into "will it rain in london". A rejected
plan falls back to the single-request behaviour that exists today, so a bad plan costs nothing that
was not already lost.

Everything in this module except `plan_turn` is a pure function: the gate, the parse, and the
verification are all testable without a model, which is where the whole safety argument lives.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

# A turn is never split into more parts than this. A plan claiming more is a runaway, not a
# request: reject it whole rather than answering an arbitrary prefix of it.
MAX_PLANNED_REQUESTS = 6
# Below this length a message is a single request whatever it contains ("btc and eth?"" is fine to
# plan, "hi" is not). Guards the planner call, not the split.
_MIN_WORDS_TO_CONSIDER_PLANNING = 4

# Tokens that can join two independent requests. This list only decides WHETHER to ask the planner,
# never HOW to split -- a message it misses simply behaves as it does today, and a message it
# wrongly admits is handed to a model that is free to answer "one request".
#
# A single "?" is deliberately NOT here. Almost every question ends with one, so including it put a
# planner call in front of "what is the price of bitcoin right now?" -- a single request that answers
# deterministically in 0.3s. Several "?" in one message is a different signal and is checked
# separately below.
_JOINING_TOKENS = (" and ", " also ", " plus ", " then ", ";", ",")

#: An internal sentence boundary: a terminator with more text after it. A trailing "." is not a
#: boundary; "e.g. word" is a tolerable false admit (one bounded planner call, no wrong split).
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+\S")

# Words that carry no topic. Used only to compare a plan against the user's own words, so being
# incomplete here can only make verification STRICTER, never more permissive.
_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did",
    "will", "would", "can", "could", "should", "shall", "may", "might", "must", "have", "has", "had",
    "i", "me", "my", "we", "us", "our", "you", "your", "it", "its", "they", "them", "their", "he",
    "she", "his", "her", "this", "that", "these", "those", "what", "whats", "which", "who", "whom",
    "where", "when", "why", "how", "and", "or", "but", "if", "then", "than", "so", "also", "plus",
    "of", "in", "on", "at", "to", "for", "from", "by", "with", "about", "as", "into", "near",
    "please", "pls", "ok", "okay", "just", "now", "today", "tell", "show", "give", "find", "check",
    "compare", "summarize", "summarise", "get", "there", "here", "much", "many", "some", "any",
    "right", "up", "out", "over", "again", "still", "very", "really", "s", "t", "m", "re", "ll",
})

# Unicode word characters (underscore excluded): a plan-verification word is a word in the
# USER'S language — Lithuanian "išsiaiškink", Japanese, anything \w matches. The English-only
# [a-z0-9] form silently dropped every non-ASCII word, so a non-English plan could not verify
# against its own original and the whole decomposition lane misfired on it.
_WORD_RE = re.compile(r"[^\W_]+")

PLANNER_SYSTEM_PROMPT = (
    "You split a user's message into the separate requests it makes, so each one can be answered.\n"
    "Return ONLY a JSON array. Each element is an object with:\n"
    '  "request"    - one self-contained request, in the user\'s own words\n'
    '  "depends_on" - array of indices of requests that must be answered first (usually empty)\n'
    "\n"
    "Rules:\n"
    "- If the message makes ONE request, return a single element.\n"
    "- Use ONLY words that appear in the user's message. Never introduce a new place, asset,\n"
    "  entity, or number. A request containing a word the user did not write is invalid.\n"
    "- Carry shared context into each request: if the user asks 'will it rain and where can I\n"
    "  change my tyres in vilnius', the weather request must say 'vilnius' too.\n"
    "- Keep requests that must be answered together as ONE request when they share a subject\n"
    "  and answer type.\n"
    "- Mark depends_on only when a request genuinely needs another's answer first.\n"
    "- No prose, no explanation, no markdown fence. Only the JSON array."
)


@dataclass(frozen=True)
class PlannedTask:
    """One self-contained request carved out of the user's message."""

    index: int
    request: str
    depends_on: tuple[int, ...] = ()


def planned_task_slot(task: PlannedTask) -> str:
    """The task's stable EXECUTING SLOT on its turn's attempt chain (ARCH-TRUTH-R1d).

    Derived from the task's own two identifying facts -- its position in the plan and the
    exact request it carries -- so it is the same value every time the same task of the
    same plan runs, and different for any other task. That is what executing exclusivity
    is keyed on: a wave of DISTINCT tasks may be live at once, while the SAME task cannot
    be executing twice. The text is hashed rather than carried so the key is bounded and
    holds no request content.
    """
    import hashlib

    digest = hashlib.sha256(str(getattr(task, "request", "") or "").encode("utf-8")).hexdigest()
    return f"task:{int(getattr(task, 'index', 0) or 0)}:{digest[:16]}"


def _content_words(text: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall(str(text or "").lower())
        if word not in _FUNCTION_WORDS and len(word) > 1
    }


def turn_may_hold_several_requests(text: str) -> bool:
    """Whether this message is worth showing the planner at all.

    Deliberately permissive and deliberately cheap: it decides only whether to spend a planner
    call, never how to split. A message it wrongly admits costs one bounded model call and is very
    likely returned as a single request anyway; a message it misses behaves exactly as it does
    today. Neither outcome can produce a wrong split, which is why a keyword list is acceptable
    HERE and not inside the split itself.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if len(_WORD_RE.findall(lowered)) < _MIN_WORDS_TO_CONSIDER_PLANNING:
        return False
    if lowered.count("?") > 1:
        return True
    # Several sentences are several candidate requests even with no joining word: "Weather in
    # Rome. Convert 100 USD to RUB. How much gold can I buy?" carries one "?" and no joiner, yet
    # plainly holds three requests -- and missing it here meant no decomposition lane ever saw it.
    if _SENTENCE_BOUNDARY_RE.search(lowered):
        return True
    return any(token in lowered for token in _JOINING_TOKENS)


def parse_plan(raw: str) -> list[PlannedTask]:
    """Parse the planner's reply. Returns [] for anything not a usable plan.

    Tolerates a markdown fence and surrounding prose, because a model that was told not to emit
    them still sometimes does, and re-asking costs a whole round trip.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            return []
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if not isinstance(payload, list) or not payload:
        return []
    tasks: list[PlannedTask] = []
    for index, item in enumerate(payload):
        if index >= MAX_PLANNED_REQUESTS:
            # A plan longer than the cap is rejected whole below; stop building here so the
            # length check sees the real count rather than a silently truncated one.
            return []
        if isinstance(item, str):
            request, depends_raw = item, []
        elif isinstance(item, dict):
            request = str(item.get("request") or item.get("text") or "").strip()
            depends_raw = item.get("depends_on") or item.get("dependsOn") or []
        else:
            return []
        request = " ".join(str(request or "").split())
        if not request:
            return []
        depends: list[int] = []
        if isinstance(depends_raw, list):
            for value in depends_raw:
                try:
                    dep = int(value)
                except (TypeError, ValueError):
                    return []
                # A dependency on itself or on a later request is not a DAG.
                if dep < 0 or dep >= index:
                    return []
                depends.append(dep)
        tasks.append(PlannedTask(index=index, request=request, depends_on=tuple(depends)))
    if len(payload) > MAX_PLANNED_REQUESTS:
        return []
    return tasks


def verify_plan(tasks: list[PlannedTask], original: str) -> bool:
    """True when the plan only rearranges the user's own words.

    This is the whole safety argument. The planner is a model, and a model asked to split a
    sentence will occasionally answer it, embellish it, or hallucinate a subject -- so the runtime
    checks the one property that makes an invented request impossible: every content word in every
    planned request must already appear in the user's message.

    It deliberately does NOT require the reverse (that the plan covers every word of the original).
    Dropping a word is how a plan legitimately strips "ok" or "please", and an incomplete plan is
    caught by the caller answering fewer things, not by a wrong thing being answered.
    """
    if not tasks:
        return False
    allowed = _content_words(original)
    if not allowed:
        return False
    for task in tasks:
        invented = _content_words(task.request) - allowed
        if invented:
            return False
    return True


def plan_turn(
    text: str,
    *,
    ask_model: Callable[[str, str], str],
    request_is_servable: Callable[[str], bool] | None = None,
) -> list[PlannedTask]:
    """The planned requests in `text`, or [] when it is a single request.

    `ask_model(system_prompt, prompt)` returns the model's raw reply. Any failure -- an exception,
    an unparseable reply, a plan that invents content, or a plan of one -- yields [], and the caller
    runs the turn exactly as it does today. This function can only ever ADD the ability to answer
    several requests; it can never subtract from what a single request already does.
    """
    from core.plain_task_routing import is_ordinary_multi_part_plain_task

    # The generic planner is for independently servable lanes. Splitting an ordinary explanation,
    # calculation and title into sub-turns buys an extra model call and loses the one response-level
    # completeness guard. Keep that family in the single plain answering lane.
    if is_ordinary_multi_part_plain_task(text) or not turn_may_hold_several_requests(text):
        return []
    # A STIPULATED turn supplies the premises for ONE computation: its bullet list is that
    # computation's parts, never several independent requests. Measured live 2026-09-16: a
    # self-contained provider-comparison ("A test run produced: ... Calculate: - total tokens -
    # cost per 1M tokens ...") was split into fragments, each fragment lost the data block that
    # made it arithmetic, the leftovers read as market tickers ('INPUT', 'OUTPUT', 'TOTAL'), and
    # the user got a Markets table of unresolvable symbols instead of the arithmetic their
    # message had already supplied every input for.
    from core.stipulated_frame import stipulated_frame_active

    if stipulated_frame_active(text):
        return []
    # A SOFTWARE-AUTHORING request is one build task whose bullet list is the artifact's SPEC,
    # not several requests. Measured live 2026-09-17: "Build me a small Python provider-health
    # checker ... - verify ... - retrieve ... - make a small request" was split into five
    # fragment sub-turns ("- measure total request latency" became a standalone task), each
    # classified differently, each dying separately, and the merge reported every part
    # unanswered. The research such a turn asks for feeds the build; it does not decompose it.
    from core.agent_runtime.grounded_mode import is_software_authoring_request

    if is_software_authoring_request(text):
        return []
    try:
        raw = ask_model(PLANNER_SYSTEM_PROMPT, str(text or "").strip())
    except Exception:
        return []
    tasks = parse_plan(raw)
    if len(tasks) < 2:
        # One request, or nothing usable. Either way the ordinary single-turn path is correct.
        return []
    if not verify_plan(tasks, text):
        return []
    if request_is_servable is not None and not plan_is_worth_splitting(tasks, request_is_servable=request_is_servable):
        # Every part must be an independent lookup. A general-knowledge message answers better in
        # one context -- see `plan_is_worth_splitting`.
        return []
    return tasks


def execution_waves(tasks: list[PlannedTask]) -> list[list[PlannedTask]]:
    """Group tasks into waves that may each run concurrently.

    Wave 0 is everything with no dependencies, wave 1 is everything whose dependencies are all in
    wave 0, and so on. A flat fan-out would be wrong for "the weather in the city where the race
    is" -- that request genuinely needs another's answer first, and running it in parallel produces
    a confident wrong answer rather than a slow right one.

    A cycle or a dangling dependency cannot come out of `parse_plan` (it rejects any dependency on
    a later or negative index), but if one ever did, the remaining tasks are emitted as a final
    wave rather than dropped -- losing a request is the defect this whole module exists to fix.
    """
    remaining = {task.index: task for task in tasks}
    done: set[int] = set()
    waves: list[list[PlannedTask]] = []
    while remaining:
        ready = [task for task in remaining.values() if set(task.depends_on) <= done]
        if not ready:
            waves.append(sorted(remaining.values(), key=lambda item: item.index))
            break
        ready.sort(key=lambda item: item.index)
        waves.append(ready)
        for task in ready:
            done.add(task.index)
            remaining.pop(task.index, None)
    return waves


class PlannedTaskNeedsApprovalError(Exception):
    """`run_one` raises this when a planned sub-task's tool call needs operator approval.

    This is not a failure -- the task is waiting on the user, not broken -- and `run_plan` must
    tell the two apart. Before this existed, an approval-pending sub-turn's own "Manual mode
    requires approval..." text was read as `run_one`'s return value like any other answer, so it
    was folded into `merge_outcomes`'s successfully-answered blocks and concatenated with whatever
    other parts of the plan DID answer -- one part of the reply silently contradicting the other.
    """


@dataclass
class TaskOutcome:
    """What actually happened to one planned request."""

    task: PlannedTask
    answer: str = ""
    error: str = ""
    outcome_kind: str = ""  # "" (ordinary success/failure) | "pending_approval"
    pending_message: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.answer.strip()) and not self.error and self.outcome_kind != "pending_approval"

    @property
    def needs_approval(self) -> bool:
        return self.outcome_kind == "pending_approval"


def run_plan(
    tasks: list[PlannedTask],
    *,
    run_one: Callable[[PlannedTask, dict[int, TaskOutcome]], str],
    max_workers: int = 4,
) -> list[TaskOutcome]:
    """Run every planned request, concurrently within each dependency wave.

    `run_one(task, done)` answers one request; `done` maps already-finished indices to their
    outcomes so a dependent request can read what it waited for. Anything it raises is captured
    against that request alone -- one failing request must never cost the answers to the others,
    which is the entire reason a person asked several things in one message.

    Falls back to sequential execution if the worker pool cannot start, because a wrong answer is a
    defect and a slow answer is not.
    """
    outcomes: dict[int, TaskOutcome] = {}
    for wave in execution_waves(tasks):
        if len(wave) == 1 or max_workers <= 1:
            for task in wave:
                outcomes[task.index] = _run_single(task, run_one, outcomes)
            continue
        try:
            import contextvars
            from concurrent.futures import ThreadPoolExecutor

            snapshot = dict(outcomes)
            with ThreadPoolExecutor(max_workers=min(max_workers, len(wave))) as pool:
                futures = {
                    pool.submit(
                        contextvars.copy_context().run,
                        _run_single,
                        task,
                        run_one,
                        snapshot,
                    ): task
                    for task in wave
                }
                for future, task in futures.items():
                    try:
                        outcomes[task.index] = future.result()
                    except Exception as exc:  # a pool-level fault, not the task's own
                        outcomes[task.index] = TaskOutcome(task=task, error=type(exc).__name__)
        except Exception:
            for task in wave:
                if task.index not in outcomes:
                    outcomes[task.index] = _run_single(task, run_one, outcomes)
    # R1g I5 — OUTCOME PARITY: exactly one owned outcome per planned task. The
    # old return comprehension filtered on `task.index in outcomes`, so a task
    # whose execution silently vanished (a sabotaged or future-buggy wave seam)
    # disappeared from the list and the merge published the survivors as
    # ordinary success — the exact absent-demand defect this module exists to
    # make impossible. A task with no recorded outcome is materialized as an
    # EXPLICIT failure naming it.
    ordered = sorted(tasks, key=lambda item: item.index)
    results: list[TaskOutcome] = []
    for task in ordered:
        outcome = outcomes.get(task.index)
        if outcome is None:
            outcome = TaskOutcome(
                task=task, error="no outcome recorded for this request"
            )
        results.append(outcome)
    return results


def _run_single(
    task: PlannedTask,
    run_one: Callable[[PlannedTask, dict[int, TaskOutcome]], str],
    done: dict[int, TaskOutcome],
) -> TaskOutcome:
    try:
        answer = str(run_one(task, done) or "").strip()
    except PlannedTaskNeedsApprovalError as exc:
        return TaskOutcome(task=task, outcome_kind="pending_approval", pending_message=str(exc).strip())
    except Exception as exc:
        return TaskOutcome(task=task, error=f"{type(exc).__name__}: {exc}"[:200])
    if not answer:
        return TaskOutcome(task=task, error="no answer returned")
    return TaskOutcome(task=task, answer=answer)


def merge_outcomes(outcomes: list[TaskOutcome]) -> str:
    """Compose one reply from every planned request, naming what did not work.

    Reporting the failures is the hard requirement, not the assembly. Returning three answers out
    of four in silence rebuilds "answered gold, dropped silver" one level up -- the exact defect
    this whole mechanism exists to remove. A person who asked four things and reads three answers
    has no way to tell which question was ignored.

    A part waiting on operator approval is neither: it did not fail and it is not answered, and its
    own "needs approval" text must never be concatenated into the parts that DID answer as though
    it were one more successful paragraph -- that mixing is what made an approval prompt and a
    correct answer read as one contradictory reply before this distinction existed.
    """
    answered = [outcome for outcome in outcomes if outcome.ok]
    pending = [outcome for outcome in outcomes if outcome.needs_approval]
    failed = [outcome for outcome in outcomes if not outcome.ok and not outcome.needs_approval]
    blocks = [outcome.answer.strip() for outcome in answered]
    if pending:
        lines = [
            f"- {outcome.task.request}" + (f" ({outcome.pending_message})" if outcome.pending_message else "")
            for outcome in pending
        ]
        header = (
            "This part of your message needs your approval before I can run it:"
            if len(pending) == 1
            else "These parts of your message need your approval before I can run them:"
        )
        blocks.append(header + "\n" + "\n".join(lines))
    if failed:
        # A failed part's detail reaches the reader through `reader_facing_reason`, never raw.
        # `outcome.error` is minted as ``f"{type(exc).__name__}: {exc}"`` by `_run_single`, and
        # the note under that prefix can itself carry internals (an OSError text, a path); the
        # composition seam is where internals must come back out -- measured live 2026-09-08,
        # "(RuntimeError: qwen2.5:7b failed...)" was served and then reprinted by the
        # publication gate's withheld-statements notice. The scrubber is fail-closed: a shape
        # it does not recognise renders a generic completion phrase rather than pass through.
        from core.conductor.compose import reader_facing_reason

        lines = [
            f"- {outcome.task.request}"
            + (
                f" ({reader_facing_reason(outcome.error)})"
                if outcome.error and outcome.error != "no answer returned"
                else ""
            )
            for outcome in failed
        ]
        header = (
            "I could not answer this part of your message:"
            if len(failed) == 1
            else "I could not answer these parts of your message:"
        )
        blocks.append(header + "\n" + "\n".join(lines))
    return "\n\n".join(block for block in blocks if block).strip()


def plan_is_worth_splitting(tasks: list[PlannedTask], *, request_is_servable: Callable[[str], bool]) -> bool:
    """True only when EVERY planned request is one a deterministic lane can answer on its own.

    This is the rule that decides when splitting helps, and it is not a word list -- it asks the
    runtime's own recognisers whether each request is something they can serve.

    Measured across four live drives, the split helps exactly when the parts are independent
    lookups and hurts exactly when they are not:

        "gold price" / "silver price"              both answered
        "weather in Vilnius" / "weather in Riga"    both answered
        "their engine sizes and rims sizes"        "I don't have any context about what they refers to"
        "what tires usually it comes with"         "the context provided doesn't mention tires"

    The failing cases share a cause: a general-knowledge question is answered by ONE model reading
    the whole sentence, which resolves "their" and "it" without help. Splitting takes that context
    away and each fragment arrives subject-less. Hermes answers the same BMW message correctly and
    does not split it -- one context, three topics, one table.

    Two narrower tests were tried first and both broke measured cases: "any pronoun orphans the
    request" wrongly refuses "will it rain in vilnius" (an expletive `it`, with its own subject),
    and "pronoun plus no word shared with a sibling" wrongly refuses that same request while
    wrongly keeping "where most of those cars are sold" on the incidental word `most`. Tuning a
    word rule against the cases in front of it is the mechanism that regressed this runtime three
    times; asking the lanes is not a guess.

    False negative: a splittable message runs as one turn -- exactly today's behaviour. False
    positive is impossible for anything the lanes do not recognise, which is the direction that
    matters.
    """
    if len(tasks) < 2:
        return False
    return all(request_is_servable(task.request) for task in tasks)
