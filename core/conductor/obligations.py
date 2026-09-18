"""What the USER asked for, owned by the runtime and independent of who decides how to serve it.

The architecture this restores is one sentence long:

    USER REQUEST -> CANONICAL OBLIGATION SET -> a planner decides HOW -> execution

and the architecture it replaces is the same sentence with the second box deleted, so that whatever
clauses a model happened to emit BECOME the user's requirements. `core.agent_runtime.turn_planner`
states the missing half outright in `verify_plan`:

    "It deliberately does NOT require the reverse (that the plan covers every word of the
     original) ... an incomplete plan is caught by the caller answering fewer things, not by a
     wrong thing being answered."

The compensating control named there does not exist. No caller counts what the user asked for, so
"answering fewer things" is not observed by anything: `compose_answer` compares the plan against
itself -- every node it planned has an outcome, therefore nothing is missing -- and reports a
complete turn while an obligation is accounted for nowhere. Measured at 866cf12a on a two-asset
request: one asset was quoted, the other appeared in no node, no outcome and no gap line, and
`complete` was True.

Two stages can shrink the set, and both were measured doing it, by mechanisms that have nothing to
do with each other:

* the planner model may simply not emit a clause;
* an operation's recognizer may expand a clause into fewer nodes than the clause names subjects.
  `_market_expand("the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold in USD")`
  yields Bitcoin alone, and `_market_expand("BTC live price, Gold live price")` yields Gold alone.

That second pair is the argument for repairing the invariant rather than either recognizer. Two
independent mechanisms produce the same erosion, so a recognizer fix removes one instance of a
class that has at least two, and the class is "a stage that declines part of the request may make
it disappear instead of reporting it".

**This module is not a planner and must never become one.** It chooses no operation, orders no
work, produces no node and answers nothing. It states what must be accounted for, and it is the
`core.task_decomposer` split-and-fill-from-templates architecture that `core.conductor.planner`
exists to replace only if someone gives it an opinion about HOW. It has none.

The obligation states are deliberately three, not two:

    SATISFIED            a node served it and succeeded
    EXPLICITLY_UNSERVED  the turn names it and says it was not served
    ABSENT               accounted for nowhere -- the defect. Never a legal terminal state.

Absence is not success. A turn holding an ABSENT obligation is incomplete however green everything
downstream looks.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Words that carry no obligation. Kept identical in spirit to `turn_planner._FUNCTION_WORDS`:
#: this module measures the SAME relation that `verify_plan` measures, in the other direction, so
#: measuring it over a different vocabulary would make the two disagree about the same plan.
from core.agent_runtime.turn_planner import _content_words
from core.turn_ir import ClauseKind

_WORD_RE = re.compile(r"[a-z0-9']+")


class ObligationState(str, Enum):
    """The TERMINAL outcome of an obligation. Every value here is assigned by production code.

    An earlier version of this enum was declared and never used: the floor reported obligations as
    free text and collapsed three different failures into one `unresolved_reason`, so nothing
    downstream could tell "the planner never asked for this" from "no capability exists" from "it
    ran and broke". Those need different answers and different reader-facing sentences, so they get
    different states.

    `PLANNER_OMITTED` is deliberately absent. How an obligation was DISCOVERED is provenance, not an
    outcome -- see `ObligationOrigin`. A serviceable obligation the planner forgot must still be
    executed, so "the planner omitted it" can never be where an obligation comes to rest.
    """

    #: A node realized this obligation and succeeded.
    SATISFIED = "satisfied"
    #: Realized into a node that ran and failed. A real attempt with a real failure.
    EXECUTION_FAILED = "execution_failed"
    #: No registered operation can turn this subject into work. Nothing was attempted.
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    #: A policy or permission boundary refused it. Distinct from "cannot" -- this is "may not".
    POLICY_BLOCKED = "policy_blocked"
    #: Realized and dispatched, outcome not yet known. Never a terminal state.
    PLANNED = "planned"
    #: Accounted for nowhere. The defect. Never a legal terminal state.
    ABSENT = "absent"


#: Outcomes an obligation may legitimately come to rest in.
TERMINAL_OBLIGATION_STATES = frozenset(
    {
        ObligationState.SATISFIED,
        ObligationState.EXECUTION_FAILED,
        ObligationState.CAPABILITY_UNAVAILABLE,
        ObligationState.POLICY_BLOCKED,
    }
)


class ObligationOrigin(str, Enum):
    """How this obligation was DISCOVERED. Provenance, never an outcome.

    Kept orthogonal to `ObligationState` because the two answer different questions and collapsing
    them is what made the previous floor unable to say anything useful. An obligation the planner
    omitted and the runtime then executed successfully is `PLANNER_OMITTED` + `SATISFIED`, and both
    halves are true and worth recording: the answer is complete, and the planner needs work.
    """

    #: The planner named it in a clause and the runtime served it.
    PLANNED_BY_MODEL = "planned_by_model"
    #: The user's request names this subject and no clause carried it forward.
    PLANNER_OMITTED = "planner_omitted"
    #: A clause named it and the serving operation's expansion did not produce a node for it.
    RECOGNIZER_OMITTED = "recognizer_omitted"
    #: A whole sentence of the request that no clause carried forward.
    CLAUSE_OMITTED = "clause_omitted"


#: Retained so existing readers of `ObligationSource` keep working; origin is the richer answer.
class ObligationSource(str, Enum):
    """Which runtime-owned rule established this obligation."""

    CLAUSE_RESIDUE = "clause_residue"
    SUBJECT_RESIDUE = "subject_residue"


@dataclass(frozen=True)
class Obligation:
    """One thing the user asked for, in the user's own words.

    `text` is never generated. It is the span of the request this obligation was read from, so an
    answer that reports a gap reports it in the words the reader used.
    """

    obligation_id: str
    text: str
    source: ObligationSource
    #: The clause this obligation was found in, when it came from one.
    clause_text: str = ""
    #: How it was discovered. Provenance for the receipt and for the reader-facing sentence.
    origin: ObligationOrigin = ObligationOrigin.PLANNED_BY_MODEL
    #: Which registered operation's domain this subject belongs to, when it has one. This is what
    #: makes realization possible: the obligation knows who could serve it.
    operation: str = ""
    #: The subject in the form that operation's realizer understands.
    subject: str = ""
    #: Obligations that must be SATISFIED before this one's work means anything. Carried HERE, on
    #: the obligation, rather than derived from whatever nodes the planner happened to produce --
    #: a prerequisite the planner omitted never becomes a node, so a node-derived edge set cannot
    #: express "this needs something that is missing", which is exactly the case that matters.
    requires: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "text": self.text,
            "source": self.source.value,
            "clause_text": self.clause_text,
            "origin": self.origin.value,
            "operation": self.operation,
            "subject": self.subject,
            "requires": list(self.requires),
        }


@dataclass(frozen=True)
class ObligationOutcome:
    """One obligation's terminal state, and the node that decided it."""

    obligation: Obligation
    state: ObligationState
    node_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation.obligation_id,
            "text": self.obligation.text,
            "origin": self.obligation.origin.value,
            "state": self.state.value,
            "node_id": self.node_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ObligationCoverage:
    """The verdict over every obligation the user's request established."""

    outcomes: tuple[ObligationOutcome, ...] = ()

    @property
    def absent(self) -> tuple[ObligationOutcome, ...]:
        return tuple(item for item in self.outcomes if item.state is ObligationState.ABSENT)

    @property
    def unfinished(self) -> tuple[ObligationOutcome, ...]:
        """Obligations that never reached a state anyone may ship, including PLANNED."""
        return tuple(
            item for item in self.outcomes if item.state not in TERMINAL_OBLIGATION_STATES
        )

    @property
    def covered(self) -> bool:
        """True when every obligation reached a state a reader could be told.

        Says nothing about whether they SUCCEEDED: a turn that executed what it could and reports a
        typed failure for the rest is covered. What is not covered is an obligation still sitting in
        ABSENT or PLANNED when the answer is being written -- the first means the runtime lost it,
        the second means it never found out what happened.
        """
        return not self.unfinished

    def state_of(self, obligation_id: str) -> ObligationState:
        for item in self.outcomes:
            if item.obligation.obligation_id == obligation_id:
                return item.state
        return ObligationState.ABSENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "covered": self.covered,
            "obligation_count": len(self.outcomes),
            "outcomes": [item.to_dict() for item in self.outcomes],
            "unfinished": [item.to_dict() for item in self.unfinished],
        }


# --- clause-level: what the planner did not carry forward ----------------------------------------


#: Content the user wrote that names no work: an instruction about HOW to answer, or a courtesy.
#: These are the words `verify_plan` says a plan legitimately strips, and a floor that demanded
#: them would refuse every well-formed plan. Structural rather than topical -- none of them names
#: a subject, a domain or a value.
_NON_OBLIGATION_WORDS = frozenset(
    {
        "please", "pls", "plz", "thanks", "thank", "thx", "kindly", "asap", "now", "then",
        "also", "additionally", "furthermore", "finally", "next", "first", "second", "third",
        "briefly", "brief", "detail", "detailed", "clearly", "exactly", "precisely", "properly",
        "step", "steps", "concise", "short", "quick", "quickly", "simple", "simply",
        "once", "have", "using", "use", "give", "show", "tell", "say", "make", "want", "need",
        "would", "could", "should", "take", "get", "got", "put", "let", "like", "just", "really",
        "very", "much", "many", "some", "any", "all", "both", "each", "every", "one", "two",
        "today", "currently", "current", "live", "latest",
    }
)


def _obligation_words(text: str) -> set[str]:
    return {word for word in _content_words(text) if word not in _NON_OBLIGATION_WORDS}


#: Sentence boundaries, plus the line breaks a user types instead of punctuation. Kept in step with
#: `shared_context._SENTENCE_SPLIT_RE`: a closing quote or bracket after the stop is still the end
#: of the sentence, and without that the quoted material and the request after it fuse into one.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])[\"'”’»)\]]*\s+|\n+")


def request_sentences(request: str) -> tuple[str, ...]:
    """The user's request in the pieces they wrote it in. Structural only -- no topic, no keyword.

    This is NOT a decomposition. It selects no operation, orders nothing and produces no node; it
    only says where the user put full stops, so a piece that no clause carried forward can be
    quoted back in their own words.
    """
    source = str(request or "")
    return tuple(" ".join(source[start:end].split()) for start, end in _sentence_spans(source))


def _sentence_spans(source: str):
    start = 0
    for boundary in _SENTENCE_SPLIT_RE.finditer(source):
        end = boundary.start()
        piece = source[start:end]
        if piece.strip():
            yield start + len(piece) - len(piece.lstrip()), end - len(piece) + len(piece.rstrip())
        start = boundary.end()
    piece = source[start:]
    if piece.strip():
        yield start + len(piece) - len(piece.lstrip()), len(source.rstrip())


#: Request kinds that ask the answer for CONTENT. Everything the floor reports is one of these.
#:
#: The two exclusions are the whole reason this filter exists, and both were measured firing
#: wrongly without it:
#:
#: * ``ACT`` is a directive to the RUNTIME, not a question to the answer -- "Run independent work
#:   in parallel where possible." The runtime obeys it, so reporting it as something that could not
#:   be answered tells the reader a completed instruction failed.
#: * ``UNKNOWN`` is a sentence never established as a request at all -- "I exchanged 8,500 kr for
#:   $1,240, then spent $300 in Singapore." That is the story the rest of the message asks about,
#:   and no clause carries it because `shared_context` consumes it as typed facts instead.
#:
#: Conservative in the honest direction. The floor may under-report a dropped request it cannot
#: recognize as one, and it will never invent an obligation the user did not make -- a floor that
#: manufactures gaps is the same defect as one that hides them, wearing the opposite face, and it
#: is the one that gets the check switched off.
_CONTENT_REQUEST_KINDS = frozenset(
    {
        ClauseKind.KNOW,
        ClauseKind.COMPUTE,
        ClauseKind.OBSERVE,
        ClauseKind.CREATE,
        ClauseKind.TRANSFORM,
        ClauseKind.RECALL,
    }
)


def _is_content_request(sentence: str) -> bool:
    """Whether this sentence asks the ANSWER for something, per the runtime's own clause kinds.

    Delegated to `core.turn_ir` rather than decided here. Turn IR already owns request-kind
    classification for this runtime, and a second opinion about what counts as a request is how two
    parts of the same system come to disagree about the same sentence.
    """
    from core.turn_ir import parse_turn_ir

    try:
        clauses = parse_turn_ir(str(sentence or "")).clauses
    except Exception:
        return False
    return any(clause.kind in _CONTENT_REQUEST_KINDS for clause in clauses)


def clause_residue_obligations(
    request: str, clause_texts: Sequence[str], *, plan_id: str = ""
) -> tuple[Obligation, ...]:
    """Sentences of `request` that no proposed clause carried forward at all.

    The dual of `turn_planner.verify_plan`, which admits a plan only when every content word in
    every clause already appears in the request. That direction makes an INVENTED request
    impossible; this one makes a DROPPED one visible. A runtime enforcing only the first can be
    handed a plan that answers nothing the user asked and still pass every check it has.

    Measured at the SENTENCE rather than the word, and that choice is load-bearing in both
    directions. Per-word would fire on every plan that legitimately strips a courtesy or a
    formatting instruction, and a floor that cries on well-formed plans gets removed. Per-sentence
    fires only when a whole request the user wrote is carried nowhere -- which is precisely the
    planner-omission shape -- and stays silent while any part of the sentence survives into a
    clause, where the subject-level rule takes over.
    """
    carried: set[str] = set()
    for clause in clause_texts:
        carried |= _obligation_words(clause)
    out: list[Obligation] = []
    from core.turn_ir import parse_turn_ir

    source = str(request or "")
    governing = parse_turn_ir(source).governing_instruction_span
    for index, (start, end) in enumerate(_sentence_spans(source)):
        if governing is not None and governing[0] <= start and end <= governing[1]:
            continue
        sentence = " ".join(source[start:end].split())
        wanted = _obligation_words(sentence)
        if not wanted:
            # A sentence that asks for nothing -- "Thanks!", "Please." -- is not an obligation, and
            # inventing one for it would make the floor refuse turns nobody dropped anything from.
            continue
        if wanted & carried:
            continue
        if not _is_content_request(sentence):
            continue
        out.append(
            Obligation(
                obligation_id=f"{plan_id}:obligation:clause_residue:{index}",
                text=sentence,
                source=ObligationSource.CLAUSE_RESIDUE,
            )
        )
    return tuple(out)


# --- subject-level: what the recognizer named but did not serve ----------------------------------


def canonical_subject_obligations(
    request: str,
    operation: str,
    named_subjects: Sequence[str],
    *,
    plan_id: str = "",
) -> tuple[Obligation, ...]:
    """Every subject of `operation`'s domain the USER'S REQUEST names.

    Read from the request, not from the planner's clause, and that is the whole correction. The
    previous rule enumerated subjects inside whatever clause the planner emitted, so a planner that
    dropped "and Nairobi" from its clause text produced a plan the floor agreed with completely --
    the obligation was never created, because the only text it looked at no longer mentioned the
    place. Measured: a two-city request planned as one city yielded an empty obligation set.

    The enumerator is offered the request only for operations the plan actually USES. That keeps the
    domain scoping the conductor is built on: the planner says which domains this turn contains, the
    operation says which entities are in it. Running every registered enumerator over every message
    would hand a weather recognizer "the price of bitcoin", which is the contamination class this
    package made unreachable by construction.
    """
    out: list[Obligation] = []
    seen: set[str] = set()
    for subject in named_subjects:
        key = _normalize_subject(subject)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            Obligation(
                obligation_id=f"{plan_id}:obligation:{operation}:{_slug(key)}",
                text=str(subject).strip(),
                source=ObligationSource.SUBJECT_RESIDUE,
                clause_text=" ".join(str(request or "").split()),
                operation=operation,
                subject=str(subject).strip(),
            )
        )
    return tuple(out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").casefold()).strip("_") or "subject"


def subject_residue_obligations(
    clause_text: str,
    named_subjects: Sequence[str],
    served_entities: Sequence[str],
    *,
    plan_id: str = "",
    clause_index: int = 0,
) -> tuple[Obligation, ...]:
    """Subjects the clause NAMES in an operation's domain that produced no node.

    `named_subjects` comes from the operation's own declaration, at the layer that reads what the
    clause mentions -- deliberately NOT the layer that decides what is serviceable. Those two are
    already separate everywhere in this runtime: `price_assets_named` finds alias spans and then
    filters them through `mention_is_market_authorized`, and `_weather_expand` finds candidate
    places and then filters them for plausibility. Asking the deciding layer whether it decided
    correctly would answer yes by construction; asking the naming layer what the user wrote does
    not.

    This restores to the conductor a property the lane it replaced already had.
    `live_data_plan._unsupported_market_subtask` exists for exactly this and says so: "the renderer
    must still show it, marked unavailable, rather than it disappearing between plan and answer."

    Identity is the operation's business, not this function's. `subjects_named` returns subjects
    under the SAME canonical names the expander puts in a node's ``entity``, so the comparison here
    is exact equality. An earlier version matched substrings to fold "btc" into "bitcoin", which
    is a text rule standing in for domain knowledge: it silently folds any short name into any
    longer one that contains it, and two genuinely different subjects that share a prefix would
    have been reported as one.
    """
    served = {_normalize_subject(entity) for entity in served_entities}
    out: list[Obligation] = []
    seen: set[str] = set()
    for subject in named_subjects:
        key = _normalize_subject(subject)
        if not key or key in served or key in seen:
            continue
        seen.add(key)
        out.append(
            Obligation(
                obligation_id=f"{plan_id}:obligation:subject:{clause_index}:{key}",
                text=str(subject).strip(),
                source=ObligationSource.SUBJECT_RESIDUE,
                clause_text=" ".join(str(clause_text or "").split()),
            )
        )
    return tuple(out)


def _normalize_subject(subject: Any) -> str:
    return " ".join(str(subject or "").casefold().split())


# --- the verdict ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalObligations:
    """The obligation set for one turn, with the answer-time coverage check.

    Frozen and built once, at plan time, from the user's request and the operations' own
    declarations -- before any node runs. Building it later, from what execution produced, would
    make it a description of the outcome instead of a contract the outcome is judged against.
    """

    request: str = ""
    obligations: tuple[Obligation, ...] = ()
    #: obligation id -> the node realizing it. Written by the planner as it realizes each one.
    realized_by: Mapping[str, str] = field(default_factory=dict)
    #: obligation id -> a terminal state fixed at PLAN time, for obligations no node can serve.
    plan_time_states: Mapping[str, ObligationState] = field(default_factory=dict)

    def coverage(self, node_states: Mapping[str, ObligationState]) -> ObligationCoverage:
        """Each obligation's terminal state, given how its realizing node ended.

        `node_states` maps a NODE id to what that node's outcome means for the obligation it
        realizes. An obligation with a plan-time state (nothing could ever serve it) takes that; an
        obligation whose node is missing from the map is ABSENT, which is the runtime having lost
        it.

        The previous version compared obligation ids against the ids of nodes carrying them, which
        it could only ever pass: the planner created each obligation TOGETHER with a node carrying
        its id, so the two sets were equal by construction. Measured over 150 plans and 300
        obligations by the review agent: zero absent, 150/150 complete. A check that cannot fail is
        not a check, and it let `complete=True` keep meaning "the surviving plan is consistent with
        itself" while claiming to mean "the user was answered".
        """
        outcomes: list[ObligationOutcome] = []
        for obligation in self.obligations:
            node_id = self.realized_by.get(obligation.obligation_id, "")
            # One source of truth. A plan-time state map used to short-circuit this, which made
            # every obligation nothing could serve take its answer from the planner rather than
            # from its node -- and left the UNRESOLVED branch of the node-state mapping unreachable,
            # so a mutation collapsing "nothing can do this" into "it ran and failed" changed
            # nothing observable. Two ways to compute one value is how the two come to disagree.
            state = (
                node_states.get(node_id, ObligationState.ABSENT)
                if node_id
                else self.plan_time_states.get(obligation.obligation_id, ObligationState.ABSENT)
            )
            outcomes.append(
                ObligationOutcome(obligation=obligation, state=state, node_id=node_id)
            )
        return ObligationCoverage(outcomes=tuple(outcomes))

    def required_prerequisites(self, obligation_id: str) -> tuple[str, ...]:
        for obligation in self.obligations:
            if obligation.obligation_id == obligation_id:
                return obligation.requires
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "obligations": [item.to_dict() for item in self.obligations],
            "realized_by": dict(self.realized_by),
            "plan_time_states": {k: v.value for k, v in self.plan_time_states.items()},
        }


__all__ = [
    "TERMINAL_OBLIGATION_STATES",
    "CanonicalObligations",
    "Obligation",
    "ObligationCoverage",
    "ObligationOrigin",
    "ObligationOutcome",
    "ObligationSource",
    "ObligationState",
    "canonical_subject_obligations",
    "clause_residue_obligations",
    "subject_residue_obligations",
]


def node_obligation_state(outcome: Any) -> ObligationState:
    """What one node's outcome means for the obligation it realizes.

    Three different failures, kept apart. Collapsing them into one "unserved" is what left the
    reader unable to tell "nothing here can do that" from "it ran and broke".

    Plan-shape truth only. This is NOT a product verdict and nothing downstream may read it as one:
    the shipping answer for a turn is `core.conductor.product_decision.ProductDecision`, once. This
    used to live in `compose.py`, where its result fed `ComposedAnswer.complete` and became the
    second of four rival authorities over the same turn.
    """
    from core.conductor.node import NodeLifecycle

    if getattr(outcome, "fulfilled", outcome.succeeded):
        return ObligationState.SATISFIED
    if outcome.succeeded:
        return ObligationState.EXECUTION_FAILED
    if outcome.state is NodeLifecycle.UNRESOLVED:
        return ObligationState.CAPABILITY_UNAVAILABLE
    if outcome.state in {NodeLifecycle.FAILED, NodeLifecycle.DEPENDENCY_FAILED}:
        return ObligationState.EXECUTION_FAILED
    return ObligationState.PLANNED
