"""Format the final answer FROM the graph's results and a decision already taken.

This module used to hold a verdict. `ComposedAnswer.complete` was described as load-bearing, and it
was -- the dispatch seam refused the turn on it -- which made composition an authority over
shipping alongside the projection's `CompletionVerdict`, the obligation floor's coverage, and a
count of apology lines at the caller. Four authorities over one fact, and the measured result was
the product disagreeing with its own receipt about the same turn.

So the verdict moved out, whole, to `core.conductor.product_decision`, which reduces the execution
report ONCE and hands the answer down. What is left here is formatting, and formatting is all it
can do: there is no flag on `ComposedAnswer` a caller could read as permission to ship.

Every sentence is attributable. `provenance` maps node id -> the exact text that node contributed,
so "which claim came from which executed piece of work" is answerable mechanically instead of by
reading. Nothing is written into the answer that did not come from a node's own renderer, or from
the decision's own failure lines.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.agent_runtime.answer_coverage import unit_is_disclosed_in
from core.conductor.node import ConductorNode, NodeFailureCode, NodeLifecycle, NodeOutcome
from core.conductor.planner import ConductorPlan
from core.conductor.product_decision import ProductDecision
from core.conductor.registry import UNRESOLVED_OPERATION, NodeContext, operation_spec

_NODE_REF_RE = re.compile(r"\[\[node:(?P<node_id>[^\]]+)\]\]")


@dataclass(frozen=True)
class ComposedAnswer:
    """Formatted text and its attribution. Deliberately holds NO verdict about the turn.

    `complete`, `answered_count`, `unserved_count`, `absent_obligations` and
    `reported_obligation_count` all used to live here, and each was read somewhere as a shipping
    decision. Five signals derived from one execution, consulted independently, is five chances for
    the product to disagree with itself -- and it did: an `answered_count >= unserved_count` ratio
    discarded three correct answers, and a non-empty string became `FULFILLED` in the receipt of a
    turn that had served nothing.

    The verdict now belongs to exactly one object, `core.conductor.product_decision.ProductDecision`,
    which is built BEFORE this runs. Composition formats what that decision already established.
    """

    text: str
    provenance: dict[str, str] = field(default_factory=dict)
    #: Nodes whose own renderer contributed a sentence. For the receipt, never for a verdict.
    answered_node_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "answered_node_ids": list(self.answered_node_ids),
            "provenance": dict(self.provenance),
        }


def _entity_label(outcome: NodeOutcome) -> str:
    node = outcome.node
    entity = str(node.arguments.get("entity") or "").strip()
    return entity or node.node_id


def _resolve_node_references(text: str, outcomes: dict[str, NodeOutcome]) -> str:
    """Rewrite `[[node:<id>]]` to that node's entity label.

    A derived operation names its winner by node id because it holds only the results it was
    given, not the plan. Resolving the label here keeps the comparison arithmetic sound -- the
    renderer cannot accidentally name an entity it never compared -- while still producing a
    sentence about a place rather than about an identifier.
    """

    def _replace(match: re.Match[str]) -> str:
        node_id = match.group("node_id").strip()
        target = outcomes.get(node_id)
        return _entity_label(target) if target is not None else node_id

    return _NODE_REF_RE.sub(_replace, text)


# `scheduler.py` stores a failed node's reason as `f"{type(exc).__name__}: {exc}"`, which is exactly
# right for the receipt and exactly wrong for the answer. Measured on the v0.5.0 smoke run
# (QA-050-026): a Lisbon/Madrid comparison whose second temperature never arrived put
# "ValueError: comparison needs at least two dependencies carrying 'temperature_c'; got 1" in front
# of the user. The reason is kept whole on the outcome and the receipt; only what a reader sees is
# rewritten.
_EXCEPTION_PREFIX_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Fault|Timeout|Interrupt))\s*:\s*(?P<detail>.+)$",
    re.DOTALL,
)
# What is left once the class name is gone is still developer prose. These are the ones this
# module's own operations raise, mapped to what the sentence actually means to the person who
# asked. Anything unrecognized used to keep its own text; R3 (AUD-20260829-003) inverted that
# polarity -- see `_looks_like_leaked_internals` below.
_INTERNAL_REASON_PHRASES = (
    ("comparison needs at least two dependencies", "not enough of the values it compares came back"),
    ("no weather observation returned", "no weather reading came back for it"),
    ("no quote returned", "no price came back for it"),
    ("no evaluable arithmetic", "there was no calculation in it to run"),
    ("no searchable terms", "there was nothing specific enough in it to look up"),
    ("no tool seam available", "no tool on this machine can serve it"),
    ("scheduler fault", "the runtime failed while running it"),
    ("no registered operation named", "nothing in this runtime knows how to do it"),
)
_INTERNAL_OPERATION_REASON_RE = re.compile(
    r"^[a-z][a-z0-9_]*\s+is\s+unavailable\s+for\s+this\s+request:\s*(?P<detail>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_CAPABILITY_REASON_RE = re.compile(
    r"^(?:request\s+is\s+outside\s+the\s+.+?\s+capability\s+domain"
    r"|.+?\s+capability\s+(?:does\s+not\s+serve|scope\s+check\s+failed|requires|cannot\s+consume).+"
    r"|.+?\s+cannot\s+satisfy\s+a\s+.+?\s+request\s+in\s+the\s+.+?\s+domain)$",
    re.IGNORECASE,
)

# R3 (AUD-20260829-003). `reader_facing_reason` used to return unrecognized text AS-IS once the
# outer exception-class prefix was stripped -- default-open. Measured leak: an `ImportError`
# raised inside `_weather_run` survived prefix-stripping (nothing after "ImportError: " matched
# any rule above) and the FULL remainder -- including an absolute filesystem path -- reached the
# operator (evidence E001). These patterns catch what a rewrite pass can leave behind: another,
# still-embedded exception-class shape (`"scheduler fault: RuntimeError: pool died"` has one
# fully inside it even though the marker table happens to catch that specific case), a filesystem
# path, or a dotted internal module path. None of this is a substitute for R3's real fix --
# `reason_for_outcome` below, which never lets exception-derived text reach here at all for a
# properly-coded failure -- it is the backstop for text that arrives here WITHOUT a failure code
# (a caller built before this field existed; see `reason_for_outcome`'s tier 2).
_EXCEPTION_SHAPE_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Fault|Timeout|Interrupt)\s*:\s")
#: A string that is NOTHING BUT an exception-class name. "ConnectionError" with no message has
#: no reader-facing content to keep, and a composer that received it raw served a bare class
#: token where a reason belonged (measured 2026-09-08 on the planned-task failure path).
_EXCEPTION_NAME_ONLY_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Fault|Timeout|Interrupt)$")
_FILESYSTEM_PATH_MARKERS = ("/Users/", "/home/", "/private/", "/var/", "/tmp/", "site-packages")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])/[\w.-]+(?:/[\w.-]+)+")
_INTERNAL_MODULE_PATH_RE = re.compile(r"\bcore\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\b")


def _looks_like_leaked_internals(text: str) -> bool:
    """True when `text` still carries a shape only a runtime internal, never a person, would write."""
    if _EXCEPTION_SHAPE_RE.search(text):
        return True
    if _EXCEPTION_NAME_ONLY_RE.match(text.strip()):
        return True
    if any(marker in text for marker in _FILESYSTEM_PATH_MARKERS):
        return True
    if _ABSOLUTE_PATH_RE.search(text):
        return True
    return bool(_INTERNAL_MODULE_PATH_RE.search(text))


def reader_facing_reason(reason: str) -> str:
    """A failure reason with the runtime's internals taken back out of it.

    FAIL CLOSED (R3, AUD-20260829-003): never invents a cause and never hides that the request
    failed, but an unrecognized reason that still looks like leaked runtime internals -- another
    embedded exception-class shape, a filesystem path, an internal module path -- renders a
    generic line instead of being passed through. Only text that survives every check clean keeps
    its own specifics.

    This is the fallback tier for callers that have no `NodeFailureCode` to offer (see
    `reason_for_outcome`, the preferred entry point, which never lets exception-derived text reach
    this function's marker-matching at all for a properly-coded production failure).
    """
    text = " ".join(str(reason or "").split())
    if not text:
        return "not answered"
    match = _EXCEPTION_PREFIX_RE.match(text)
    if match is not None:
        text = match.group("detail").strip()
    operation_match = _INTERNAL_OPERATION_REASON_RE.match(text)
    if operation_match is not None:
        text = operation_match.group("detail").strip()
    if _INTERNAL_CAPABILITY_REASON_RE.match(text):
        return "the available answer path does not safely match this request"
    lowered = text.lower()
    for marker, phrase in _INTERNAL_REASON_PHRASES:
        if marker in lowered:
            return phrase
    if _looks_like_leaked_internals(text):
        return "could not be completed"
    return text


# Codes that can only ever originate from a caught exception (see NodeFailureCode's docstring).
# `reason_for_outcome` NEVER reads `failure_reason`/`failure_detail_full` for these -- the generic
# phrase is the whole answer, regardless of what the exception actually said. This is what makes
# the fallback fail CLOSED: a message shape this module has never seen cannot leak just because no
# rule caught it, because for these codes no rule ever LOOKS at the message.
_GENERIC_REASON_BY_EXCEPTION_CODE: dict[NodeFailureCode, str] = {
    NodeFailureCode.RENDER_FAILED: "the runtime hit an internal fault while preparing this answer",
    NodeFailureCode.NODE_EXCEPTION: "the runtime hit an internal fault while working on this",
    NodeFailureCode.SCHEDULER_FAULT: "the runtime hit an internal fault while running this",
}


def reason_for_outcome(outcome: NodeOutcome) -> str:
    """The reader-facing reason for one node's failure -- the preferred, fail-closed entry point.

    Tier 1: codes that can ONLY originate from a caught exception
    (`_GENERIC_REASON_BY_EXCEPTION_CODE`) map to a fixed generic phrase plus the receipt id.
    `failure_reason` / `failure_detail_full` are NEVER consulted for these -- not "cleaned", not
    read at all -- which is what makes this tier fail closed regardless of what the exception
    said. `RESULT_MISSING_FIELDS`, `PLAN_DEADLINE_EXPIRED`, `NOT_REACHED` are likewise fixed,
    code-only phrases (the specific missing-field names are appended separately by
    `compose_answer` from `outcome.missing_result_fields`, which is structural, not
    exception-derived).

    Tier 2: everything else -- `UNRESOLVED`, `DEPENDENCY_FAILED`, and `NONE`/unrecognized (a
    caller built before this field existed, most commonly a test constructing `NodeOutcome`
    directly). This tier's text is developer/planner-authored, not exception-derived by
    convention -- but "by convention" is not a guarantee: `core/conductor/capabilities.py`'s scope-
    check failure embeds a raw `f"{type(exc).__name__}: {exc}"` INTO an `unresolved_reason` when
    the capability check itself raises. So tier 2 always routes through `reader_facing_reason`,
    which applies its full cleanup (capability/operation phrase rewriting, the marker table) AND
    its own fail-closed leak check as a backstop -- never returned raw, even here.
    """
    code = outcome.failure_code

    if code is NodeFailureCode.CANCELLED:
        # Structural, never exception-derived: the owning turn's cancellation marker
        # fired before the node started, and the fixed phrase is the whole answer.
        receipt_suffix = f" (receipt {outcome.receipt_id})" if outcome.receipt_id else ""
        return "this turn was cancelled before this part could run" + receipt_suffix
    if code is NodeFailureCode.TRANSPORT_FAILED:
        # A network fact with an actionable boundary, named as one -- the live source
        # was unreachable, which is not a runtime fault and sends nobody to debug
        # their own code. The type-derived token rides on the receipt; the phrase is
        # fixed and carries no exception text.
        receipt_suffix = f" (receipt {outcome.receipt_id})" if outcome.receipt_id else ""
        return "the live source for this could not be reached" + receipt_suffix
    if code in _GENERIC_REASON_BY_EXCEPTION_CODE:
        receipt_suffix = f" (receipt {outcome.receipt_id})" if outcome.receipt_id else ""
        return _GENERIC_REASON_BY_EXCEPTION_CODE[code] + receipt_suffix
    if code is NodeFailureCode.RESULT_MISSING_FIELDS:
        return "the runtime did not get back everything it needed to answer this"
    if code is NodeFailureCode.RESULT_UNFULFILLED:
        return reader_facing_reason(outcome.rendered) if outcome.rendered else "the operation established no answer"
    if code is NodeFailureCode.PLAN_DEADLINE_EXPIRED:
        return "the runtime ran out of time before finishing this"
    if code is NodeFailureCode.NOT_REACHED:
        return "the runtime never got to this before the turn ended"

    # Tier 2: UNRESOLVED, DEPENDENCY_FAILED, NONE, or anything unrecognized.
    return reader_facing_reason(str(outcome.failure_reason or outcome.node.unresolved_reason or "not answered"))


# AUD-20260829-003 pass 2 (C1, C2). Every row this module builds has the shape
# "- {subject} — {reason}" -- `_unserved_line`, the DEPENDENCY_FAILED branch, the general branch
# below, and `core.conductor.realization.failure_lines` (consumed via `decision.failure_lines`)
# all follow it. Extracting just the subject (before the em-dash) is what makes cross-row
# comparison mean anything: the SAME slot can fail once from a node with one reason and again from
# the requirement ledger with a completely different reason ("could not be answered: the runtime
# hit an internal fault..." vs "was attempted and did not come back"), and comparing whole lines
# would miss that both name the same ask.
_ROW_SUBJECT_SEPARATOR = " — "


def _row_subject(line: str) -> str:
    """The request-naming portion of one composed row, before the em-dash separator."""
    body = line[2:] if line.startswith("- ") else line
    subject, _, _ = body.partition(_ROW_SUBJECT_SEPARATOR)
    return subject.strip()


def _subjects_overlap(a: str, b: str) -> bool:
    """Whether two request-naming subjects are about the same underlying demand.

    Reuses `answer_coverage.unit_is_disclosed_in` -- BLUE-1's own token-based per-line dedup on
    the accounting/sweep side -- rather than a second, drifting implementation of the same idea.
    Tried BOTH ways round: `unit_is_disclosed_in(x, y)` alone only catches "every content token of
    x is present in y", which is asymmetric by design for BLUE-1's use (x is always the short
    RSS-canonical unit, y the longer served body). Two composed subjects can duplicate either way
    -- measured live: the raw fragment "how much gold can I buy with it" is a token subset of its
    own rephrasing "How much gold can I buy with 1000 EUR?", but not the reverse (the rephrasing
    adds "1000"/"EUR") -- so both directions are checked. Confirmed this does not collide on
    shared incidental tokens: "What is 1000 EUR to RUB?" and "How much gold can I buy with 1000
    EUR?" share "1000"/"EUR" and correctly do NOT overlap either way (their remaining content
    tokens -- rub/eur vs gold/buy -- never fully cover one another).
    """
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    return unit_is_disclosed_in(a, b) or unit_is_disclosed_in(b, a)


def _entity_of(node: ConductorNode) -> str:
    return str(node.arguments.get("entity") or "").strip()


def _same_slot(entity_a: str, text_a: str, entity_b: str, text_b: str) -> bool:
    """Whether two rows are about the same underlying demand.

    Entity identity is checked FIRST and, when both sides have one, is DECISIVE -- not merely a
    tiebreaker. Measured live regression: a single clause naming multiple entities ("get the
    current weather for Kaunas and Tallinn") expands into one node per city, and BOTH nodes carry
    that IDENTICAL clause-level `request_text` -- a text-overlap check alone (the C1 fix, taken in
    isolation) collapsed "Kaunas failed" and "Tallinn failed" into one row, because each city's
    token trivially appears inside the OTHER node's shared text too (both mention both cities).
    Entity equality tells them apart: they are the same node only if their resolved entities match.
    Falls back to subject-text overlap only when at least one side has no resolved entity -- a
    `decision.failure_lines` entry (no node at all), or a node whose operation never extracted one
    (`quantitative_reasoning`, `unresolved`) -- which is the C1/C2 cases this exists for: a raw
    clause fragment and its coreference-resolved rephrasing carry no entity on either side, and
    neither does an `unresolved` node next to the wrong-location node it should suppress.
    """
    entity_a, entity_b = entity_a.strip(), entity_b.strip()
    if entity_a and entity_b:
        return entity_a.casefold() == entity_b.casefold()
    return _subjects_overlap(text_a, text_b)


def _carved_from(node_subject: str, clause_subject: str) -> bool:
    """Whether a node's subject is a piece of the clause (the node was carved out of it)."""
    import re as _re

    node_words = " ".join(_re.findall(r"[0-9a-z]+", str(node_subject or "").casefold()))
    clause_words = " ".join(_re.findall(r"[0-9a-z]+", str(clause_subject or "").casefold()))
    return bool(node_words) and bool(clause_words) and node_words != clause_words and node_words in clause_words


#: The closed class of English interrogatives. Read only by `_suffix_carved_from`, as the
#: boundary between "the leftover head is a preamble" and "the leftover head still asks".
_INTERROGATIVE_HEAD_RE = re.compile(r"\b(?:what|which|who|whom|whose|when|where|why|how)\b")


def _suffix_carved_from(node_subject: str, clause_subject: str) -> bool:
    """Whether the clause is this subject with a LEADING preamble attached in front of it.

    An enumerating lead-in is a clause boundary the planner model keeps attached to the first
    question ("Please answer all three: What is 5+5?"), so the same ask reaches the plan twice:
    once as its own clause (answered) and once as the lead-in superset (unresolvable -- it is not
    itself a question). The node carved from the TAIL answers the superset's only actual demand,
    unlike the embedded carve in `_carved_from` ("Water" resolved to a town inside a water-
    temperature clause), where the fragment answered something the clause never asked.
    """
    import re as _re

    node_words = " ".join(_re.findall(r"[0-9a-z]+", str(node_subject or "").casefold()))
    clause_words = " ".join(_re.findall(r"[0-9a-z]+", str(clause_subject or "").casefold()))
    if not node_words or not clause_words or node_words == clause_words:
        return False
    if not clause_words.endswith(node_words):
        return False
    # A preamble is everything the tail did not ask: once the answered subject is carved off
    # the tail, the leftover head must not still contain a question. The interrogatives are a
    # closed grammatical class (not a topic word list): "answer all three" asks nothing, so the
    # tail absorbed the whole demand; "what is the water temperature in the | baltic sea" still
    # asks WATER TEMPERATURE -- its tail fragment (a place) answered a different thing and the
    # suppression must hold (the AUD-20260829-003 measured pin).
    head = clause_words[: len(clause_words) - len(node_words)].strip()
    return not _INTERROGATIVE_HEAD_RE.search(head)


def _append_unserved_line(unserved: list[tuple[str, str, str]], entity: str, subject: str, candidate: str) -> bool:
    """Append `candidate` to `unserved` unless it names the same slot as a row present (C1).

    The old merge (`if line not in unserved`) was an exact-string check, so a node's raw-fragment
    failure and the requirement ledger's rephrased failure for the SAME slot both survived as
    separate rows -- measured live: the gold slot rendered three rows for one ask. `unserved`
    entries carry `(entity, subject, display_line)` rather than just the display line, so this and
    the C2 gate in `compose_answer` can both use `_same_slot`'s entity-first comparison instead of
    losing that information to a string round-trip through the rendered text. Returns whether the
    line was actually kept, so callers can skip provenance for a dropped duplicate.
    """
    if subject and any(_same_slot(entity, subject, e, s) for e, s, _line in unserved):
        return False
    unserved.append((entity, subject, candidate))
    return True


def _unserved_line(outcome: NodeOutcome) -> str:
    node = outcome.node
    request = str(node.request_text or node.node_id).strip()
    return f"- {request} — {reason_for_outcome(outcome)}"


def compose_answer(
    plan: ConductorPlan,
    outcomes: Sequence[NodeOutcome],
    decision: ProductDecision,
) -> ComposedAnswer:
    """Format the answer FROM the graph's results and the decision that has already been made.

    Every sentence is attributable. `provenance` maps node id -> the exact text that node
    contributed, so "which claim came from which executed piece of work" is answerable mechanically
    instead of by reading. Nothing is written into the answer that did not come from a node's own
    renderer or from the decision's own failure lines.

    This function decides nothing. It cannot refuse the turn, cannot count its way to a verdict and
    has no flag a caller could read as one -- which is what "composer FORMATS" means as code.

    Two passes, not one (AUD-20260829-003 pass 2, C2). A succeeded node is held as a CANDIDATE
    until every unserved row -- node-level AND requirement-level -- is known, then checked against
    all of them: a node the planner proposed that nobody asked for still renders as answer content
    by default (R01's report named this "Layer 2" -- the composer's universe is the plan, not the
    request), which is how two `weather_lookup` nodes for the fragments "Water" and "Baltic Sea"
    (spawned alongside this SAME plan's own `unresolved` node for "What is the water temperature
    in the Baltic Sea?") were served as answers -- a wrong nearby town's air temperature standing
    in for a water-temperature ask the runtime was, in the same body, also refusing. A succeeded
    node whose own subject overlaps a subject this plan reports as unserved is not a second,
    independent answer: it is content for a slot the runtime is simultaneously refusing, and must
    not render as one.
    """
    by_id = {outcome.node.node_id: outcome for outcome in outcomes}

    # (node_id, entity, subject, segment) -- entity is "" when the node extracted none.
    answered_candidates: list[tuple[str, str, str, str]] = []
    # (entity, subject, display_line) -- see `_append_unserved_line` / `_same_slot`.
    unserved: list[tuple[str, str, str]] = []
    provenance: dict[str, str] = {}

    unplannable: set[str] = set()
    unplannable_lines: set[str] = set()
    # A STATED refusal (a runtime-owned clause declined with its own reason: one share of an
    # allocation whose payer is an unresolved ticker, a target nothing quotes, a parts mismatch)
    # is neither a rider the answer covers nor a failed run that holds the slot against the
    # quote beside it. It renders as its own row, always, and suppresses nothing (FINDINGS F15).
    stated_refusal_lines: set[str] = set()
    for node in plan.nodes:
        outcome = by_id.get(node.node_id)
        if outcome is None:
            # Deliberately not skipped silently: a node with no outcome is a missing REDUCTION, and
            # the decision has already stamped this turn INTEGRITY_FAILURE for it.
            continue

        entity = _entity_of(node)
        subject = str(node.request_text or node.node_id).strip()

        if outcome.succeeded and outcome.rendered:
            segment = _resolve_node_references(outcome.rendered, by_id)
            answered_candidates.append((node.node_id, entity, subject, segment))
            continue

        if outcome.state is NodeLifecycle.UNRESOLVED or node.operation == UNRESOLVED_OPERATION:
            segment = _unserved_line(outcome)
            if bool(dict(node.arguments or {}).get("stated_refusal")):
                stated_refusal_lines.add(segment)
            else:
                unplannable.add(node.node_id)
        elif outcome.state is NodeLifecycle.DEPENDENCY_FAILED:
            segment = f"- {node.request_text} — not attempted: {reason_for_outcome(outcome)}"
        else:
            has_reason = bool(outcome.failure_code or outcome.failure_reason)
            detail = reason_for_outcome(outcome) if has_reason else "no result returned"
            if outcome.missing_result_fields:
                detail = f"{detail} (missing: {', '.join(outcome.missing_result_fields)})"
            segment = f"- {node.request_text} — could not be answered: {detail}"
        if _append_unserved_line(unserved, entity, subject, segment):
            provenance[node.node_id] = segment
            if node.node_id in unplannable:
                unplannable_lines.add(segment)

    # Requirement-level failures, named by the realization's own display SUBJECT -- "EUR to JPY",
    # never the amount that happened to be the first unresolved slot, and never a node's request
    # text. Taken from the decision rather than recomputed, because recomputing is how the answer
    # came to disagree with the receipt about the same turn. Same C1 dedup as the node loop above:
    # a requirement-level line naming a slot a node already named (in different words, or with a
    # different reason) must not become a second row. No node backs a requirement line, so it
    # carries no entity -- `_same_slot` falls back to subject-text overlap for it, same as today.
    for line in decision.failure_lines:
        _append_unserved_line(unserved, "", _row_subject(line), line)

    # C2 gate: every unserved subject is now known. A candidate that names the same slot as one of
    # them is content for a slot the SAME body simultaneously refuses -- suppressed, never a
    # "second opinion" answer.
    # The gate is typed by what the same-slot row IS. A node that RAN and FAILED or was refused holds
    # the slot: content for it from another node is a second opinion and is suppressed (C2). A node
    # the planner could not bind an operation to (UNRESOLVED) refused nothing -- it is a clause the
    # plan had no operation for, typically a rider or clarification of a clause that was answered
    # ("what is TRY?" answered; "i meant money TRY" unplannable). There the answer holds the slot
    # and the unplannable row is dropped as covered. Measured 2026-09-08: the only served node of an
    # eight-clause message was suppressed by its own clarification and the reply was all refusals.
    answered: list[str] = []
    answered_ids: list[str] = []
    covered_lines: set[str] = set()
    for node_id, entity, subject, segment in answered_candidates:
        same_slot = [
            (u_entity, u_subject, u_line)
            for u_entity, u_subject, u_line in unserved
            if u_line not in stated_refusal_lines and _same_slot(entity, subject, u_entity, u_subject)
        ]
        if same_slot and any(
            u_line not in unplannable_lines
            or (
                _carved_from(subject, u_subject)
                and not _suffix_carved_from(subject, u_subject)
            )
            for _e, u_subject, u_line in same_slot
        ):
            # A row that RAN and failed holds its slot; so does an unplannable clause this node was
            # carved out of ("Water" resolved to Newberry Springs beside the unresolved "water
            # temperature in the Baltic Sea" -- a wrong resolution of that very clause). A
            # SUFFIX carve is the other thing: the unplannable row is the lead-in superset of an
            # ask this node answered in full, so the row is this answer's duplicate, not its
            # rival -- suppressing the answered line left a computed "5 + 5 = 10" unserved beside
            # an unmet demand that asked exactly that (measured 2026-09-08).
            continue
        for _e, _s, u_line in same_slot:
            covered_lines.add(u_line)
            provenance[f"covered_by:{node_id}"] = u_line
        answered.append(segment)
        answered_ids.append(node_id)
        provenance[node_id] = segment

    unserved_lines = [line for _entity, _subject, line in unserved if line not in covered_lines]

    # An aggregate may replace only visible, fulfilled sources that its registered
    # representation contract can reproduce from the current dependency results.
    represented: dict[str, tuple[str, str]] = {}
    visible = set(answered_ids)
    for node_id, segment in zip(answered_ids, answered, strict=True):
        outcome = by_id[node_id]
        spec = operation_spec(outcome.node.operation)
        if spec is None or spec.represented_dependency_segments is None:
            continue
        dependencies = outcome.node.depends_on
        if not dependencies or any(dep not in visible or not by_id[dep].fulfilled for dep in dependencies):
            continue
        context = NodeContext(shared_context=getattr(plan, "shared_context", None),
            dependency_results={dep: by_id[dep].result for dep in dependencies})
        try:
            if str(spec.render(outcome.node, outcome.result) or "").strip() != outcome.rendered:
                continue
            fragments = dict(spec.represented_dependency_segments(outcome.node, outcome.result, context))
            if set(fragments) != set(dependencies) or any(
                not isinstance(fragment, str) or not fragment or fragment not in segment
                for fragment in fragments.values()
            ):
                continue
        except Exception:
            continue
        for dependency, fragment in fragments.items():
            represented.setdefault(dependency, (node_id, fragment))
    if represented:
        answered = [segment for node_id, segment in zip(answered_ids, answered, strict=True)
                    if node_id not in represented]
        for node_id, (presenter, fragment) in represented.items():
            provenance[node_id] = fragment
            provenance["represented_by:" + node_id] = presenter

    sections: list[str] = []
    if answered:
        # Each operation returns a complete Markdown block, not a row of its
        # neighbour's table or list. Keep the block boundary through composition.
        sections.append("\n\n".join(answered))
    if unserved_lines:
        # Named plainly rather than buried. A request the runtime could not serve is a fact about
        # the answer, and a reader who cannot see it will read the rest as complete.
        sections.append("Could not be answered:\n" + "\n".join(unserved_lines))

    return ComposedAnswer(
        text="\n\n".join(sections).strip(),
        provenance=provenance,
        answered_node_ids=tuple(answered_ids),
    )


__all__ = ["ComposedAnswer", "compose_answer", "reader_facing_reason", "reason_for_outcome"]
