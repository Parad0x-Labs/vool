"""Run the governed retrieval BEFORE the model call, for a turn that needs current facts.

At base the grounded reasoning lane collected live web notes at
`turn_reasoning.execute_grounded_turn`, well AFTER `memory_router.resolve` had already
produced the publishable bytes. For a timeless question that ordering costs nothing --
the notes are evidence for the record, not input to an answer. For a question the
requirements authority has already marked `current_information_required`, it is the
whole defect: the answer is written from weights, and the evidence arrives in time to
be receipted behind it but not in time to be used.

This module moves that one retrieval to the front of the lane for exactly those turns,
and records what it moved. It deliberately does NOT:

* widen, narrow or second-guess `requirements_for` -- it only reads the flag;
* judge whether the answer used the evidence -- `core.evidence_binding` owns that;
* touch the fast live-data lanes, which resolve their own observations before any
  model call already and never reach here.

The retrieval itself is unchanged: the same `_collect_live_web_notes`, through the same
effect gateway, writing the same `web_retrieval_started` / `web_retrieval_completed`
receipts. Only its POSITION in the turn moves, plus the binding record that proves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.grounded_synthesis_binding import (
    OUTCOME_BOUND,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_REFUSED,
    EvidenceSet,
    binding_record,
    emit_evidence_bound,
    last_retrieval_receipt,
    mint_evidence_set,
    outcome_for_receipt,
    turn_scope,
)

#: Outcomes that must not be handed to the model dressed as current evidence.
#
# A transport failure and a policy refusal always block: the model would be answering from
# weights about something only an observation can settle.
BLOCKING_OUTCOMES = frozenset({OUTCOME_FAILED, OUTCOME_REFUSED})

#: `OUTCOME_PARTIAL` -- retrieval ran and the provider had nothing -- is CONDITIONAL, and the
#: condition is whether anything else on the turn is already handling the gap.
#
# The runtime has a designed path for weak evidence: the research lane admits uncertainty, says
# so IN the prompt, and the model answers tentatively. That is better product behaviour than a
# hard refusal, and blocking unconditionally destroyed it -- caught by
# `test_adaptive_research_surfaces_uncertainty_when_evidence_stays_weak`, which drives exactly
# that case and only started failing once M1 widened the classifier to cover those turns.
#
# So an empty retrieval blocks only when NOTHING is handling it: no admitted uncertainty, no
# research lane engaged. That is the measured fabrication's shape -- a turn that needed an
# observation, made none, and had nothing to tell the model it was working blind.
PARTIAL_BLOCKS_UNLESS_HANDLED = OUTCOME_PARTIAL


@dataclass
class PrefetchResult:
    """What the hoisted retrieval did, for the lane to act on and for the record."""

    ran: bool = False
    reason: str = "not_current_information"
    outcome: str = ""
    notes: list[dict[str, Any]] = field(default_factory=list)
    evidence_set: EvidenceSet | None = None
    binding: dict[str, Any] = field(default_factory=dict)
    receipt: dict[str, Any] = field(default_factory=dict)
    model_input: str = ""
    #: Appended to a prompt another lane already built, instead of replacing it. The
    #: adaptive-research lane frames its own observations; discarding that framing to
    #: restate the same rows under a different channel changes what the model is asked
    #: without adding anything, so this lane contributes only the identity manifest.
    prompt_suffix: str = ""

    @property
    def bound(self) -> bool:
        """Evidence is identified and enters the answering prompt.

        Deliberately independent of `ran`: the adaptive-research path retrieves before this lane
        is reached, so it binds without this lane performing the retrieval. `ran` answers "did
        this lane fetch?", which is what the post-model collector must not duplicate; `bound`
        answers "does the answering call carry identified evidence?", which is what the envelope
        stamp and the binding event report.
        """
        return bool(self.outcome == OUTCOME_BOUND and self.notes)

    #: True when some other lane on this turn already tells the model the evidence is weak.
    uncertainty_handled: bool = False

    @property
    def blocking(self) -> bool:
        """Retrieval was attempted for a current-information turn and did not deliver."""
        if not self.ran:
            return False
        if self.outcome in BLOCKING_OUTCOMES:
            return True
        return bool(
            self.outcome == PARTIAL_BLOCKS_UNLESS_HANDLED and not self.uncertainty_handled
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ran": self.ran,
            "reason": self.reason,
            "outcome": self.outcome,
            "source_count": len(self.notes),
        }
        if self.binding:
            payload["binding"] = dict(self.binding)
        return payload


def requires_current_information(effective_input: str, source_context: dict[str, Any] | None) -> bool:
    """Read the requirements authority. Never re-derive the judgement, never widen it.

    Fail-closed here means fail to FALSE: if the classifier cannot be consulted, the lane
    keeps its base behaviour rather than hoisting a retrieval onto a turn that may not
    want one. A missed hoist is the pre-existing defect; a spurious one is a new latency
    cost charged to every turn whose classification could not be read.
    """
    try:
        from core.execution_requirements import requirements_for

        return bool(requirements_for(effective_input, source_context=source_context).current_information_required)
    except Exception:
        return False


def _observations(evidence_set: EvidenceSet, *, query: str) -> dict[str, Any]:
    """The retrieved rows, plus the ids the envelope will claim entered this prompt.

    The ids ride INSIDE the prompt material, not merely alongside it, so
    `proves="prompt_entry"` is checkable by reading the prompt rather than by trusting
    the stamp that describes it.
    """
    from core.agent_runtime.chat_surface import live_info_observations

    observations = live_info_observations(
        query=query,
        mode="fresh_lookup",
        notes=[dict(note) for note in evidence_set.notes],
    )
    observations["evidence_set_id"] = evidence_set.evidence_set_id
    observations["note_ids"] = list(evidence_set.note_ids)
    return observations


def evidence_manifest(evidence_set: EvidenceSet) -> str:
    """The identity block appended to a prompt another lane already built.

    Only the ids and the sources they name. It carries no instructions and no reframing, so
    appending it cannot change what the model was asked -- it makes the envelope's
    `proves="prompt_entry"` claim checkable by reading the prompt rather than by trusting the
    stamp that describes it.
    """
    lines = [
        "Evidence identity for this turn (retrieved before this answer was written):",
        f"evidence_set_id: {evidence_set.evidence_set_id}",
    ]
    for note_id, note in zip(evidence_set.note_ids, evidence_set.notes, strict=False):
        domain = str(note.get("origin_domain") or "").strip()
        lines.append(f"- {note_id}{f' ({domain})' if domain else ''}")
    return "\n".join(lines)


def build_model_input(evidence_set: EvidenceSet, *, user_input: str, query: str) -> str:
    """The answering call's prompt: the user's question plus this turn's retrieved rows.

    `observation_prompt` with the `live_info`/`fresh_lookup` pair is the runtime's existing
    strict-grounding frame ("Answer ONLY using the search results below ... do NOT guess or
    fill in from general knowledge"). Reused rather than re-authored, so a change to how
    the runtime frames retrieved evidence lands here too instead of drifting apart.
    """
    from core.agent_runtime.chat_surface import observation_prompt

    return observation_prompt(
        user_input=user_input,
        observations=_observations(evidence_set, query=query),
    )


def _record_lifecycle(
    source_context: dict[str, Any] | None,
    *,
    outcome: str,
    receipt: dict[str, Any] | None = None,
    notes: list[dict[str, Any]] | None = None,
    binding: dict[str, Any] | None = None,
    scope: str = "",
) -> None:
    """M3 stages RETRIEVED and BOUND_TO_SYNTHESIS, on this turn's grounding lifecycle.

    Two separate facts, recorded separately on purpose. RETRIEVED is the terminal truth of the
    retrieval, read off its own receipt. BOUND is the claim that identified rows entered the
    answering prompt -- and it is stored only after `binding_admitted_for_turn` re-derives the
    scope digest, so a record carried in from another turn by a retry or a resume is refused
    rather than counted.

    Never raises: the lifecycle is a record of what this lane did, and failing to write it must
    not stop the lane from doing it. A stage that never lands makes the publication gate refuse,
    which is the safe direction.
    """
    if not isinstance(source_context, dict):
        return
    try:
        from core import grounding_lifecycle

        grounding_lifecycle.record_retrieved(
            source_context,
            outcome=outcome,
            receipt=receipt,
            source_count=len(list(notes or [])),
            notes=list(notes or []),
        )
        if binding:
            grounding_lifecycle.record_bound(
                source_context, binding=binding, notes=list(notes or []), scope=scope
            )
    except Exception:
        return


def _bind_existing_notes(
    notes: list[dict[str, Any]],
    *,
    task: Any,
    effective_input: str,
    source_context: dict[str, Any] | None,
) -> PrefetchResult:
    """Identify and bind evidence that a pre-model lane already retrieved this turn.

    No retrieval happens here -- `ran` stays False so the post-model collector keeps its own
    guard semantics -- but the notes get a stable evidence-set id, the ids ride into the prompt,
    and the binding event is emitted, so the answering call's envelope names exactly what it
    carried instead of leaving the question unanswerable.
    """
    context = source_context if isinstance(source_context, dict) else {}
    clean = [dict(note) for note in list(notes or []) if isinstance(note, dict)]
    if not clean:
        return PrefetchResult(reason="evidence_already_collected_pre_model")
    scope = turn_scope(context, session_id="", task_id=str(getattr(task, "task_id", "") or ""))
    evidence_set = mint_evidence_set(clean, scope=scope, query=effective_input)
    record = binding_record(evidence_set, outcome=OUTCOME_BOUND, model_call_stage="grounded_synthesis_prefetched")
    emit_evidence_bound(source_context, record, task_id=str(getattr(task, "task_id", "") or ""))
    _record_lifecycle(
        source_context, outcome=OUTCOME_BOUND, notes=clean, binding=record, scope=scope
    )
    return PrefetchResult(
        ran=False,
        reason="bound_evidence_collected_pre_model",
        outcome=OUTCOME_BOUND,
        notes=clean,
        evidence_set=evidence_set,
        binding=record,
        prompt_suffix=evidence_manifest(evidence_set),
    )

def prefetch_current_evidence(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, Any] | None,
    is_chat_surface: bool,
    action_forbidden: bool,
    remote_fetch_disabled: bool,
    skip_for_local_answer: bool,
    already_have_notes: bool,
    already_collected_notes: list[dict[str, Any]] | None = None,
    uncertainty_handled: bool = False,
) -> PrefetchResult:
    """Retrieve first, then report what may be bound into the synthesis call.

    Returns `ran=False` untouched for every turn this does not apply to, so the DIRECT and
    no-retrieval lanes pay nothing: one classifier read, no network, no extra model call.
    """
    if not is_chat_surface:
        return PrefetchResult(reason="not_a_chat_surface")
    current_required = requires_current_information(effective_input, source_context)
    if already_have_notes:
        # Adaptive research already retrieved BEFORE the model call, so the ordering this lane
        # exists to enforce already holds and a second retrieval would buy nothing. What is still
        # missing is the RECORD: measured live on the isolated daemon (drive r2), this path put
        # four real sources into the prompt and left no evidence set, no ids and no binding event,
        # so nothing downstream could say which evidence entered the answering call. Identify and
        # bind those notes rather than re-fetching them.
        if not current_required or not already_collected_notes:
            return PrefetchResult(reason="evidence_already_collected_pre_model")
        return _bind_existing_notes(
            already_collected_notes,
            task=task,
            effective_input=effective_input,
            source_context=source_context,
        )
    if action_forbidden or remote_fetch_disabled:
        return PrefetchResult(reason="remote_fetch_not_permitted")
    if skip_for_local_answer:
        return PrefetchResult(reason="local_answer_no_research")
    if not current_required:
        return PrefetchResult(reason="not_current_information")

    context = source_context if isinstance(source_context, dict) else {}
    receipts_before = len(context.get("web_retrieval_receipts") or []) if isinstance(context, dict) else 0
    if receipts_before:
        # A governed retrieval ALREADY ran on this turn and came back with nothing usable --
        # otherwise `already_have_notes` would have routed us to the binding path above.
        # Searching again is not a second chance, it is a second identical query to the same
        # provider, and the lane that ran it is already handling the empty result its own way
        # (the research lane admits uncertainty and says so in the prompt).
        #
        # Measured: without this, a turn whose adaptive research returned nothing got a FOURTH
        # search from this lane, and on a stubbed transport that surfaced as a transport failure
        # which then blocked the model call outright -- destroying the designed weak-evidence
        # path. One turn gets one governed retrieval; that was M2's rule and it applies to this
        # lane too.
        return PrefetchResult(reason="governed_retrieval_already_ran_this_turn")
    try:
        notes = agent._collect_live_web_notes(
            task_id=getattr(task, "task_id", ""),
            query_text=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
        )
    except Exception:
        # `_collect_live_web_notes` handles its own failures and receipts them; an escape past
        # it is still a retrieval that did not deliver, and must not be reported as one that did.
        return PrefetchResult(ran=True, reason="retrieval_raised", outcome=OUTCOME_FAILED)

    clean_notes = [dict(note) for note in list(notes or []) if isinstance(note, dict)]
    receipt = last_retrieval_receipt(context)
    receipts_after = len(context.get("web_retrieval_receipts") or []) if isinstance(context, dict) else 0
    if receipts_after <= receipts_before:
        # No receipt was written, so no governed retrieval ran -- the collector declined at one of
        # its own policy gates. That is not a failure to report to the user; it is this lane not
        # applying, and the turn keeps its base behaviour.
        if not clean_notes:
            return PrefetchResult(reason="no_governed_retrieval_ran")
        receipt = {}

    outcome = outcome_for_receipt(receipt, notes=clean_notes)
    if outcome != OUTCOME_BOUND:
        _record_lifecycle(source_context, outcome=outcome, receipt=receipt)
        return PrefetchResult(
            ran=True,
            reason="retrieval_did_not_deliver",
            outcome=outcome,
            notes=[],
            receipt=receipt,
            uncertainty_handled=bool(uncertainty_handled),
        )

    scope = turn_scope(context, session_id="", task_id=str(getattr(task, "task_id", "") or ""))
    evidence_set = mint_evidence_set(
        clean_notes,
        scope=scope,
        query=effective_input,
        provider_id=str(receipt.get("provider_id") or ""),
    )
    record = binding_record(evidence_set, outcome=OUTCOME_BOUND)
    emit_evidence_bound(source_context, record, task_id=str(getattr(task, "task_id", "") or ""))
    _record_lifecycle(
        source_context,
        outcome=OUTCOME_BOUND,
        receipt=receipt,
        notes=clean_notes,
        binding=record,
        scope=scope,
    )
    return PrefetchResult(
        ran=True,
        reason="bound_before_synthesis",
        outcome=OUTCOME_BOUND,
        notes=clean_notes,
        evidence_set=evidence_set,
        binding=record,
        receipt=receipt,
        model_input=build_model_input(evidence_set, user_input=effective_input, query=effective_input),
    )


def unavailable_current_answer(effective_input: str, outcome: str) -> str:
    """What a person is told when the turn could not get the current facts it needed.

    Names the actual boundary. A refusal is a permission fact and sends nobody to fix a
    working key; a failure is a transport fact. Neither is dressed as an answer, and
    neither invites the model to fill the gap from its weights.
    """
    subject = " ".join(str(effective_input or "").split()).strip()
    tail = f' for "{subject[:160]}"' if subject else ""
    if outcome == OUTCOME_REFUSED:
        return (
            f"I could not look up current information{tail}: this runtime is not currently "
            "permitted to search the web. Nothing was fetched, so I won't answer from memory — "
            "a current answer would be a guess. Enable web access for this runtime and ask again."
        )
    if outcome == OUTCOME_PARTIAL:
        return (
            f"I searched for current information{tail} and the provider returned no results. "
            "The search itself worked, so this is not a broken connection — there was nothing to "
            "retrieve. I won't answer from memory, because a current answer with no observation "
            "behind it would be a guess. Try a narrower or differently worded query."
        )
    return (
        f"I could not look up current information{tail}: the search did not come back. "
        "Nothing was retrieved, so I won't answer from memory — a current answer would be a "
        "guess. Check the search provider's connection and ask again."
    )


__all__ = [
    "BLOCKING_OUTCOMES",
    "OUTCOME_BOUND",
    "OUTCOME_FAILED",
    "OUTCOME_PARTIAL",
    "OUTCOME_REFUSED",
    "PrefetchResult",
    "build_model_input",
    "evidence_manifest",
    "prefetch_current_evidence",
    "requires_current_information",
    "unavailable_current_answer",
]
