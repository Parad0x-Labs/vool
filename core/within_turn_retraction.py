"""A turn that takes an instruction back has not given it.

Measured on the served surface, 2026-08-15::

    U: Search my local workspace for `password.txt`. WAIT, STOP. Cancel the search immediately.
       Instead, write a 2-line javascript function that returns "Hello". ...
    A: (empty answer)
       Work log: workspace.search_text  query=password.txt  ->  no_results
                 web0.open_builder_draft
       32,151 tokens

The search the user cancelled mid-sentence ran anyway, against a filename they had just named as
the thing not to look for.

WHY THE NEIGHBOURING TURN WAS FINE. The same session opened with "Trigger a web search for the
current weather in Paris. WAIT! Stop. Do NOT search the web." and no search ran -- because
`analyze_retrieval_constraints` recognises "do NOT search the web" and returns `has_prohibition=True`.
It returns False for "Cancel the search immediately", for "Cancel that", and for "Actually ignore
that". Prohibition vocabulary was covered; RETRACTION vocabulary was not, and the two are different
speech acts: one forbids a class of action, the other withdraws an instruction already given.

THE RULE, and it is about position rather than about searching or the web:

    a retraction cue withdraws what came BEFORE it in the same turn; what follows is the live
    request.

So the runtime plans from the live remainder. The withdrawn text is never treated as an instruction
-- not by a classifier, not by a planner, not by a model choosing a tool.

DELIBERATELY NARROW. A cue only retracts when it stands as its own clause and something follows it
to do instead; "stop" inside "stop words are removed by the tokenizer" is a topic, and "wait for the
build to finish" is an instruction. Getting this wrong in the permissive direction silently discards
what the user asked for, which is the same harm in the other direction.
"""

from __future__ import annotations

import re

__all__ = [
    "intake_request_text",
    "live_request_after_retraction",
    "retraction_cue_span",
    "turn_retracts_an_instruction",
]

#: A conjunction right after the cue's object CONTINUES the same request instead of retracting it:
#: "cancel the request and tell me why it failed" is ONE request -- cancel something, then explain
#: -- and narrowing it to the second half silently drops the work the user asked for. Measured
#: before this guard: that turn narrowed to "tell me why it failed". The scan stays inside the
#: cue's own clause: words are separated by plain spaces only, so sentence punctuation ends it --
#: "Cancel the search immediately. Instead, list the files and count them" never reaches the "and"
#: on the far side of the period, and the retraction still fires.
_SAME_REQUEST_CONJUNCTION = r"(?:and|then|plus|also|&)"
_OBJECT_CONTINUES_SAME_REQUEST = (
    rf"(?![ \t]*,?[ \t]*(?:[\w'()-]{{1,14}}[ \t]+){{0,3}}{_SAME_REQUEST_CONJUNCTION}\b)"
)

#: The withdrawable objects stay a CLOSED noun set on purpose -- "cancel the subscription" is a
#: TASK, and the earlier open object pattern swallowed "cancel the subscription and" and treated a
#: real request as a retraction of itself. Multi-word nouns sit first so the alternation prefers
#: them whole.
_WITHDRAWN_NOUN = (
    r"(?:file\s+read|network\s+request|api\s+call"
    r"|search|request|task|command|action|above|last|previous|first"
    r"|read|lookup|fetch|download|query|call|operation|scan)"
)

#: The object may carry one leading modifier ("the FINANCIAL lookup", "the NETWORK request") and
#: one trailing word ("the search QUERY") -- measured 2026-08-15: "cancel the network request"
#: missed because the old pattern allowed only a trailing word, so a leading modifier broke the
#: match even though "request" was in-set. Neither slot may absorb a conjunction: with a plain \w+
#: the engine consumed " and" as the trailing word and stepped over the continuation guard.
_OBJECT_SLOT_WORD = rf"(?!{_SAME_REQUEST_CONJUNCTION}\b)[\w'-]+"

#: The cue itself. Each alternative is a withdrawal of something already said, not a prohibition of
#: something in general -- `analyze_retrieval_constraints` already covers "do not X", and a turn may
#: reasonably carry both.
_RETRACTION_CUE = (
    r"(?:"
    r"wait[!,.\s]+(?:stop|no|hold\s+on|scratch\s+that)"
    # R1g: 'no wait' is the commutation of 'wait, no' above and the correction
    # users actually type ("..., no wait, ... instead" minted BOTH the withdrawn
    # demand and the correction at base — found by the R1g corrections gauntlet).
    # A cue only, never a phrase about waiting: the continuation requirement
    # below still applies, so "no wait for the build" does not retract.
    r"|no[!,.\s]+wait\b"
    r"|stop[!,.\s]+(?:cancel|scratch\s+that|ignore\s+that|do\s+not|don't)"
    # "cancel/abort/halt the search" withdraws -- a withdrawn action is withdrawn whatever verb
    # names it (measured 2026-08-15: "Abort the financial lookup" ran the lookup because only
    # "cancel" was read). Possessives stay excluded ("cancel MY subscription" is a real task), and
    # the conjunction guard keeps "cancel the download and tell me which mirror is faster" whole.
    rf"|(?:cancel|abort|halt|scrap|call\s+off)\s+"
    rf"(?:that|it|(?:the\s+)?(?:{_OBJECT_SLOT_WORD}\s+)?{_WITHDRAWN_NOUN}(?:\s+{_OBJECT_SLOT_WORD})?)"
    rf"{_OBJECT_CONTINUES_SAME_REQUEST}"
    r"|(?:actually|ok(?:ay)?)[,\s]+(?:ignore|forget|scratch|disregard|skip)\s+(?:that|it|the\s+above|what\s+i\s+said)"
    r"|(?:ignore|forget|scratch|disregard)\s+(?:all\s+(?:of\s+)?)?(?:that|it|this|the\s+above|what\s+i\s+(?:just\s+)?said)"
    r"|i\s+changed\s+my\s+mind"
    r"|never\s*mind(?:\s+that)?"
    r"|scratch\s+that"
    r"|belay\s+that"
    r")"
)

#: What makes a cue a RETRACTION rather than a passing phrase: a live request has to follow it.
#: Without this, "cancel the subscription and tell me why" -- where cancelling IS the request --
#: would withdraw itself.
_CONTINUATION = (
    r"(?:instead|rather|in\s+its\s+place|new\s+task|new\s+request|"
    r"just\s+|please\s+|now\s+|let's\s+|lets\s+|"
    # Ordinary imperative openers. A retraction is followed by what to do instead, and this is what
    # "instead" looks like when the user does not use the word.
    r"(?:write|give|tell|show|make|do|answer|explain|draft|create|generate|list|find|send|add|"
    r"set|run|build|summari[sz]e|translate|check|open|read|search|help|describe|compare)\s+|"
    # Interrogative openers. A question is the most common follow-up form, and it was entirely
    # absent: measured 2026-08-15 (audit, executed probes) "Cancel the search immediately. What is
    # 25+20?" produced NO narrowing, so the withdrawn search stayed the live request for the
    # planner and the research gate -- the exact incident shape this module exists to close.
    r"(?:what|whats|what's|how|why|who|whos|who's|where|when|which|is|are|was|were|can|could|"
    r"would|should|do|does|did)\s+)"
)

#: A few words may sit between the cue and the live request without breaking the pair. Measured
#: live 2026-08-15 (hostile seed 777): "search my workspace for wallet_backup.zip. WAIT no, cnacel
#: that, just tell me 25+20" ran the withdrawn search, because "WAIT no" IS a recognised cue but
#: the continuation check demanded the request immediately after it -- and the user's misspelled
#: second cue ("cnacel") sat in the gap. A typo in one cue must not resurrect a search the same
#: sentence cancelled twice. Bounded to four short words so a real clause can never be swallowed:
#: both a cue AND a continuation are still required, only their adjacency is relaxed.
#: LAZY, not greedy. The first version ate the request itself: on "Cancel that, show me the summary
#: instead." it consumed "show me the summary " as noise and left "instead." -- so a working
#: retraction stopped working. Lazy matching takes the EARLIEST continuation, which is the request.
#: The noise may never swallow ANOTHER cue's own words. Without this exclusion, "WAIT, STOP. Cancel
#: the search immediately. Instead, ..." matched as cue("WAIT, STOP.") + noise("Cancel the") +
#: request("search ...") -- because "search" is a legitimate request verb -- and the live remainder
#: became "search immediately. Instead, ...", putting the withdrawn search BACK. Excluding cue-
#: initial words leaves that position unmatched, so the later, correct cue ("Cancel the search")
#: is the one that binds.
#: Determiners are excluded for a different reason than cue words: a word after "the" is a NOUN
#: OBJECT, not a new imperative. "STOP. Cancel" + noise("the") + "search ..." read the cue's own
#: object as the fresh request and handed the withdrawn search straight back.
_CUE_WORD = (
    r"(?:cancel|abort|halt|scrap|call|off|ignore|forget|scratch|disregard|skip|stop|wait|never"
    r"|belay|changed|the|a|an|my|your|our)"
)
_CUE_TO_REQUEST_NOISE = rf"(?:[\s,.!;:-]+(?!{_CUE_WORD}\b)[\w']{{1,12}}){{0,4}}?[\s,.!;:-]*"

_RETRACTION_RE = re.compile(
    rf"(?<![a-z]){_RETRACTION_CUE}\b{_CUE_TO_REQUEST_NOISE}(?={_CONTINUATION})",
    re.IGNORECASE,
)


def intake_request_text(text: str) -> str:
    """The text every reasoning consumer sees for this turn: the live request, narrowed ONCE.

    This is the single intake seam. When the turn retracts an instruction mid-sentence, the
    remainder after the last retraction cue is the request; otherwise the turn passes through
    byte-identical. Callers narrow HERE, at interpretation intake, rather than sprinkling per-lane
    checks -- a classifier, a planner, and a research gate that each re-decide retraction drift
    apart, and the lane that forgets is the one that runs the withdrawn instruction.

    Idempotent by construction: the remainder contains no retraction cue with a live continuation,
    so narrowing a narrowed text returns it unchanged. `None` from the detector means "nothing
    retracted", never "everything retracted", so this function always returns a request.
    """

    body = str(text or "")
    live = live_request_after_retraction(body)
    return live if live is not None else body


def turn_retracts_an_instruction(text: str) -> bool:
    """Whether this turn takes back something it said earlier in the same turn."""

    return live_request_after_retraction(text) is not None


def retraction_cue_span(text: str) -> tuple[int, int] | None:
    """The ``[start, end)`` of the LAST retraction cue, under exactly the rule that makes it a
    retraction (a live request of at least three words must follow it), else None.

    Exposed for the RequestGraph producer, which must record the cue as source and supersede what
    came before it -- reading the same match this module acts on rather than re-deriving one.
    """

    body = str(text or "")
    if not body.strip():
        return None
    last: re.Match[str] | None = None
    for match in _RETRACTION_RE.finditer(body):
        last = match
    if last is None:
        return None
    remainder = body[last.end() :].strip()
    if len(remainder.split()) < 3:
        return None
    return (last.start(), last.end())


def live_request_after_retraction(text: str) -> str | None:
    """The part of the turn that is still a live instruction, or None when nothing was retracted.

    Returns the text AFTER the last retraction cue. The last one rather than the first: a turn may
    change its mind twice ("do A. no wait, do B. actually, do C"), and only the final request
    stands.

    None means "no retraction here" -- never "everything was retracted". A cue with nothing usable
    after it returns None too, because withdrawing the entire turn would leave the runtime with no
    request at all, and a turn that says only "cancel that" is a message about a PREVIOUS turn,
    which is a different lane's problem.
    """

    body = str(text or "")
    if not body.strip():
        return None
    last: re.Match[str] | None = None
    for match in _RETRACTION_RE.finditer(body):
        last = match
    if last is None:
        return None
    remainder = body[last.end() :].strip()
    if len(remainder.split()) < 3:
        return None
    return remainder
