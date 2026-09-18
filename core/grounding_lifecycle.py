"""The typed lifecycle a current-information turn must complete before its bytes may ship.

THE DEFECT THIS CLOSES
----------------------
M1 decides a turn needs current information. M2 retrieves and records, by id, which evidence
entered the answering model call. M4 decides, per claim, which of those claims a bound source
actually supports. On ``b3f5117f`` all three ran and **nothing consumed them at the seam that
puts bytes on the wire.** ``core.finalization.finalize_answer`` -- the one authority every
served answer traverses -- never saw a grounding fact: it takes ``source_context`` and
immediately ``del``s it. The envelope stamp M2 writes
(``model_source_context["evidence_synthesis_binding"]``) had, measured by grep at that SHA,
**zero production readers** -- only tests and proof scripts. A fabricated current answer with a
green retrieval receipt behind it published exactly as a grounded one did.

Two facts were being conflated, and the conflation is the whole defect:

    a retrieval HAPPENED           -- proved by a receipt (`core.retrieval_observability`)
    the answer DERIVES from it     -- proved by nothing, and asserted by everyone

WHAT THIS MODULE IS
-------------------
A turn-scoped ledger of ONE typed lifecycle::

    REQUIRED -> RETRIEVED -> BOUND_TO_SYNTHESIS -> CLAIM_SUPPORTED -> PUBLISHED

with the exits ``FAILED`` / ``REFUSED`` / ``PARTIAL``. Each stage is written by the authority
that already owns it and by no one else:

* ``REQUIRED``            -- `core.execution_requirements` (M1), when it freezes a decision
                             that reads ``current_information_required``;
* ``RETRIEVED``           -- `core.agent_runtime.current_evidence_prefetch` (M2), from the
                             receipt's own terminal outcome;
* ``BOUND_TO_SYNTHESIS``  -- M2's binding record, admitted only for THIS turn's scope
                             (`core.grounded_synthesis_binding.binding_admitted_for_turn`);
* ``CLAIM_SUPPORTED``     -- `core.claim_support.match_claims` (M4), recomputed by the
                             publication gate over the exact bytes about to commit;
* ``PUBLISHED``           -- `core.finalization`, and nowhere else.

WHAT IT IS NOT
--------------
There is no classifier here, no binding algorithm, no support predicate and no vocabulary of
any kind. Every judgement is imported from the lane that owns it. This module records, indexes
by turn identity, and refuses to hand a record to a turn that is not the one that wrote it.

WHY A LEDGER AND NOT AN ARGUMENT
--------------------------------
``finalize_answer`` is reached through six doors and several of them pass no context at all.
A gate that depended on a caller remembering to hand it the lifecycle would be a gate every
un-updated door walks past, which is the shape of the defect, not its repair. So the same
pattern the closure verdict already uses at that seam applies here: **the ledger is
authoritative and the caller supplies nothing.** Absence of a record means M1 never marked the
turn current-information, which is exactly the DIRECT/timeless case that must stay untouched.

IDENTITY, AND WHY A PRIOR TURN CANNOT LEND EVIDENCE
---------------------------------------------------
A record is indexed under the turn-unique identities available where it was written --
``request_id``, ``cancel_turn_id``/``turn_id``, ``task_id`` -- and NEVER under ``session_id``,
which spans turns and would let turn A's evidence resolve for turn B. The fence tuple
(``execution_id``, ``generation``, ``runtime_epoch``) is stored when the lane is onboarded and
compared at publication: a record from another generation is refused rather than consumed. The
bound evidence carries M2's own scope digest on top of that, so a resumed or cached binding
record fails ``binding_admitted_for_turn`` before it is ever stored.

Writes travel by a stamped id, not a ContextVar, for the same reason
`core.turn_model_call_ledger` refuses one: provider execution crosses a shallow COPY of the
request context, so the model lane and the enclosing turn must find the same row through a
value that copies with the dict.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: Where a turn's lifecycle id rides. Copies with `dict(source_context)`, which is what makes
#: the model lane's shallow copy write into the same row as the turn that opened it.
LIFECYCLE_ID_KEY = "_grounding_lifecycle_id"

STAGE_REQUIRED = "required"
STAGE_RETRIEVED = "retrieved"
STAGE_BOUND = "bound_to_synthesis"
STAGE_SUPPORTED = "claim_supported"
STAGE_PUBLISHED = "published"

#: Stage order, used only to name the FIRST stage a turn failed to reach.
STAGE_ORDER = (STAGE_REQUIRED, STAGE_RETRIEVED, STAGE_BOUND, STAGE_SUPPORTED, STAGE_PUBLISHED)

EXIT_FAILED = "failed"
EXIT_REFUSED = "refused"
EXIT_PARTIAL = "partial"

#: Support minted by a typed observation lane from its OWN successful observation, rather than
#: from bound web notes. A weather fetch that returned a reading is evidence of that reading by
#: exactly the route `core.observation_evidence` already recognises; it never travels through a
#: search provider and has no evidence set to be bound into.
ORIGIN_TYPED_OBSERVATION = "typed_observation"
ORIGIN_BOUND_EVIDENCE = "bound_evidence"
ORIGIN_COMPUTED_VALUE = "computed_value"

_MAX_TRACKED_TURNS = 256

_LOCK = threading.Lock()
_RECORDS: OrderedDict[str, GroundingLifecycle] = OrderedDict()
#: identity token -> lifecycle id. Turn-unique tokens only; see the module docstring.
_INDEX: dict[str, str] = {}
#: session id -> lifecycle id of the session's most recent PUBLISHED (or partial) answer. The one
#: pointer a later turn in the same session may adopt support from (see
#: `adopt_previous_publication_if_representation`). Session-scoped on purpose: a re-presentation
#: can only be of an answer this session was shown.
_LAST_PUBLISHED_BY_SESSION: dict[str, str] = {}
#: Reason code stamped on a lifecycle opened for a turn that re-presents the previous answer.
REASON_RE_PRESENTATION = "re_presentation_of_published_answer"
#: Stamped on a lifecycle opened for a turn that re-presents an answer the session WITHHELD
#: (refused or failed). It carries no support rows on purpose: nothing supported exists to
#: reformat, and the publication gate says so instead of publishing model prose or a table.
REASON_RE_PRESENTATION_OF_WITHHELD = "re_presentation_of_withheld_answer"
#: session id -> lifecycle id of the session's most recent REFUSED/FAILED exit, cleared by the next
#: published answer.
_LAST_EXIT_BY_SESSION: dict[str, str] = {}
#: Words that ask for the previous answer in another size or register while naming nothing new. A
#: request made only of these (plus dialogue meta and scaffolding) re-presents the previous answer
#: exactly like an explicit shape ("as a table") does.
_RE_PRESENTATION_WORDS = frozenset({
    "shorter", "short", "briefer", "brief", "concise", "condense", "condensed", "shorten", "compress",
    "summarize", "summarise", "summary", "tldr", "tl", "dr", "expand", "elaborate", "longer", "detail",
    "details", "detailed", "rephrase", "reword", "simpler", "simplify", "plainer", "bullet", "bullets",
    "version", "again",
})


@dataclass(frozen=True)
class TurnIdentity:
    """The turn a lifecycle belongs to, as far as the writing seam could see it."""

    request_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    session_id: str = ""
    execution_id: str = ""
    generation: str = ""
    runtime_epoch: str = ""
    #: The A0 request id BOUND in the calling context, recorded separately from the one the
    #: source_context carries. Measured on the isolated daemon: a served turn's context carries
    #: no `request_id` at all while the door has `req:http:f71aba…` bound, and finalization can
    #: only present the bound one -- so a record indexed under just one of the two is a record
    #: the publication seam cannot find. Both are indexed; neither is preferred.
    bound_request_id: str = ""

    @property
    def tokens(self) -> tuple[str, ...]:
        """Turn-unique identities this record may be found by. `session_id` is excluded."""

        return tuple(
            dict.fromkeys(
                token
                for token in (self.request_id, self.bound_request_id, self.turn_id, self.task_id)
                if token
            )
        )

    @property
    def fenced(self) -> bool:
        return bool(self.execution_id)

    def same_generation_as(self, other: TurnIdentity) -> bool:
        """Fence comparison. Two UNFENCED identities are not compared -- a legacy lane binds no
        tuple, and treating "both absent" as a match would be a claim neither side made."""

        if not (self.fenced and other.fenced):
            return True
        return (
            self.execution_id == other.execution_id
            and self.generation == other.generation
            and self.runtime_epoch == other.runtime_epoch
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "execution_id": self.execution_id,
            "generation": self.generation,
            "runtime_epoch": self.runtime_epoch,
            "bound_request_id": self.bound_request_id,
        }


@dataclass
class SynthesisCall:
    """One model invocation this turn entered, and the evidence-set id its prompt carried.

    Recorded at call ENTRY from the context the provider was actually called with, so a call
    made BEFORE anything was bound carries no id -- which is how "the answer was generated
    before the retrieval" is detected without anyone timing anything.
    """

    model_call_id: str = ""
    call_role: str = ""
    evidence_set_id: str = ""
    sequence: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_call_id": self.model_call_id,
            "call_role": self.call_role,
            "evidence_set_id": self.evidence_set_id,
            "sequence": self.sequence,
        }


@dataclass
class GroundingLifecycle:
    """One current-information turn's progress through the stages, and nothing else."""

    lifecycle_id: str
    identity: TurnIdentity
    request_text: str = ""
    reason_codes: tuple[str, ...] = ()
    #: M2's terminal retrieval outcome, read off its receipt. "" until retrieval reports.
    retrieval_outcome: str = ""
    retrieval_receipt: dict[str, Any] = field(default_factory=dict)
    retrieved_source_count: int = 0
    #: The rows retrieval returned, whether or not they were ever bound into a prompt. Kept for
    #: the record and for Activity -- deliberately NOT offered to the publication gate, which
    #: reads `bound_notes`. The distance between these two fields is the whole M3 defect: rows
    #: that were fetched are not rows an answer was made of.
    retrieved_notes: tuple[dict[str, Any], ...] = ()
    #: M2's binding record, stored ONLY after `binding_admitted_for_turn` accepted it here.
    binding: dict[str, Any] = field(default_factory=dict)
    #: Every evidence-set id this turn's own bindings minted, in mint order. A conductor
    #: plan binds INCREMENTALLY as observation nodes complete, so a generation call that
    #: answered with the evidence available at its moment references the set minted to
    #: that point; later observations grow the set and re-mint. Every id here was
    #: scope-admitted for THIS turn by `record_bound`'s acceptance gate, so a call
    #: referencing any of them referenced this turn's evidence -- never a foreign set.
    evidence_set_ids: tuple[str, ...] = ()
    #: The rows the binding names. The publication gate matches claims against exactly these.
    bound_notes: tuple[dict[str, Any], ...] = ()
    #: Binding records this turn saw and REFUSED, with why. A resumed or cached record lands
    #: here instead of vanishing, so the failed stage can name what was rejected.
    rejected_bindings: tuple[dict[str, Any], ...] = ()
    synthesis_calls: list[SynthesisCall] = field(default_factory=list)
    #: Successful typed observations this turn made (weather, quote, file read). Each is an
    #: entry `core.observation_evidence.records_a_usable_observation` accepted.
    typed_observations: tuple[dict[str, Any], ...] = ()
    #: Deterministic computations this turn's plan produced (a calculation node's rendered
    #: line). Deliberately NOT typed observations -- nothing was looked up, so `retrieved` and
    #: `bound` above do not read this field. It exists so the publication gate can support a
    #: computed claim in a mixed turn whose SIBLING clause needed current information.
    computed_values: tuple[dict[str, Any], ...] = ()
    #: Stable-knowledge renders this turn's plan produced (an OPEN-authority knowledge node's
    #: rendered line). NOT support of any kind -- `_support_rows` never offers it, because the
    #: text was written by a model and the model's own output may not certify itself. The
    #: publication gate consumes it as a per-claim EXEMPTION on the plan's typed authority:
    #: the same decision under which a DIRECT knowledge turn publishes with no lifecycle at
    #: all (FINDINGS F43: the served turn that generated "The Berlin Wall fell in 1989."
    #: correctly and then withheld it beside a live sibling's gate).
    stable_knowledge: tuple[dict[str, Any], ...] = ()
    #: Did a MODEL write this turn's publishable bytes? Two independent recorders answer it and
    #: either one is enough: the provider-call ledger's entry seam (`synthesis_calls`) and the
    #: reasoning lane's own `model_execution.used_model`. One recorder can be bypassed by a lane
    #: that reaches a provider some other way; both being wrong at once is a much harder thing to
    #: do by accident, and the gate applies whenever EITHER says a generation happened.
    model_authored: bool = False
    #: M4's map as the reasoning lane computed it, for the record. ADVISORY only: the gate
    #: recomputes over the bytes actually being committed, which may differ.
    claim_support: dict[str, Any] = field(default_factory=dict)
    #: Per-child lifecycle state for a planned multi-part turn (requirement 9).
    children: tuple[dict[str, Any], ...] = ()
    #: Set once, by `core.finalization`, and by nothing else.
    publication: dict[str, Any] = field(default_factory=dict)

    # -- derived stage truth ------------------------------------------------------------
    @property
    def required(self) -> bool:
        """A lifecycle exists only for a turn M1 marked current-information."""

        return True

    @property
    def retrieved(self) -> bool:
        """Retrieval reached a terminal outcome that delivered rows, or a typed lane observed."""

        return bool(self.retrieved_source_count) or bool(self.typed_observations)

    @property
    def bound(self) -> bool:
        """Identified evidence entered the answering prompt, or a typed lane observed directly."""

        return bool(self.binding.get("evidence_set_id")) or bool(self.typed_observations)

    @property
    def bound_evidence_set_id(self) -> str:
        return str(self.binding.get("evidence_set_id") or "")

    @property
    def synthesis_referenced_bound_evidence(self) -> bool:
        """Did a model call this turn entered carry the bound evidence-set id in its prompt?

        A turn that made no model call at all (the typed observation lanes render
        deterministically) answers True: there is no generation to bind, and requiring a
        reference to one would refuse the lanes whose evidence is strongest.

        A call may reference an EARLIER mint of this turn's set: incremental binding (a
        conductor plan's observations completing one by one) grows the set, and a
        generation call that answered with the evidence of its moment is not ungrounded
        because a later observation enlarged it. Every id in `evidence_set_ids` was
        admitted for this turn, so accepting them cannot admit a foreign set.
        """

        minted = set(self.evidence_set_ids) or ({self.bound_evidence_set_id} if self.bound_evidence_set_id else set())
        if not minted:
            return False
        if not self.synthesis_calls:
            return not bool(self.binding)
        return any(call.evidence_set_id in minted for call in self.synthesis_calls)

    @property
    def generated(self) -> bool:
        """Whether a model wrote the publishable bytes -- the condition for gating them at all.

        A turn with no generation composed its answer in runtime code: a weather render, a quote
        render, a typed refusal. Code does not fabricate, and holding deterministic output to a
        claim-match against sources it was already built from would refuse the lanes whose
        evidence is strongest.

        SERVED, not attempted. Two recorders set this and both answer the served question --
        `record_served_usage` (the response that was actually returned) and the reasoning lane's
        `model_execution.used_model`. The provider-call ENTRY seam deliberately does NOT: a call
        that was entered and then FAILED produced no bytes, and counting it made the gate treat
        the runtime's own "model synthesis failed" notice as model output and refuse it
        (measured live, browser round b1: `model.call_failed` -> `model_routing_failed` ->
        a typed failure notice the gate then rewrote).
        """

        return bool(self.model_authored)

    @property
    def support_origin(self) -> str:
        if self.bound_evidence_set_id:
            return ORIGIN_BOUND_EVIDENCE
        if self.typed_observations:
            return ORIGIN_TYPED_OBSERVATION
        return ""

    def reached(self, stage: str) -> bool:
        if stage == STAGE_REQUIRED:
            return True
        if stage == STAGE_RETRIEVED:
            return self.retrieved
        if stage == STAGE_BOUND:
            # A bound evidence set has to have been NAMED by the answering call. A turn with no
            # evidence set reaches this stage only the other way it can be reached: runtime code
            # composed the bytes from a typed observation it made itself, so there is no
            # generation standing between the observation and the answer to be bound.
            if self.bound_evidence_set_id:
                return self.synthesis_referenced_bound_evidence
            return bool(self.typed_observations) and not self.generated
        if stage == STAGE_SUPPORTED:
            return bool(self.publication.get("supported_claim_count"))
        if stage == STAGE_PUBLISHED:
            return str(self.publication.get("state") or "") == STAGE_PUBLISHED
        return False

    def failed_stage(self) -> str:
        """The FIRST stage this turn did not reach, or "" when it published.

        A published turn names no failed stage, whichever route it took to get there: a
        deterministic render binds no evidence set and is not thereby a turn that failed at
        BOUND. Reporting one would put a contradiction in the record -- "published" beside
        "failed at bound_to_synthesis" -- and a record that contradicts itself is worth less
        than no record.
        """

        if self.reached(STAGE_PUBLISHED):
            return ""
        for stage in STAGE_ORDER:
            if not self.reached(stage):
                return stage
        return ""

    def as_dict(self) -> dict[str, Any]:
        """The whole lifecycle, for Activity and the API. Every stage, or the one that failed."""

        return {
            "schema": "vool.grounding_lifecycle.v1",
            "lifecycle_id": self.lifecycle_id,
            "identity": self.identity.as_dict(),
            "reason_codes": list(self.reason_codes),
            "stages": {
                STAGE_REQUIRED: True,
                STAGE_RETRIEVED: self.retrieved,
                STAGE_BOUND: self.reached(STAGE_BOUND),
                STAGE_SUPPORTED: self.reached(STAGE_SUPPORTED),
                STAGE_PUBLISHED: self.reached(STAGE_PUBLISHED),
            },
            "failed_stage": self.failed_stage(),
            "retrieval_outcome": self.retrieval_outcome,
            "retrieved_source_count": self.retrieved_source_count,
            "evidence_set_id": self.bound_evidence_set_id,
            "evidence_set_ids_minted": list(self.evidence_set_ids),
            "support_origin": self.support_origin,
            "synthesis_calls": [call.as_dict() for call in self.synthesis_calls],
            "typed_observation_count": len(self.typed_observations),
            "computed_value_count": len(self.computed_values),
            "model_authored": self.generated,
            "rejected_binding_count": len(self.rejected_bindings),
            "children": [dict(child) for child in self.children],
            "claim_support": dict(self.claim_support),
            "publication": dict(self.publication),
        }


# ------------------------------------------------------------------------------- identity


def _identity_from(source_context: Any, *, task_id: str = "") -> TurnIdentity:
    """Read the turn's identity from wherever this seam can see it. Never invented."""

    context = source_context if isinstance(source_context, dict) else {}
    request_id = str(context.get("request_id") or "").strip()
    try:
        from core.semantic.semantic_admissions import current_request_id

        bound_request_id = str(current_request_id() or "").strip()
    except Exception:
        bound_request_id = ""
    fence: dict[str, Any] = {}
    try:
        from core.semantic.semantic_admissions import current_execution_identity

        fence = dict(current_execution_identity() or {})
    except Exception:
        fence = {}
    return TurnIdentity(
        request_id=request_id,
        bound_request_id=bound_request_id,
        turn_id=str(context.get("cancel_turn_id") or context.get("turn_id") or "").strip(),
        task_id=str(task_id or context.get("task_id") or "").strip(),
        session_id=str(context.get("session_id") or context.get("runtime_session_id") or "").strip(),
        execution_id=str(fence.get("execution_id") or "").strip(),
        generation=str(fence.get("generation") or "").strip(),
        runtime_epoch=str(fence.get("runtime_epoch") or "").strip(),
    )


def turn_identity(source_context: Any, *, task_id: str = "") -> TurnIdentity:
    """The public name for the turn-identity read every turn-keyed ledger needs.

    `core.final_answer_authorship` indexes its records by the same tokens this module does, and
    for the same reason (finalization presents a turn id while the writing seam saw a request
    id). Sharing the reader is the point: two ledgers that resolve "which turn is this?"
    differently are two ledgers that disagree about the same turn.
    """

    return _identity_from(source_context, task_id=task_id)


def merge_turn_identity(stored: TurnIdentity, seen: TurnIdentity) -> TurnIdentity:
    """Public name for the widening merge. See :func:`turn_identity`."""

    return _merge_identity(stored, seen)


def _merge_identity(stored: TurnIdentity, seen: TurnIdentity) -> TurnIdentity:
    """Widen a stored identity with components a later seam learned. Never overwrites."""

    return TurnIdentity(
        request_id=stored.request_id or seen.request_id,
        bound_request_id=stored.bound_request_id or seen.bound_request_id,
        turn_id=stored.turn_id or seen.turn_id,
        task_id=stored.task_id or seen.task_id,
        session_id=stored.session_id or seen.session_id,
        execution_id=stored.execution_id or seen.execution_id,
        generation=stored.generation or seen.generation,
        runtime_epoch=stored.runtime_epoch or seen.runtime_epoch,
    )


def _index_unlocked(record: GroundingLifecycle) -> None:
    for token in record.identity.tokens:
        _INDEX[token] = record.lifecycle_id


def _evict_unlocked() -> None:
    while len(_RECORDS) > _MAX_TRACKED_TURNS:
        dropped_id, _dropped = _RECORDS.popitem(last=False)
        for token, owner in list(_INDEX.items()):
            if owner == dropped_id:
                _INDEX.pop(token, None)


def _record_for(source_context: Any) -> GroundingLifecycle | None:
    """This turn's row, found by the id stamped into the context. Callers hold `_LOCK`."""

    lifecycle_id = str((source_context or {}).get(LIFECYCLE_ID_KEY) or "").strip() if isinstance(
        source_context, dict
    ) else ""
    if not lifecycle_id:
        return None
    record = _RECORDS.get(lifecycle_id)
    if record is not None:
        _RECORDS.move_to_end(lifecycle_id)
    return record


# ------------------------------------------------------------------------------- writers


def register_required(
    source_context: dict[str, Any] | None,
    *,
    request_text: str,
    reason_codes: tuple[str, ...] = (),
    task_id: str = "",
) -> str:
    """M1 stage. Open (or widen) this turn's lifecycle. Returns the lifecycle id.

    Idempotent for one turn, in two ways. A context that already carries an id updates the
    reason codes and gets the same id back. A context that carries none but whose turn identity
    is already indexed ADOPTS that row -- which is what keeps a planned sub-turn and its parent
    on one lifecycle instead of two, whichever of them reaches M1 first. Callers that hold no
    dict get "" and no row: a lifecycle with nowhere to be stamped could never be found again
    at publication.
    """

    if not isinstance(source_context, dict):
        return ""
    seen = _identity_from(source_context, task_id=task_id)
    with _LOCK:
        existing = _record_for(source_context)
        if existing is None:
            # ADOPTION. A planned sub-turn runs on a COPY of the parent's context and reaches
            # M1 on its own; if it minted a second row, the index would point at the child and
            # the parent's bound evidence would be invisible at publication -- one turn, two
            # lifecycles, and the merged answer gated against the wrong one. A row already
            # indexed under this turn's identity IS this turn's lifecycle, whichever half of
            # the turn arrives first.
            for token in seen.tokens:
                candidate = _RECORDS.get(_INDEX.get(token, ""))
                if candidate is not None and candidate.identity.same_generation_as(seen):
                    existing = candidate
                    break
        if existing is not None:
            existing.identity = _merge_identity(existing.identity, seen)
            if request_text and not existing.request_text:
                existing.request_text = str(request_text)
            merged = tuple(dict.fromkeys((*existing.reason_codes, *reason_codes)))
            existing.reason_codes = merged
            _index_unlocked(existing)
            adopted_id = existing.lifecycle_id
        else:
            adopted_id = ""
    if adopted_id:
        source_context[LIFECYCLE_ID_KEY] = adopted_id
        return adopted_id
    with _LOCK:
        # Recheck under the lock: concurrent planned sub-turns in one wave can both find no row
        # and both reach here, and two rows for one turn is the split this adoption exists to
        # prevent. The first writer wins and the second adopts it.
        for token in seen.tokens:
            candidate = _RECORDS.get(_INDEX.get(token, ""))
            if candidate is not None and candidate.identity.same_generation_as(seen):
                candidate.identity = _merge_identity(candidate.identity, seen)
                _index_unlocked(candidate)
                source_context[LIFECYCLE_ID_KEY] = candidate.lifecycle_id
                return candidate.lifecycle_id
        lifecycle_id = f"gl-{uuid.uuid4().hex}"
        record = GroundingLifecycle(
            lifecycle_id=lifecycle_id,
            identity=seen,
            request_text=str(request_text or ""),
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )
        _RECORDS[lifecycle_id] = record
        _RECORDS.move_to_end(lifecycle_id)
        _index_unlocked(record)
        _evict_unlocked()
    source_context[LIFECYCLE_ID_KEY] = lifecycle_id
    return lifecycle_id


def withdraw_required(source_context: dict[str, Any] | None, *, reason: str) -> bool:
    """Close a lifecycle that a PROVISIONAL widening opened and that nothing ever filled.

    The counterpart of `register_required` for `core.execution_requirements.
    retract_provisional_escalation`: adaptive research asked to retrieve, the ask widened the
    turn and opened this row, and the retrieval found nothing. With no row the publication gate
    reads the turn as DIRECT and publishes the model's answer unchanged -- which is the answer
    the authority's own reading always called for.

    Refuses (returns False) when the row holds anything a reader could rely on: bound rows,
    minted evidence sets, typed observations or a synthesis call. Those are facts about this
    turn and a withdrawal may not erase them.
    """
    if not isinstance(source_context, dict):
        return False
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return False
        if (
            record.bound_notes
            or record.evidence_set_ids
            or record.typed_observations
            or record.synthesis_calls
            or record.children
        ):
            return False
        _RECORDS.pop(record.lifecycle_id, None)
        for token, owner in list(_INDEX.items()):
            if owner == record.lifecycle_id:
                _INDEX.pop(token, None)
    source_context.pop(LIFECYCLE_ID_KEY, None)
    withdrawn = source_context.setdefault("_grounding_lifecycle_withdrawn", [])
    if isinstance(withdrawn, list):
        withdrawn.append({"lifecycle_id": record.lifecycle_id, "reason": str(reason or "")})
    return True


def record_child_lifecycle(
    parent_context: dict[str, Any] | None,
    child_context: dict[str, Any] | None,
    *,
    task_index: int = 0,
    request: str = "",
) -> bool:
    """Requirement 9. A planned child reports the stage IT reached, on the parent's row.

    Children run on a copy of the parent's context and therefore write into the parent's
    lifecycle already; this records, per child, WHICH stage that child got to, so a parent
    that merges four answers can be read for the one child whose evidence never bound instead
    of only for the aggregate. The parent's own publication is unaffected by what is recorded
    here -- it is gated on the merged bytes as a whole, which is the only way a merged claim
    cannot slip through between two children's accounts.
    """

    with _LOCK:
        parent = _record_for(parent_context)
        child = _record_for(child_context)
        if parent is None:
            return False
        stages = child.as_dict()["stages"] if child is not None else {}
        failed = child.failed_stage() if child is not None else STAGE_REQUIRED
        parent.children = (
            *parent.children,
            {
                "task_index": int(task_index or 0),
                "request": " ".join(str(request or "").split())[:200],
                "same_lifecycle": bool(child is not None and child.lifecycle_id == parent.lifecycle_id),
                "stages": stages,
                "failed_stage": failed,
            },
        )
        return True


def record_retrieved(
    source_context: dict[str, Any] | None,
    *,
    outcome: str,
    receipt: dict[str, Any] | None = None,
    source_count: int = 0,
    notes: list[dict[str, Any]] | None = None,
) -> bool:
    """M2 stage. The terminal truth of this turn's retrieval, read off its own receipt."""

    rows = tuple(dict(note) for note in list(notes or []) if isinstance(note, dict))
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return False
        record.retrieval_outcome = str(outcome or "")
        record.retrieval_receipt = dict(receipt or {})
        record.retrieved_source_count = max(record.retrieved_source_count, int(source_count or 0))
        if rows:
            record.retrieved_notes = rows
        return True


def record_bound(
    source_context: dict[str, Any] | None,
    *,
    binding: dict[str, Any] | None,
    notes: list[dict[str, Any]] | None,
    scope: str,
) -> bool:
    """M2 stage. Store the binding record -- but only if it is THIS turn's.

    `binding_admitted_for_turn` recomputes the scope digest rather than trusting the id written
    on the record, so a record carried in by a retry, a resume or a cache hit is refused here
    and kept in `rejected_bindings` where the failed stage can name it.
    """

    from core.grounded_synthesis_binding import binding_admitted_for_turn

    payload = dict(binding or {})
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return False
        if not payload or not binding_admitted_for_turn(payload, scope=scope):
            record.rejected_bindings = (
                *record.rejected_bindings,
                {
                    "evidence_set_id": str(payload.get("evidence_set_id") or ""),
                    "scope_digest": str(payload.get("scope_digest") or ""),
                    "reason": "foreign_turn_scope" if payload else "no_binding_record",
                },
            )
            return False
        record.binding = payload
        record.evidence_set_ids = tuple(
            dict.fromkeys(
                (*record.evidence_set_ids, str(payload.get("evidence_set_id") or ""))
            )
        ) or record.evidence_set_ids
        record.evidence_set_ids = tuple(item for item in record.evidence_set_ids if item)
        record.bound_notes = tuple(dict(note) for note in list(notes or []) if isinstance(note, dict))
        record.retrieved_source_count = max(
            record.retrieved_source_count, len(record.bound_notes)
        )
        return True


def record_synthesis_call(
    source_context: dict[str, Any] | None,
    *,
    model_call_id: str = "",
    call_role: str = "",
) -> bool:
    """Record that a model call was entered, and which evidence set its prompt carried.

    The evidence-set id is read from the SAME context the provider is being invoked with --
    never from the enclosing turn -- because the question this answers is "did the prompt that
    produced the publishable bytes name the bound evidence?", and only the call's own context
    can answer it.
    """

    context = source_context if isinstance(source_context, dict) else {}
    envelope = context.get("evidence_synthesis_binding")
    evidence_set_id = (
        str(envelope.get("evidence_set_id") or "").strip() if isinstance(envelope, dict) else ""
    )
    with _LOCK:
        record = _record_for(context)
        if record is None:
            return False
        record.synthesis_calls.append(
            SynthesisCall(
                model_call_id=str(model_call_id or ""),
                call_role=str(call_role or ""),
                evidence_set_id=evidence_set_id,
                sequence=len(record.synthesis_calls) + 1,
            )
        )
        return True


#: Where a lane records the OUTPUT of the tool it ran, in the order they are tried. Each is a
#: field an existing writer already populates -- `response_preview` is what
#: `core.agent_runtime.response_policy_tool_history` puts a tool's own text in (bounded to 1800
#: chars), and it is what the model was shown.
_OBSERVATION_OUTPUT_FIELDS = ("summary", "response_preview", "snippet", "text", "content", "live_quote")


def _as_support_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Project a typed observation into the note shape `core.claim_support` reads.

    A projection, not an extraction: the value copied into `summary` is the lane's own recorded
    output, untouched. Without it a typed lane's evidence is invisible to the matcher and its own
    correct answer reads unsupported -- measured, and it stripped both real readings out of a
    two-city weather answer whose lookups had both succeeded
    (`test_a_successful_multi_entity_turn_keeps_both_readings`).
    """

    row = dict(entry)
    if str(row.get("summary") or "").strip():
        return row
    for field_name in _OBSERVATION_OUTPUT_FIELDS:
        value = str(row.get(field_name) or "").strip()
        if value:
            row["summary"] = value
            return row
    return row


def record_typed_observations(
    source_context: dict[str, Any] | None,
    *,
    entries: Any,
) -> int:
    """Requirement 7. A typed lane's OWN successful observations mint support.

    Success is not judged here. `core.observation_evidence.usable_observations` owns that
    question for every reader in the runtime, and a failed lookup -- the weather fetch that
    returned nothing and had its reading invented anyway -- is filtered out by the same rule
    that already governs `turn_ran_observations`.
    """

    from core.observation_evidence import usable_observations

    usable = [
        _as_support_row(entry)
        for entry in usable_observations(entries)
        if isinstance(entry, dict)
    ]
    if not usable:
        return 0
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return 0
        known = {repr(sorted(entry.items(), key=lambda kv: str(kv[0]))) for entry in record.typed_observations}
        added = [
            entry
            for entry in usable
            if repr(sorted(entry.items(), key=lambda kv: str(kv[0]))) not in known
        ]
        record.typed_observations = (*record.typed_observations, *added)
        return len(added)

def record_computed_values(
    source_context: dict[str, Any] | None,
    *,
    entries: Any,
) -> int:
    """A deterministic computation's rendered line mints support of its own kind.

    The computation channel is not the observation channel: nothing was looked up, and this
    recorder never touches `typed_observations` (so `retrieved`/`bound` do not change). It
    exists for the mixed turn where a SIBLING clause needed current information and opened this
    lifecycle -- the computed clause's own line must remain matchable support or the sibling's
    ungrounded prose takes the arithmetic down with it. Entries are published by
    `core.conductor.evidence.publish_conductor_computations`, which only emits SUCCEEDED
    computations; success is therefore already decided by the one seam that knows it.
    """

    if entries is None:
        return 0
    if isinstance(entries, dict):
        entries = [entries]
    try:
        items = [dict(entry) for entry in list(entries) if isinstance(entry, dict)]
    except TypeError:
        return 0
    usable = [entry for entry in items if str(entry.get("summary") or "").strip()]
    if not usable:
        return 0
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return 0
        known = {
            repr(sorted(entry.items(), key=lambda kv: str(kv[0])))
            for entry in record.computed_values
        }
        added = [
            entry
            for entry in usable
            if repr(sorted(entry.items(), key=lambda kv: str(kv[0]))) not in known
        ]
        record.computed_values = (*record.computed_values, *added)
        return len(added)


def record_stable_knowledge(
    source_context: dict[str, Any] | None,
    *,
    entries: Any,
) -> int:
    """An OPEN-authority knowledge node's rendered line mints an exemption record of its own kind.

    Mirrors `record_computed_values` in shape and idempotency, and mirrors its boundary in
    kind: this channel is not the observation channel, not the computation channel, and above
    all NOT support -- `retrieved`, `bound` and `_support_rows` never read it. It exists for
    the mixed turn where a SIBLING clause needed current information and opened this
    lifecycle: the knowledge clause's own rendered line must remain publishable on the plan's
    typed authority, or the sibling's gate takes the knowledge down with it (F43). Entries are
    published by `core.conductor.evidence.publish_conductor_stable_knowledge`, which emits
    only SUCCEEDED open-authority knowledge nodes; that eligibility is decided by the one seam
    that knows it.
    """

    if entries is None:
        return 0
    if isinstance(entries, dict):
        entries = [entries]
    try:
        items = [dict(entry) for entry in list(entries) if isinstance(entry, dict)]
    except TypeError:
        return 0
    usable = [entry for entry in items if str(entry.get("summary") or "").strip()]
    if not usable:
        return 0
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return 0
        known = {
            repr(sorted(entry.items(), key=lambda kv: str(kv[0])))
            for entry in record.stable_knowledge
        }
        added = [
            entry
            for entry in usable
            if repr(sorted(entry.items(), key=lambda kv: str(kv[0]))) not in known
        ]
        record.stable_knowledge = (*record.stable_knowledge, *added)
        return len(added)


def harvest_turn_observations(source_context: dict[str, Any] | None) -> int:
    """Requirement 7, without an enumeration. Sweep every same-turn evidence channel.

    The channel list is `core.model_output_guard._TURN_OBSERVATION_KEYS` -- the same set
    `turn_ran_observations` and `turn_has_current_evidence` already read, imported rather than
    restated so a lane that adds a channel is covered here the moment those predicates cover it.
    Writing a private list would be one more place to forget, and forgetting here means a lane
    that really observed is asked to prove it twice.
    """

    if not isinstance(source_context, dict):
        return 0
    try:
        from core.model_output_guard import _TURN_OBSERVATION_KEYS
    except Exception:
        return 0
    added = 0
    for channel in _TURN_OBSERVATION_KEYS:
        added += record_typed_observations(source_context, entries=source_context.get(channel))
    return added


def seal_turn_grounding(
    source_context: dict[str, Any] | None,
    text: str,
    result: Any = None,
) -> str:
    """Bring this turn's lifecycle up to date at the ONE seam every served result passes.

    Why this exists, measured on the isolated daemon (2026-09-01, drive `m3diag1`): a
    current-information question -- "what is the current state of volcanic activity on the
    Reykjanes peninsula" -- was classified `research` and answered by the WORKFLOW-PLANNER
    tool loop, not by the grounded reasoning lane. Its recorded chain was
    `workflow_planner_step → web.search → web_retrieval_completed(Brave, 4 sources) →
    tool_synthesizing → model.call_completed`. M1's authority was never consulted with the
    turn's context on that path and M2's prefetch never ran, so no lifecycle existed and the
    publication gate stood down on a turn that had retrieved four real sources and shipped a
    model's answer over them.

    A gate whose coverage depends on which lane happened to answer is not a gate. So the
    lifecycle is completed HERE, at `core.agent_runtime.agent._seal_semantic_result` -- the
    seam whose own docstring is "every served semantic result in the turn spine must pass
    through this function", and the last place the turn's context and its request text are
    both in scope.

    Everything it records is read from an authority that already owns the question:

    * REQUIRED comes from `core.execution_requirements.requirements_for` -- the canonical M1
      authority, consulted, never re-derived. A turn it does not mark current gets no
      lifecycle and this function returns "".
    * RETRIEVED comes from the turn's own governed retrieval receipt.
    * The support surface comes from the same-turn observation channels, filtered by
      `core.observation_evidence` -- which is how a lane that retrieved through the tool loop
      rather than through M2's prefetch still has its real rows to be judged against.
    * Authorship comes from the turn's provider-call ledger, the third recorder.

    Never raises. A lifecycle that cannot be completed leaves the stages it did reach, and the
    gate reports the first one missing.
    """

    if not isinstance(source_context, dict):
        return ""
    try:
        from core.execution_requirements import requirements_for

        if not requirements_for(
            str(text or ""), source_context=source_context
        ).current_information_required:
            return ""
    except Exception:
        return ""

    lifecycle_id = str(source_context.get(LIFECYCLE_ID_KEY) or "")
    if not lifecycle_id:
        return ""

    receipts = source_context.get("web_retrieval_receipts")
    if isinstance(receipts, list) and receipts:
        last = receipts[-1] if isinstance(receipts[-1], dict) else {}
        try:
            from core.grounded_synthesis_binding import outcome_for_receipt

            outcome = outcome_for_receipt(last, notes=None)
        except Exception:
            outcome = str(last.get("status") or "")
        record_retrieved(
            source_context,
            outcome=outcome,
            receipt=last,
            source_count=int(last.get("source_count") or 0),
        )

    harvest_turn_observations(source_context)
    harvest_execution_records(source_context)
    harvest_retrieval_receipt(source_context)

    authored = False
    try:
        from core.turn_model_call_ledger import turn_served_usage

        authored = bool(turn_served_usage(source_context))
    except Exception:
        authored = False
    if authored and isinstance(result, dict) and bool(result.get("runtime_rendered_final")):
        # Usage was metered for the tool-choosing call, but its bytes were never served: the
        # lane composed the answer from the tool result (see below). Same rule as the conductor.
        authored = False
    if not authored and isinstance(result, dict):
        # A lane that owns its own authorship answer is not second-guessed from a COUNT.
        # The conductor records model authorship precisely -- when one of its
        # generation-authored nodes' rendered text is in the served bytes -- because its
        # planning and semantic-proof calls cross the same ledger without ever writing an
        # answer byte. Counting those here read a deterministic compose as model prose,
        # and a mixed turn whose live observation failed lost its fully-supported
        # arithmetic to a whole-answer refusal with the failed clause -- measured over
        # the real HTTP surface: "Get the current weather in Vilnius, and calculate
        # 37 x 19" served the typed refusal while 37*19 = 703 needed nothing retrieved.
        lane_owns_authorship = isinstance(result.get("conductor_product_decision"), dict)
        # The tool loop declares the same thing in one typed field when the LAST tool's
        # contract says its result is the answer (`renders_final_answer`): a model chose the
        # tool, runtime code composed the published bytes. Declared by the lane that rendered
        # them, never inferred from the count of calls that crossed the ledger.
        if bool(result.get("runtime_rendered_final")):
            lane_owns_authorship = True
        if not lane_owns_authorship:
            authored = bool(result.get("model_calls")) or bool(
                (result.get("model_execution") or {}).get("used_model")
                if isinstance(result.get("model_execution"), dict)
                else False
            )
    if authored:
        record_model_authorship(source_context)

    # Requirement 11, the first Activity row: this turn needed current information, and here is
    # the lifecycle it will be judged on. Emitted here rather than at M1's decision because this
    # is where the turn's identity is settled and where every lane converges -- so the rail shows
    # the same set of turns the gate will judge, and a turn that reaches the gate with no row is
    # visibly a turn that never got here.
    try:
        from core.runtime_task_events import emit_runtime_event

        record = _RECORDS.get(lifecycle_id)
        stages = record.as_dict()["stages"] if record is not None else {}
        emit_runtime_event(
            source_context,
            event_type="grounding_required",
            message="This turn needs current information; its grounding lifecycle is open.",
            details={
                "schema": "vool.grounding_lifecycle.v1",
                "lifecycle_id": lifecycle_id,
                "stages_reached": [name for name, done in stages.items() if done],
                "evidence_set_id": record.bound_evidence_set_id if record is not None else "",
                "model_authored": bool(authored),
                "typed_observation_count": len(record.typed_observations) if record is not None else 0,
            },
        )
    except Exception:
        pass
    return lifecycle_id


def harvest_retrieval_receipt(source_context: dict[str, Any] | None) -> bool:
    """Record RETRIEVED from the turn's own durable retrieval receipt, wherever the lane put it.

    Why this is not optional. Measured on the isolated daemon: the workflow-planner tool loop
    retrieves through `web.search` on a context COPY, so at the sealing seam the caller's dict
    holds no receipt -- and the gate refused the turn saying "the retrieval returned no usable
    rows" when Brave had returned four. The refusal was right and its stated reason was false,
    which is its own defect: a record that does not match the bytes is not a record.

    The receipt is read back from the DURABLE runtime event store, scoped to this turn by the
    identity `emit_runtime_event` already stamps on every row -- so a previous turn's retrieval
    in the same session cannot be read as this one's.
    """

    identity = _identity_from(source_context)
    if not identity.session_id:
        return False
    try:
        from core.runtime_continuity import list_runtime_session_events

        rows = list_runtime_session_events(identity.session_id, limit=200)
    except Exception:
        return False
    receipt: dict[str, Any] = {}
    for row in rows:
        if str(row.get("event_type") or "") != "web_retrieval_completed":
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else row
        row_turn = str(details.get("client_turn_id") or details.get("turn_id") or "").strip()
        if identity.turn_id and row_turn and row_turn != identity.turn_id:
            continue
        receipt = dict(details)
    if not receipt:
        return False
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return False
        if not record.retrieval_outcome:
            record.retrieval_outcome = str(receipt.get("lifecycle") or receipt.get("status") or "")
        record.retrieval_receipt = record.retrieval_receipt or receipt
        record.retrieved_source_count = max(
            record.retrieved_source_count, int(receipt.get("source_count") or 0)
        )
        return True


def harvest_execution_records(source_context: dict[str, Any] | None) -> int:
    """The turn's own tool receipts, shaped into rows a claim can be matched against.

    Measured on the isolated daemon: the workflow-planner tool loop retrieves through
    `web.search` on a context COPY, so at the sealing seam the turn's observation channels are
    empty even though four Brave rows were fetched, receipted and put in front of the model. A
    gate that judged that turn against nothing would refuse it for the wrong reason -- and a
    refusal whose stated reason is false is its own defect, however right the refusal is.

    `core.execution_records` is the durable per-turn tool ledger
    `core.unsourced_current_claim.turn_has_current_evidence` already consults for exactly this
    question, and it is turn-stamped: an UNATTRIBUTED record (empty ``turn_id``) is not this
    turn's and is not read. Only records that report their own success contribute, by the same
    rule `core.observation_evidence` applies everywhere else.

    The record is reshaped, not reinterpreted: its ``items`` are the result rows the tool
    returned and its ``citations`` are where they came from, and they are placed in the note
    fields `core.claim_support` already reads. No new extraction and no new judgement.
    """

    identity = _identity_from(source_context)
    if not (identity.session_id and identity.turn_id):
        return 0
    try:
        from core import execution_records
    except Exception:
        return 0
    rows: list[dict[str, Any]] = []
    for entry in execution_records.records_for_turn(identity.session_id, identity.turn_id):
        if not entry.ok:
            continue
        for index, item in enumerate(entry.items):
            text = str(item or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "summary": text,
                    "result_url": entry.citations[index] if index < len(entry.citations) else "",
                    "origin_domain": entry.resolved_target,
                    "source_type": "tool_receipt",
                    "intent": entry.intent,
                    "ok": True,
                }
            )
    if not rows:
        return 0
    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return 0
        if not record.retrieved_source_count:
            record.retrieved_source_count = len(rows)
        if not record.retrieval_outcome:
            record.retrieval_outcome = "tool_receipt"
    return record_typed_observations(source_context, entries=rows)


def record_model_authorship(source_context: dict[str, Any] | None) -> bool:
    """A model produced this turn's served answer. Raise-only; never lowered.

    Three independent recorders set it, at three layers, because the gate's whole subject is
    model-generated bytes and a lane that escaped one recorder must not thereby present a
    generation as deterministic runtime output: the provider-call ENTRY seam
    (`core.turn_model_call_ledger.record_provider_call`), the reasoning lane's own
    `model_execution.used_model`, and the SERVED-usage record
    (`core.turn_model_call_ledger.record_served_usage`), which names the response that was
    actually returned rather than any call that was merely attempted.
    """

    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return False
        record.model_authored = True
        return True


def record_claim_support(
    source_context: dict[str, Any] | None,
    *,
    payload: Any,
    model_authored: bool | None = None,
) -> bool:
    """M4 stage, as the reasoning lane saw it. Advisory -- the gate recomputes over the bytes.

    `model_authored` is the reasoning lane's own answer to "did a model write this?", recorded
    here because this is the seam where `model_execution` is in scope. It only ever raises the
    flag: a lane that did not call a model cannot lower one another recorder already set.
    """

    with _LOCK:
        record = _record_for(source_context)
        if record is None:
            return False
        record.claim_support = dict(payload or {}) if isinstance(payload, dict) else {}
        if model_authored:
            record.model_authored = True
        return True


def record_publication(lifecycle_id: str, payload: dict[str, Any]) -> bool:
    """PUBLISHED (or the typed exit). Written by `core.finalization` and by nothing else."""

    with _LOCK:
        record = _RECORDS.get(str(lifecycle_id or ""))
        if record is None:
            return False
        _RECORDS.move_to_end(record.lifecycle_id)
        record.publication = dict(payload or {})
        state = str(record.publication.get("state") or "")
        session_id = str(record.identity.session_id or "").strip()
        if session_id and state in {STAGE_PUBLISHED, EXIT_PARTIAL}:
            _LAST_PUBLISHED_BY_SESSION[session_id] = record.lifecycle_id
            _LAST_EXIT_BY_SESSION.pop(session_id, None)
        elif session_id and state in {EXIT_REFUSED, EXIT_FAILED}:
            _LAST_EXIT_BY_SESSION[session_id] = record.lifecycle_id
        return True


def previous_exit_for_session(session_id: str) -> GroundingLifecycle | None:
    """The session's most recent refused/failed lifecycle (cleared by a later published answer)."""
    key = str(session_id or "").strip()
    if not key:
        return None
    with _LOCK:
        lifecycle_id = _LAST_EXIT_BY_SESSION.get(key)
        return _RECORDS.get(lifecycle_id or "") if lifecycle_id else None


def previous_publication_for_session(session_id: str) -> GroundingLifecycle | None:
    """The session's most recent published (or partial) lifecycle, or None."""
    key = str(session_id or "").strip()
    if not key:
        return None
    with _LOCK:
        lifecycle_id = _LAST_PUBLISHED_BY_SESSION.get(key)
        return _RECORDS.get(lifecycle_id or "") if lifecycle_id else None


_ANAPHORA_WORD_RE = re.compile(r"[a-z]+")
_ANAPHORA_STEM_CHARS = 5


def _stems(words: Any) -> set[str]:
    out: set[str] = set()
    for word in words:
        token = str(word or "").lower()
        if len(token) >= 4:
            out.add(token[:_ANAPHORA_STEM_CHARS])
    return out


def re_presentation_target(request_text: str, session_id: str) -> GroundingLifecycle | None:
    """The published answer this turn re-presents, or None when the turn is not a re-presentation.

    A re-presentation carries an explicit presentation shape (the parser's own matchers) and
    names NOTHING of its own: every word left once shape language, shape filler, request
    scaffolding and dialogue-meta words are removed must refer to the previous exchange -- it
    stems to a word of the previous request or of the previous answer's adjudicated claims.
    "present the comparison as a table" after "how does VW passat compare to golf?" is one;
    "explain photosynthesis in a table" names a new subject and is not. No word list names a
    subject here: the previous exchange itself is the vocabulary.
    """
    previous = previous_publication_for_session(session_id)
    if previous is None:
        return None
    return previous if _re_presents(request_text, previous) else None


def _asks_for_a_shape_or_size(text: str) -> bool:
    """An explicit presentation shape, a list shape, or a size/register change ("shorter please",
    "make it briefer", "tl;dr") that names nothing of its own."""
    from core.response_constraints import parse_response_constraint

    constraint = parse_response_constraint(text)
    if constraint is not None and (
        getattr(constraint, "presentation_format", None) or getattr(constraint, "list_items", None)
    ):
        return True
    from core.live_data_continuation import _DIALOGUE_META, _SCAFFOLDING
    from core.response_constraints import residual_words_outside_shape

    words = [
        w for w in residual_words_outside_shape(text)
        if w not in _DIALOGUE_META and w not in _SCAFFOLDING
    ]
    if not words:
        return False
    return all(w in _RE_PRESENTATION_WORDS for w in words)


def _re_presents(request_text: str, previous: GroundingLifecycle) -> bool:
    """Whether `request_text` re-presents `previous`: it asks for a shape or size and every word
    it names refers to the previous exchange."""
    text = str(request_text or "")
    if not text.strip():
        return False
    if not _asks_for_a_shape_or_size(text):
        return False
    from core.response_constraints import residual_words_outside_shape

    claims = list(dict(previous.publication.get("claim_support") or {}).get("claims") or [])
    withheld = list(previous.publication.get("withheld_claims") or [])
    vocabulary = _stems(
        _ANAPHORA_WORD_RE.findall(
            " ".join(
                [
                    str(previous.request_text or "").lower(),
                    *(str(c.get("text") or "").lower() for c in claims if isinstance(c, dict)),
                    *(str(w).lower() for w in withheld),
                ]
            )
        )
    )
    from core.live_data_continuation import _DIALOGUE_META, _SCAFFOLDING
    residual = [
        word
        for word in residual_words_outside_shape(text)
        if word not in _DIALOGUE_META and word not in _SCAFFOLDING
    ]
    for word in residual:
        if len(word) < 4:
            continue
        if word in _RE_PRESENTATION_WORDS:
            continue
        if word[:_ANAPHORA_STEM_CHARS] not in vocabulary:
            return False
    return True


def adopt_previous_publication_if_representation(
    source_context: dict[str, Any] | None, *, request_text: str
) -> bool:
    """Open THIS turn's lifecycle on the previous answer's published support when the turn is a
    re-presentation of that answer. True when a lifecycle was opened.

    The boundary this closes (2026-09-06, native window): a partial publication lists the claims
    it withheld, verbatim, in its notice; the next turn asked for the same answer "as a table";
    the model reformatted the notice's bullets as rows, the turn had no lifecycle, and the gate
    published lengths and prices the sources had never supported. The support for a re-presented
    answer is exactly what was PUBLISHED as supported: those rows are adopted as this turn's
    typed observations, and the same claim adjudication that decided the original answer decides
    the reformatted one -- supported facts survive any shape, withheld and never-adjudicated
    claims cannot become facts by being restated.
    """
    if not isinstance(source_context, dict):
        return False
    session_id = str(
        source_context.get("session_id") or source_context.get("runtime_session_id") or ""
    ).strip()
    previous = re_presentation_target(request_text, session_id)
    if previous is None:
        # No published answer to adopt. A re-presentation of an answer this session WITHHELD
        # (refused / failed) opens a lifecycle with no support rows, so the gate refuses the
        # reformatted bytes with a typed notice instead of publishing them ungated (measured
        # 2026-09-06: "shorter please" after a refused comparison published the model's text
        # verbatim; "put that in a table" rendered a table of the refusal).
        withheld = previous_exit_for_session(session_id)
        if withheld is None or _record_for(source_context) is not None or not _re_presents(request_text, withheld):
            return False
        lifecycle_id = register_required(
            source_context,
            request_text=str(request_text or ""),
            reason_codes=(REASON_RE_PRESENTATION, REASON_RE_PRESENTATION_OF_WITHHELD),
        )
        return bool(lifecycle_id)
    if _record_for(source_context) is not None:
        return False
    claims = [
        c
        for c in list(dict(previous.publication.get("claim_support") or {}).get("claims") or [])
        if isinstance(c, dict)
        and str(c.get("status") or "") == "supported"
        and str(c.get("text") or "").strip()
    ]
    rows = [
        {
            "text": str(c.get("text") or "").strip(),
            "source": f"published:{previous.lifecycle_id}",
            "source_label": "previous answer (published support)",
            "ok": True,
        }
        for c in claims
    ]
    if REASON_RE_PRESENTATION in tuple(previous.reason_codes or ()):
        # The previous answer was itself a re-presentation ("as a table"): its support is the rows
        # IT adopted -- the original answer's published sentences, which carry their subjects --
        # and its own supported cells come along. A "shorter please" after a table after a
        # comparison re-presents the comparison (measured 2026-09-06: adopting only the table's
        # cell fragments left "Golf" unwitnessed and the shortened answer was refused; with an
        # empty table map the model's text shipped ungated).
        rows = [dict(row) for row in previous.typed_observations if isinstance(row, dict)] + rows
    if not rows:
        # A shape or size request that matches the previous exchange, but nothing supported
        # exists to re-present: say so, typed, rather than letting model prose ship ungated.
        lifecycle_id = register_required(
            source_context,
            request_text=str(request_text or ""),
            reason_codes=(REASON_RE_PRESENTATION, REASON_RE_PRESENTATION_OF_WITHHELD),
        )
        return bool(lifecycle_id)
    lifecycle_id = register_required(
        source_context,
        request_text=str(request_text or ""),
        reason_codes=(REASON_RE_PRESENTATION,),
    )
    if not lifecycle_id:
        return False
    record_typed_observations(source_context, entries=rows)
    return True


# ------------------------------------------------------------------------------- readers


def lifecycle_for_context(source_context: Any) -> GroundingLifecycle | None:
    """This turn's lifecycle, found by its stamped id. For lanes that still hold the context."""

    with _LOCK:
        return _record_for(source_context)


def adopt_lifecycle_id(source_context: dict[str, Any] | None) -> str:
    """Stamp this turn's lifecycle id onto a context copy that predates M1's consultation.

    Writers on this ledger resolve the row by the id stamped IN the context dict, and a
    lane that works on a copy made before M1 first marked the turn current-information
    (the conductor's deadline-bound copy, a planned sub-turn's shallow copy) holds no id
    even though the turn has a row -- indexed under the turn identity the same copy still
    carries. Adoption finds that row by identity and stamps the id; a context that already
    carries one, or a turn with no row, is returned untouched. Never raises, never invents
    an id: a turn nothing opened stays unwritten.
    """
    if not isinstance(source_context, dict):
        return ""
    if str(source_context.get(LIFECYCLE_ID_KEY) or "").strip():
        return str(source_context[LIFECYCLE_ID_KEY])
    seen = _identity_from(source_context)
    with _LOCK:
        for token in seen.tokens:
            record = _RECORDS.get(_INDEX.get(token, ""))
            if record is not None and record.identity.same_generation_as(seen):
                source_context[LIFECYCLE_ID_KEY] = record.lifecycle_id
                return record.lifecycle_id
    return ""


def lifecycle_for_publication(*, turn_id: str = "") -> GroundingLifecycle | None:
    """The lifecycle of the turn now finalizing, or None when M1 never opened one.

    None is the DIRECT/timeless answer and means "publish unchanged": a turn that never needed
    current information has no lifecycle to complete. Resolution is by turn-UNIQUE identity
    only, and a record whose fence tuple disagrees with the current one is refused -- a resumed
    generation may not collect the evidence of the generation it replaced.
    """

    current = _identity_from(None)
    presented_turn = str(turn_id or "").strip()
    candidates = [
        token
        for token in (current.bound_request_id, current.request_id, presented_turn)
        if token
    ]
    with _LOCK:
        for token in candidates:
            lifecycle_id = _INDEX.get(token)
            if not lifecycle_id:
                continue
            record = _RECORDS.get(lifecycle_id)
            if record is None:
                continue
            if not record.identity.same_generation_as(current):
                continue
            # One matching token is not enough. A finalization that presents a request id AND a
            # turn id must not consume a row that agrees on one and DISAGREES on the other: ids
            # are reused (fixed ids in tests, `fast:<session>:<hash>` in the fast lane), and a
            # stale row consumed by a later turn is the cross-turn leak this ledger exists to
            # prevent, pointed the other way.
            if presented_turn and _turn_conflicts(record.identity, presented_turn):
                continue
            if (
                current.bound_request_id
                and record.identity.bound_request_id
                and current.bound_request_id != record.identity.bound_request_id
            ):
                continue
            _RECORDS.move_to_end(lifecycle_id)
            return record
    return None


def _turn_conflicts(identity: TurnIdentity, presented_turn: str) -> bool:
    """Does the presented turn id contradict every turn identity this record carries?

    Contradiction, not absence: a record that never learned a turn id agrees with any, and a
    record whose turn id or task id matches agrees. Only a record that carries turn identities
    and matches NONE of them is refused.
    """

    known = tuple(token for token in (identity.turn_id, identity.task_id) if token)
    if not known:
        return False
    return presented_turn not in known


def reset_for_tests() -> None:
    """Drop every tracked lifecycle. Test-support only."""

    with _LOCK:
        _RECORDS.clear()
        _INDEX.clear()


__all__ = [
    "EXIT_FAILED",
    "EXIT_PARTIAL",
    "EXIT_REFUSED",
    "LIFECYCLE_ID_KEY",
    "ORIGIN_BOUND_EVIDENCE",
    "ORIGIN_TYPED_OBSERVATION",
    "STAGE_BOUND",
    "STAGE_ORDER",
    "STAGE_PUBLISHED",
    "STAGE_REQUIRED",
    "STAGE_RETRIEVED",
    "STAGE_SUPPORTED",
    "GroundingLifecycle",
    "SynthesisCall",
    "TurnIdentity",
    "adopt_lifecycle_id",
    "harvest_turn_observations",
    "lifecycle_for_context",
    "lifecycle_for_publication",
    "record_bound",
    "record_child_lifecycle",
    "record_claim_support",
    "record_model_authorship",
    "record_publication",
    "record_retrieved",
    "record_synthesis_call",
    "record_typed_observations",
    "register_required",
    "reset_for_tests",
    "seal_turn_grounding",
]
