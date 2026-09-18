"""The VOOL agent runtime (MILESTONE 1: runtime-owned agent behavior, core-owned home).

This module was `apps/vool_agent.py`; the class and every module-level helper moved
here unchanged and `apps/vool_agent.py` is now a thin CLI/composition facade that
re-exports them. REMOVAL CONDITION for the facade: once no caller imports a name from
`apps.vool_agent` (patch sites included -- they must target THIS module, or a patch
lands on a dead facade copy), the facade shrinks to `main()` alone.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import sys as _bootstrap_sys
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)

from core import audit_logger, feedback_engine, policy_engine
from core.agent_runtime import fast_command_surface as agent_fast_command_surface
from core.agent_runtime import hive_followups as agent_hive_followups
from core.agent_runtime import hive_runtime as agent_hive_runtime
from core.agent_runtime import memory_runtime as agent_memory_runtime
from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import presence as agent_presence_runtime
from core.agent_runtime import turn_dispatch as agent_turn_dispatch
from core.agent_runtime import turn_frontdoor as agent_turn_frontdoor
from core.agent_runtime import turn_reasoning as agent_turn_reasoning
from core.agent_runtime.action_honesty_validator import (
    emit_turn_honesty_receipt,
    enforce_final_action_honesty,
)
from core.agent_runtime.builder_facade import BuilderFacadeMixin
from core.agent_runtime.chat_surface_facade import ChatSurfaceFacadeMixin
from core.agent_runtime.empty_turn import (
    describe_turn_attachments,
    empty_turn_reply,
    resumed_empty_turn_reply,
    turn_carries_retained_document,
    turn_has_no_request,
)
from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
from core.agent_runtime.hive_review_runtime import HiveReviewRuntimeMixin
from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
from core.agent_runtime.voolbook_runtime import VoolBookRuntimeMixin
from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin
from core.agent_runtime.public_hive_support import PublicHiveSupportMixin
from core.agent_runtime.request_authority import bounded_evidence_items, turn_command_text
from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin
from core.agent_runtime.runtime_checkpoint_support import RuntimeCheckpointSupportMixin
from core.agent_runtime.task_persistence_support import TaskPersistenceSupportMixin
from core.agent_runtime.tool_result_surface import ToolResultSurfaceMixin
from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from core.channel_actions import dispatch_outbound_post_intent, parse_channel_post_intent
from core.credit_ledger import (
    escrow_credits_for_task,
    get_credit_balance,
    transfer_credits,
)
from core.curiosity_roamer import CuriosityRoamer
from core.hive_activity_tracker import (
    HiveActivityTracker,
    clear_hive_interaction_state,
    prune_stale_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
    update_session_hive_state,
)
from core.human_input_adapter import adapt_user_input, runtime_session_id
from core.identity_manager import load_active_persona
from core.knowledge_fetcher import request_relevant_holders
from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
from core.logging_config import setup_logging
from core.media_analysis_pipeline import MediaAnalysisPipeline
from core.media_ingestion import build_media_context_snippets, ingest_media_evidence
from core.memory_first_router import MemoryFirstRouter
from core.onboarding import get_agent_display_name
from core.parent_orchestrator import orchestrate_parent_task
from core.persistent_memory import (
    append_conversation_event,
    augment_history_from_session_log,
    ensure_memory_files,
    maybe_handle_memory_command,
    search_user_heuristics,
)
from core.public_hive_bridge import PublicHiveBridge
from core.reasoning_engine import (
    Plan,
    build_plan,
    explicit_planner_style_requested,
    render_response,
    should_use_planner_renderer,
)
from core.runtime_continuity import (
    AttemptLifecycle,
    append_runtime_event,
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_failed_checkpoint,
    latest_resumable_checkpoint,
    mark_stale_runtime_attempts_abandoned,
    mark_stale_runtime_checkpoints_interrupted,
    record_runtime_tool_progress,
    recover_stale_queue_items,
    resume_runtime_checkpoint,
    runtime_checkpoint_restoration_scope,
    sweep_stale_checkpoints_if_due,
    update_runtime_checkpoint,
)
from core import runtime_active_clock
from core.runtime_task_events import emit_runtime_event
from core.semantic import reach as semantic_reach
from core.semantic.semantic_result_seam import (
    SemanticSource,
    admit_semantic_result,
    reset_admission,
)
from core.shard_synthesizer import build_generalized_query, from_task_result
from core.task_router import (
    classify,
    create_task_record,
    load_task_record,
    looks_like_semantic_hive_request,
)
from core.tiered_context_loader import TieredContextLoader
from core.tool_intent_executor import (
    _looks_like_workspace_bootstrap_request,
    execute_tool_intent,
    plan_tool_workflow,
    render_capability_truth_response,
    should_attempt_tool_intent,
)
from core.turn_contract import TURN_REQUEST_KEY, TURN_STATE_KEY, TurnRequest
from core.user_preferences import load_preferences, maybe_handle_preference_command
from core.within_turn_retraction import intake_request_text
from network import signer as signer_mod
from retrieval.swarm_query import dispatch_query_shard
from retrieval.web_adapter import WebAdapter
from storage.migrations import run_migrations

#: Fast-path route reasons whose text is a stated NON-fulfilment of the request (FINDINGS F15):
#: the door closes such a turn's attempt PARTIAL_SUCCESS, never SUCCEEDED, so a follow-up "why?"
#: has something to bind to and the record never says the work succeeded.
_UNFULFILLED_FAST_PATH_REASONS = frozenset(
    {
        "ambiguity_adjudication_unresolved",
        "model_tool_intent_tool_synthesis_failed",
        "live_data_plan_unavailable",
        "conductor_integrity_failure",
        "demand_owned_mixed_turn_failed",
        "demand_owned_mixed_turn_degraded",
    }
)

_log = logging.getLogger(__name__)


def _augment_history_from_session_log(
    history: list[dict[str, str]] | None,
    *,
    session_id: str,
    user_text: str,
    limit: int = 6,
) -> list[dict[str, str]]:
    return augment_history_from_session_log(
        history,
        session_id=session_id,
        user_text=user_text,
        limit=limit,
    )

_PATCH_COMPAT_EXPORTS = (
    pick_autonomous_research_signal,
    research_topic_from_signal,
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_failed_checkpoint,
    latest_resumable_checkpoint,
    record_runtime_tool_progress,
    resume_runtime_checkpoint,
    update_runtime_checkpoint,
    emit_runtime_event,
    create_task_record,
    load_task_record,
    execute_tool_intent,
    plan_tool_workflow,
    render_capability_truth_response,
    should_attempt_tool_intent,
    search_user_heuristics,
    _looks_like_workspace_bootstrap_request,
    WebAdapter,
)


@dataclass
class AgentRuntime:
    backend_name: str
    device: str
    persona_id: str
    swarm_enabled: bool


@dataclass
class GateDecision:
    mode: str
    reason: str
    requires_user_approval: bool
    allowed_actions: list[str]


class ResponseClass(str, Enum):
    SMALLTALK = "smalltalk"
    UTILITY_ANSWER = "utility_answer"
    TASK_LIST = "task_list"
    TASK_SELECTION_CLARIFICATION = "task_selection_clarification"
    TASK_STARTED = "task_started"
    TASK_STATUS = "task_status"
    TASK_FAILED_USER_SAFE = "task_failed_user_safe"
    RESEARCH_PROGRESS = "research_progress"
    APPROVAL_REQUIRED = "approval_required"
    SYSTEM_ERROR_USER_SAFE = "system_error_user_safe"
    GENERIC_CONVERSATION = "generic_conversation"


@dataclass
class ChatTurnResult:
    text: str
    response_class: ResponseClass
    workflow_summary: str = ""
    debug_origin: str | None = None
    allow_planner_style: bool = False


def _record_conductor_demand_receipts(plan: Any, outcomes: Any, *, request: str) -> None:
    """File the demand receipts the conductor's own EXECUTION record supports.

    RSS's discharge channel is written by the lane whose work it attests
    (`core/conductor/obligation_ledger.py`): a lane records a consumption receipt, and the
    finalization sweep -- the sole writer of dispositions -- turns it into `satisfied`. Every
    deterministic fast-path lane in `turn_frontdoor` files one. The conductor, the lane that
    serves the multi-demand turns the census exists to account for, filed none, so a turn that
    answered every requested slot still certified `demand_satisfied: 0`. Measured served on build
    d6be47f9, acceptance turn 7: both demands answered in the bytes, ledger snapshot
    `obset-6f17822a34de4f23` holding `consumption: []` and both units `indeterminate`.

    The receipt is a fact about EXECUTION, not about prose: `demands_this_plan_served` reads
    succeeded node outcomes and binds them to units by span containment. Nothing here reads the
    answer text, so no phrasing can buy coverage -- which is the property C-7 protects.

    Never raises: accounting may not take down a served answer. A failure is logged and the
    sweep still reaches its own verdict from the evidence it holds.
    """
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.conductor import obligation_ledger
        from core.conductor.planner import CONDUCTOR_LANE_ID, demands_this_plan_served

        active = obligation_ledger.active_set()
        if active is None:
            return
        units = demand_units(str(request or ""))
        if not units:
            return
        for unit_id, node_id in demands_this_plan_served(plan, outcomes, units):
            obligation_ledger.record_slice_consumption(
                active[0],
                active[1],
                unit_id=unit_id,
                family=CONDUCTOR_LANE_ID,
                evidence="slice_answer_record",
                excerpt=node_id,
            )
    except Exception:  # pragma: no cover - accounting never breaks a served turn
        _log.exception("conductor demand receipts not recorded")


def _record_planned_turn_demand_receipts(outcomes: Any, *, request: str) -> None:
    """File the demand receipts the PLANNED SUB-TURN lane's own execution record supports.

    The same discharge channel the conductor uses, reached through the other per-unit execution
    contract this runtime already has: `turn_planner.plan_turn` carves the message into
    `PlannedTask`s, `run_plan` runs each one, and `TaskOutcome.ok` is true only when that
    sub-turn produced an answer with no error and no pending approval. That is a lane running
    for one requested slot -- not a model asserting it succeeded, not a length, and not a word
    match against the reply.

    Binding is the SAME geometry as the conductor's, through the one authority
    (`demands_served_by_spans`): the task's request is located in the user's own text and the
    span it occupies must sit inside a single demand, or cover exactly one. A task whose text is
    not found verbatim, or found more than once, binds to nothing -- wording never decides
    ownership, so there is no text-similarity fallback.

    Never raises: accounting may not take down a served answer.
    """
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.conductor import obligation_ledger
        from core.conductor.planner import demands_served_by_spans

        active = obligation_ledger.active_set()
        if active is None:
            return
        message = str(request or "")
        units = demand_units(message)
        if not units:
            return
        spans: list[tuple[int, int, str]] = []
        for index, outcome in enumerate(outcomes or ()):
            if not bool(getattr(outcome, "ok", False)):
                continue
            carved = str(getattr(getattr(outcome, "task", None), "request", "") or "").strip()
            if not carved:
                continue
            first = message.find(carved)
            if first < 0 or message.find(carved, first + 1) >= 0:
                # Absent, or ambiguous. Either way this lane cannot say WHICH slot it served.
                continue
            spans.append((first, first + len(carved), f"planned_task:{index}"))
        for unit_id, label in demands_served_by_spans(spans, units):
            obligation_ledger.record_slice_consumption(
                active[0],
                active[1],
                unit_id=unit_id,
                family="planned_turn",
                evidence="slice_answer_record",
                excerpt=label,
            )
    except Exception:  # pragma: no cover - accounting never breaks a served turn
        _log.exception("planned-turn demand receipts not recorded")


def _record_demand_consumption(
    active: tuple[str, str],
    *,
    request: str,
    answer: str,
    source_context: dict[str, object] | None,
) -> None:
    """Write this turn's consumption receipts: which requested slots a lane served.

    RSS (AUD-20260829-003) property 2. Receipts are FACTS, never dispositions — the
    ledger refuses a demand disposition from anyone but the finalization sweep, so
    nothing written here can close anything by itself. Two structural readings, both
    already present at this seam; neither invents an answer and neither consults a
    family registry to decide what the user wanted:

    R1  a lane that recorded per-slice coverage named its own clauses. Believe it,
        for the demand units inside those clauses.
    Evidence read off the SERVED BYTES is deliberately NOT written here any more. It used to
    be, on an any-one-token-overlap rule, and RED-1 broke it: `Kanona, United States of
    America` — a wrong-location answer to a DIFFERENT slot — discharged
    `the water temperature in the United States` through the shared tokens `united`/`states`,
    and the certificate went back to `covered: true` over a slot nobody answered. That is the
    original defect re-entering through the new mechanism. Byte evidence is now read once, in
    the finalization sweep, against the FINAL bytes, and it requires every anchor of a unit
    rather than any one token (`unit_answer_evidence`).

    The blanket `served_bytes` reading this replaces did not distinguish slots at
    all: any bytes at all satisfied the one obligation standing in for the whole
    request, which is why three dropped slots could certify as `covered: true`.
    """
    from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY, units_in_slices
    from core.conductor import obligation_ledger

    del answer  # byte evidence belongs to the sweep, which holds the FINAL bytes

    def _receipt_units(item: dict) -> list[str]:
        """The units a record names, at the finest grain the producer declared.

        A live-data record carries `consumed_units` — the demand units its own subtasks'
        needle spans bound (B2 interim, unit grain). Without it the record is clause
        space (`consumed` = slice ids, the shape every pre-live-data caller writes) and
        maps clause->units — the grain that absolves co-clause siblings nothing served
        (Incident 3, measured live 2026-08-30), kept ONLY for producers that have not
        learned to name units yet.
        """
        declared = [str(value) for value in list(item.get("consumed_units") or [])]
        if declared:
            return declared
        return list(units_in_slices(request, [str(value) for value in list(item.get("consumed") or [])]))

    seen: set[str] = set()
    record = (source_context or {}).get(COVERAGE_CONTEXT_KEY)
    if isinstance(record, dict):
        for item in list(record.get("answers") or []):
            if not isinstance(item, dict):
                continue
            for unit_id in _receipt_units(item):
                if unit_id in seen:
                    continue
                seen.add(unit_id)
                obligation_ledger.record_slice_consumption(
                    active[0],
                    active[1],
                    unit_id=unit_id,
                    family=str(item.get("family") or ""),
                    evidence="slice_answer_record",
                    excerpt=str(item.get("response") or ""),
                )
        # B4: the other half of the discharge channel. The dispatch record — what the lane
        # ATTEMPTED, succeeded or not — rides beside the receipts, so the sweep can tell
        # 'not dispatched' from 'dispatched, failed' from 'dispatched, unverifiable'. A
        # dispatch binds to the units the producing edge named (`unit_ids`, the subtask's
        # own needle spans); a dispatch whose span could not be bound to ANY unit is
        # written as an unbindable marker, because it makes 'not dispatched' unprovable
        # for this turn.
        for item in list(record.get("dispatches") or []):
            if not isinstance(item, dict):
                continue
            kwargs = dict(
                subtask_id=str(item.get("subtask_id") or ""),
                operation=str(item.get("operation") or ""),
                state=str(item.get("state") or ""),
                failure_reason=str(item.get("failure_reason") or ""),
            )
            unit_ids = [str(value) for value in list(item.get("unit_ids") or [])]
            if unit_ids:
                for unit_id in unit_ids:
                    obligation_ledger.record_slice_dispatch(
                        active[0], active[1], unit_id=unit_id, **kwargs
                    )
                continue
            slice_id = str(item.get("slice_id") or "")
            if not slice_id:
                obligation_ledger.record_slice_dispatch(active[0], active[1], unit_id="", **kwargs)
                continue
            for unit_id in units_in_slices(request, (slice_id,)):
                obligation_ledger.record_slice_dispatch(
                    active[0], active[1], unit_id=unit_id, **kwargs
                )


def _serves_a_stipulated_contract(result: object) -> bool:
    """Whether a closed-contract lane produced this answer as its literal deliverable.

    The `*_contract` lanes (`stable_file_format_standard_contract`,
    `stable_si_unit_reference_contract`, `currency_value_contract`, …) answer with a stipulated
    exact string that frozen tests pin byte for byte. Appending a disclosure row to one is the
    same class of violation as appending to a raw-output contract: measured when the entrance
    sweep turned `PNG — ISO/IEC 15948:2004.` into that plus a denial of the turn's own
    answer-shaping clause. The turn is still accounted; only the rendering is withheld.
    """
    if not isinstance(result, dict):
        return False
    reason = str(result.get("route_reason") or "")
    return reason.endswith("_contract") or bool(result.get("exact_response_control"))


#: Keys a real serving lane puts on its result. None of them appears on a bare stub.
_ENTRANCE_SERVED_MARKERS = frozenset(
    {
        "_semantic_admission",
        "_closure_verdict",
        "answer_provenance",
        "backend",
        "fast_path_hit",
        "route_reason",
        "usage_summary",
    }
)


def _arm_demand_set_for_entrance(user_input: str) -> dict[str, str] | None:
    """Mint and bind this turn's demand set when no door upstream already did.

    Returns the set, or None when a set is already bound (the `run_once` spine minted it) or
    when the turn asks for nothing. Never raises: an entrance that cannot open a ledger must
    still serve its turn.
    """
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.conductor import obligation_ledger

        if obligation_ledger.active_set() is not None:
            return None
        units = demand_units(str(user_input or ""))
        if not units:
            return None
        obset = obligation_ledger.open_obligation_set(
            request_text=str(user_input or ""),
            obligations=[
                {
                    "obligation_id": f"ob:entrance:{unit.unit_id}",
                    "text": unit.text,
                    "kind": "demand",
                    "unit_id": unit.unit_id,
                    "slice_id": unit.slice_id,
                }
                for unit in units
            ],
        )
        obligation_ledger.bind_active_set(obset["set_id"], obset["version"])
        return obset
    except Exception:
        return None


def _release_entrance_demand_set() -> None:
    try:
        from core.conductor import obligation_ledger

        obligation_ledger.clear_active_set()
    except Exception:
        pass


def _certify_entrance_turn(
    outcome: object, obset: dict[str, str], source_context: dict[str, object] | None = None
) -> None:
    """Give the entrance's own result a finalized commit, so it certifies what it served.

    The sweep runs inside `finalize_answer`, so this is also what applies the accounting and
    the disclosure to a turn that never reaches the transport door. A turn already carrying a
    commit is left alone — one turn, one finalization — and any failure here leaves the
    result exactly as the lane produced it.
    """
    result = (outcome or {}).get("result") if isinstance(outcome, dict) else None
    if not isinstance(result, dict):
        return
    if isinstance(result.get("vool_response_commit"), dict):
        return
    # Certify only a result a LANE actually served. A served answer names how it was produced —
    # a backend, a route reason, a fast-path hit; a bare `{"response": ...}` handed back by a
    # test double standing in for a lane does not, and this door must stay transparent to it,
    # because `_handle_turn_frontdoor` returns the lane's own object and several frozen tests
    # pin that object exactly.
    if not _ENTRANCE_SERVED_MARKERS & set(result):
        return
    content = str(result.get("response") or "")
    if not content.strip():
        return
    try:
        from core.finalization import finalize_answer

        # C11: the entrance-certified commit carried NO display_metadata at all, which is the
        # structural `provenance = {}` surface the signoff records -- the frontdoor certified
        # what it served without saying what served it. Per-slot receipts come from the demand
        # ledger this turn wrote; a turn that minted no demand still gets a byte-identical
        # commit, because the builder returns {} and nothing is attached.
        from core.response_provenance import slot_receipts

        per_slot = slot_receipts(source_context, result)
        commit = finalize_answer(
            turn_id=str(result.get("task_id") or ""),
            canonical_content=content,
            display_metadata=(
                {"provenance": dict(result.get("answer_provenance") or {}), "slot_receipts": per_slot}
                if per_slot
                else None
            ),
            closure={
                **_entrance_closure(obset),
                "set_id": obset["set_id"],
                **(
                    {"literal_answer_lane": True}
                    if _serves_a_stipulated_contract(result)
                    else {}
                ),
            },
        )
    except Exception:
        return
    result["vool_response_commit"] = commit
    served = str(commit.get("canonical_content") or "")
    if served and served != content:
        # The door serves what it certified, exactly as the transport door does.
        result["response"] = served


def _constraint_is_unit_scoped(text: str, constraint: Any) -> bool:
    """Whether a parsed response shape belongs to ONE demand unit of a multi-unit turn.

    The test is structural, not textual: re-parse the SAME constraint parser over each
    minted execution unit, and if any single unit alone reproduces the constraint, the
    shape lives inside that unit ("Tell me a two-word joke." -> two words) and the whole
    turn's composed reply must not be trimmed by it. A constraint no single unit carries
    ("answer everything in exactly ten words") stays whole-turn. Single-unit turns are
    untouched: with one unit there is no sibling to erase and the shape binds as ever.
    Fail-soft to False (constraint keeps binding) so a mint failure never silently
    drops a user's explicit whole-turn shape.
    """
    # Inclusion requirements survive composition: requiring a diagram somewhere in
    # the answer cannot truncate a sibling, unlike a clause's word/sentence limit.
    if getattr(constraint, "requested_formats", ()) and not any(
        getattr(constraint, field, None)
        for field in ("exact_words", "max_words", "exact_sentences", "max_sentences",
                      "list_items", "one_item_per_line", "per_item_sentences")
    ):
        return False
    try:
        from core.agent_runtime.demand_ownership import execution_units
        from core.response_constraints import parse_response_constraint

        value = str(text or "")
        units = execution_units(value)
        if len(units) < 2:
            return False
        for _unit_id, unit_text in units:
            if parse_response_constraint(unit_text) == constraint:
                return True
        # The parser can fire on the WHOLE text for a phrase that lives inside
        # one unit ("…And finish with a two-word joke." parses only in company).
        # Removal is the honest probe: take one unit's words out and re-parse —
        # if the shape vanishes, that unit was its only source and the shape is
        # that unit's, not the turn's. A whole-turn shape ("answer everything in
        # exactly ten words") survives the removal of any one unit.
        for _unit_id, unit_text in units:
            remainder = value.replace(unit_text, " ", 1).strip()
            if remainder and parse_response_constraint(remainder) != constraint:
                return True
        return False
    except Exception:
        return False


def _one_model_call_already_answers_every_part(text: str) -> bool:
    """Whether this multi-part turn belongs to the SINGLE plain answering lane.

    `turn_planner.plan_turn` already refuses to split this family at its own dispatch
    decision -- "splitting an ordinary explanation, calculation and title into sub-turns
    buys an extra model call and loses the one response-level completeness guard. Keep
    that family in the single plain answering lane." The demand path is a SECOND
    decomposer that reached dispatch without asking, so it split the family anyway: each
    sub-turn asked the model, the model answered the WHOLE turn every time, and merging
    the per-unit slices appended the complete answer to itself. Measured on
    "Explain ocean blue. Calculate 39 x 24. Give 7-word title." the merge emitted the
    full three-part answer, then "39*24. = 936." again, then a truncated repeat of part
    one.

    Two existing authorities decide it, and BOTH have to agree -- neither alone is
    enough:

    * `is_ordinary_multi_part_plain_task` is the predicate `plan_turn` consults. On its
      own it is too broad for this seam: it also reads True for
      "Explain how a hash table works and tell me the weather in Kaunas", a turn the
      demand path MUST own, because plan_turn declining that one is precisely how the
      demand path comes to see it.
    * `execution_requirements.requirements_for` -- the canonical current-information
      authority -- is asked of every execution unit. If ANY unit needs external evidence
      or current information, one model call cannot answer every part, whatever the
      prose looks like, and the demand path keeps the turn.

    So this is True only where the turn is ordinary prose AND nothing in it needs the
    world: exactly the family that has a response-level completeness guard of its own.
    Every genuinely mixed turn measured on this branch reads False -- the incident turn
    (file read + arithmetic + judgement), "No web. Read notes.txt and give the current
    ETH price.", the weather/explanation pairs, and the currency/explanation pairs.
    """
    from core.agent_runtime.demand_ownership import demand_coverage, execution_units
    from core.execution_requirements import requirements_for
    from core.plain_task_routing import is_ordinary_multi_part_plain_task

    if not is_ordinary_multi_part_plain_task(text):
        return False
    units = execution_units(text)
    # P0 MIXED-DEMAND TERMINAL CLOSURE — the carve-out. This helper's precedent
    # protects turns a model answers BETTER whole ("Explain ocean blue.
    # Calculate 39 x 24. Give 7-word title." — splitting tripled the answer).
    # But a turn EVERY unit of which a deterministic lane claims needs no model
    # at all, and handing it to the single plain lane measured as sibling
    # erasure: the model could not publish, and the degradation erased the
    # clock and arithmetic answers the runtime held outright. Fully-claimed
    # turns go to the demand-owned seam; every model-shaped family keeps this
    # lane exactly as before.
    try:
        coverage = demand_coverage(text)
        if coverage.unit_count >= 2 and coverage.mixed:
            claimed = sum(1 for lanes in coverage.per_unit_lanes if lanes)
            if claimed == coverage.unit_count:
                return False
    except Exception:
        pass
    for _unit_id, unit_text in units:
        requirements = requirements_for(unit_text)
        if requirements.external_evidence_required or requirements.current_information_required:
            return False
    return True


class _ConductorHandoffNotApplicableError(Exception):
    """Raised to skip the conductor's decline-to-another-lane check.

    That check's own `except` arm already means "accounting may never break the lane it
    accounts for", which is exactly the behaviour wanted when the hand-off does not
    apply: leave the pre-existing claim untouched. Raising a named exception says so on
    purpose rather than relying on a falsy list.
    """


def _entrance_closure(obset: dict[str, str]) -> dict[str, object]:
    from core.conductor import obligation_ledger

    return dict(obligation_ledger.closure_verdict(obset["set_id"], obset["version"]))


def _degraded_unit_synthesis(outcomes: list) -> str:
    """A merge that cannot raise: successful outcomes verbatim, failures named.

    The last-resort composition for a `merge_outcomes` fault after children ran —
    every exit keeps the successful units' text and names the failed ones, because
    the alternative (discarding the outcomes) is the compose-discard defect this
    amendment exists to close."""
    blocks = [
        str(outcome.answer).strip()
        for outcome in outcomes
        if getattr(outcome, "ok", False) and str(outcome.answer or "").strip()
    ]
    failed = [outcome for outcome in outcomes if not getattr(outcome, "ok", False)]
    if failed:
        # Same law as `turn_planner.merge_outcomes`: the last-resort composer routes failure
        # detail through the reader-facing scrubber too, or the leak it exists to close comes
        # straight back on the merge-fault path. See that function for the measured incident.
        from core.conductor.compose import reader_facing_reason

        lines = [
            f"- {getattr(outcome.task, 'request', '')}"
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


def _secret_intake_failure_result() -> dict:
    """The typed safe failure/defer response for an unprovable secret intake.

    Static by construction — no field of this result can echo any part of the
    message that could not be processed."""
    return {
        "ok": False,
        "response": (
            "I could not process this message safely just now — it may contain "
            "a credential I could not handle, so I stopped without running "
            "anything and nothing was stored. Please retry, or set the key "
            "from your own local session with `cloud key <your-key>`."
        ),
        "confidence": 0.5,
        "reason": "secret_intake_failed",
    }


def _reject_undeclared_finalization(result: dict) -> dict:
    """R1g amendment requirement 8 — the MECHANICAL finalization check.

    Every deterministic finalization crosses the seam that calls this, and a
    finalization whose lane family is not declared in the ACTIVE catalog is
    REFUSED here rather than shipped: undeclared finalization is a runtime
    violation (an omitted lane keeping the freedom to swallow demand), not a
    tuple a developer must remember to update. Non-deterministic envelopes
    (model-lane routes, intake gates) resolve to their declared families or to
    None (unjudged) — see `core.lane_registry.finalization_family`."""
    try:
        from core import lane_registry

        reason = str((result or {}).get("reason") or "")
        route = str((result or {}).get("route") or "")
        family = lane_registry.finalization_family(reason, route=route)
        if family is None or lane_registry.find_spec(family) is not None:
            return result
        return _finalization_refusal(result, f"undeclared_finalization:{family}")
    except Exception:
        # R1g2 amendment, defect 3 — the fence FAILS CLOSED. A family/catalog
        # resolution failure means the fence could not validate this
        # finalization; shipping the original result would be exactly the
        # fail-open hole. The typed internal-routing refusal ships instead.
        return _finalization_refusal(result, "finalization_check_failed")


def _finalization_refusal(result: dict, reason: str) -> dict:
    """The one typed internal-routing refusal — static text, no content of the
    unchecked result can leak through it."""
    return {
        "ok": False,
        "response": (
            "I could not complete this request through an unregistered route, "
            "so nothing ran. This is an internal routing fault, not a refusal "
            "of your request."
        ),
        "confidence": 0.5,
        "reason": reason,
        "route": str((result or {}).get("route") or ""),
    }


def conserve_request_for_resume(turn_request: TurnRequest, restored_text: str) -> TurnRequest:
    """P0 POLICY CONSERVATION — re-freeze a resumed turn's prohibitions.

    A resume/retry turn's canonical request was minted from the LITERAL message
    ("continue"), which carries no prohibition; the STORED request text is what
    re-runs, so its constraints must be re-frozen onto the request object the
    children inherit. Identity (request/turn/session) is preserved verbatim —
    only the derived constraint set is re-minted from the restored text.
    """
    from dataclasses import replace as _replace

    from core.turn_prohibitions import prohibitions_from_text

    return _replace(
        turn_request, prohibitions=prohibitions_from_text(str(restored_text or ""))
    )


class _GraphRowsDivergedError(RuntimeError):
    """The lexical graph's obligation rows differ from the demand mint (a named, recorded fallback)."""


def _unmet_unit_summary(tasks: list, outcomes: list) -> str:
    """The all-failed typed summary: every planned unit, named, with its error."""
    errors = {
        getattr(outcome.task, "index", None): str(outcome.error or "")
        for outcome in outcomes
    }
    lines = []
    for task in tasks:
        error = errors.get(getattr(task, "index", None), "")
        lines.append(
            f"- {getattr(task, 'request', '')}"
            + (f" ({error})" if error and error != "no answer returned" else "")
        )
    return "I could not answer any part of this message:\n" + "\n".join(lines)


def _seal_semantic_result(
    result: dict,
    *,
    session_id: str,
    user_input: str,
    effective_input: str = "",
    source_context: dict[str, object] | None = None,
    semantic_source: SemanticSource | None = None,
    route_id: str = "",
) -> dict:
    """Admit through the semantic seam, then apply the honesty seal.

    Every served semantic result in the turn spine must pass through this
    function before being returned. It is the mechanical enforcement of the
    "one turn, one admitted semantic result" law.

    The semantic seam is called FIRST (observation/admission), then the
    honesty seal (enforce_final_action_honesty + emit_turn_honesty_receipt).
    """
    # K-08 ORDERING-FROZEN: ALL semantic transforms happen INSIDE the sealing
    # context, BEFORE admit — response-control is a semantic transform, so it
    # is applied here, ahead of the honesty seal and A2 admission. Post-boundary
    # application sites are verify-only asserts.
    from core.web.api.response_control import apply_exact_response_control

    # M3: bring the turn's grounding lifecycle up to date BEFORE the transforms, because this
    # is the last seam that holds the turn's context and its request text together -- and the
    # coverage a gate at finalization has is exactly the coverage this call gives it. Measured
    # on the isolated daemon: the workflow-planner tool loop answers current-information turns
    # without ever consulting M1's authority with the turn's context, so a gate that waited for
    # M2's prefetch to register the turn stood down on a lane that had retrieved four real
    # sources and shipped a model's answer over them.
    try:
        from core.grounding_lifecycle import seal_turn_grounding

        seal_turn_grounding(source_context, effective_input or user_input, result)
    except Exception:
        pass

    result = apply_exact_response_control(dict(result or {}), user_input)
    # A-12/R-5: URL grounding is a semantic transform — it belongs INSIDE the
    # sealing context, where it may still legally change bytes pre-admit.
    # M1: imported from its core home (`action_honesty_validator`), never through
    # the web API module -- that round-trip was the core<->apps cycle's return leg.
    try:
        from core.agent_runtime.action_honesty_validator import enforce_url_grounding as _ground_urls
        from core.context_retrieval import remote_fetch_attempt_count

        result = _ground_urls(
            result, user_input=user_input, fetch_attempts=remote_fetch_attempt_count()
        )
    except ImportError:
        pass
    # M3 slice 4 -- the FALLBACK claim. This seam admits every served result; the
    # deterministic lanes record their own typed claims on the way here. When a
    # turn carries canonical demand units and NO lane has claimed any of them,
    # the route that actually serves the turn claims the WHOLE set -- the model/
    # fallback path is the whole-turn answer of last resort, and its claim is
    # typed rather than implicit. Non-answer routes (empty turns, resumes,
    # integrity failures) are excluded: they do not claim work.
    _m3d_excluded = {
        "empty_turn_fast_path",
        "empty_turn_fast_path_resume",
        "runtime_resume_missing",
        "conductor_integrity_failure",
        "chat_namespace_inactive",
    }
    if isinstance(source_context, dict):
        try:
            from core.agent_runtime.answer_coverage import demand_units as _m3d_units
            from core.turn_contract import TURN_PROPOSALS_KEY, LaneProposal

            _m3d_reason = str(
                route_id or result.get("route_reason") or result.get("reason") or ""
            )
            _m3d_items = list(source_context.get(TURN_PROPOSALS_KEY) or [])
            _m3d_any_claim = any(
                isinstance(item, LaneProposal) and item.obligations_claimed
                for item in _m3d_items
            )
            _m3d_units_minted = _m3d_units(str(effective_input or user_input or ""))
            if (
                _m3d_units_minted
                and not _m3d_any_claim
                and _m3d_reason
                and _m3d_reason not in _m3d_excluded
            ):
                _m3d_items.append(
                    LaneProposal(
                        lane_id=_m3d_reason,
                        obligations_claimed=tuple(
                            u.unit_id for u in _m3d_units_minted
                        ),
                        required_capabilities=("model",),
                        confidence=float(result.get("confidence") or 0.0),
                    )
                )
                source_context[TURN_PROPOSALS_KEY] = _m3d_items
        except Exception:
            pass
    guarded = enforce_final_action_honesty(
        dict(result or {}),
        user_input=user_input,
        effective_input=effective_input or user_input,
        session_id=session_id,
        source_context=source_context,
    )
    # Extract the canonical turn identity for the semantic result id.
    # The intake spine sets _canonical_user_turn_id on source_context.
    _turn_id = ""
    if isinstance(source_context, dict):
        _turn_id = str(
            source_context.get("_canonical_user_turn_id")
            or source_context.get("cancel_turn_id")
            or ""
        ).strip()
    # Pass through the semantic seam (observation/admission).
    # The seam mints the stable semantic_result_id = sr:<turn_id>:<seq>.
    admitted = admit_semantic_result(
        guarded,
        source_override=semantic_source,
        route_id_override=route_id
        or str(guarded.get("route_reason") or guarded.get("reason") or ""),
        turn_id=_turn_id,
    )
    # R-6 (K-05): discharge the turn's prose obligation from SERVED BYTES and
    # compute the closure verdict NOW — it must ride the payload so the
    # transport-shim finalize can consume it after ContextVar scope exit.
    try:
        from core.conductor import obligation_ledger

        _active = obligation_ledger.active_set()
        if _active is not None:
            identity = (source_context or {}).get("_execution_identity") or {}
            obset = identity.get("obligation_set") or {}
            if (
                obset
                and (_active[0], _active[1]) == (obset["set_id"], obset["version"])
            ):
                obligation_ledger.record_disposition(
                    _active[0],
                    _active[1],
                    f"ob:{identity.get('attempt_id')}:answer",
                    "satisfied",
                    evidence_source="served_bytes",
                )
            _record_demand_consumption(
                _active,
                request=str(user_input or ""),
                answer=str(admitted.get("response") or guarded.get("response") or ""),
                source_context=source_context,
            )
        admitted["_closure_verdict"] = obligation_ledger.closure_verdict(*_active) if _active else {
            "covered": True,
            "open_count": 0,
            "set_version": "",
        }
        if _active and _serves_a_stipulated_contract(guarded):
            admitted["_closure_verdict"]["literal_answer_lane"] = True
        if _active:
            # The sweep runs inside `core.finalization`, after this scope has exited
            # and the ContextVar binding is gone. `set_version` alone cannot address a
            # set; the id has to ride the payload with it or the sweep is blind on
            # every door but the ones that still hold the binding.
            admitted["_closure_verdict"]["set_id"] = _active[0]
    except Exception:
        raise
    # C11: the per-demand ledger rides the payload for the same reason the closure verdict
    # above does. The transport door builds `source_context` and hands `run_once` a COPY
    # (`runtime.py::_run_agent_locked` starts from `default_agent_source_context()` and updates
    # it), so the ledger this turn writes lands on the runtime's dict and the door still holds
    # its own pre-run one. Measured: `display_metadata` at the served door carried
    # `['provenance', 'provenance_footer', 'usage']` and no `slot_receipts`, while the identical
    # in-process path -- where caller and runtime share one dict -- carried receipts for every
    # slot. The obligation-set fallback cannot rescue it either: `run_once` clears the active
    # binding in its own `finally` before returning.
    #
    # Stashing on the payload crosses that boundary the way the verdict already does, rather
    # than writing back into a caller dict that may itself have been rebound upstream.
    try:
        from core.turn_contract import TURN_DEMAND_LEDGER_KEY

        _ledger = (source_context or {}).get(TURN_DEMAND_LEDGER_KEY)
        if isinstance(_ledger, (list, tuple)) and _ledger:
            admitted["_turn_demand_ledger"] = [dict(row) for row in _ledger if isinstance(row, dict)]
    except Exception:
        pass
    # Residue-3 (H-2): the fence tuple rides the payload so DELIVERY writers
    # (which run after run_once's scope exits) can present it.
    try:
        from core.semantic.semantic_admissions import current_execution_identity

        _ident = current_execution_identity()
        if _ident:
            admitted["_execution_identity"] = dict(_ident)
    except Exception:
        pass
    # ROOT-CAUSE CONTRACT — same payload-stash law as `_closure_verdict`: the
    # buffered transport shim finalizes AFTER ContextVar scope exit, so the
    # turn's repair diagnosis rides the payload as its dict projection for the
    # shim to hand `finalize_answer`. `current_contract` resolves the registry
    # latest, so a context copy made mid-turn still stashes current truth.
    try:
        from core.root_cause_contract import current_contract as _rcc_current

        _rcc = _rcc_current(source_context)
        if _rcc is not None:
            admitted["_root_cause_contract"] = _rcc.to_dict()
    except Exception:
        pass
    # C19 PRESENTATION SELECTION — same payload-stash law: the buffered
    # transport shim finalizes after this scope exits, so the router's
    # pre-gate selection record rides the payload for the display_metadata
    # assembly (finalize re-stamps it against the committed bytes).
    _selection_record = dict(source_context or {}).get("presentation_selection")
    if not isinstance(_selection_record, dict):
        from core.turn_model_call_ledger import turn_presentation_selection

        _selection_record = turn_presentation_selection(source_context)
    if not _selection_record:
        # Fast paths do not traverse the model router's selector. Capture their
        # typed stand-down before this context is lost at buffered delivery.
        from core.presentation_selection import select_presentation

        _selection_record = select_presentation(str(admitted.get("response") or ""), source_context)
    if isinstance(_selection_record, dict) and _selection_record.get("schema"):
        admitted["_presentation_selection"] = dict(_selection_record)
    # Emit a signed, offline-verifiable honesty receipt for this finalized turn.
    return emit_turn_honesty_receipt(
        admitted,
        user_input=user_input,
        session_id=session_id,
        source_context=source_context,
    )


#: ARCH-TRUTH-R1d: follow-up intents that RECALL what the turn was asked, and therefore
#: read every answer-bearing child of its chain rather than one resolved row. The acting
#: intents (retry, continue) deliberately stay bound to the single attempt they act on.
_CHAIN_RECALL_INTENTS = frozenset({"LIST_ORIGINAL_ENTITIES", "EXPLAIN_ATTEMPT_FAILURE"})


def _r3_open_turn_execution(
    observed_context: dict,
    *,
    turn_request: TurnRequest,
    agent: Any = None,
) -> None:
    """R-3/A-7: give EVERY canonical turn execution identity.

    Mints the lightweight attempt (the sole-minter unit), claims it RUNNING
    via the aborting CAS, opens/aliases the L0 fence row idempotently under
    the A0 request bound at the ingress door, and publishes the fence identity
    into the turn context for outcome writers (R-4). A claim/mint refusal
    PROPAGATES — the turn aborts; no fail-open limbo.

    ARCH-TRUTH-R1b/R1c — this seam CONSUMES the turn's canonical request; it does not
    derive identity. The request identity, the turn identity, the session and the
    request text below are all read off the one immutable TurnRequest the ingress
    minted before this call: no `current_request_id()` re-read, no turn-id mint of its
    own, no separate session reading. That is what makes the attempt row, the L0 fence
    and the obligation set the SAME turn as the contract rather than a parallel one —
    the defect it replaced: on every door-served turn the trigger turn id was a fresh
    `turn-<uuid>` unrelated to the request's, minted right here.

    The session was the last of the four to unify, and only R1c could do it. R1b
    measured why: filing this row under the request's session makes it visible to
    `latest_runtime_attempt`, and because the turn door closes its attempt LAST it
    became the session's newest-updated row — so the next turn's referential follow-up
    bound to this bookkeeping row instead of the attempt that answered and reported
    "No entities were recorded for that request". The row this mints is now the ROOT of
    the turn's one chain and says so (`AttemptRole.TURN_ROOT`), and follow-up resolution
    ranks by role before recency, so the identity can be the true one on every surface
    instead of being hidden behind an empty session on the in-process lane.
    """
    from core.invocation.ledger import (
        current_runtime_epoch,
        open_execution,
    )
    from core.runtime_continuity import (
        AttemptClaimRefused,
        AttemptRole,
        claim_runtime_attempt,
        create_runtime_attempt,
    )

    if not isinstance(turn_request, TurnRequest):
        raise TypeError(
            "_r3_open_turn_execution requires the canonical TurnRequest "
            "(server-derived, minted before execution identity); got "
            f"{type(turn_request).__name__}"
        )
    request_id = turn_request.request_id
    if not request_id:
        # No A0 context bound: this lane was not onboarded through a door —
        # refuse rather than run unfenced (fail closed per H1 §2).
        raise RuntimeError("execution identity refused: no A0 request bound at ingress")

    trigger = turn_request.turn_id
    if not trigger:
        # The ingress binds the turn id before this seam runs. An unbound one
        # cannot be minted HERE (that is the second authority this repair
        # removed) — refuse, same fail-closed stance as the missing A0 request.
        raise RuntimeError("execution identity refused: no canonical turn id bound at ingress")
    session_id = turn_request.session_id
    user_input = turn_request.user_text
    created = create_runtime_attempt(
        session_id=str(session_id or ""),
        original_request=str(user_input or ""),
        origin_user_turn_id=trigger,
        trigger_user_turn_id=trigger,
        # This row IS the turn's chain: every other attempt the turn writes carries this
        # attempt id as its root, and the L0 fence aliases it.
        role=AttemptRole.TURN_ROOT.value,
    )
    attempt_id = str(created.get("attempt_id") or "")
    if not created.get("idempotent_replay"):
        try:
            claim_runtime_attempt(attempt_id, to_state="RUNNING")
        except AttemptClaimRefused:
            raise
    ex = open_execution(
        request_id=request_id,
        root_attempt_id=str(created.get("root_attempt_id") or attempt_id),
    )
    identity = {
        "attempt_id": attempt_id,
        "execution_id": str(ex.get("execution_id") or ""),
        # The FENCE generation (the executions row's counter, starts at 0 and
        # only advances via bump_generation) — every presenter of this tuple
        # (finality bind, delivery truth, effect claims) is checked against
        # that row. `execution_generation` is the ATTEMPT chain's counter, a
        # different namespace: presenting it made every served fence
        # presentation refuse against its own row.
        "generation": int(ex.get("generation") or 0),
        "fence_generation": int(ex.get("generation") or 0),
        "runtime_epoch": current_runtime_epoch(),
        "request_id": request_id,
    }
    observed_context["_execution_identity"] = identity
    from core.semantic.semantic_admissions import set_execution_context

    set_execution_context(identity)
    # R-6 (K-05/A-4b): the turn door OPENS + BINDS the obligation set for the
    # turn — one prose obligation (the served answer) plus any effect
    # obligations registered later by executing lanes. Finalization consumes
    # the verdict; an open set bricks finalization BY DESIGN.
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.conductor import obligation_ledger

        # RSS (AUD-20260829-003): the turn's REQUESTED-SLOT SET is minted here, from
        # the request itself, before routing — one demand obligation per thing the
        # user asked for. Until this existed the only demand artifact was the single
        # prose obligation below, whose text is a 240-character PREFIX of the message
        # and which is discharged by the mere existence of served bytes: a set of one
        # standing in for a request of four, so `closure_verdict` could truthfully
        # report `covered: true, open_count: 0` over three dropped slots.
        #
        # The prose obligation is KEPT and unchanged. It no longer represents the
        # request — it marks that bytes were delivered, which is true and is what the
        # delivery/fence predicates that consume it have always meant by it. The
        # request is represented by the demand obligations beside it.
        _demand = demand_units(str(user_input or ""))
        _legacy_rows = [
            {
                "obligation_id": f"ob:{attempt_id}:answer",
                "text": str(user_input or "")[:240],
                "kind": "prose",
            },
            *(
                {
                    "obligation_id": f"ob:{attempt_id}:demand:{unit.unit_id}",
                    "text": unit.text,
                    "kind": "demand",
                    "unit_id": unit.unit_id,
                    "slice_id": unit.slice_id,
                }
                for unit in _demand
            ),
        ]
        # KEYSTONE (RequestGraph): the requested-slot set is ONE graph, built here from the same
        # reading that mints the demand units (RequestId == SlotId == unit id), persisted in the
        # set's snapshot so the publication sweep reads the frozen slot set instead of re-lexing
        # the request, and projected text-free onto the turn context for the receipt. The rows it
        # yields must be byte-identical to the demand mint -- checked HERE, every turn: any
        # difference or producer fault falls back to the legacy rows and is recorded on the
        # receipt as a failed projection, never raised into the turn.
        _graph_payload = None
        _graph_object = None
        _rows = _legacy_rows
        try:
            from core.agent_runtime.answer_coverage import interpret_request as _interpret_request
            from core.semantic.bridges.ledger import demand_obligation_rows, snapshot_graph_payload
            from core.semantic.producers.lexical import (
                PRODUCER_NAME,
                PRODUCER_VERSION,
                lexical_request_graph,
            )
            from core.semantic.turn_graph import graph_projection

            _graph = lexical_request_graph(
                turn_request,
                turn_id=trigger,
                interpretation=_interpret_request(str(user_input or "")),
                source_context=observed_context,
            )
            _graph_rows = demand_obligation_rows(
                _graph,
                attempt_id=attempt_id,
                request_text=str(user_input or ""),
                units={unit.unit_id: unit for unit in _demand},
            )
            if _graph_rows != _legacy_rows:
                # Alias parity is the SHADOW-stage invariant: the lexical graph IS the demand
                # reading. A producer that conserves more than the mint is a change in the
                # obligation universe, which belongs to a later rung -- here it is a named
                # divergence, recorded, and the mint stays authoritative.
                raise _GraphRowsDivergedError("rows_diverged")
            _rows = _graph_rows
            _graph_object = _graph
            _graph_payload = snapshot_graph_payload(_graph)
            _graph_projection = graph_projection(
                _graph, producer=PRODUCER_NAME, producer_version=PRODUCER_VERSION
            )
        except Exception as _graph_exc:
            from core.semantic.turn_graph import failed_projection

            _graph_projection = failed_projection(
                str(_graph_exc) if isinstance(_graph_exc, _GraphRowsDivergedError) else type(_graph_exc).__name__,
                turn_id=trigger,
            )
        _turn_obset = obligation_ledger.open_obligation_set(
            request_text=str(user_input or ""),
            request_id=str(request_id or ""),
            obligations=_rows,
            request_graph=_graph_payload,
        )
        obligation_ledger.bind_active_set(_turn_obset["set_id"], _turn_obset["version"])
        identity["obligation_set"] = _turn_obset
        from core.semantic.turn_graph import publish_request_graph

        publish_request_graph(observed_context, _graph_projection)
        # KEYSTONE (SHADOW rung): observe the model resolver BESIDE the deterministic reading.
        # OFF by default. When the operator asked for SHADOW and a graph resolver is registered,
        # ONE bounded, detached shadow run is queued: the turn never waits on it, nothing it
        # produces is dispatched, and only a text-free ticket rides the context for the receipt.
        try:
            from core.agent_runtime.semantic_shadow import (
                ensure_shadow_resolver_registered,
                submit_turn_shadow,
            )

            if _graph_object is not None and ensure_shadow_resolver_registered():
                _shadow = submit_turn_shadow(
                    agent,
                    source_context=observed_context,
                    turn_id=trigger,
                    session_id=str(session_id or ""),
                    text=str(user_input or ""),
                    heuristic_graph=_graph_object,
                )
                if _shadow:
                    observed_context["_semantic_shadow"] = _shadow
        except Exception:
            pass
    except Exception:
        observed_context["_execution_identity"].pop("obligation_set", None)
        raise


class VoolAgent(
    RuntimeCheckpointSupportMixin,
    VoolBookRuntimeMixin,
    ToolResultSurfaceMixin,
    HiveReviewRuntimeMixin,
    FastPathFacadeMixin,
    HiveTopicFacadeMixin,
    ChatSurfaceFacadeMixin,
    ProceedIntentSupportMixin,
    PublicHiveSupportMixin,
    TaskPersistenceSupportMixin,
    BuilderFacadeMixin,
    ResearchToolLoopFacadeMixin,
):
    ResponseClass = ResponseClass
    GateDecision = GateDecision
    ChatTurnResult = ChatTurnResult

    def __init__(self, backend_name: str, device: str, persona_id: str = "default"):
        self.backend_name = backend_name
        self.device = device
        self.persona_id = persona_id
        self.swarm_enabled = True
        self.context_loader = TieredContextLoader()
        self.memory_router = MemoryFirstRouter()
        self.curiosity = CuriosityRoamer()
        self.media_pipeline = MediaAnalysisPipeline()
        self.public_hive_bridge = PublicHiveBridge()
        self.hive_activity_tracker = HiveActivityTracker()
        self._public_presence_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._public_presence_running = False
        self._public_presence_registered = False
        self._public_presence_status = "idle"
        self._public_presence_source_context: dict[str, object] | None = None
        self._public_presence_thread: threading.Thread | None = None
        self._public_presence_sync_thread: threading.Thread | None = None
        self._public_presence_sync_inflight = False
        self._public_presence_sync_pending = False
        self._idle_commons_running = False
        self._idle_commons_thread: threading.Thread | None = None
        self._last_user_activity_ts = time.time()
        self._last_idle_commons_ts = 0.0
        self._last_curiosity_execute_ts = 0.0
        self._turns_in_flight = 0
        self._last_idle_hive_research_ts = 0.0
        self._idle_commons_seed_index = 0
        self._hive_create_pending: dict[str, dict[str, Any]] = {}
        self._voolbook_pending: dict[str, dict[str, str]] = {}

    def start(self) -> AgentRuntime:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        # A fresh/isolated runtime home has an empty DB; create the schema before any
        # startup query so start() does not crash with "no such table" (e.g.
        # runtime_checkpoints). Idempotent (migrations use IF NOT EXISTS).
        run_migrations()
        mark_stale_runtime_checkpoints_interrupted()
        mark_stale_runtime_attempts_abandoned()
        recover_stale_queue_items()
        # R-7 (A-6/K-10): startup DELIVERY_RETRY sweep — committed bytes whose
        # handoff ended ATTEMPTED_UNKNOWN are queued for byte-for-byte resend.
        # Pure derivation over durable facts; zero model/tool work; no truth
        # upgrade here (reconciliation authors DELIVERED on evidence only).
        try:
            from core.finalization import sweep_attempted_unknown_deliveries

            _retryable = sweep_attempted_unknown_deliveries()
            if _retryable:
                logging.getLogger(__name__).warning(
                    "delivery_retry_sweep: %d finalization(s) in ATTEMPTED_UNKNOWN "
                    "queued for byte-for-byte resend",
                    len(_retryable),
                )
        except Exception:
            logging.getLogger(__name__).exception("delivery retry sweep failed")
        ensure_memory_files()
        _ = load_active_persona(self.persona_id)
        self._sync_public_presence(status=self._idle_public_presence_status())
        if self._background_runtime_threads_enabled():
            self._start_public_presence_heartbeat()
            self._start_idle_commons_loop()

        return AgentRuntime(
            backend_name=self.backend_name,
            device=self.device,
            persona_id=self.persona_id,
            swarm_enabled=self.swarm_enabled,
        )

    def _background_runtime_threads_enabled(self) -> bool:
        if str(self.backend_name or "").strip().lower().startswith("test-"):
            return False
        if str(self.device or "").strip().lower().endswith("-test"):
            return False
        return not os.environ.get("PYTEST_CURRENT_TEST")

    def run_once(
        self,
        user_input: str,
        *,
        session_id_override: str | None = None,
        source_context: dict[str, object] | None = None,
    ) -> dict:
        # Put the request's per-turn autonomy override (Auto mode / the inline allow-controls) in
        # force for the whole turn, then always clear it -- so the approval gate honours it without
        # any caller threading it, and it never leaks into the next turn. See core.execution_gate.
        from core import execution_gate

        token = execution_gate.set_request_autonomy_override(
            str((source_context or {}).get("autonomy_override") or "") or None
        )
        # R-2 (H-3) A0 interior fallback: in-process lanes (CLI REPL, one-shot,
        # discord's in-process agent) bypass the HTTP door, so run_once accepts
        # the invocation ONLY when no request context is already bound.
        import hashlib as _r2_hashlib

        from core.semantic.semantic_admissions import (
            _CURRENT_REQUEST_ID as _req_var,
        )
        from core.semantic.semantic_admissions import (
            set_request_context as _bind_req,
        )

        # Bound ONLY when this frame accepted its own invocation (the interior
        # fallback below). When the A0 door already bound the request context
        # (every served lane), this stays None: the finally must not touch the
        # door's token — the door resets it in its own finally (service.py).
        _req_token = None
        _provider_terminal = None
        if not str(_req_var.get() or "").strip():
            # ARCH-TRUTH-R1c: minted in the DIALOGUE-TURN id space (a uuid4), because this
            # is the turn's one canonical identity and `record_dialogue_turn` now persists
            # it verbatim. A `turn-<hex>` shape here would have been a second id space
            # wearing the canonical name.
            _presented_turn_id = str(
                (source_context or {}).get("_canonical_user_turn_id") or ""
            ).strip()
            _turn_id = _presented_turn_id or str(uuid.uuid4())
            _r2_turn_value = _turn_id
            _raw_digest = (
                "sha256:"
                + _r2_hashlib.sha256(str(user_input or "").encode("utf-8")).hexdigest()
            )
            try:
                _accepted = __import__("core.invocation.ledger", fromlist=["accept_invocation"]).accept_invocation(
                    external_kind="turn",
                    external_value=_turn_id,
                    principal="owner_local",
                    session_binding=str(session_id_override or ""),
                    privacy_local_only=True,
                    raw_digest=_raw_digest,
                )
                _req_token = _bind_req(_accepted["request_id"])
            except __import__(
                "core.invocation.ledger", fromlist=["InvocationConflict"]
            ).InvocationConflict:
                # The runtime itself stamps `_canonical_user_turn_id` into the
                # caller-owned context further down this turn (one-identity carrier,
                # see the R1c seam at the `_canonical_user_turn_id` write). An
                # in-process caller -- CLI, test, in-process agent -- that reuses one
                # context dict across DIFFERENT inputs then presents the previous
                # turn's stamp as this input's external identity. The accept-once
                # door is right to refuse the byte-diff redelivery; what it must not
                # do is kill the turn: this input IS a new user turn. Mint a fresh
                # identity and record the anomaly. A caller-authored id under the
                # SAME bytes never lands here (ACCEPTED_IDENTICAL, one request row).
                if not _presented_turn_id:
                    _req_token = None
                    raise
                _log.warning(
                    "stale canonical turn id %s redelivered with different bytes by an "
                    "in-process lane; minting a fresh turn identity for this input",
                    _presented_turn_id[:64],
                )
                _turn_id = str(uuid.uuid4())
                _r2_turn_value = _turn_id
                try:
                    _accepted = __import__("core.invocation.ledger", fromlist=["accept_invocation"]).accept_invocation(
                        external_kind="turn",
                        external_value=_turn_id,
                        principal="owner_local",
                        session_binding=str(session_id_override or ""),
                        privacy_local_only=True,
                        raw_digest=_raw_digest,
                    )
                    _req_token = _bind_req(_accepted["request_id"])
                except Exception:
                    _req_token = None
            except Exception:
                _req_token = None
        else:
            _r2_turn_value = ""
            _turn_id = ""
        observed_context: dict = {}
        try:
            # Observation only: records which gate got to decide this turn, and writes one
            # `resolution_receipt_v1` onto the runtime ledger when the turn ends. It cannot change
            # what the turn returns -- every call inside is fail-soft and returns nothing the turn
            # branches on -- and it is a no-op under VOOL_SEMANTIC_REACH=0. See
            # `core.semantic.turn_observation`.
            from core.semantic.turn_observation import observe_turn

            # A caller may pass no context at all (the CLI does). `_run_once_inner` then builds its
            # own and the observer never sees it, so the receipt had no session id and was never
            # stored -- found by a hostile review. Handing the inner call a dict we also hold means
            # the runtime fills OUR dict, and the receipt can read the session id it created.
            observed_context = source_context if isinstance(source_context, dict) else {}
            from core.context_retrieval import reset_retrieval_telemetry
            from core.remote_fetch_policy import remote_fetch_turn_scope

            # The runtime front door owns per-turn retrieval/web accounting for every caller.
            # The HTTP adapter already opens a compatible outer scope; direct CLI/library turns
            # must not inherit its process/context state or report a prior turn's calls.
            # ARCH-TRUTH-R1/R1b (M2 truth repair) — the canonical typed request is
            # constructed HERE, at the very top of the turn: before EXECUTION
            # IDENTITY, before any checkpoint work, routing, lane selection, model
            # call, tool execution, or visible answer construction. It is the
            # turn's ONE identity authority, and every seam below consumes it.
            #
            # Where each field comes from (all server-derived, all read once):
            #  * request_id — the A0/ledger request context, bound by the HTTP door
            #    or by the interior fallback above. Empty means no door onboarded
            #    this lane, and the execution seam below refuses the turn for it.
            #  * turn_id  — the interior turn value when this frame accepted its own
            #    invocation (itself the caller's canonical turn key or a mint), else
            #    a mint HERE. R1b moved that mint out of `_r3_open_turn_execution`:
            #    a door-served turn used to leave this empty and let the execution
            #    seam mint an unrelated `turn-<uuid>` of its own. R1c made this THE
            #    turn identity: `record_dialogue_turn` persists this exact value, so
            #    the dialogue row, the request and every attempt of the turn agree.
            #  * session_id — the override, else the door-stamped context session,
            #    else the interior mint (a pure device+persona function, so no
            #    second authority is introduced).
            from core.semantic.semantic_admissions import (
                current_request_id as _ingress_request_id,
            )

            _canonical_turn_id = str(_r2_turn_value or "").strip() or str(uuid.uuid4())
            _turn_request = TurnRequest.from_ingress(
                user_text=str(user_input or ""),
                source_context=observed_context,
                request_id=_ingress_request_id(),
                turn_id=_canonical_turn_id,
                session_id=str(
                    session_id_override
                    or observed_context.get("session_id")
                    or runtime_session_id(
                        device=self.device, persona_id=self.persona_id
                    )
                ),
            )
            # Publication overwrites any inbound value under the reserved key: a
            # forged `turn_request` riding a caller's context cannot survive
            # ingress (the HTTP door strips it; in-process callers get it here).
            observed_context[TURN_REQUEST_KEY] = _turn_request
            # R-3/A-7: EVERY canonical turn also gets execution identity — a
            # lightweight attempt row (RECEIVED → claim RUNNING → terminal)
            # whose execution_id aliases its own root. A refused claim ABORTS
            # the turn (fail closed); the identity is published into the turn
            # context for downstream fence predicates (R-4). R1b: it is opened
            # FROM the canonical request above — same request, same turn, same
            # session, same request text — and derives none of them itself.
            _r3_open_turn_execution(observed_context, turn_request=_turn_request, agent=self)
            reset_retrieval_telemetry()
            # Every checkpoint consumer in one effective turn shares this bounded restoration
            # view. A resume restores durable evidence once; task resolution, loop-state adoption,
            # checkpoint updates and finalization reuse it without another JSON decode allowance.
            #
            # v0.5.0 milestone integration: the checkpoint scope and the semantic observer arrived
            # from two independently frozen lanes and wrap the same inner call for unrelated
            # reasons, so they are nested rather than chosen between. The restoration scope stays
            # OUTERMOST and still contains the sweep, exactly as it did before -- it bounds the
            # whole effective turn, sweep included. The observer sits directly around the inner
            # call, which is the span it reports on, and stays fail-soft.
            with self._turn_in_flight(), remote_fetch_turn_scope(observed_context):
                with runtime_checkpoint_restoration_scope():
                    # A checkpoint that wedges AFTER startup used to stay `running` for the life of the
                    # process, because the only sweep ran at process start. Throttled to once a minute.
                    sweep_stale_checkpoints_if_due()
                    with observe_turn(observed_context) as observation:
                        result = self._run_once_inner(
                            user_input,
                            session_id_override=session_id_override,
                            source_context=observed_context,
                            turn_request=_turn_request,
                        )
                        # ARCH-TRUTH-R1: the request was constructed BEFORE the
                        # inner call (see the ingress block above) and this frame
                        # still holds the ONE object — there is no post-turn
                        # construction. This line is publication of that same
                        # immutable object, so a caller's context always ENDS the
                        # turn holding the canonical request even when the inner
                        # runtime replaced the context wholesale (the checkpoint
                        # merge) or a test seam dropped it.
                        observed_context[TURN_REQUEST_KEY] = _turn_request
                        # M2 SLICE 2 -- the typed turn state. The ledger binding is read
                        # HERE (the turn spine, where the R-6 binding's lifetime is
                        # visible: bound at the inner mint, unbound in this frame's
                        # finally) and handed to the state as IDS ONLY -- the ledger
                        # remains the sole writer of dispositions. The state's
                        # execution identity is the SAME dict R-3 published (a
                        # reference, not a copy), which is what makes the displaced
                        # read below one truth rather than a second one.
                        _m2_bound = None
                        _m2_ob_ids: tuple[str, ...] = ()
                        try:
                            from core.conductor import obligation_ledger as _m2_ol

                            _m2_active = _m2_ol.active_set()
                            if _m2_active is not None:
                                _m2_bound = tuple(_m2_active)
                                _m2_ob_ids = tuple(
                                    str(item.get("obligation_id") or "")
                                    for item in _m2_ol.demand_obligations(*_m2_active)
                                )
                        except Exception:
                            _m2_bound = None
                        from core.turn_contract import TURN_STATE_KEY, TurnState

                        _m4_state = TurnState.from_intake(
                            request=_turn_request,
                            source_context=observed_context,
                            obligation_set=_m2_bound,
                            canonical_obligations=_m2_ob_ids,
                        )
                        # M4 SLICE 1 -- the kernel's claim decision, derived at
                        # the spine from what the lanes RECORDED (the audit
                        # side of lanes-propose/kernel-decides). Fail-soft: a
                        # decision failure may never take the turn down.
                        try:
                            from core.agent_runtime.answer_coverage import (
                                demand_units as _m4_units,
                            )
                            from core.lane_registry import decide_claims

                            _m4_state.claim_ledger = decide_claims(
                                _m4_units(str(user_input or "")),
                                _m4_state.proposals,
                            )
                        except Exception:
                            _m4_state.claim_ledger = None
                        observed_context[TURN_STATE_KEY] = _m4_state
                        observation.bind_context(observed_context)
                        # The turn's own report of itself -- the only source for "a model was really
                        # invoked", which a marker part-way down the pipeline cannot supply.
                        observation.record_outcome(result)
                        # A pending-approval pause is not a failure: the turn is waiting on the
                        # OPERATOR, not a provider. Recorded on the typed turn state (this is the
                        # `pending_approvals` list's first writer) so the terminal boundary below
                        # can finalize the attempt as WAITING_APPROVAL instead of FAILED_PROVIDER
                        # -- the mislabel that made Activity narrate a provider fault that never
                        # happened on every manual-mode approval pause.
                        if str((result or {}).get("task_outcome") or "") == "pending_approval":
                            _m4_state.pending_approvals.append(
                                str((result or {}).get("approval_id") or "") or "pending"
                            )
                        from core.runtime_task_outcome import terminal_fulfillment_outcome

                        _terminal = terminal_fulfillment_outcome(result, source_context=observed_context)
                        _provider_terminal = _terminal if _terminal.failure_stage == "provider_execution" else None
                        # M2 SLICE 2 DISPLACEMENT: the scattered `_execution_identity`
                        # read and its hand-rolled write now go through the typed state
                        # (same shared dict underneath -- one truth, one door).
                        observed_context[TURN_STATE_KEY].mark_turn_ok(
                            bool((result or {}).get("success", True))
                        )
                        return result
        finally:
            execution_gate.reset_request_autonomy_override(token)
            # ROOT-CAUSE CONTRACT — the turn-scoped diagnosis binding is
            # TURN-scoped: cleared here so a later ordinary turn never
            # inherits this turn's repair diagnosis by adjacency (rule 5).
            # The durable diagnosis itself survives for a real continuation.
            try:
                from core.root_cause_contract import clear_turn_diagnosis_binding

                clear_turn_diagnosis_binding()
            except Exception:
                pass
            if _req_token is not None:
                _req_var.reset(_req_token)
            try:
                from core.semantic.semantic_admissions import clear_execution_context

                clear_execution_context()
            except Exception:
                pass
            try:
                from core.operator_profile_turn import clear_turn_scope as _clear_profile_scope

                _clear_profile_scope()
            except Exception:
                pass
            # R-6 pairing for _r3_open_turn_execution's bind_active_set: the
            # obligation binding is turn-scoped, so the turn unbinds it here —
            # a binding left behind shadowed the NEXT turn's finalization on
            # this context with a foreign set (OBLIGATIONS_OPEN refusals).
            try:
                from core.conductor.obligation_ledger import clear_active_set

                clear_active_set()
            except Exception:
                pass
            # R-3/A9: the per-turn attempt reaches a terminal in the same scope —
            # no zombie RECEIVED/RUNNING rows. A9 RC-4: a turn whose cancellation
            # marker fired before its outcome lands as CANCELLED — never
            # FAILED_PROVIDER, which is an unresolved/retryable state and would make
            # the next "why did that fail?" narrate a provider fault that never
            # happened. A9 RC-3: the L0 fence row the turn door opened is closed here
            # too — ACTIVE-forever executions made the terminal vocabulary (incl.
            # budget custody) unreachable from production.
            try:
                from core.invocation.ledger import set_execution_terminal
                from core.runtime_continuity import update_runtime_attempt

                def _cancellation_marker_fired(marker: object) -> bool:
                    # A9 RC-4 pass-002: the router accepts BOTH cancellation
                    # representations (threading.Event-like `.is_set()` and a plain
                    # callable token). The terminal boundary must understand the same
                    # vocabulary, or a user cancellation finalizes as retryable
                    # FAILED_PROVIDER and poisons the next failure/retry referent.
                    if marker is None:
                        return False
                    is_set = getattr(marker, "is_set", None)
                    probe = is_set if callable(is_set) else (marker if callable(marker) else None)
                    if probe is None:
                        return False
                    try:
                        return bool(probe())
                    except Exception:
                        return False

                _identity = observed_context.get("_execution_identity")
                if isinstance(_identity, dict) and _identity.get("attempt_id"):
                    # A9 RC-3 pass-002: absent outcome truth is NEVER success. `turn_ok`
                    # is written only after `_run_once_inner` returns; when it is missing
                    # here the inner call raised, so the only lawful default is failure.
                    _turn_ok = bool(_identity.get("turn_ok", False))
                    _was_cancelled = (
                        not _turn_ok
                        and _cancellation_marker_fired(
                            observed_context.get("cancel_event")
                            or observed_context.get("cancellation_token")
                        )
                    )
                    # A turn that paused for operator approval is WAITING, not failed: the
                    # spine records the pause on the typed turn state (see the
                    # `pending_approvals` writer above). Finalizing it as a provider fault
                    # made Activity narrate "FAILED_PROVIDER" over a healthy turn that was
                    # simply waiting for its operator's click.
                    _awaiting_approval = False
                    _turn_state = observed_context.get(TURN_STATE_KEY)
                    if _turn_state is not None and list(
                        getattr(_turn_state, "pending_approvals", None) or []
                    ):
                        _awaiting_approval = True
                    _attempt_state = (
                        "SUCCEEDED"
                        if _turn_ok
                        else (
                            AttemptLifecycle.WAITING_APPROVAL.value
                            if _awaiting_approval
                            else ("CANCELLED" if _was_cancelled else "FAILED_PROVIDER")
                        )
                    )
                    # FULFILMENT, NOT MERELY "DID NOT RAISE" (FINDINGS F14.5 / F15): a turn
                    # whose final text is a stated non-fulfilment -- an unresolved
                    # adjudication ask-back, the chat safe fallback, withheld live figures,
                    # a conductor disposition short of FULFILLED -- closes PARTIAL_SUCCESS
                    # with that reason, so "why?" binds to it and the record does not say
                    # the work succeeded. The mark is written by the seam that produced the
                    # text (`response._mark_turn_unfulfilled`, `_fast_path_result`).
                    _unfulfilled = str(_identity.get("turn_outcome") or "").strip()
                    if _turn_ok and _unfulfilled:
                        _attempt_state = "PARTIAL_SUCCESS"
                    # A router refusal can return normally with honest prose. The router's
                    # typed terminal, shared with HTTP fulfillment, owns the attempt truth.
                    if _turn_ok and _provider_terminal is not None:
                        _attempt_state = "FAILED_PROVIDER"
                        _unfulfilled = ", ".join(_provider_terminal.failure_codes)
                    update_runtime_attempt(
                        str(_identity["attempt_id"]),
                        lifecycle_state=_attempt_state,
                        terminal_reason=_unfulfilled if (_turn_ok and _unfulfilled) else None,
                        retryable=_provider_terminal.retryable if _provider_terminal is not None else None,
                        client_turn_id=str(observed_context.get("turn_key") or observed_context.get("client_turn_id") or ""),
                        # WAITING_APPROVAL is a resumable pause, not a terminal: the row
                        # stays open until the approved turn's own attempt closes it.
                        completed=_attempt_state != AttemptLifecycle.WAITING_APPROVAL.value,
                    )
                    if _attempt_state != AttemptLifecycle.WAITING_APPROVAL.value:
                        _execution_terminal = {
                            "SUCCEEDED": "COMPLETED",
                            "CANCELLED": "CANCELLED",
                        }.get(_attempt_state, "FAILED")
                        set_execution_terminal(
                            str(_identity.get("execution_id") or ""),
                            _execution_terminal,
                            runtime_epoch=str(_identity.get("runtime_epoch") or "") or None,
                        )
            except Exception:
                pass

    def _run_once_inner(
        self,
        user_input: str,
        *,
        session_id_override: str | None = None,
        source_context: dict[str, object] | None = None,
        turn_request: TurnRequest,
    ) -> dict:
        # ARCH-TRUTH-R1 (M2 truth repair): the canonical request is a REQUIRED
        # typed argument. Every entry into the inner runtime goes through the one
        # server-derived TurnRequest — run_once's pre-execution ingress
        # construction, or the planner hook's sub-turn construction through the
        # same `from_ingress`. A downstream dictionary lookup cannot mechanically
        # enforce that; this parameter does, and the guard below makes a forged
        # or absent object fail loudly instead of degrade silently.
        if not isinstance(turn_request, TurnRequest):
            raise TypeError(
                "_run_once_inner requires the canonical TurnRequest "
                "(server-derived, constructed before execution); got "
                f"{type(turn_request).__name__}"
            )
        # Keep frontdoor precedence explicit: safety/resume -> utility/direct fast paths ->
        # task-bound follow-ups -> Hive/task interactions -> grounded answer -> generic fallback.
        reset_admission()
        persona = load_active_persona(self.persona_id)
        # ARCH-TRUTH-R1 consumption: the session the turn runs under comes FROM
        # the typed request — the ingress derivation at run_once owns it (the
        # override, else the door-stamped context session, else this same
        # interior mint, which is a pure function of device+persona). The
        # fallbacks below stay only for direct compatibility calls whose request
        # was built without a session; run_once-served turns never reach them.
        session_id = (
            turn_request.session_id
            or session_id_override
            or runtime_session_id(device=self.device, persona_id=self.persona_id)
        )
        self._mark_user_activity()
        # The API owns this server-created context dictionary for the duration
        # of a turn.  Keep that identity so redacted receipts produced deep in
        # the router reach the terminal trace; do not create a second private
        # copy that silently drops context/provider manifest links.
        caller_source_context = (
            source_context if isinstance(source_context, dict) else None
        )
        runtime_source_context = (
            caller_source_context if caller_source_context is not None else {}
        )
        # Bound the turn's evidence HERE, at the runtime's own front door, so the bound holds for
        # every entry point and not only for the channel gateway -- a library caller reaching
        # `run_once` directly hands in its own `source_context`. Replacing the list in place means
        # every consumer downstream (authority resolution, the attachment description, media
        # ingestion, its fetches and its `media_evidence_log` writes) receives the SAME bounded
        # collection rather than each re-slicing a payload it has already paid to walk.
        if isinstance(runtime_source_context.get("external_evidence"), (list, tuple)) or (
            runtime_source_context.get("external_evidence") is not None
        ):
            runtime_source_context["external_evidence"] = bounded_evidence_items(
                runtime_source_context.get("external_evidence")
            )
        # Resolve, once and here, WHICH TEXT IN THIS TURN IS ALLOWED TO BECOME A COMMAND -- before
        # the action policy, the access policy and every gate below read `user_input`.
        #
        # The answer is the user's own visible text, returned unchanged. It is emphatically NOT
        # whatever the turn is carrying: an attachment's caption, extracted text, OCR or transcript
        # is something the user SHOWED the agent, and a PDF that reads "delete all files" is data,
        # not an instruction. `core/agent_runtime/request_authority.py` holds that rule, the closed
        # schema of fields that could ever carry authority (empty today, with the provenance
        # finding that makes it empty), and the turn-level bound.
        user_input = turn_command_text(user_input, runtime_source_context)
        # This is server-derived metadata, never model-facing prompt text and never caller-trusted.
        # A clear "do not take action" instruction applies to every execution seam in this turn.
        from core.agent_runtime.intent_claims import NO_COMMANDS_CONTEXT_KEY, turn_action_constraints

        runtime_source_context.pop("action_policy", None)
        runtime_source_context.pop(NO_COMMANDS_CONTEXT_KEY, None)
        # Two values, because "do not run tests" is a constraint on ONE operation and reading it as
        # a ban on the turn cancelled the build it was attached to. The narrower ban travels beside
        # the policy so the tool boundary can honour it without the lane being switched off.
        constraints = turn_action_constraints(user_input)
        runtime_source_context["action_policy"] = constraints.policy.value
        if constraints.forbid_commands:
            runtime_source_context[NO_COMMANDS_CONTEXT_KEY] = True
        from core.context_scope import ContextAccessPolicy

        access_policy = ContextAccessPolicy.for_request(
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if access_policy.namespace_state != "active":
            return _seal_semantic_result(
                {
                    "ok": False,
                    "response": (
                        f"This chat is {access_policy.namespace_state} and cannot "
                        "accept new messages."
                    ),
                    "error": "chat_namespace_inactive",
                    "chat_id": access_policy.chat_id,
                    "lifecycle_state": access_policy.namespace_state,
                },
                session_id=session_id,
                user_input=user_input,
                effective_input=user_input,
                source_context=runtime_source_context,
                semantic_source=SemanticSource.REFUSAL_POLICY,
                route_id="chat_namespace_inactive",
            )
        runtime_source_context["chat_id"] = access_policy.chat_id
        if access_policy.project_id:
            runtime_source_context["_trusted_project_id"] = access_policy.project_id
        # A turn with no request in it is stopped HERE, ahead of everything that would record it.
        # It used to run the whole front door and die at `TaskEnvelopeV1.__post_init__` --
        # `ValueError: goal is required`, because the envelope goal is the request text stripped and
        # there was nothing left of it -- with nothing between here and there catching it, so the
        # exception escaped `run_once` into the CLI and into `process_channel_request`.
        #
        # Ahead of `adapt_user_input` and `_prepare_runtime_checkpoint` specifically: those persist a
        # dialogue turn and a resumable checkpoint, and the crash then finalized that checkpoint as
        # `interrupted`, leaving a request-less turn sitting in the session for a later "continue" to
        # adopt. An empty turn should leave no such state behind.
        #
        # Deliberately narrow -- punctuation and emoji are NOT empty and keep the ordinary path.
        # What reaches here is a turn whose own text carries nothing. A turn that ARRIVED carrying
        # a great deal reaches here too, and should: an attachment is data, so a turn with nothing
        # but attachments has no request in it, and the reply below names what came through rather
        # than claiming the message was blank. See core/agent_runtime/request_authority.py for why
        # evidence never becomes a command, and empty_turn.py for where the text line is drawn.
        # Asked of `user_input` ALONE, on purpose: the line above already folded the whole turn
        # into it, so re-passing the source context here would resolve the evidence a second time
        # and double the bounded work for no new answer.
        if turn_has_no_request(user_input):
            # REACH: observation only, same contract as every `claimed` below -- returns nothing,
            # cannot raise, no-op when observation is off. It is recorded HERE rather than left
            # implicit because this gate answers the turn: without it the recorder sees no claim at
            # all, `preempted_by` reads back empty, and the receipt reports the turn as belonging to
            # `model_lane` -- a lane this return is specifically preventing it from reaching.
            semantic_reach.claimed(semantic_reach.GATE_EMPTY_TURN, "front door: no request in turn")
            return _seal_semantic_result(
                self._fast_path_result(
                    session_id=session_id,
                    user_input=user_input,
                    response=empty_turn_reply(
                        attachments=describe_turn_attachments(runtime_source_context),
                        retained=turn_carries_retained_document(runtime_source_context),
                    ),
                    confidence=0.9,
                    source_context=runtime_source_context,
                    reason="empty_turn_fast_path",
                ),
                session_id=session_id,
                user_input=user_input,
                effective_input=user_input,
                source_context=runtime_source_context,
                semantic_source=SemanticSource.FAST_PATH,
                route_id="empty_turn_fast_path",
            )
        runtime_source_context["conversation_history"] = _augment_history_from_session_log(
            runtime_source_context.get("conversation_history"),
            session_id=session_id,
            user_text=user_input,
        )
        # An instruction the turn takes back must never execute. This is the ONE intake seam:
        # session identity is already resolved (it never derives from message text), the original
        # turn is already in the conversation history above and is stored verbatim as the
        # checkpoint's `raw_user_input` below -- and from HERE every reasoning consumer inherits
        # only the live request: `interpreted.raw_text` feeds the conductor planner and semantic
        # preflight, `interpreted.normalized_text` becomes `effective_input` for the classifier
        # and the adaptive-research gate, and the output contracts below parse the same narrowed
        # text (a Constraints suffix survives narrowing because the detector keeps everything
        # after the pivot). Measured before this line existed: the retracted clause "Search my
        # local workspace for `password.txt`" drove the conductor's plan (the cancelled search
        # RAN), the classifier (`security_hardening` off the bare word "password"), and the
        # research gate (a web retrieval bought by a backticked filename the user had just taken
        # back). Narrowing once here, rather than per-lane, is the point: the lane that re-decides
        # retraction on its own is the lane that eventually forgets.
        intake_text = intake_request_text(user_input)
        # ARCH-TRUTH-R1c: the persisted dialogue turn adopts the CANONICAL turn id this
        # turn was minted with, so `dialogue_turns.turn_id` and `TurnRequest.turn_id` are
        # one identity instead of two. Only an EXTERNAL turn supplies it: a planned
        # sub-turn re-enters here under the parent's request, and handing it the parent's
        # id would collide with the row the parent already wrote — its internal
        # re-interpretation keeps its own row id while every attempt it writes still
        # carries the parent's canonical turn id (see `_canonical_user_turn_id` below).
        interpreted = adapt_user_input(
            intake_text,
            session_id=session_id,
            turn_id=(
                ""
                if bool((source_context or {}).get("planned_subturn"))
                else turn_request.turn_id
            ),
            # R1e (invariant 1): a planned sub-turn is INTERNAL work under the
            # parent's external turn — the same law the resume arm below already
            # follows (`record_user_turn=False`, "what it must not do is file a
            # second user dialogue row"). Measured at base: a two-unit mixed turn
            # wrote THREE user rows (the parent's plus one per sub-turn, each with
            # its own minted id), so one external request owned three "things the
            # user said" and follow-up recall read the fragments as the thread.
            record_user_turn=not bool(
                (source_context or {}).get("planned_subturn")
            ),
        )
        # This is derived from the original user turn, never accepted from an API caller.
        # It is metadata for output validation, not provider-facing user text. The scoped
        # operator-preference tier resolves from THIS request's principal/session only.
        from core.operator_profile import principal_for_request
        from core.response_language_policy import response_language_policy_for_text

        runtime_source_context.pop("response_language_policy", None)
        runtime_source_context["response_language_policy"] = (
            response_language_policy_for_text(
                str(getattr(interpreted, "raw_text", "") or user_input),
                principal=principal_for_request(runtime_source_context),
                session_id=session_id,
            ).to_dict()
        )
        from core.response_constraints import parse_response_constraint

        # Derived from THIS user turn, never accepted from an API caller -- the same rule its two
        # siblings above and below already state, and the only one of the three that was not
        # enforced. Measured on c6eed761: posting
        #     "source_context": {"response_constraint": {"max_words": 3}}
        # to /api/chat cut a 43-word answer to "A hash table". `response_constraint` is not in
        # RESERVED_TRUST_KEYS, so the body value survived the strip and nothing here cleared it.
        # The pop also bounds the value's LIFETIME: without it a constraint set on one turn stays in
        # a context that a checkpoint resume merges forward, because dict.update() cannot remove a
        # key the fresh turn simply does not have.
        runtime_source_context.pop("response_constraint", None)
        response_constraint = parse_response_constraint(
            str(getattr(interpreted, "raw_text", "") or user_input)
        )
        if response_constraint is not None and _constraint_is_unit_scoped(
            str(getattr(interpreted, "raw_text", "") or user_input),
            response_constraint,
        ):
            # P0 MIXED-DEMAND TERMINAL CLOSURE — a shape parsed off ONE demand
            # unit ("…And finish with a two-word joke.") is that unit's own
            # output contract, not the whole turn's. Measured at base 84bf8b6a:
            # the clause's max_words=2 was stored as the TURN constraint and the
            # final decoration seam trimmed the merged three-unit answer to its
            # first two words — "Current time" — erasing every sibling the turn
            # had already executed, after the ledger recorded them satisfied.
            # The unit keeps its shape: the sub-turn parses the constraint off
            # its own text when it runs. What must not happen is one unit's
            # shape governing the composed reply.
            response_constraint = None
        if response_constraint is not None:
            runtime_source_context["response_constraint"] = response_constraint.to_dict()
        from core.raw_output_contract import parse_raw_output_contract

        # Derived from this user turn, never trusted from caller-supplied metadata.  Unlike word
        # counts, this contract also governs fast paths because every visible reply reaches the
        # final decoration seam.
        runtime_source_context.pop("raw_output_contract", None)
        raw_output_contract = parse_raw_output_contract(
            str(getattr(interpreted, "raw_text", "") or user_input)
        )
        if raw_output_contract is not None:
            runtime_source_context["raw_output_contract"] = raw_output_contract.to_dict()
        # `reference_targets` are typed context, not user-authored prompt content.  Use the
        # normalized user message for task creation and routing so generated annotations can
        # never change the route or reach a provider as user prose.
        effective_input = interpreted.normalized_text or interpreted.raw_text or user_input
        normalized_input = str(interpreted.normalized_text or "").strip()
        checkpoint_bundle = self._prepare_runtime_checkpoint(
            session_id=session_id,
            raw_user_input=user_input,
            effective_input=effective_input,
            source_context=runtime_source_context,
            allow_followup_resume=not self._blocks_runtime_followup_resume(
                session_id=session_id,
                source_context=runtime_source_context,
            ),
        )
        checkpoint_source_context = dict(
            checkpoint_bundle.get("source_context") or runtime_source_context
        )
        # A checkpoint's stored source context is evidence from whenever it was WRITTEN, under
        # whatever rules applied then, and the lines below replace this turn's already-bounded
        # context with it wholesale -- so a hundred-item checkpoint recreated a hundred-item runtime
        # view and the front-door bound bought nothing. Re-bound at the restoration boundary,
        # before the merge, before normalization, description, fetch, persistence and any
        # downstream classification.
        if checkpoint_source_context.get("external_evidence") is not None:
            checkpoint_source_context["external_evidence"] = bounded_evidence_items(
                checkpoint_source_context.get("external_evidence")
            )
        # Tell the live-turn registry which durable checkpoint this worker is executing. That is what
        # lets the stale sweep tell a turn that is quiet but ALIVE from one whose process is gone --
        # without it, a long background turn that spends 15 minutes inside a single model call gets
        # closed out as "interrupted" and offered back to the operator as a Resume, which would run
        # it a second time alongside the one still going. See core/live_turns.py.
        _live_checkpoint_id = str(
            checkpoint_source_context.get("runtime_checkpoint_id") or ""
        ).strip()
        _live_turn_id = str(checkpoint_source_context.get("cancel_turn_id") or "").strip()
        if _live_checkpoint_id and _live_turn_id:
            from core.live_turns import note_checkpoint

            note_checkpoint(session_id, _live_turn_id, _live_checkpoint_id)
        if caller_source_context is not None:
            caller_source_context.clear()
            caller_source_context.update(checkpoint_source_context)
            runtime_source_context = caller_source_context
        else:
            runtime_source_context = checkpoint_source_context
        # ARCH-TRUTH-R1: the checkpoint merge above REPLACES the caller's context
        # contents wholesale; re-publish the canonical request onto the FINAL
        # runtime context (the same immutable object — one truth, no second
        # construction) so every downstream consumer of this turn reads it off
        # the context that actually serves the turn.
        runtime_source_context[TURN_REQUEST_KEY] = turn_request
        # Open this turn's provider-call ledger, now that the context carries the request/turn
        # identity it is keyed against and BEFORE the front door -- every gate that could reach a
        # provider runs below this line. The id is stamped into the context, so the shallow copies
        # provider execution travels through and the conductor's worker threads all count into the
        # same ledger. See core/turn_model_call_ledger.py for what `model_calls` means exactly.
        from core.turn_model_call_ledger import begin_turn as begin_model_call_ledger

        begin_model_call_ledger(runtime_source_context)
        checkpoint_state = str(checkpoint_bundle.get("state") or "created")
        if checkpoint_state == "missing_resume":
            # "try again" with neither a mid-task checkpoint to resume nor a preceding failed task
            # to retry -- ask what to run rather than emitting unrelated channel-setup instructions.
            return _seal_semantic_result(
                self._fast_path_result(
                    session_id=session_id,
                    user_input=user_input,
                    response=(
                        "There's nothing in this chat to retry yet -- no task has run or failed here. "
                        "Tell me what you'd like me to do and I'll run it."
                    ),
                    confidence=0.78,
                    source_context=runtime_source_context,
                    reason="runtime_resume_missing",
                ),
                session_id=session_id,
                user_input=user_input,
                effective_input=effective_input,
                source_context=runtime_source_context,
                semantic_source=SemanticSource.RECOVERY,
                route_id="runtime_resume_missing",
            )
        # A resume replaces this turn's text with the stored request of the checkpoint being picked
        # back up, so the front-door gate above (which saw "continue") cannot vouch for it. Resolve
        # it the same way and for the same reason -- the stored request may itself have arrived as
        # a caption or a transcript rather than as text.
        checkpoint_effective_input = (
            ""
            if checkpoint_state == "rejected_resume"
            else str(checkpoint_bundle.get("effective_input") or effective_input)
        )
        effective_input = turn_command_text(
            checkpoint_effective_input,
            runtime_source_context,
        )
        # Every install that hit the crash this fix removes has a poisoned checkpoint: the
        # whitespace turn created it, then the handler at the bottom of this method finalized it
        # `interrupted`, which is exactly what a later "continue" resumes. Answering here also
        # finalizes that checkpoint completed, so it is offered once and then stops coming back.
        # Note the asymmetry with the front door on purpose: there, no checkpoint exists yet and
        # there is nothing to close; here a real one does, and closing it truthfully is the point.
        if turn_has_no_request(effective_input):
            # The resume arm of the same gate, and it claims the turn just as squarely -- see the
            # note at the front-door arm for why an unrecorded claim makes the receipt read wrong.
            semantic_reach.claimed(semantic_reach.GATE_EMPTY_TURN, "resume: stored request is empty")
            return _seal_semantic_result(
                self._fast_path_result(
                    session_id=session_id,
                    user_input=user_input,
                    response=resumed_empty_turn_reply(),
                    confidence=0.9,
                    source_context=runtime_source_context,
                    reason="empty_turn_fast_path",
                ),
                session_id=session_id,
                user_input=user_input,
                effective_input=effective_input,
                source_context=runtime_source_context,
                semantic_source=SemanticSource.FAST_PATH,
                route_id="empty_turn_fast_path_resume",
            )
        if checkpoint_state in ("resumed", "retried"):
            # Both resume the original request text (resumed = a mid-task checkpoint; retried = the
            # preceding failed task's envelope), so re-interpret the effective input for this turn.
            # The checkpoint stores the ORIGINAL turn, so a resumed retraction turn re-narrows here
            # -- the same single intake rule as the front door, and idempotent when the stored
            # request was already the live remainder.
            resume_intake_text = intake_request_text(effective_input)
            # ARCH-TRUTH-R1d: this is the turn RE-READING its own restored request, not the
            # user saying something else. It still derives the interpretation and still
            # updates the session's typed continuity state from the resolved text — what it
            # must not do is file a second user dialogue row, which is what made one
            # external turn own two user turns and two turn ids.
            interpreted = adapt_user_input(
                resume_intake_text,
                session_id=session_id,
                turn_id=turn_request.turn_id,
                record_user_turn=False,
            )
            normalized_input = str(interpreted.normalized_text or "").strip()
            if resume_intake_text != effective_input:
                # Mirror the front door's contract: reasoning consumers downstream read
                # `effective_input`, and on this arm it still carried the withdrawn clause.
                effective_input = normalized_input or resume_intake_text
            # P0 POLICY CONSERVATION — the canonical request was minted from the
            # LITERAL message ("continue"/"try again"), which freezes nothing.
            # The restored request text is the turn's authority, so its
            # prohibitions are re-frozen onto the request the children inherit
            # — same request/turn/session identity, conserved constraints. A
            # resumed plan may never widen what its parent froze.
            turn_request = conserve_request_for_resume(turn_request, effective_input)
            runtime_source_context[TURN_REQUEST_KEY] = turn_request
        # Repair 4 (Mnemosyne review): the canonical, persisted `dialogue_turns.turn_id` this turn
        # was recorded under (`core.human_input_adapter.adapt_user_input` -> `record_dialogue_turn`)
        # -- the real turn identity `runtime_attempts.origin_user_turn_id`/`trigger_user_turn_id`
        # need. Was silently empty on every production attempt/retry before this: threaded through
        # `source_context` (the existing carrier for internal, non-provider-facing turn metadata --
        # see `_trusted_project_id`/`response_language_policy` above) rather than adding a new
        # parameter to every call in the chain down to `_create_live_data_runtime_attempt` and
        # `execute_attempt_retry`.
        # ARCH-TRUTH-R1c: ONE turn identity. This key is the canonical request's turn id —
        # the same value `record_dialogue_turn` persisted for this turn above, and the same
        # one the turn door filed its execution attempt under. It used to be read back off
        # whichever interpretation ran last, which on a resumed turn was a SECOND dialogue
        # row minted mid-turn; the attempts of one turn then carried two different turn ids.
        runtime_source_context["_canonical_user_turn_id"] = str(
            turn_request.turn_id or getattr(interpreted, "turn_id", "") or ""
        )
        source_context = runtime_source_context
        # Semantic authority is resolved only NOW, after a resume/retry has replaced ``continue``
        # with the checkpoint's original request and re-run input interpretation above.  The raw
        # interpretation is kept separate from ``effective_input`` because normalization may
        # rewrite punctuation, whitespace, quoted literals and therefore every source span.
        from core.semantic.preflight import semantic_preflight

        semantic_text = str(getattr(interpreted, "raw_text", "") or effective_input)
        preflight = semantic_preflight(
            semantic_text,
            ambiguous_reference=(
                "ambiguous_reference" in tuple(getattr(interpreted, "quality_flags", ()) or ())
            ),
        )

        # A response headed by "List" is still commonly a knowledge request (list SI units, list
        # members of a standard). A model proposal cannot turn that verb alone into permission to
        # inspect a local directory. The existing side-effect-free list-directory probe owns the
        # actual path/machine grammar; only its positive claim supplies explicit-text proof.
        from core.agent_runtime.intent_claims import FAMILY_LIST_DIRECTORY, probe_claims
        from core.semantic.preflight import (
            SemanticFrame,
            admit_deterministic_candidate,
            explicit_text_whole_turn_candidate,
        )

        list_directory_claimed = any(
            claim.family == FAMILY_LIST_DIRECTORY for claim in probe_claims(semantic_text)
        )
        list_directory_admitted = False
        if list_directory_claimed:
            list_directory_candidate = explicit_text_whole_turn_candidate(
                preflight,
                route_id="machine.list_directory",
                source_id="core.agent_runtime.intent_claims.list_directory.v1",
                allowed_frames=frozenset({SemanticFrame.REAL, SemanticFrame.UNKNOWN}),
            )
            list_directory_admitted = admit_deterministic_candidate(
                preflight,
                list_directory_candidate,
            ).admitted
        runtime_source_context["_semantic_machine_list_directory_admitted"] = (
            list_directory_admitted
        )

        # The initial action policy was necessarily computed before checkpoint resolution, when a
        # resumed turn still read only ``continue``. Rebind it to the authoritative semantic text.
        # A stipulated/fictional premise or quoted example is input to reason over, not permission
        # to enact the action it describes. The ordinary model path remains available; only tool
        # and effect lanes are muted. An explicit frame exit creates a later REAL scope and leaves
        # ``effect_routes_safe`` true, so a separately stated real action is not globally erased.
        semantic_constraints = turn_action_constraints(semantic_text)
        runtime_source_context["action_policy"] = semantic_constraints.policy.value
        runtime_source_context.pop(NO_COMMANDS_CONTEXT_KEY, None)
        if semantic_constraints.forbid_commands or not preflight.effect_routes_safe:
            runtime_source_context[NO_COMMANDS_CONTEXT_KEY] = True
        if not preflight.effect_routes_safe:
            from core.agent_runtime.intent_claims import ActionPolicy

            runtime_source_context["action_policy"] = ActionPolicy.FORBIDDEN.value

        def _guard_final_result(
            result: dict,
            *,
            semantic_source: SemanticSource | None = None,
            route_id: str = "",
        ) -> dict:
            return _seal_semantic_result(
                _reject_undeclared_finalization(result),
                session_id=session_id,
                user_input=user_input,
                effective_input=effective_input,
                source_context=source_context,
                semantic_source=semantic_source,
                route_id=route_id,
            )

        source_surface = str((source_context or {}).get("surface", "cli")).lower()

        # R1g AMENDMENT — the typed SECRET INTAKE, before any lane, before the
        # task_received event, and before any model or planner can see the text.
        # A secret route consuming a credential is ONE owned outcome of the
        # turn, never a license to finalize the whole external turn and drop
        # every other demand beside it (measured at 610076c0: cloud-key,
        # image-key and bare-secret each swallowed "explain entropy briefly").
        # The sanitized remainder is the ONLY text the rest of the turn may
        # see — children, model input, planner tasks, receipts, events.
        raw_turn_text = str(getattr(interpreted, "raw_text", "") or user_input)
        secret_intake = None
        try:
            from core.agent_runtime.secret_intake import consume_secret_intake
            from core.request_trust import request_is_owner_local

            secret_intake = consume_secret_intake(
                raw_turn_text,
                owner_local=request_is_owner_local(runtime_source_context),
            )
        except Exception:
            # R1g2 amendment, defect 2 — FAIL CLOSED. The text MIGHT contain a
            # secret this runtime could not process; the raw text may reach no
            # event, model, child, planner, receipt, telemetry, dialogue or
            # log. The turn ends HERE in a typed safe failure/defer response
            # (returned before the task_received emit), never continues on
            # the raw text under uncertainty.
            return _guard_final_result(_secret_intake_failure_result())
        if secret_intake is not None and secret_intake.reply:
            # A credential route claimed part of the message. Once its handler
            # ran, the credential side effect has happened — ownership is
            # irreversible (the R1e amendment law): every exit below returns a
            # result, never None into the cascade that would re-run the raw
            # secret text through other lanes.
            if secret_intake.remainder_has_demand:
                return _guard_final_result(
                    self._answer_secret_intake_turn(
                        intake=secret_intake,
                        effective_input=effective_input,
                        session_id=session_id,
                        source_context=runtime_source_context,
                    )
                )
            return _guard_final_result(
                self._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=secret_intake.reply,
                    confidence=0.95,
                    source_context=runtime_source_context,
                    reason=secret_intake.route,
                )
            )
        if secret_intake is not None and secret_intake.remainder.strip():
            # sanitized_only: nothing claimed, nothing to finalize — the cascade
            # continues on the REDACTED text (e.g. a pasted config plus a
            # question: the model answers the question and never sees the key).
            raw_turn_text = secret_intake.remainder
            effective_input = secret_intake.remainder
            try:
                from dataclasses import replace as _dc_replace

                carried = runtime_source_context.get(TURN_REQUEST_KEY)
                if carried is not None:
                    # The canonical request COPY the cascade hands the model
                    # router keeps the turn id and carries the SANITIZED text —
                    # the verbatim original stays in the ingress-owned request
                    # and the (redacted) persistence layer, never in model input.
                    runtime_source_context[TURN_REQUEST_KEY] = _dc_replace(
                        carried, user_text=secret_intake.remainder
                    )
            except Exception:
                pass

        prune_stale_hive_interaction_state(session_id)
        self._emit_runtime_event(
            source_context,
            event_type="task_resumed" if checkpoint_state == "resumed" else "task_received",
            message=(
                f"Resuming interrupted task: {self._runtime_preview(effective_input)}"
                if checkpoint_state == "resumed"
                else f"Received request: {self._runtime_preview(effective_input)}"
            ),
            request_preview=self._runtime_preview(effective_input, limit=160),
            resume_available=checkpoint_state == "resumed",
        )

        # A message that asks several things must produce several answers. Everything below this
        # point routes a turn to ONE mode and one subject -- `live_info_mode()` returns a single
        # string, and the price/market/weather/news lanes each then select a single target -- so a
        # multi-part message cannot survive past the front door. Measured on the shipped build:
        # "what is the euro to usd today and will it rain and where i can change my tyres in
        # vilnius" answered only the weather, with the whole sentence used as the place name.
        # Splitting here, ahead of the front door, is the only place it can be done once instead of
        # once per lane. Returns None for an ordinary single-request turn, which is the common path
        # and pays nothing.
        # A short follow-up ("why did that fail?", "retry", "which assets did I ask for?") is
        # checked BEFORE anything else -- Step 9/10. Found live: "Retry the exact failed request
        # now." reached the adaptive-research subsystem, which used the literal phrase as a web
        # search query and returned unrelated scraped content as "grounding." This gate resolves
        # against a persisted `runtime_attempts` record deterministically, before any model or
        # web-search machinery can see the raw phrase. Returns None for anything that does not
        # classify as a follow-up, or that classifies but has no resolvable attempt -- in which
        # case the turn proceeds exactly as it always did, honestly, with no fabricated antecedent.
        followup_result = self._maybe_answer_attempt_followup_turn(
            effective_input=effective_input,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        # REACH: observation only. `semantic_reach.claimed`/`declined` return nothing, cannot raise,
        # and are no-ops when observation is off -- the lane order below is untouched.
        if followup_result is not None:
            semantic_reach.claimed(semantic_reach.GATE_ATTEMPT_FOLLOWUP)
            return _guard_final_result(followup_result)
        semantic_reach.declined(semantic_reach.GATE_ATTEMPT_FOLLOWUP)
        # "where is the folder?!" after this chat just built one: answered from the runtime's own
        # mutation receipts BEFORE any lane can misroute it to a whole-disk folder search.
        location_result = self._maybe_answer_location_followup_turn(
            effective_input=effective_input,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if location_result is not None:
            return _guard_final_result(location_result)
        # A9 P0 — an armed routing retry re-dispatches the ORIGINAL request: the phrase the
        # user typed ("retry that exact request") is replaced by the text of the failed
        # request, so the generation that runs is linked to the original query, never a
        # literal replay of the retry phrase as a new query. BOTH text variables rebind —
        # `user_input` is what the task/interpretation/prompt builders read — while the
        # dialogue record keeps the retry phrase the user actually typed (the door wrote
        # it before this point) and the plan provenance carries the linkage.
        if isinstance(runtime_source_context, dict):
            _routing_retry_marker = runtime_source_context.get("turn_routing_retry")
            if isinstance(_routing_retry_marker, dict) and _routing_retry_marker.get("user_text"):
                effective_input = str(_routing_retry_marker["user_text"])
                user_input = effective_input
                # The interpretation object was minted at intake from the literal retry
                # phrase; every prompt builder downstream reads IT (raw/reconstructed
                # text), not the locals above. Re-derive it from the ORIGINAL request —
                # the same `record_user_turn=False` seam the resume arm above uses, so
                # the substituted execution never files a second user dialogue row.
                try:
                    interpreted = adapt_user_input(
                        effective_input,
                        session_id=session_id,
                        turn_id=turn_request.turn_id,
                        record_user_turn=False,
                    )
                except Exception:
                    pass

        # Currency identity is stable local knowledge; currency VALUE is an observation.  Enforce
        # that boundary before the conductor, planner, or model can turn an unavailable rate into
        # plausible prose.  The same seam resolves a terse "local only" follow-up against the
        # recent user comparison, which is the production shape that fabricated DKK/USD and
        # CNY/USD conversions after live lookup had been ruled out.
        #
        # R1e — DEMAND OWNERSHIP, first application of the general law: this contract's
        # whole-text reading fused a conversion shape in one clause with "exchange rate"
        # wording in another, and claimed a two-unit mixed turn whole (measured at base:
        # "Convert 100 USD to EUR and explain what an exchange rate spread is" answered
        # with the FX-availability template; the explanation vanished). The contract may
        # END the external turn only when it accounts for every demand unit; a strict
        # subset declines here and the demand-owned seam below the conductor executes
        # every unit through its owning lane. Single-unit and all-currency turns are
        # untouched (the probe covers every unit or the turn mints one).
        from core.currency_value_contract import maybe_answer_currency_value

        _currency_may_claim_whole = True
        try:
            from core.agent_runtime.demand_ownership import (
                LANE_CURRENCY,
                lane_may_claim_whole_turn,
            )

            _currency_may_claim_whole = lane_may_claim_whole_turn(
                intake_text, LANE_CURRENCY
            )
        except Exception:
            _currency_may_claim_whole = True
        currency_value_reply = (
            maybe_answer_currency_value(
                effective_input,
                source_context=runtime_source_context,
            )
            if _currency_may_claim_whole
            else None
        )
        if currency_value_reply is not None:
            semantic_reach.claimed("currency_value_contract")
            return _guard_final_result(
                self._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=currency_value_reply.response,
                    confidence=0.98,
                    source_context=runtime_source_context,
                    reason=currency_value_reply.reason,
                )
            )
        semantic_reach.declined("currency_value_contract")

        # Closed whole-turn contracts do not need semantic decomposition.  Running the conductor
        # first bought a real local-model planner call for exact haikus and user-supplied FX math,
        # then returned a fast-path envelope claiming model_calls=0.  Admit only reviewed contracts
        # whose coverage owns the entire turn; mixed requests continue to the conductor below.
        from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn

        if closed_semantic_contract_covers_turn(
            semantic_text,
            session_id=session_id,
            source_context=source_context,
            preflight=preflight,
        ):
            closed_bundle = self._handle_turn_frontdoor(
                raw_user_input=semantic_text,
                effective_input=effective_input,
                normalized_input=normalized_input,
                source_surface=source_surface,
                session_id=session_id,
                # The runtime-owned context, not the raw parameter: an
                # in-process caller may pass source_context=None, and
                # `record_slice_answer` silently no-ops on a non-dict — the
                # conversion it computed was thrown away with no telemetry.
                source_context=runtime_source_context,
                persona=persona,
                interpreted=interpreted,
                access_policy=access_policy,
                semantic_preflight=preflight,
            )
            closed_result = closed_bundle.get("result")
            if closed_result is not None:
                semantic_reach.claimed(semantic_reach.GATE_FRONTDOOR)
                return _guard_final_result(closed_result)

        # A message that mixes DOMAINS is claimed here, ahead of every single-domain lane below.
        # Measured on the shipped build: "What is 137 x 29? ... Also get the current weather for
        # Kaunas and Tallinn and tell me which city is warmer" answered the weather and the
        # comparison and the arithmetic simply VANISHED -- not refused, not reported, absent from a
        # confident reply. `requirements_for()` classifies that whole message LIVE_DATA because it
        # names two cities, and the live-data lane then returns before any lane that could do the
        # arithmetic ever runs. Whichever single domain claims first answers, and the rest is lost.
        #
        # The conductor is deliberately conservative about claiming: `requires_conductor()` is
        # False for a plan the existing lanes already serve together, so a pure weather-and-markets
        # turn still reaches `_maybe_answer_live_data_turn` below and its typed plan, real
        # concurrency and overlap proof are untouched. This can only ADD the ability to answer a
        # mixed message.
        # Install one planner artifact in the server-owned context before the conductor binds its
        # shorter provider deadline. The bound copy and the generic planner below then observe the
        # same object: a conductor decline cannot buy a second classification generation.
        from core.agent_runtime.turn_planner_hook import ensure_shared_planner_artifact

        ensure_shared_planner_artifact(runtime_source_context)
        conductor_result = self._maybe_answer_conductor_turn(
            effective_input=effective_input,
            raw_input=raw_turn_text,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if conductor_result is not None:
            semantic_reach.claimed(semantic_reach.GATE_CONDUCTOR)
            return _guard_final_result(conductor_result)
        semantic_reach.declined(semantic_reach.GATE_CONDUCTOR)

        # R1e — CANONICAL DEMAND OWNERSHIP. Between the conductor and the single-domain
        # lanes sits the one seam that owns MIXED turns: the request's demand units are
        # minted before execution, and when no deterministic lane accounts for every unit
        # (a live clause beside an explanation, a conversion beside a question), the turn
        # executes as a per-unit plan — each unit through its owning lane, one synthesized
        # answer, partial failure named. Measured at base: the live-data lane claimed
        # "Explain how a hash table works and tell me the weather in Kaunas" whole (the
        # explanation vanished) and the currency family did the same to a conversion-plus-
        # explanation turn. Pure turns never reach the executor: one lane covering every
        # unit, or no lane covering any, returns None here and keeps its existing path.
        demand_owned_result = self._maybe_answer_demand_owned_turn(
            effective_input=effective_input,
            raw_input=raw_turn_text,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if demand_owned_result is not None:
            semantic_reach.claimed(semantic_reach.GATE_PLANNED_TURN)
            return _guard_final_result(demand_owned_result)
        semantic_reach.declined(semantic_reach.GATE_PLANNED_TURN)

        # A live-data request (price/weather) gets the typed plan first, ahead of the general
        # text-splitting planner below: that planner carries each part as a raw string and has no
        # concept of "N cities" or "N assets" as separate typed lanes, which is exactly how a
        # multi-city weather clause ended up losing its own boundary. Returns None for anything
        # that is not LIVE_DATA-classified, or when the typed plan itself finds nothing to answer
        # -- the ordinary planner and every existing lane are untouched below.
        live_data_result = self._maybe_answer_live_data_turn(
            effective_input=effective_input,
            raw_input=raw_turn_text,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if live_data_result is not None:
            semantic_reach.claimed(semantic_reach.GATE_LIVE_DATA)
            return _guard_final_result(live_data_result)
        semantic_reach.declined(semantic_reach.GATE_LIVE_DATA)

        planned_result = self._maybe_answer_planned_turn(
            effective_input=effective_input,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if planned_result is not None:
            semantic_reach.claimed(semantic_reach.GATE_PLANNED_TURN)
            return _guard_final_result(planned_result)
        semantic_reach.declined(semantic_reach.GATE_PLANNED_TURN)

        # Priority C (F45): one plain knowledge question is adjudicated for ENTITY AMBIGUITY
        # before any answering generation runs. Served on f2b3fa6f, "What is the population
        # of Springfield?" published "approximately 250,000 people" twice and "approximately
        # 12,000 residents" once across three sessions — two invented numbers, proving no
        # contract stood between the question and the bytes. The probe is a typed model
        # judgment (the only authority that can know a name is multiply-referenced), asked
        # once, bounded, fail-open; the RUNTIME enforces its consequence — an ambiguous
        # verdict means the answer never generates and a clarification naming the referents
        # is served instead. No surface list: the eligibility gate is structural (ONE KNOW
        # demand, no attachments), which already excludes read/act verbs and multi-part
        # turns, and surfaces are caller-supplied strings a gate must not enumerate.
        ambiguity_result = self._maybe_clarify_ambiguous_entity(
            effective_input=effective_input,
            session_id=session_id,
            source_context=runtime_source_context,
        )
        if ambiguity_result is not None:
            return _guard_final_result(ambiguity_result)

        frontdoor_bundle = self._handle_turn_frontdoor(
            raw_user_input=semantic_text,
            effective_input=effective_input,
            normalized_input=normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            # Same seam as the closed-contract pass above: slice answers must
            # land in the runtime-owned context the claiming lanes share.
            source_context=runtime_source_context,
            persona=persona,
            interpreted=interpreted,
            access_policy=access_policy,
            semantic_preflight=preflight,
        )
        frontdoor_result = frontdoor_bundle.get("result")
        if frontdoor_result is not None:
            semantic_reach.claimed(semantic_reach.GATE_FRONTDOOR)
            return _guard_final_result(frontdoor_result)
        semantic_reach.declined(semantic_reach.GATE_FRONTDOOR)
        # ENTERED, not "reached". Execution arriving here is not a model having read the message --
        # several early returns below this line end the turn with zero model calls, and a receipt
        # that called this "reached the model lane" was demonstrably false on those turns. Whether a
        # model actually ran comes from the finished turn's own `model_calls`, recorded in
        # `run_once` via `observation.record_outcome`.
        semantic_reach.entered(semantic_reach.GATE_MODEL_LANE)
        try:  # routing telemetry: no fast path claimed -> the model lane owns this turn
            from core.routing_decision_log import record_decision

            record_decision(session_id=session_id, user_input=effective_input, family="model_lane", handled=False)
        except Exception:
            pass

        self._sync_public_presence(status="busy", source_context=source_context)
        try:
            turn_bundle = self._prepare_turn_task_bundle(
                effective_input=effective_input,
                user_input=user_input,
                session_id=session_id,
                source_context=source_context,
                interpreted=interpreted,
            )
            frontdoor_result = turn_bundle.get("result")
            if frontdoor_result is not None:
                return _guard_final_result(frontdoor_result)
            task = turn_bundle["task"]
            classification = dict(turn_bundle.get("classification") or {})

            return _guard_final_result(
                self._execute_grounded_turn(
                    task=task,
                    effective_input=effective_input,
                    classification=classification,
                    interpreted=interpreted,
                    persona=persona,
                    session_id=session_id,
                    source_context=source_context,
                )
            )
        except Exception as exc:
            self._finalize_runtime_checkpoint(
                source_context,
                status="interrupted",
                failure_text=str(exc),
            )
            self._emit_runtime_event(
                source_context,
                event_type="task_interrupted",
                message=f"Task failed: {self._runtime_preview(str(exc), limit=200)}",
            )
            raise
        finally:
            self._sync_public_presence(
                status=self._idle_public_presence_status(),
                source_context=source_context,
            )

    def _maybe_handle_memory_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        access_policy: Any | None = None,
    ) -> dict[str, Any] | None:
        return agent_memory_runtime.maybe_handle_memory_fast_path(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            access_policy=access_policy,
            maybe_handle_memory_command_fn=maybe_handle_memory_command,
        )

    def _model_final_response_text(self, model_execution: Any) -> str:
        return agent_memory_runtime.model_final_response_text(model_execution)

    def _prepare_turn_task_bundle(
        self,
        *,
        effective_input: str,
        user_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
        interpreted: Any,
    ) -> dict[str, Any]:
        return agent_turn_dispatch.prepare_turn_task_bundle(
            self,
            effective_input=effective_input,
            user_input=user_input,
            session_id=session_id,
            source_context=source_context,
            interpreted=interpreted,
            classify_fn=classify,
            parse_channel_post_intent_fn=parse_channel_post_intent,
            dispatch_outbound_post_intent_fn=dispatch_outbound_post_intent,
            parse_operator_action_intent_fn=parse_operator_action_intent,
            dispatch_operator_action_fn=dispatch_operator_action,
        )

    def _maybe_answer_demand_owned_turn(
        self,
        *,
        effective_input: str,
        raw_input: str = "",
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """Answer a MIXED-demand turn as one plan over its demand units, or None.

        None — and the turn exactly as it always was — is the outcome for every pure
        shape: fewer than two demand units (single requests and near-misses keep their
        lanes), every unit covered by ONE deterministic lane (that lane's whole-turn
        claim stands, registry-mediated), and no unit covered at all (a pure general
        turn keeps the normal answer path). The executor only runs when a whole-turn
        claim would truncate: some lane covers some units and no lane covers them all.

        THE OWNERSHIP LAW (R1e amendment). The method has two phases with one seam
        between them: the first child dispatch.

        BEFORE it — eligibility. A classification/probe failure may decline with
        None and zero side effects; the ordinary cascade has lost nothing.

        AFTER it — ownership is irreversible. The external request has been executed
        (units ran; model calls, tools and effects happened), so there is no path
        back to None: the cascade below would re-run a request whose work is already
        done. Some children succeeding returns the truthful partial synthesis; every
        child failing returns ONE deterministic typed failure summary naming every
        unmet unit; a merge or finalization fault is answered from the outcomes the
        children already produced — degraded, never swallowed into fallback.

        Execution reuses the R1d planner machinery unchanged — each unit is a
        `PlannedTask` served by a real sub-turn under the parent's canonical
        TurnRequest (one chain, stable per-task execution slots), and `merge_outcomes`
        composes ONE reply in which a failed unit cannot erase a successful one and
        partial failure is named, never silent. Deterministic by construction: the
        split is the demand-unit mint over the user's own words, so no model chooses
        it and no word the user did not write enters a task.
        """
        if bool((source_context or {}).get("planned_subturn")):
            # A sub-turn IS one demand unit; planning it again would recurse.
            return None
        if dict((source_context or {}).get("raw_output_contract") or {}).get(
            "structured_labels"
        ):
            return None
        # A STIPULATED turn supplies the premises for ONE computation: its demand units are
        # that computation's parts, and no fragment carries the data block the arithmetic
        # runs on. Measured live 2026-09-16: a self-contained provider comparison was carved
        # into per-unit sub-turns, the fragments lost their numbers and read as market
        # tickers, and the merge served "I could not answer these parts" beside a Markets
        # table of unresolvable symbols instead of the arithmetic the message supplied.
        try:
            from core.stipulated_frame import stipulated_frame_active

            if stipulated_frame_active(str(raw_input or effective_input)):
                return None
        except Exception:
            pass
        # Same law for the software-authoring register: "Build a polished single-file HTML
        # application ... - timeline - filters - summary panel" is ONE build task whose bullets
        # are the artifact's spec. Measured live 2026-09-17: the demand seam carved such a
        # request into six units ("Make it genuinely usable" was one of them), several
        # "servable" units were claimed by lookup lanes, and the turn answered as a merge of
        # fragments instead of the one coherent artifact the user asked for.
        try:
            from core.agent_runtime.grounded_mode import is_software_authoring_request

            if is_software_authoring_request(str(raw_input or effective_input)):
                return None
        except Exception:
            pass
        # --------------------------------------------------------------------
        # PHASE 1 — PRE-DISPATCH ELIGIBILITY. Nothing here may leave side
        # effects: no unit has been handed to a lane, so declining is free.
        # --------------------------------------------------------------------
        try:
            from core.agent_runtime.demand_ownership import (
                demand_coverage,
                units_as_plan,
            )

            demand_text = str(raw_input or effective_input)
            if _one_model_call_already_answers_every_part(demand_text):
                return None
            coverage = demand_coverage(demand_text)
            if not coverage.mixed:
                return None
            tasks = units_as_plan(demand_text)
            if len(tasks) < 2:
                return None
        except Exception:
            # A classification/probe failure before any dispatch: decline clean.
            return None

        # ------------------------------------------------------------------
        # P0 POLICY CONSERVATION — parent authority ∩ child capability, per
        # unit, BEFORE any dispatch. The parent's prohibitions are frozen on
        # the canonical request the context carries (minted at ingress from
        # the WHOLE user text); a unit whose serving capability consumes a
        # frozen family is never handed to its lane — its demand ends typed
        # REFUSED while the units the parent did not freeze execute
        # normally. This is the seam that closes the measured defect: the
        # child slice "Tell me the current ETH price." re-parsed its own
        # clean text and fetched CoinGecko under a "No web." parent.
        # ------------------------------------------------------------------
        try:
            from core.agent_runtime.demand_ownership import conserved_unit_refusals

            conserved_refusals = conserved_unit_refusals(coverage, source_context)
        except Exception:
            conserved_refusals = {}

        # Lanes-propose, kernel-decides: a lane whose probe reads SOME units of a
        # mixed turn records that unit-scoped claim explicitly (it is true — the
        # sub-turn below will be served by that lane) and is marked ineligible to
        # FINALIZE the external turn, which belongs to this plan, not to one lane.
        # Accounting is still pre-dispatch and still fail-soft.
        # P0 MIXED-DEMAND — recorded for EVERY coverage-declaring lane in the
        # active catalog, not for one hardcoded family. At base only the
        # live-data lane's partial claim was written down, so a mixed turn made
        # of any other pair of families produced an accounting record naming no
        # claimant at all — the registry knew who owned each unit and the audit
        # trail did not. The catalog is the list; there is no list here.
        try:
            from core.agent_runtime.demand_ownership import is_coverage_capability
            from core.lane_registry import active_catalog
            from core.turn_contract import LaneProposal

            # ONE proposal per coverage CAPABILITY, not per lane, and it names
            # the family's PLAN-SERVING lane.
            #
            # The catalog deliberately declares several lanes against one
            # capability — the two live lanes are "the live family, one
            # capability" (LaneSpec docstring), as are the two currency lanes.
            # A per-lane loop therefore makes a family CONTEST ITSELF: the second
            # lane's identical claim is mediated as superseded by the first, and
            # the family's own claim lands as a refusal (measured — it broke the
            # R1e two-live-demands case, whose weather units then went unserved).
            #
            # Which lane of a family owns the unit HERE is not a coin toss: the
            # earlier lanes of a family are its whole-turn fast paths, which have
            # already declined by the time this composite seam runs, so the unit
            # is served by the family's LAST lane — the one that serves inside a
            # plan. That is exactly what `demand_ownership`'s own canonical
            # constants (LANE_LIVE_DATA / LANE_CURRENCY) name, and this
            # derivation reproduces both from the catalog rather than repeating
            # any lane-id literal here (`test_the_route_metadata_comes_from_the
            # _proposal` pins that those literals have ONE home, and it is not
            # this module).
            plan_lane_for: dict[str, str] = {}
            for spec in active_catalog():
                if is_coverage_capability(spec.coverage):
                    plan_lane_for[spec.coverage] = spec.lane_id
            for lane_id in plan_lane_for.values():
                claimed = coverage.lane_unit_ids(lane_id)
                if not claimed or len(claimed) >= coverage.unit_count:
                    continue
                self._record_lane_proposal(
                    source_context,
                    LaneProposal(
                        lane_id=lane_id,
                        obligations_claimed=tuple(claimed),
                        unclaimed_obligations=tuple(
                            unit_id
                            for (unit_id, _text) in coverage.units
                            if unit_id not in claimed
                        ),
                        terminal_eligibility="ineligible",
                    ),
                )
        except Exception:
            pass

        # --------------------------------------------------------------------
        # PHASE 2 — POST-DISPATCH EXECUTION. From the first child dispatch the
        # external turn BELONGS to this plan; every exit below returns a
        # result. Returning None here would hand the request back to the
        # cascade to be executed AGAIN (repeated model calls, tools, effects).
        # --------------------------------------------------------------------
        from core.agent_runtime.turn_planner import run_plan
        from core.agent_runtime.turn_planner_hook import build_planner_run_one

        outcomes: list[Any] = []
        try:
            outcomes = run_plan(
                [task for task in tasks if task.index not in conserved_refusals],
                run_one=build_planner_run_one(
                    self, session_id=session_id, source_context=source_context
                ),
                # Sequential, deliberately — the planned-turn precedent: a sub-turn is
                # a whole turn with its own generation, and fanning them at one local
                # model produced read timeouts (measured 2026-08-05, see
                # `_maybe_answer_planned_turn`).
                max_workers=1,
            )
        except Exception:
            # A pool-level fault after units began dispatching is terminal, not a
            # fallback: the outcomes list stays as partial truth and the summary
            # below names every unit that never produced one.
            outcomes = []
        # The conserved refusals join the outcome list as typed refused rows —
        # never dispatched, never retried, named in the merged answer below.
        from core.agent_runtime.turn_planner import TaskOutcome as _RefusedOutcome

        for refused_index, decision in conserved_refusals.items():
            outcomes.append(
                _RefusedOutcome(
                    task=tasks[refused_index],
                    error=str(decision.refusal_text or "refused by parent constraint"),
                    outcome_kind="refused",
                )
            )

        # R1g I5 — PARITY AT THE OWNER. `run_plan` enforces one outcome per task
        # itself; this seam enforces it AGAIN against whatever it was handed (a
        # patched, replaced or future-broken run_plan), because ownership of the
        # plan is HERE: a task whose outcome never arrived is named as an
        # explicit failure, never silently dropped from the synthesis.
        from core.agent_runtime.turn_planner import TaskOutcome as _TaskOutcome

        _by_index = {
            getattr(getattr(outcome, "task", None), "index", None): outcome
            for outcome in outcomes
        }
        outcomes = [
            _by_index.get(
                task.index,
                _TaskOutcome(task=task, error="no outcome recorded for this request"),
            )
            for task in tasks
        ]

        # P0 MIXED-DEMAND — the per-demand ledger, written from the CONSERVED
        # outcome list above (one row per task, missing outcomes already
        # materialized as explicit failures). Every demand carries its stable
        # identity, the capability the registry selected for it, whether an
        # execution was attempted, and its typed terminal state — so "three
        # demands were counted" can never again stand in for "three demands
        # ran". Accounting may never break the turn it accounts for.
        try:
            from core.agent_runtime.demand_ownership import demand_records
            from core.turn_contract import TURN_DEMAND_EXECUTORS_KEY

            _demand_ledger = demand_records(
                str(raw_input or effective_input),
                outcomes,
                # The executor facts the sub-turns stamped on themselves. The seam
                # never infers who ran a demand — it reads what ran.
                (source_context or {}).get(TURN_DEMAND_EXECUTORS_KEY),
                # The conservation seam's per-unit refusals: refused demands end
                # DEMAND_REFUSED carrying the parent's reason codes.
                conserved_refusals,
            )
            self._record_demand_ledger(source_context, _demand_ledger)
            self._record_demand_discharge(source_context, _demand_ledger, outcomes)
        except Exception:
            pass

        # P0 MIXED-DEMAND TERMINAL CLOSURE — the authorship publication gate
        # reads this turn's record, and everything on it predates the first
        # unit: a pre-generation block decided before any lane ran reads as
        # "the lane came back empty" and refuses the composed reply wholesale.
        # What the turn ACTUALLY holds after dispatch is one minted row per
        # executed unit — the clock reading, the conversion attempt, the typed
        # refusals — and those rows are exactly the support the gate's
        # per-claim adjudicator reads. Recorded raise-only and claiming no
        # author: the question for backed composed bytes is per-claim backing,
        # decided at publication against these rows, never here.
        try:
            from core.final_answer_authorship import record_runtime_support

            _support_rows = [
                {
                    "summary": str(getattr(outcome, "answer", "") or ""),
                    "intent": f"demand_unit:{getattr(outcome.task, 'request', '')[:60]}",
                    "source": "demand_owned_mixed_turn",
                }
                for outcome in outcomes
                if getattr(outcome, "ok", False)
                and str(getattr(outcome, "answer", "") or "").strip()
            ]
            record_runtime_support(
                source_context,
                support_rows=_support_rows,
                request_text=str(raw_input or effective_input),
            )
        except Exception:
            pass

        merged = ""
        try:
            from core.agent_runtime.turn_planner import merge_outcomes

            merged = merge_outcomes(outcomes)
        except Exception:
            # The merge is presentation, never ownership: synthesize from the
            # outcomes directly so a merge fault cannot discard the children's work.
            merged = _degraded_unit_synthesis(outcomes)

        # R1g I7 — MERGE HONESTY. Finalization may not publish ordinary success
        # while any demand's outcome is missing from the merge: every satisfied
        # outcome's answer and every unsatisfied outcome's request must appear
        # in the merged text. A merge that lost one (a sabotage, a future
        # refactor) is replaced by the degraded synthesis, which composes from
        # the outcomes directly and cannot lose a block.
        def _merge_names_every_outcome(candidate: str) -> bool:
            for outcome in outcomes:
                if outcome.ok:
                    answer = str(outcome.answer or "").strip()
                    if answer and answer not in candidate:
                        return False
                else:
                    request = str(getattr(outcome.task, "request", "") or "").strip()
                    if request and request not in candidate:
                        return False
            return True

        if not _merge_names_every_outcome(merged):
            merged = _degraded_unit_synthesis(outcomes)

        if any(outcome.ok for outcome in outcomes):
            response = merged.strip() or _degraded_unit_synthesis(outcomes)
            # Every ok outcome's answer is in `response` by construction: the merge-honesty check
            # above replaces a merge that lost one with the degraded synthesis, which composes
            # from the outcomes directly.
            _record_planned_turn_demand_receipts(outcomes, request=str(effective_input or ""))
            # EXECUTION TRUTH FOR THE PARENT TURN. Each demand unit ran as its own
            # sub-turn with its own turn id, so the facts they recorded (and the
            # receipts they filed) key to THOSE turns — the parent's completion-claim
            # guards read THIS turn's records and would otherwise see a turn that
            # verifiably executed a note write or a calendar read as though nothing
            # ran. ONE GOVERNING TEST for which units may carry a receipt: the
            # sub-turn's OWN route_reason must name an EXECUTED effect (for the
            # operator lane: operator_action_executed). An `ok` outcome alone is an
            # ANSWER, and answers, previews and pending approvals are not effect
            # evidence — recasting them as receipts would fabricate exactly the
            # backing the honesty ledger exists to check. The route is read from what
            # the sub-turn stamped on itself, never inferred from wording.
            try:
                if isinstance(source_context, dict):
                    from core.turn_contract import TURN_DEMAND_EXECUTORS_KEY

                    _executors = source_context.get(TURN_DEMAND_EXECUTORS_KEY)
                    _receipts = list(source_context.get("tool_receipts") or [])
                    for _index, _outcome in enumerate(outcomes):
                        if not getattr(_outcome, "ok", False):
                            continue
                        _route = ""
                        _route_reason = ""
                        if isinstance(_executors, dict):
                            _stamped = _executors.get(_index) or {}
                            _route = str(_stamped.get("route") or "")
                            _route_reason = str(_stamped.get("route_reason") or "")
                        if not _route_reason.endswith(("_executed", "tool_executed")):
                            continue
                        _receipts.append(
                            {
                                "tool": _route or f"demand_unit:{_index}",
                                "receipt_key": f"demand:{_index}",
                                "status": "executed",
                                "execution": {"executed": True, "status": "executed"},
                            }
                        )
                    source_context["tool_receipts"] = _receipts
            except Exception:
                pass
            try:
                # THE PARENT'S ROUTE TELLS THE TRUTH OF ITS UNITS. A mixed turn whose demand
                # unit ended at the operator lane's approval boundary ('this will delete ...
                # reply "yes, delete ..."') is an ASK, not a deterministic answer: the lane
                # asked and nothing ran. Reproduced by the lane's own polarity suite
                # (paraphrase/refused/deferred withholdings at d89cc833): the answer was the
                # ask, but the parent stamped `deterministic:demand_owned_mixed_turn`, hiding
                # the approval boundary from everything that reads the route. The route is
                # read from what the sub-turns STAMPED on themselves -- never re-derived from
                # the wording here.
                _turn_reason = "demand_owned_mixed_turn"
                _turn_prefix = "deterministic"
                try:
                    if isinstance(source_context, dict):
                        from core.turn_contract import TURN_DEMAND_EXECUTORS_KEY

                        _stamped_routes = [
                            str((row or {}).get("route_reason") or "")
                            for row in (source_context.get(TURN_DEMAND_EXECUTORS_KEY) or {}).values()
                            if isinstance(row, dict)
                        ]
                        _operator_routes = [r for r in _stamped_routes if r.startswith("operator_action_")]
                        if _operator_routes and all(r.endswith("approval_required") for r in _operator_routes):
                            _turn_reason = _operator_routes[0]
                            _turn_prefix = "action"
                except Exception:
                    pass
                return self._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=response,
                    confidence=0.86,
                    source_context=source_context,
                    reason=_turn_reason,
                    route_prefix=_turn_prefix,
                )
            except Exception:
                # Finalization faulted AFTER children ran. The outcomes are the
                # truth this turn has: ship them through the minimal typed
                # envelope instead of handing the turn back to the cascade.
                return {
                    "ok": True,
                    "response": response,
                    "confidence": 0.5,
                    "reason": "demand_owned_mixed_turn_degraded",
                }

        # Every unit failed. ONE deterministic typed failure summary naming every
        # unmet unit; the external request is not retried through another lane.
        summary = _unmet_unit_summary(tasks, outcomes)
        try:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=summary,
                confidence=0.6,
                source_context=source_context,
                reason="demand_owned_mixed_turn_failed",
                failure_text="all demand units failed",
            )
        except Exception:
            return {
                "ok": False,
                "response": summary,
                "confidence": 0.5,
                "reason": "demand_owned_mixed_turn_failed",
            }

    def _answer_secret_intake_turn(
        self,
        *,
        intake,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict:
        """R1g amendment — the secret intake's composite executor.

        The credential outcome (already computed by its handler on the
        command ALONE) and the sanitized remainder's demand units are owned
        outcomes of ONE canonical turn: each remainder unit runs as a real
        sub-turn under the parent's TurnRequest (the R1d/R1e machinery — chain,
        slots, cumulative evidence, no duplicate user rows), `merge_outcomes`
        composes the children truthfully (parity enforced by `run_plan`, the
        same two-level law as the demand-owned seam), and the credential reply
        is one more block of the same answer.

        NEVER returns None: the credential handler has already run (store or
        refusal side effects happened), so ownership is irreversible — a fault
        anywhere below degrades to a typed envelope that still carries the
        credential reply and names every unmet unit. The secret bytes exist
        only inside `intake` (consumed spans); the remainder is the only text
        that reaches children, models, receipts or events."""
        from core.agent_runtime.demand_ownership import units_as_plan
        from core.agent_runtime.turn_planner import (
            TaskOutcome,
            merge_outcomes,
            run_plan,
        )
        from core.agent_runtime.turn_planner_hook import build_planner_run_one

        child_source_context = dict(source_context or {})
        try:
            from dataclasses import replace as _dc_replace

            carried = child_source_context.get(TURN_REQUEST_KEY)
            if carried is not None:
                # The children share the parent's CANONICAL turn id; the copy
                # they carry holds the sanitized remainder, so no model call,
                # receipt or event below can ever see the consumed bytes.
                child_source_context[TURN_REQUEST_KEY] = _dc_replace(
                    carried, user_text=intake.remainder
                )
        except Exception:
            pass
        tasks = []
        try:
            tasks = units_as_plan(intake.remainder)
        except Exception:
            tasks = []
        # P0 POLICY CONSERVATION — the remainder's demand units run as children
        # of a turn whose ORIGINAL text (consumed secret spans included) is what
        # the canonical request froze its prohibitions from; the sanitized copy
        # above carries that frozen set unchanged. Refused units end typed
        # REFUSED without dispatch, exactly as the demand-owned seam does.
        secret_refusals: dict[int, Any] = {}
        try:
            from core.agent_runtime.demand_ownership import (
                conserved_unit_refusals,
                demand_coverage,
            )

            _secret_coverage = demand_coverage(intake.remainder)
            secret_refusals = conserved_unit_refusals(
                _secret_coverage, child_source_context
            )
        except Exception:
            secret_refusals = {}
        outcomes = []
        try:
            if tasks:
                outcomes = run_plan(
                    [task for task in tasks if task.index not in secret_refusals],
                    run_one=build_planner_run_one(
                        self, session_id=session_id, source_context=child_source_context
                    ),
                    # Sequential, deliberately — the demand-owned seam's own
                    # precedent: a sub-turn is a whole turn with its own
                    # generation; fanning them at one local model measured
                    # read timeouts (2026-08-05).
                    max_workers=1,
                )
        except Exception:
            outcomes = []
        for refused_index, decision in secret_refusals.items():
            outcomes.append(
                TaskOutcome(
                    task=tasks[refused_index],
                    error=str(decision.refusal_text or "refused by parent constraint"),
                    outcome_kind="refused",
                )
            )
        # Outcome parity at the owner (I5): a task whose outcome never arrived
        # is materialized as an explicit failure naming it, never dropped.
        by_index = {
            getattr(getattr(outcome, "task", None), "index", None): outcome
            for outcome in outcomes
        }
        outcomes = [
            by_index.get(
                task.index,
                TaskOutcome(task=task, error="no outcome recorded for this request"),
            )
            for task in tasks
        ]
        try:
            merged_children = merge_outcomes(outcomes)
        except Exception:
            merged_children = _degraded_unit_synthesis(outcomes)
        response = "\n\n".join(
            part for part in (intake.reply.strip(), merged_children.strip()) if part
        )
        try:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.9,
                source_context=source_context,
                reason="secret_intake_turn",
            )
        except Exception:
            return {
                "ok": True,
                "response": response,
                "confidence": 0.5,
                "reason": "secret_intake_turn_degraded",
            }

    def _maybe_answer_live_data_turn(
        self,
        *,
        effective_input: str,
        raw_input: str = "",
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """Answer a live-data (price/weather) turn from a typed, per-entity plan, or None.

        None is the common path for anything `requirements_for()` does not classify as LIVE_DATA,
        and for anything that fails at any step below -- this can only ADD a deterministic answer
        path, never subtract from what the general planner and existing lanes already do.

        Every entity in the plan comes from the same recognizers the deterministic fetch lanes
        already trust (see `core.agent_runtime.live_data_plan`), so the full user prompt can never
        become a city or ticker here -- there is no step where free text is handed to a tool
        argument. Each subtask's approval is evaluated live against the real permission gate
        (`evaluate_approval_policy`), so a subtask that needs approval is preserved in its own
        table row as unavailable with its real reason, never silently merged as a false success.

        `raw_input` (the turn's ORIGINAL text, before `core.input_normalizer`'s unconditional
        `re.sub(r"\\s+", " ", ...)` collapses every newline) is what actually reaches entity
        extraction below -- `effective_input` still drives classification and everything else.
        Entity-contamination incident (2026-08-07): `_extract_weather_locations`/
        `_extract_market_entity_candidates` are bounded to paragraphs and lines specifically so a
        later, unrelated sentence can never be scanned as part of an earlier request's entity list
        -- but `effective_input` has ALREADY had every paragraph/line boundary erased by the time
        it reaches this function, so that structural fix did nothing against the real `/api/chat`
        path until entity extraction was given text that still has its boundaries intact.
        """
        # ARCH-TRUTH-R1d: `planned_subturn` blocks RECURSIVE PLANNING (see
        # `_maybe_answer_planned_turn` and the conductor), never an execution lane. This
        # lane used to refuse a planned sub-request outright, so the planner admitted a
        # request as servable and the runtime then declined it — the sub-turn fell through
        # to "Live web lookup is disabled on this runtime" for a lookup the same message
        # gets served when it arrives whole. A sub-request is one request by construction,
        # which is exactly what this lane wants.
        if dict((source_context or {}).get("raw_output_contract") or {}).get(
            "structured_labels"
        ):
            # The trailing scaffold is a response-binding contract, not three independent turns.
            # Keep the untouched batch together for one semantic model call; splitting it here
            # loses the shared quiz instruction and lets per-row constraints become global.
            return None

        raw_input = raw_input or effective_input

        from core.execution_requirements import requirements_for

        try:
            # `source_context` is what lets a bare continuation ("and?", "well?") be recognised as
            # the live request it re-asks. Without it this gate reads the current text alone, the
            # turn falls through to the model with no evidence, and the model answers anyway --
            # measured: a denial that it could reach the network at all, an invented 9 C where the
            # fetch had said 10 C, and a citation of wttr.in on a turn with web_calls=0.
            requirements = requirements_for(effective_input, source_context=source_context)
        except Exception:
            return None
        if requirements.answer_mode != "LIVE_DATA":
            return None

        # R1e — DEMAND OWNERSHIP, enforced in the lane itself so no call order can
        # reintroduce the whole-turn claim: this lane may END the external turn only
        # when it accounts for every demand unit of it. A whole-text recognizer
        # reading "weather" in one clause of a multi-unit message (the classification
        # above) is evidence about THAT clause, not a claim on the turn: measured at
        # base, "Explain how a hash table works and tell me the weather in Kaunas"
        # was answered with the weather line alone. A sub-turn is one unit by
        # construction, so the gate is free there.
        try:
            from core.agent_runtime.demand_ownership import (
                LANE_LIVE_DATA,
                lane_may_claim_whole_turn,
            )

            if not lane_may_claim_whole_turn(
                str(raw_input or effective_input), LANE_LIVE_DATA
            ):
                return None
        except Exception:
            pass

        # Checkpoint 6.5 fail-closed invariant, scoped to MULTIPART LIVE_DATA turns specifically.
        # Found live: "Market data only for Bitcoin and gold." (two assets) classified LIVE_DATA
        # here but `build_live_data_plan()` independently found nothing to build (a keyword-gated
        # extraction path disagreeing with the classifier's own), so this used to `return None` and
        # let the ordinary turn free-form a response -- the model fabricated a Bitcoin price with
        # no real data behind it.
        #
        # A SINGLE-entity LIVE_DATA turn ("Brent crude price now?", "solana price?") is
        # deliberately NOT covered by this guard: `core.agent_runtime.turn_frontdoor` already has
        # its own purpose-built, already-tested honest-degrade mechanism for exactly that shape
        # (`_unresolved_price_lookup_response` / `_live_info_failure_text`, gated by
        # `_looks_like_grounded_price_lookup`), reached via `_try_live_quote_notes` and the
        # structured weather lookup -- fetch paths distinct from this module's own. Widening this
        # guard to single-entity turns duplicated that mechanism with a DIFFERENT fetch path
        # underneath it and pre-empted it outright, which silently broke it (found by running the
        # full suite: `test_brent_quote_fast_path_returns_grounded_structured_answer`,
        # `test_weather_live_lookup_uses_structured_weather_wording`,
        # `test_openclaw_direct_crypto_price_request_degrades_honestly_when_quote_is_ungrounded`,
        # and the alpha-hardening London-weather case all regressed). The typed multipart plan
        # exists specifically because the single-asset fast path cannot represent "N assets, M
        # cities" as separate results (see the module docstring in `live_data_plan.py`) -- so this
        # guard's job is to close the fail-open gap for exactly the shape only IT can attempt,
        # never to re-decide what a single-entity turn's failure mode should be.
        #
        # `requirements.multipart` alone is not a safe gate here: it is a structural heuristic that
        # can fire on punctuation unrelated to a real entity list. Found live: "some big guy in
        # Solana, Toly or Tolly, who is he" set `multipart=True` from the comma/"or" shape while
        # naming exactly one real asset (Solana) -- gating on that flag alone pre-empted the fuzzy-
        # entity recovery lane the same way the single-entity cases above did. Count the entities
        # the SAME way the fail-closed fallback message would name them, and require at least two
        # concrete names before treating this as a genuinely multipart request.
        entity_count = self._named_live_data_entity_count(raw_input, requirements.allowed_toolsets)
        if entity_count < 2:
            # Original, unchanged behavior: succeed if the typed plan works, otherwise defer
            # entirely so the frontdoor's own single-entity honest-degrade mechanism gets its turn.
            return self._answer_single_live_data_turn(
                effective_input=effective_input,
                raw_input=raw_input,
                session_id=session_id,
                source_context=source_context,
            )
        return self._answer_multipart_live_data_turn(
            effective_input=effective_input,
            raw_input=raw_input,
            session_id=session_id,
            source_context=source_context,
            requirements=requirements,
        )

    def _named_live_data_entity_count(self, user_text: str, allowed_toolsets: tuple[str, ...]) -> int:
        """Best-effort count of concretely-named market/weather entities in the raw text -- the
        same extraction `render_live_data_plan_unavailable` uses for its fallback message, run once
        up front so the multipart fail-closed guard is scoped to requests that actually name two or
        more entities, not merely ones `requirements_for()` flagged `multipart` for structural
        reasons unrelated to entity count."""
        names: set[str] = set()
        try:
            from core.retrieval_constraints import retrieval_candidate_text

            user_text = retrieval_candidate_text(user_text)
            if "market_prices" in allowed_toolsets:
                from core.agent_runtime.fast_live_info_price import price_assets_named
                from tools.web.web_research import _extract_market_entity_candidates

                names.update(price_assets_named(user_text))
                names.update(_extract_market_entity_candidates(user_text))
            if "weather" in allowed_toolsets:
                from tools.web.web_research import _extract_weather_locations

                names.update(_extract_weather_locations(user_text))
        except Exception:
            return 0
        return len(names)

    # ------------------------------------------------------------------------------------------
    # Step 7: runtime-attempt persistence for LIVE_DATA turns. Every method here is best-effort --
    # a persistence failure never blocks or alters the LIVE_DATA answer path, matching the same
    # "can only ADD, never subtract" contract every other fast-path helper in this class follows.
    # ------------------------------------------------------------------------------------------

    # Per-subtask retry policy keyed by SubtaskLifecycle member NAME (uppercase) -- Step 7
    # correction #6. UNSUPPORTED_ENTITY is deliberately not retryable by default; WAITING_APPROVAL
    # and CANCELLED are resumable/explicit-only, never auto-retried.
    _LIVE_DATA_SUBTASK_RETRY_POLICY: dict[str, tuple[str, bool, str]] = {
        "FAILED": ("transient", True, "transient tool failure"),
        "UNSUPPORTED_ENTITY": ("unsupported", False, "unsupported unless capabilities or the request change"),
        "WAITING_APPROVAL": ("", False, "resumable, not automatically executable"),
        "CANCELLED": ("", False, "not automatically retryable unless explicitly requested"),
        "SUCCEEDED": ("", False, "carry forward; refresh only under an explicit staleness policy"),
    }

    @staticmethod
    def _live_data_subtask_entity_fields(task: Any) -> tuple[str, str]:
        args = dict(task.arguments or {})
        if task.operation == "market_quote":
            return str(args.get("kind") or ""), str(args.get("asset_key") or task.entity)
        if task.operation == "weather_lookup":
            return "city", str(args.get("location") or task.entity)
        if task.operation == "unsupported_market_entity":
            return "unsupported", str(args.get("requested_text") or task.entity)
        return "", str(task.entity or "")

    @staticmethod
    def _canonical_user_turn_id(source_context: dict[str, object] | None) -> str:
        """The real `dialogue_turns.turn_id` this turn was recorded under (Repair 4, Mnemosyne
        review) -- see the assignment site in `_run_once_inner` for why this rides in
        `source_context` rather than a dedicated parameter threaded through every call in the
        chain. Empty only when a caller genuinely bypassed the normal turn-intake path (e.g. a
        test constructing `source_context` directly) -- callers must not invent a substitute."""
        return str((source_context or {}).get("_canonical_user_turn_id") or "").strip()

    @staticmethod
    def _client_turn_id(source_context: dict[str, object] | None) -> str:
        """The CLIENT-sent turn id (`cancel_turn_id`, the same key `emit_runtime_event` reads) --
        the id the chat page groups the Activity ledger by. Distinct from
        `_canonical_user_turn_id` (the dialogue-turn row id): the two id spaces must not be
        blended, and neither may be invented when absent."""
        return str((source_context or {}).get("cancel_turn_id") or "").strip()

    @staticmethod
    def _turn_root_attempt_id(source_context: dict[str, object] | None) -> str:
        """The turn's chain root — the attempt the turn door minted and published as this
        turn's execution identity (ARCH-TRUTH-R1c). Empty only for a caller that reached a
        lane without passing through `run_once`'s door; such a row then starts its own
        chain exactly as it did before, rather than being attached to a root nobody opened.
        """
        identity = (source_context or {}).get("_execution_identity")
        if not isinstance(identity, dict):
            return ""
        return str(identity.get("attempt_id") or "").strip()

    def _create_live_data_runtime_attempt(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        effective_input: str,
        answer_mode: str,
    ) -> str:
        try:
            from core.runtime_continuity import AttemptRole, create_runtime_attempt

            turn_id = self._canonical_user_turn_id(source_context)
            # ARCH-TRUTH-R1c: this row is the turn's ANSWER, not a turn of its own. It
            # joins the chain the turn door opened (same root, therefore the same L0
            # fence and the same execution identity) and says so in its role, so a
            # referential follow-up can find it by structure. `parent_attempt_id` stays
            # empty deliberately: a parent means RETRY in this table (idempotency key +
            # a generation allocated by the fence CAS), and the turn's answer is
            # generation 1 of the chain, not a second attempt at it.
            attempt = create_runtime_attempt(
                session_id=session_id,
                checkpoint_id=self._runtime_checkpoint_id(source_context),
                original_request=effective_input,
                answer_mode=answer_mode,
                root_attempt_id=self._turn_root_attempt_id(source_context),
                role=(
                    AttemptRole.PLANNER_TASK.value
                    if bool((source_context or {}).get("planned_subturn"))
                    else AttemptRole.ANSWER.value
                ),
                # ARCH-TRUTH-R1d: a planned sub-request executes its own slot of the
                # chain; the turn's single answer executes the chain's ordinary slot ('').
                execution_slot=str((source_context or {}).get("planned_task_slot") or ""),
                # First execution: origin and trigger are the SAME canonical turn (Repair 4's
                # spec). No separate "visible conversation event id" concept exists in this
                # codebase yet (the conversation log line for this turn is written only AFTER the
                # response is computed, so it cannot be referenced yet at attempt-creation time) --
                # the dialogue turn id IS the canonical per-turn identity, reused here rather than
                # inventing a second, parallel id space.
                origin_user_turn_id=turn_id,
                trigger_user_turn_id=turn_id,
                origin_conversation_event_id=turn_id,
                # Rides only on the emitted Activity event (not the attempt row), so the chat
                # page can file "Runtime attempt created" inside its turn instead of "Between
                # turns".
                client_turn_id=self._client_turn_id(source_context),
            )
            return str(attempt.get("attempt_id") or "")
        except Exception:
            return ""

    def _agent_node_emitter(self, session_id: str, source_context: dict[str, object] | None = None):
        """A per-turn emitter putting node lifecycle on the durable stream the UI polls.

        Shared by BOTH concurrent lanes. The conductor is deliberately the rare one -- it only
        claims a turn no single lane can serve -- so wiring only it would leave the Agents panel
        blank for the common case, which is exactly what a live drive showed on 2026-08-14:
        a two-city turn produced `live_data_plan_completed` and zero node events.

        Called from worker threads, so it must be safe concurrently: `append_runtime_event` is a
        single INSERT holding no cross-call state. Both runners swallow anything raised here, so a
        write failure costs observability and never the turn.

        `source_context` carries the client turn id (`cancel_turn_id`, the same key
        `emit_runtime_event` stamps). This emitter writes through `append_runtime_event`
        directly -- below that stamping seam -- so it has to carry the tag itself, captured ONCE
        at emitter creation (on the turn's own thread) rather than read per-emit from a dict a
        worker thread might see mid-mutation. Without it every node row lands untagged and the
        chat page files it under "Between turns" instead of inside its turn (observed live
        2026-08-14, session openclaw:ed890df3, seqs 15-16/79-82).
        """
        client_turn_id = str((source_context or {}).get("cancel_turn_id") or "").strip()
        # The canonical turn identity, captured on the turn's own thread for the same reason
        # `client_turn_id` is: this emitter writes BELOW the seam that stamps it, so it must carry
        # the value rather than let the seam resolve it. Without this the node rows are the only
        # events in a turn with no canonical identity, and a reader joining execution facts to
        # lifecycle rows silently loses the subtasks -- the same fragmentation one layer down.
        try:
            from core.execution_truth import resolve_turn_key

            node_turn_key = resolve_turn_key(source_context, None)
        except Exception:
            node_turn_key = ""

        def emit(event_type: str, detail: dict) -> None:
            node_id = str(detail.get("node_id") or "?")
            if event_type == "agent_node_started":
                message = f"{node_id} started ({detail.get('operation') or 'node'})"
            else:
                took = detail.get("duration_s")
                took_text = f" in {float(took):.1f}s" if isinstance(took, (int, float)) else ""
                message = f"{node_id} {detail.get('state') or 'finished'}{took_text}"
            details = dict(detail)
            if client_turn_id:
                # setdefault: additive, never overrides a tag a runner already put on its event.
                details.setdefault("client_turn_id", client_turn_id)
            if node_turn_key:
                details.setdefault("turn_key", node_turn_key)
            # Route through the STREAMING emitter, not the ledger-only append: it performs the
            # same durable `append_runtime_event` internally AND pushes the event onto the live
            # `vool_event` channel. Ledger-only was why a turn that really did fetch live data
            # reached the client as task.started -> task.completed with nothing between, so the
            # Companion's typed activity language (117 phrases / 15 categories) had no events to
            # narrate and sat on IDLE while real work happened (measured live 2026-08-29).
            self._emit_runtime_event(
                source_context,
                event_type=event_type,
                message=message,
                **details,
            )

        return emit

    def _record_live_data_plan_subtasks(
        self, *, runtime_attempt_id: str, plan_id: str, plan: Any, client_turn_id: str = "",
    ) -> None:
        """PLANNED rows for every subtask, written before any of them execute -- so a crash mid-
        run leaves a durable record of what was planned, not just what happened to finish."""
        if not runtime_attempt_id:
            return
        from core.runtime_continuity import (
            AttemptClaimRefused,
            claim_runtime_attempt,
            get_runtime_attempt,
            update_runtime_attempt,
            upsert_runtime_attempt_subtask,
        )

        # Best-effort bookkeeping: subtask PLANNED rows.
        try:
            update_runtime_attempt(
                runtime_attempt_id, plan_id=plan_id, lifecycle_state="PLANNED", client_turn_id=client_turn_id,
            )
            for task in plan.subtasks:
                entity_type, entity_key = self._live_data_subtask_entity_fields(task)
                upsert_runtime_attempt_subtask(
                    attempt_id=runtime_attempt_id,
                    subtask_id=task.subtask_id,
                    plan_id=plan_id,
                    operation=task.operation,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    arguments=dict(task.arguments or {}),
                    lifecycle_state="PLANNED",
                )
        except Exception:
            pass
        # H-1/INV-2: the claim to RUNNING is a CAS whose refusal ABORTS the
        # executor — surfaced as a typed refusal + event, never swallowed.
        try:
            claim_runtime_attempt(runtime_attempt_id, to_state="RUNNING")
        except AttemptClaimRefused as claim_exc:
            current = get_runtime_attempt(runtime_attempt_id) or {}
            session_id_ref = str(current.get("session_id") or "")
            if session_id_ref:
                with contextlib.suppress(Exception):
                    append_runtime_event(
                        session_id=session_id_ref,
                        event_type="attempt_claim_refused",
                        message=(
                            f"Execution claim refused for attempt {runtime_attempt_id}: "
                            f"{claim_exc}. This executor aborts."
                        ),
                        details={"attempt_id": str(runtime_attempt_id), "status": "claim_refused"},
                    )
            raise

    def _remember_live_data_obligation(
        self, *, session_id: str, plan: Any, outcomes: list[Any], raw_input: str, source_context: Any
    ) -> None:
        """Record what this turn actually grounded, so the next turn's follow-up has a subject.

        `plan.original_request` rather than `raw_input` is the request text, because on a turn that
        was ITSELF a continuation the two differ -- "What about Tallinn?" resolves to "Get weather
        for Tallinn.", and the resolved form is the one a further follow-up has to rebind. The raw
        text is kept separately, as the turn this obligation absorbed, so the walk-back can tell it
        from an unrelated request that interrupted the thread.

        Best-effort by construction: a failure to record state about a turn must never fail the turn.
        """
        try:
            from core.runtime_continuity import remember_live_data_obligation

            grounded: dict[str, list[str]] = {}
            for outcome in list(outcomes or ()):
                if not getattr(outcome, "ok", False):
                    continue
                subtask = getattr(outcome, "subtask", None)
                operation = str(getattr(subtask, "operation", "") or "")
                entity = str(getattr(subtask, "entity", "") or "").strip()
                if not operation or not entity:
                    continue
                grounded.setdefault(operation, []).append(entity)
            if not grounded:
                return
            # One obligation per session. A turn that grounded two operations at once has no single
            # subject a follow-up could rebind, so the larger one wins and a tie takes the first --
            # deterministic, and never a merged record that claims a shape the turn did not have.
            operation, slots = max(grounded.items(), key=lambda item: len(item[1]))
            remember_live_data_obligation(
                session_id,
                operation=operation,
                slots=slots,
                request_text=str(getattr(plan, "original_request", "") or raw_input),
                absorbed_text=str(raw_input or ""),
                source_turn_id=self._client_turn_id(source_context),
            )
        except Exception:
            return

    def _record_live_data_outcomes(self, *, runtime_attempt_id: str, plan_id: str, outcomes: list[Any]) -> None:
        """One row update per subtask outcome, after execution -- each outcome touches only its
        OWN row (Step 7 correction #1), so concurrent subtasks completing in any order never race."""
        if not runtime_attempt_id:
            return
        try:
            from core.runtime_continuity import upsert_runtime_attempt_subtask

            for outcome in outcomes:
                subtask = outcome.subtask
                state_name = outcome.state.name
                entity_type, entity_key = self._live_data_subtask_entity_fields(subtask)
                failure_class, retryable, retry_reason = self._LIVE_DATA_SUBTASK_RETRY_POLICY.get(
                    state_name, ("", False, ""),
                )
                result = dict(outcome.result or {})
                retrieved_at = str(result.get("retrieved_at") or result.get("observed_at") or "") or None
                upsert_runtime_attempt_subtask(
                    attempt_id=runtime_attempt_id,
                    subtask_id=subtask.subtask_id,
                    plan_id=plan_id,
                    operation=subtask.operation,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    arguments=dict(subtask.arguments or {}),
                    lifecycle_state=state_name,
                    result_summary=result,
                    failure_class=failure_class,
                    failure_reason=str(outcome.failure_reason or ""),
                    approval_state=str(outcome.approval_decision or ""),
                    retryable=retryable,
                    retry_reason=retry_reason,
                    queued_at=outcome.queued_at_iso or None,
                    started_at=outcome.started_at_iso or None,
                    completed_at=outcome.completed_at_iso or None,
                    retrieved_at=retrieved_at,
                )
        except Exception:
            pass

    def _finalize_live_data_runtime_attempt(
        self, runtime_attempt_id: str, *, plan_valid: bool = True, client_turn_id: str = "",
    ) -> dict | None:
        if not runtime_attempt_id:
            return None
        try:
            from core.runtime_continuity import finalize_runtime_attempt

            return finalize_runtime_attempt(runtime_attempt_id, plan_valid=plan_valid, client_turn_id=client_turn_id)
        except Exception:
            return None

    @staticmethod
    def _attempt_failure_text(finalized: dict | None) -> str:
        """The checkpoint's `failure_text` for this turn -- empty only when the attempt fully
        succeeded. Fixes the exact gap Step 7 was built to close: a checkpoint that finalized
        `completed` with an empty `failure_text` even though a subtask genuinely failed."""
        if not finalized:
            return ""
        state = str(finalized.get("lifecycle_state") or "")
        if state == "SUCCEEDED":
            return ""
        return str(finalized.get("terminal_reason") or state)

    @staticmethod
    def _live_data_fulfillment_outcome(finalized: dict | None) -> dict | None:
        """The attempt store's verdict, carried onto the result so the turn trace cannot derive
        FULFILLED from non-empty prose while the same turn's attempt says PARTIAL_SUCCESS
        (measured at b7857c6c). `terminal_fulfillment_outcome` reads an explicit
        `fulfillment_outcome` before every prose-derived fallback; this puts the canonical state
        where the finality authority already looks first."""
        if not finalized:
            return None
        from core.runtime_task_outcome import fulfillment_outcome_from_attempt_lifecycle

        return fulfillment_outcome_from_attempt_lifecycle(
            str(finalized.get("lifecycle_state") or ""),
            terminal_reason=str(finalized.get("terminal_reason") or ""),
        )

    @staticmethod
    def _prepend_unserved_slice_answers(rendered: str, source_context: object) -> str:
        """Compose recorded slice answers the typed plan cannot serve into the reply.

        The coverage contract (core.answer_coverage.record_slice_answer) keeps a
        lane's answer for its OWN clauses when that lane cannot end the turn.
        The typed live-data lane ends the turn WITHOUT a model, and nothing in
        it read the coverage record — measured live: the USD->EUR conversion of
        "1000 usd to eur and then to gold? also btc price and 24 change on eth"
        was computed, recorded, and then dropped when the markets table claimed
        the reply. Families the plan itself serves (market/live-info) are
        excluded so a clause is never answered twice; record order preserves
        the user's clause order ahead of the table.
        """
        if not isinstance(source_context, dict) or not rendered.strip():
            return rendered
        from core.agent_runtime.answer_coverage import (
            COVERAGE_CONTEXT_KEY,
            FAMILY_LIVE_INFO,
            FAMILY_MARKET_QUOTE,
        )

        record = source_context.get(COVERAGE_CONTEXT_KEY)
        if not isinstance(record, dict):
            return rendered
        plan_served_families = {FAMILY_MARKET_QUOTE, FAMILY_LIVE_INFO}
        extras: list[str] = []
        for item in record.get("answers") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("family") or "") in plan_served_families:
                continue
            response = str(item.get("response") or "").strip()
            if response and response not in extras:
                extras.append(response)
        if not extras:
            return rendered
        return "\n\n".join([*extras, rendered])

    @staticmethod
    def _kernel_allows_lane(
        source_context: dict[str, object] | None,
        *,
        lane_id: str,
        unit_ids: tuple[str, ...],
    ) -> tuple[bool, str]:
        """M4 SLICE 3 — CONSULT-BEFORE-SERVE.

        The lane asks the kernel whether it may EXECUTE for these units (not
        just whether its claim may land): `mediate` compares the lane's
        registry rank against the claims already recorded this turn. Returns
        (allowed, superseded_by). Fail-OPEN — a mediation failure may never
        block a lane the cascade already selected; the recorder's mediation
        (slice 2) still refuses superseded CLAIMS, so a fail-open serve still
        cannot double-claim.
        """
        if not isinstance(source_context, dict) or not unit_ids:
            return True, ""
        try:
            from core.lane_registry import mediate
            from core.turn_contract import TURN_PROPOSALS_KEY

            verdict = mediate(
                [
                    item
                    for item in source_context.get(TURN_PROPOSALS_KEY) or []
                    if hasattr(item, "lane_id")
                ],
                lane_id,
                unit_ids,
            )
            if verdict is not None and not verdict.allowed:
                return False, verdict.superseded_by
        except Exception:
            pass
        return True, ""

    @staticmethod
    def _record_demand_discharge(
        source_context: dict[str, object] | None, records: Any, outcomes: Any
    ) -> None:
        """Register this lane's execution in the DISCHARGE CHANNEL the honesty
        sweeps read — the receipt half of doing the work.

        Measured over HTTP at 3ad2ca6c+fix: the composite plan executed all three
        demands and the served answer carried all three results, while the durable
        ledger still read `u1 indeterminate, u2 satisfied, u3 indeterminate` and the
        commit reported `fulfilled_obligations: 1, unresolved_obligations: 2`. The
        finalization sweep had no receipt from this lane, so it fell back to reading
        anchors off the prose — and a file read answers with the FILE'S bytes, which
        contain none of the request's words. A lane that does real work and files no
        receipt gets its truthful result rewritten as unaccounted.

        Two records, because the sweep's evidence ladder distinguishes them:

        * an ANSWER receipt (`slice_answer_record`, unit-grained) for a demand that
          reached `executed` — lane-attested, the top of the ladder;
        * a DISPATCH row for EVERY demand, executed or not. That is what turns a
          failed demand into a provable `unanswered` ("dispatched, failed") instead
          of a vague `indeterminate`, and it is what demotes the attempt from
          SUCCEEDED to PARTIAL_SUCCESS. Filing only the successes would buy a
          quieter ledger by hiding the failures — the exact inversion of the defect
          above.

        Fail-soft: accounting may never break the lane it accounts for.
        """
        if not isinstance(source_context, dict):
            return
        try:
            from core.agent_runtime.answer_coverage import (
                COVERAGE_CONTEXT_KEY,
                record_slice_answer,
            )
            from core.agent_runtime.demand_ownership import (
                DEMAND_EXECUTED,
                DEMAND_REFUSED,
            )

            rows = tuple(records or ())
            by_index = {
                getattr(getattr(outcome, "task", None), "index", None): outcome
                for outcome in tuple(outcomes or ())
            }
            dispatches: list[dict[str, object]] = []
            for index, record in enumerate(rows):
                outcome = by_index.get(index)
                executed = record.terminal_state == DEMAND_EXECUTED
                if executed:
                    record_slice_answer(
                        source_context,
                        text=record.request,
                        family=record.lane_id or "demand_owned_mixed_turn",
                        response=str(getattr(outcome, "answer", "") or ""),
                        reason="demand_owned_mixed_turn",
                        # Every REQUEST unit the execution unit carried: the answer that served a
                        # batched currency turn served each of its questions.
                        consumed_units=tuple(
                            getattr(record, "accounted_unit_ids", None) or (record.demand_id,)
                        ),
                    )
                # P0 policy conservation: a refused demand's dispatch row is
                # REFUSED with the parent's reason codes as its failure
                # reason — the receipt NAMES the prohibition, and no refused
                # demand ever records an answer receipt or a retrieval
                # success above.
                if record.terminal_state == DEMAND_REFUSED:
                    state = "REFUSED"
                    failure_reason = (
                        ";".join(record.refusal_reasons) or record.terminal_state
                    )
                else:
                    state = "SUCCEEDED" if executed else "FAILED"
                    failure_reason = (
                        ""
                        if executed
                        else str(getattr(outcome, "error", "") or record.terminal_state)
                    )
                dispatches.append(
                    {
                        "unit_ids": list(
                            getattr(record, "accounted_unit_ids", None) or (record.demand_id,)
                        ),
                        "subtask_id": f"demand:{record.demand_id}",
                        "operation": record.capability,
                        "state": state,
                        "failure_reason": failure_reason,
                    }
                )
            coverage_record = source_context.get(COVERAGE_CONTEXT_KEY)
            if isinstance(coverage_record, dict) and dispatches:
                existing = list(coverage_record.get("dispatches") or [])
                coverage_record["dispatches"] = existing + dispatches
        except Exception:
            return

    @staticmethod
    def _record_demand_ledger(
        source_context: dict[str, object] | None, records: Any
    ) -> None:
        """Publish the turn's per-demand ledger on the reserved transport key.

        Fail-soft and side-effect free like `_record_lane_proposal`: a turn that
        cannot record its ledger still answers, it just cannot be audited from it.
        """
        if not isinstance(source_context, dict):
            return
        try:
            from dataclasses import asdict

            from core.turn_contract import TURN_DEMAND_LEDGER_KEY

            source_context[TURN_DEMAND_LEDGER_KEY] = [
                asdict(record) for record in tuple(records or ())
            ]
        except Exception:
            return

    @staticmethod
    def _record_lane_proposal(
        source_context: dict[str, object] | None, proposal: Any
    ) -> None:
        """M2 slice 3: a lane records its typed claim/decline on the turn.

        Appends to the reserved TURN_PROPOSALS_KEY transport; the TurnState
        absorbs the list at its construction. Fail-soft like every fast-path
        helper: accounting may never break the lane it accounts for.
        """
        if not isinstance(source_context, dict):
            return
        try:
            from core.lane_registry import mediate
            from core.turn_contract import TURN_PROPOSALS_KEY

            items = list(source_context.get(TURN_PROPOSALS_KEY) or [])
            # M4 SLICE 2 — MEDIATION: the lane consults the kernel at record
            # time. A superseded claim is recorded as the typed refusal the
            # mediator returns (the earlier-ranked lane owns the units); an
            # allowed claim lands as the lane proposed it. Fail-OPEN on a
            # mediation failure — accounting must never block a lane — with
            # the verdict stamped beside the proposal for the audit trail.
            verdict = None
            try:
                verdict = mediate(
                    [item for item in items if hasattr(item, "lane_id")],
                    getattr(proposal, "lane_id", ""),
                    getattr(proposal, "obligations_claimed", ()) or (),
                )
                if verdict is not None and not verdict.allowed:
                    from dataclasses import replace as _dc_replace

                    proposal = _dc_replace(
                        proposal,
                        obligations_claimed=(),
                        refusal_reason=(
                            f"superseded by {verdict.superseded_by} "
                            f"(mediated: {', '.join(verdict.contested)})"
                        ),
                    )
            except Exception:
                verdict = None
            items.append(proposal)
            source_context[TURN_PROPOSALS_KEY] = items
        except Exception:
            pass

    def _record_live_data_discharge(
        self,
        source_context: dict[str, object] | None,
        *,
        raw_text: str,
        plan: Any,
        outcomes: list[Any],
        rendered: str,
        runtime_attempt_id: str,
    ) -> None:
        """THE PRODUCING EDGE (PLAN-discharge-channel.md §4 B3): the serving lane declares what
        it served, so the reconciler no longer has to ask the prose.

        Two records are written into the coverage context this turn carries to its seal:

        `answers`  — via record_slice_answer with consumed=served slice ids (clause space,
        for arbitration) and consumed_units=served DEMAND units (the grain the receipt is
        written against). `consumed`/`consumed_units` are REQUIRED and are NEVER omitted:
        without them the span falls back to coverage_for(text), a request-text inference,
        and a receipt whose span is a text inference is not a receipt. The ids come from
        EXECUTION OUTCOME — succeeded subtasks read off the finalized attempt's own
        dispatch record — never from the plan: a subtask that was planned and failed must
        not contribute, or the receipt would absolve a slot nothing served (the
        'and buy silver.' direction of this defect). The unit binding is the subtask's
        own needle span (B2 interim): one served unit discharges ITSELF, never its
        co-clause siblings — measured live 2026-08-30, one London weather receipt
        absolved the diesel, petrol and Riga-Vilnius demands sharing its clause.

        `dispatches` — every planned subtask with its execution state, succeeded or not,
        bound to its unit ids. This is what makes 'unanswered' provable downstream (B4):
        a unit the record shows was never dispatched may be accused; one that was
        dispatched but failed names its failure; everything else stays indeterminate.

        The consumer at _record_demand_consumption turns both into ledger receipts/records;
        no new plumbing is needed between here and the ledger.
        """
        if not isinstance(source_context, dict):
            return
        from core.agent_runtime.answer_coverage import (
            COVERAGE_CONTEXT_KEY,
            FAMILY_LIVE_INFO,
            FAMILY_MARKET_QUOTE,
            record_slice_answer,
        )

        market_ops = {"market_quote", "unsupported_market_entity"}
        served_unit_ids = self._live_data_served_unit_ids(runtime_attempt_id, plan, outcomes)
        market_units: list[str] = []
        live_units: list[str] = []
        market_slices: list[str] = []
        live_slices: list[str] = []
        for task in plan.subtasks:
            if task.subtask_id not in served_unit_ids:
                continue
            is_market = task.operation in market_ops
            bucket_units = market_units if is_market else live_units
            bucket_slices = market_slices if is_market else live_slices
            for unit_id in task.unit_ids:
                if unit_id not in bucket_units:
                    bucket_units.append(unit_id)
            if task.slice_id and task.slice_id not in bucket_slices:
                bucket_slices.append(task.slice_id)
        for family, unit_ids, slice_ids in (
            (FAMILY_MARKET_QUOTE, market_units, market_slices),
            (FAMILY_LIVE_INFO, live_units, live_slices),
        ):
            # The observation row must exist whenever execution SERVED clauses of this family, even
            # when the unit binding is empty. A slot-recovered clarification rebinds the obligation
            # but mints no demand units of its own (its raw text asks nothing), so gating on
            # unit_ids alone withheld the render's content row -- the one row the publication gate
            # can match claims against -- and the turn was refused as though its readings had
            # reached no synthesis ("bound_to_synthesis", measured on the G1 clarification). The
            # receipt stays honest: consumed_units carries exactly what bound, nothing inferred.
            if not unit_ids and not slice_ids:
                continue
            from core.live_data_plan import LIVE_DATA_LANE_ID

            record_slice_answer(
                source_context,
                text=raw_text,
                family=family,
                response=rendered,
                reason=LIVE_DATA_LANE_ID,
                consumed=tuple(slice_ids),
                consumed_units=tuple(unit_ids),
            )
        record = source_context.get(COVERAGE_CONTEXT_KEY)
        if isinstance(record, dict):
            outcome_by_subtask = {
                outcome.subtask.subtask_id: outcome for outcome in outcomes
            }
            record["dispatches"] = [
                {
                    "slice_id": task.slice_id,
                    "unit_ids": list(task.unit_ids),
                    "subtask_id": task.subtask_id,
                    "operation": task.operation,
                    "state": outcome_by_subtask[task.subtask_id].state.name,
                    "failure_reason": str(outcome_by_subtask[task.subtask_id].failure_reason or ""),
                }
                for task in plan.subtasks
                if task.subtask_id in outcome_by_subtask
            ]

    @staticmethod
    def _live_data_served_unit_ids(runtime_attempt_id: str, plan: Any, outcomes: list[Any]) -> frozenset[str]:
        """Subtask ids whose EXECUTION actually succeeded — see `_record_live_data_discharge`.

        Same store-first read as the slice form it generalizes: the persisted
        attempt-subtask rows the finalization just reconciled, falling back to the
        in-memory outcomes (the same execution fact) only when the store read fails.
        """
        succeeded: set[str] | None = None
        try:
            from core.runtime_continuity import list_runtime_attempt_subtasks

            rows = list_runtime_attempt_subtasks(runtime_attempt_id)
            succeeded = {
                str(row.get("subtask_id") or "")
                for row in rows
                if str(row.get("lifecycle_state") or "") == "SUCCEEDED"
            }
        except Exception:
            succeeded = None
        if succeeded is None:
            succeeded = {
                outcome.subtask.subtask_id for outcome in outcomes if getattr(outcome, "ok", False)
            }
        return frozenset(succeeded)

    def _answer_single_live_data_turn(
        self,
        *,
        effective_input: str,
        raw_input: str = "",
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """A single-entity LIVE_DATA turn: builds and runs the typed plan, returns its answer on
        success, `None` on ANY failure -- unchanged from before Checkpoint 6.5, deliberately not
        covered by the fail-closed guard (see `_maybe_answer_live_data_turn`'s docstring)."""
        raw_input = raw_input or effective_input
        runtime_attempt_id = self._create_live_data_runtime_attempt(
            session_id=session_id, source_context=source_context,
            effective_input=effective_input, answer_mode="LIVE_DATA",
        )
        try:
            import uuid

            from core.agent_runtime.live_data_plan import (
                build_live_data_plan,
                declined_live_data_proposal,
                evaluate_approval_policy,
                lane_proposal_for_plan,
            )
            from core.agent_runtime.live_data_render import render_live_data_answer
            from core.agent_runtime.live_data_runner import concurrency_report, run_live_data_plan
            from core.live_data_retrieval_receipts import publish_live_data_retrieval_receipts

            plan_id = f"livedata-{uuid.uuid4().hex[:12]}"
            plan_attempt_id = runtime_attempt_id or f"attempt-{uuid.uuid4().hex[:12]}"
            # `raw_input`, not `effective_input`: entity extraction needs the paragraph/line
            # boundaries `core.input_normalizer` erases (see `_maybe_answer_live_data_turn`).
            from core.agent_runtime.answer_coverage import demand_units as _m3_units

            plan = build_live_data_plan(
                raw_input, plan_id=plan_id, attempt_id=plan_attempt_id,
                source_context=source_context,
                canonical_units=_m3_units(raw_input),
            )
            if plan is None:
                self._record_lane_proposal(
                    source_context,
                    declined_live_data_proposal(
                        "no typed plan from the recognized entities (single-entity path)"
                    ),
                )
                self._finalize_live_data_runtime_attempt(
                    runtime_attempt_id, plan_valid=False, client_turn_id=self._client_turn_id(source_context),
                )
                return None
            self._record_live_data_plan_subtasks(
                runtime_attempt_id=runtime_attempt_id, plan_id=plan_id, plan=plan,
                client_turn_id=self._client_turn_id(source_context),
            )
            from core.execution_requirements import requirements_for as _requirements_for

            _proposal = lane_proposal_for_plan(
                plan, _requirements_for(effective_input, source_context=source_context)
            )
            self._record_lane_proposal(source_context, _proposal)
            # M4 SLICE 3 — consult before EXECUTE: if an earlier-ranked lane
            # already owns every unit this plan binds, the kernel refuses the
            # serve; the lane declines with the typed reason and the turn falls
            # through (the earlier lane's service stands).
            _allowed, _superseder = self._kernel_allows_lane(
                source_context,
                lane_id=_proposal.lane_id,
                unit_ids=_proposal.obligations_claimed,
            )
            if not _allowed:
                self._record_lane_proposal(
                    source_context,
                    declined_live_data_proposal(
                        f"superseded by {_superseder} (consult-before-serve)"
                    ),
                )
                self._finalize_live_data_runtime_attempt(
                    runtime_attempt_id, plan_valid=False, client_turn_id=self._client_turn_id(source_context),
                )
                return None
            self._emit_runtime_event(
                source_context,
                event_type="live_data_plan_created",
                message=f"Live-data plan {plan_id}: {len(plan.subtasks)} subtasks",
                plan_id=plan_id,
                attempt_id=plan_attempt_id,
                subtasks=[task.to_dict() for task in plan.subtasks],
            )
            approval_decisions = evaluate_approval_policy(plan, source_context=source_context)
            outcomes = run_live_data_plan(
                plan,
                approval_decisions=approval_decisions,
                emit_node_event=self._agent_node_emitter(session_id, source_context),
            )
            # This lane really reaches the network. Receipt it like every other retrieval lane, or
            # the turn reports web_calls: 0 while quoting a live source (observed 2026-08-13).
            publish_live_data_retrieval_receipts(
                source_context, outcomes, plan_id=plan_id, attempt_id=plan_attempt_id
            )
            report = concurrency_report(outcomes)
            self._emit_runtime_event(
                source_context,
                event_type="live_data_plan_completed",
                message=(
                    f"Live-data plan {plan_id}: {sum(1 for o in outcomes if o.ok)}/{len(outcomes)} "
                    f"succeeded, max_concurrent={report.max_concurrent}"
                ),
                plan_id=plan_id,
                attempt_id=plan_attempt_id,
                outcomes=[outcome.to_dict() for outcome in outcomes],
                concurrency=report.to_dict(),
            )
            self._record_live_data_outcomes(runtime_attempt_id=runtime_attempt_id, plan_id=plan_id, outcomes=outcomes)
            if not any(outcome.ok for outcome in outcomes):
                unsupported_only = bool(outcomes) and all(
                    str(getattr(outcome.subtask, "operation", "") or "") == "unsupported_market_entity"
                    for outcome in outcomes
                )
                if not unsupported_only:
                    # Nothing at all could be answered -- let the ordinary turn try rather than
                    # shipping a page of "unavailable" rows.
                    self._finalize_live_data_runtime_attempt(
                        runtime_attempt_id, client_turn_id=self._client_turn_id(source_context),
                    )
                    return None
                # EVERY subtask was an entity the runtime KNOWS it cannot quote (decided at plan
                # time, nothing fetched). Handing the turn to a model here is what produced "I
                # don't have real-time cryptocurrency pricing built in" and the publication
                # gate's "no usable rows" refusal (owner transcript 2026-09-10 23:42, FINDINGS
                # F15): the runtime holds the accurate answer -- the typed rows carrying the
                # identifying question -- and ships it as a stated non-fulfilment.
            rendered = render_live_data_answer(plan, outcomes)
            # Same composition contract as the multipart lane: recorded slice
            # answers for clauses this plan cannot serve end the turn here.
            rendered = self._prepend_unserved_slice_answers(rendered, source_context)
            if not rendered.strip():
                self._finalize_live_data_runtime_attempt(
                    runtime_attempt_id, client_turn_id=self._client_turn_id(source_context),
                )
                return None
            # The obligation this turn just fulfilled, recorded BEFORE the answer goes out, so the
            # next turn's follow-up resolves against what was actually grounded rather than against
            # the prose. Only entities that really produced an observation are recorded: a subtask
            # that failed leaves no slot for a later "which one is warmer?" to compare, which is the
            # difference between an aggregate over evidence and an aggregate over hopes.
            self._remember_live_data_obligation(
                session_id=session_id,
                plan=plan,
                outcomes=outcomes,
                raw_input=raw_input,
                source_context=source_context,
            )
            finalized = self._finalize_live_data_runtime_attempt(
                runtime_attempt_id, client_turn_id=self._client_turn_id(source_context),
            )
            # B3: the producing edge — this lane served the turn, so it declares what it
            # served (receipts + dispatch record) before the answer is sealed.
            self._record_live_data_discharge(
                source_context,
                raw_text=raw_input,
                plan=plan,
                outcomes=outcomes,
                rendered=rendered,
                runtime_attempt_id=runtime_attempt_id,
            )
            result = self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=rendered,
                confidence=_proposal.confidence,
                source_context=source_context,
                reason=_proposal.lane_id,
                failure_text=self._attempt_failure_text(finalized),
            )
            # The turn's finality is the attempt store's verdict, never the rendered prose.
            outcome = self._live_data_fulfillment_outcome(finalized)
            if isinstance(result, dict) and outcome is not None:
                result["fulfillment_outcome"] = outcome
            return result
        except Exception:
            self._finalize_live_data_runtime_attempt(
                runtime_attempt_id, plan_valid=False, client_turn_id=self._client_turn_id(source_context),
            )
            return None

    def _answer_multipart_live_data_turn(
        self,
        *,
        effective_input: str,
        raw_input: str = "",
        session_id: str,
        source_context: dict[str, object] | None,
        requirements: Any,
    ) -> dict:
        """A multipart LIVE_DATA turn: Checkpoint 6.5's fail-closed invariant applies -- every exit
        returns a dict, never None (see `_maybe_answer_live_data_turn`'s docstring for why this is
        scoped to multipart requests only)."""
        raw_input = raw_input or effective_input
        runtime_attempt_id = self._create_live_data_runtime_attempt(
            session_id=session_id, source_context=source_context,
            effective_input=effective_input, answer_mode=requirements.answer_mode,
        )
        try:
            import uuid

            from core.agent_runtime.live_data_plan import (
                build_live_data_plan,
                declined_live_data_proposal,
                evaluate_approval_policy,
                lane_proposal_for_plan,
            )
            from core.agent_runtime.live_data_render import render_live_data_answer
            from core.agent_runtime.live_data_runner import concurrency_report, run_live_data_plan
            from core.live_data_retrieval_receipts import publish_live_data_retrieval_receipts

            plan_id = f"livedata-{uuid.uuid4().hex[:12]}"
            plan_attempt_id = runtime_attempt_id or f"attempt-{uuid.uuid4().hex[:12]}"
            # `raw_input`, not `effective_input`: entity extraction needs the paragraph/line
            # boundaries `core.input_normalizer` erases (see `_maybe_answer_live_data_turn`). This
            # is the exact call site of the production incident: `effective_input` here reproduced
            # "Warmest current city: - Source (27 C)" and a missing Warsaw even after extraction
            # itself was fixed, because by this point every newline the fix relies on was already
            # gone.
            from core.agent_runtime.answer_coverage import demand_units as _m3_units

            plan = build_live_data_plan(
                raw_input, plan_id=plan_id, attempt_id=plan_attempt_id,
                source_context=source_context,
                canonical_units=_m3_units(raw_input),
            )
            if plan is None:
                self._record_lane_proposal(
                    source_context,
                    declined_live_data_proposal(
                        "no typed plan from the recognized entities (multipart fail-closed path)"
                    ),
                )
                self._finalize_live_data_runtime_attempt(
                    runtime_attempt_id, plan_valid=False, client_turn_id=self._client_turn_id(source_context),
                )
                return self._live_data_plan_unavailable_result(
                    effective_input=effective_input,
                    raw_input=raw_input,
                    session_id=session_id,
                    source_context=source_context,
                    requirements=requirements,
                    plan_id=plan_id,
                    attempt_id=plan_attempt_id,
                )
            self._record_live_data_plan_subtasks(
                runtime_attempt_id=runtime_attempt_id, plan_id=plan_id, plan=plan,
                client_turn_id=self._client_turn_id(source_context),
            )
            # The plan must be inspectable before execution -- emitted here, before approval or
            # any tool runs, not reconstructed afterward from the rendered answer.
            _proposal = lane_proposal_for_plan(plan, requirements)
            self._record_lane_proposal(source_context, _proposal)
            # M4 SLICE 3 — consult before EXECUTE (see the single path).
            _allowed, _superseder = self._kernel_allows_lane(
                source_context,
                lane_id=_proposal.lane_id,
                unit_ids=_proposal.obligations_claimed,
            )
            if not _allowed:
                self._record_lane_proposal(
                    source_context,
                    declined_live_data_proposal(
                        f"superseded by {_superseder} (consult-before-serve)"
                    ),
                )
                self._finalize_live_data_runtime_attempt(
                    runtime_attempt_id, plan_valid=False, client_turn_id=self._client_turn_id(source_context),
                )
                return None
            self._emit_runtime_event(
                source_context,
                event_type="live_data_plan_created",
                message=f"Live-data plan {plan_id}: {len(plan.subtasks)} subtasks",
                plan_id=plan_id,
                attempt_id=plan_attempt_id,
                subtasks=[task.to_dict() for task in plan.subtasks],
            )
            approval_decisions = evaluate_approval_policy(plan, source_context=source_context)
            outcomes = run_live_data_plan(
                plan,
                approval_decisions=approval_decisions,
                emit_node_event=self._agent_node_emitter(session_id, source_context),
            )
            # This lane really reaches the network. Receipt it like every other retrieval lane, or
            # the turn reports web_calls: 0 while quoting a live source (observed 2026-08-13).
            publish_live_data_retrieval_receipts(
                source_context, outcomes, plan_id=plan_id, attempt_id=plan_attempt_id
            )
            report = concurrency_report(outcomes)
            self._emit_runtime_event(
                source_context,
                event_type="live_data_plan_completed",
                message=(
                    f"Live-data plan {plan_id}: {sum(1 for o in outcomes if o.ok)}/{len(outcomes)} "
                    f"succeeded, max_concurrent={report.max_concurrent}"
                ),
                plan_id=plan_id,
                attempt_id=plan_attempt_id,
                outcomes=[outcome.to_dict() for outcome in outcomes],
                concurrency=report.to_dict(),
            )
            self._record_live_data_outcomes(runtime_attempt_id=runtime_attempt_id, plan_id=plan_id, outcomes=outcomes)
            # Even when every subtask failed or was denied, `render_live_data_answer` renders a
            # deterministic table with each entity marked "unavailable" and its real failure
            # reason -- that IS the fail-closed answer here, not a reason to fall through. A
            # multipart request has no single-entity fast path waiting behind this one that could
            # do better with the same failed subtasks.
            rendered = render_live_data_answer(plan, outcomes)
            # A slice lane (e.g. the currency conversion) may have already answered clauses
            # this plan does not serve. This lane ends the turn, so those answers ride
            # out HERE or not at all.
            rendered = self._prepend_unserved_slice_answers(rendered, source_context)
            if not rendered.strip():
                self._finalize_live_data_runtime_attempt(
                    runtime_attempt_id, client_turn_id=self._client_turn_id(source_context),
                )
                return self._live_data_plan_unavailable_result(
                    effective_input=effective_input,
                    raw_input=raw_input,
                    session_id=session_id,
                    source_context=source_context,
                    requirements=requirements,
                    plan_id=plan_id,
                    attempt_id=plan_attempt_id,
                )
            # Same record as the single-entity lane above. A multipart turn is the one that most
            # needs it: "Kaunas and Tallinn" grounds two slots at once, and an aggregate follow-up
            # ("which one is warmer?") has nothing to aggregate unless BOTH were written down.
            self._remember_live_data_obligation(
                session_id=session_id,
                plan=plan,
                outcomes=outcomes,
                raw_input=raw_input,
                source_context=source_context,
            )
            finalized = self._finalize_live_data_runtime_attempt(
                runtime_attempt_id, client_turn_id=self._client_turn_id(source_context),
            )
            # B3: the producing edge — same record as the single-entity lane. A multipart
            # turn is the one that most needs it: this lane ends the turn without a model,
            # so whatever it did not declare here is invisible to the reconciler.
            self._record_live_data_discharge(
                source_context,
                raw_text=raw_input,
                plan=plan,
                outcomes=outcomes,
                rendered=rendered,
                runtime_attempt_id=runtime_attempt_id,
            )
            result = self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=rendered,
                confidence=_proposal.confidence,
                source_context=source_context,
                reason=_proposal.lane_id,
                failure_text=self._attempt_failure_text(finalized),
            )
            # The turn's finality is the attempt store's verdict, never the rendered prose.
            outcome = self._live_data_fulfillment_outcome(finalized)
            if isinstance(result, dict) and outcome is not None:
                result["fulfillment_outcome"] = outcome
            return result
        except Exception:
            self._finalize_live_data_runtime_attempt(
                runtime_attempt_id, plan_valid=False, client_turn_id=self._client_turn_id(source_context),
            )
            return self._live_data_plan_unavailable_result(
                effective_input=effective_input,
                raw_input=raw_input,
                session_id=session_id,
                source_context=source_context,
                requirements=requirements,
                plan_id=None,
                attempt_id=runtime_attempt_id or None,
            )

    def _live_data_plan_unavailable_result(
        self,
        *,
        effective_input: str,
        raw_input: str = "",
        session_id: str,
        source_context: dict[str, object] | None,
        requirements: Any,
        plan_id: str | None,
        attempt_id: str | None,
    ) -> dict:
        """The fail-closed answer for a LIVE_DATA-classified turn that produced no usable plan.

        Never fabricates a figure. Names whatever entities a second, independent extraction pass
        can still find in the raw text (best effort -- the fact that no plan could be built at all
        means this pass may also come up empty), and states plainly that live data could not be
        retrieved. This is the ONLY other exit `_maybe_answer_live_data_turn` has once a turn is
        classified LIVE_DATA -- see Checkpoint 6.5.

        `attempt_id` here is the durable `runtime_attempts` row when the caller already created
        one (finalized as FAILED_VALIDATION before calling this), so the checkpoint's failure_text
        reflects that -- never left empty just because a deterministic fallback text was returned.
        """
        from core.agent_runtime.live_data_render import render_live_data_plan_unavailable

        try:
            rendered = render_live_data_plan_unavailable(raw_input or effective_input, requirements.allowed_toolsets)
        except Exception:
            rendered = "The requested live data could not be retrieved right now. No price or forecast figures were generated for this reply."
        self._emit_runtime_event(
            source_context,
            event_type="live_data_plan_unavailable",
            message=f"Live-data turn classified but no plan could be built or executed (plan_id={plan_id})",
            plan_id=plan_id,
            attempt_id=attempt_id,
        )
        return self._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=rendered,
            confidence=0.9,
            source_context=source_context,
            reason="live_data_plan_unavailable",
            failure_text="plan could not be constructed or validated",
        )

    # ------------------------------------------------------------------------------------------
    # Step 9/10: runtime-owned follow-up resolution and exact retry against a persisted attempt.
    # ------------------------------------------------------------------------------------------

    _LOCATION_QUESTION_RE = re.compile(
        r"\bwhere\b|\bwhich\s+(?:folder|file|directory|place)\b|\bwhat\s+(?:folder|path|place)\b",
        re.IGNORECASE,
    )
    _LOCATION_REFERENT_RE = re.compile(
        r"\b(?:folders?|files?|it|that|those|they|scaffold|project|build|bot|workspace)\b",
        re.IGNORECASE,
    )

    def _maybe_answer_location_followup_turn(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """A "where is the folder/file?" question about what THIS chat created, answered from
        the runtime's own mutation receipts -- or None.

        Measured live 2026-09-18: after a build created `finalbot` inside VOOL's internal
        workspace, "ok and where is the foldeR?!" was misrouted to ``machine.find_folder``
        with the filler "ok" as the name and answered with a whole-disk listing of unrelated
        system folders. The runtime already knows exactly where it wrote every file. None for
        anything that is not a short location question about the chat's own recent creations,
        so ordinary searches and conversation fall through unchanged.
        """
        if bool((source_context or {}).get("planned_subturn")):
            return None
        text = " ".join(str(effective_input or "").split()).strip()
        if not text or len(text) > 160:
            return None
        if self._LOCATION_QUESTION_RE.search(text) is None:
            return None
        if self._LOCATION_REFERENT_RE.search(text) is None:
            return None
        try:
            from core.runtime_continuity import list_recent_runtime_session_events

            events = list_recent_runtime_session_events(session_id, limit=120)
        except Exception:
            return None
        paths: list[str] = []
        for event in events:
            if str(event.get("event_type") or "") != "workspace_mutation_completed":
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            path = str(details.get("path") or "").strip()
            if not path:
                match = re.search(r"`([^`]+)`", str(event.get("message") or ""))
                path = str(match.group(1)).strip() if match else ""
            if path and path not in paths:
                paths.append(path)
        if not paths:
            return None
        root = str(
            (source_context or {}).get("workspace")
            or (source_context or {}).get("workspace_root")
            or ""
        ).strip()
        if not root:
            try:
                from core.runtime_paths import active_workspace_dir

                root = str(active_workspace_dir())
            except Exception:
                root = ""
        from pathlib import PurePosixPath

        top_folders: list[str] = []
        for path in paths:
            if path.startswith(("/", "~")):
                continue
            first = str(PurePosixPath(path).parts[0]) if PurePosixPath(path).parts else ""
            if first and first not in top_folders:
                top_folders.append(first)
        if not top_folders:
            return None
        root_clean = root.strip("/")
        absolute = f"/{root_clean}/{top_folders[0].strip('/')}" if root_clean else top_folders[0]
        listed = ", ".join(f"`{path}`" for path in paths[:8])
        response = (
            f"It's in this chat's workspace: `{absolute or top_folders[0]}`.\n"
            f"Files created/updated there: {listed}."
        )
        if len(top_folders) == 1:
            response += (
                f" (`{top_folders[0]}` is a top-level folder there.)"
            )
        return self._fast_path_result(
            session_id=session_id,
            user_input=text,
            response=response,
            confidence=0.95,
            source_context=source_context,
            reason="location_followup_receipts",
        )

    def _maybe_answer_attempt_followup_turn(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """A short follow-up ("why?", "retry", "which assets did I ask for?") resolved against a
        persisted `runtime_attempts` record, or None. None is the common path: for anything that
        does not classify as a follow-up at all, and -- deliberately -- for anything that DOES
        classify but has no resolvable attempt, so the turn falls through honestly to ordinary
        conversation rather than fabricating an antecedent (see
        `core.attempt_followup`'s module docstring for the incident this closes)."""
        if bool((source_context or {}).get("planned_subturn")):
            return None
        try:
            from core.attempt_followup import classify_followup_intent, resolve_followup_attempt
            from core.runtime_continuity import (
                list_chain_answer_subtasks,
                list_runtime_attempt_subtasks,
                record_followup_resolution,
            )

            intent = classify_followup_intent(effective_input)
            if intent is None:
                return None
            # A9 pass-002 (CE-5): the canonical door already minted THIS follow-up
            # turn's own attempt; it must be excluded from referential resolution so
            # "why did that fail?"/"retry" binds to the PRIOR authoritative execution,
            # never to itself merely because it is newest.
            _turn_identity = (source_context or {}).get("_execution_identity")
            exclude_attempt_id = (
                str(_turn_identity.get("attempt_id") or "")
                if isinstance(_turn_identity, dict)
                else ""
            )
            # A9 P0 — routing-layer failures resolve BEFORE attempt resolution. A turn whose
            # model routing failed (timeout/unavailable/refused/cancelled/partial) never
            # reached its subtasks, so the attempt renderer has nothing to name; the typed
            # routing failure row names the exact provider/model/kind. A turn whose routing
            # succeeded never writes one of these rows, so subtask failures keep their
            # existing resolution untouched.
            if intent in ("EXPLAIN_ATTEMPT_FAILURE", "RETRY_ATTEMPT"):
                routing_row = self._resolve_routing_failure_followup(
                    session_id, source_context=source_context
                )
                if routing_row is not None:
                    record_followup_resolution(
                        session_id=session_id,
                        resolution_intent=intent,
                        resolution_reason="latest typed routing failure in session",
                        resolution_confidence=0.9,
                        fallback_used=False,
                        follow_up_turn_id=self._canonical_user_turn_id(source_context),
                    )
                    if intent == "EXPLAIN_ATTEMPT_FAILURE":
                        from core.turn_routing import render_routing_failure_explanation

                        return self._fast_path_result(
                            session_id=session_id,
                            user_input=effective_input,
                            response=render_routing_failure_explanation(routing_row),
                            confidence=0.9,
                            source_context=source_context,
                            reason="routing_followup_explain_failure",
                        )
                    # RETRY: re-dispatch the ORIGINAL request as a NEW GENERATION of the
                    # failed plan. The marker below carries the linkage; the turn falls
                    # through to ordinary execution with the ORIGINAL text substituted at
                    # the follow-up call site — "retry that exact request" must never run
                    # the literal phrase as a query.
                    return self._arm_routing_retry_redispatch(
                        routing_row,
                        session_id=session_id,
                        effective_input=effective_input,
                        source_context=source_context,
                    )
            attempt, reason = resolve_followup_attempt(
                session_id, effective_input, intent, exclude_attempt_id=exclude_attempt_id
            )
            if attempt is None:
                record_followup_resolution(
                    session_id=session_id, resolution_intent=intent, resolution_reason=reason,
                    resolution_confidence=0.0, fallback_used=True,
                    follow_up_turn_id=self._canonical_user_turn_id(source_context),
                )
                return None
            subtasks = list_runtime_attempt_subtasks(
                attempt["attempt_id"], execution_generation=int(attempt.get("execution_generation") or 1)
            )
            # ARCH-TRUTH-R1d: a RECALL is about the turn, not about whichever child of it
            # resolution landed on. A planned turn answers through one child per planned
            # request, so "which cities did I ask for?" over the child that served the
            # weather leg used to lose the market leg entirely. Recall reads the whole
            # chain; ACTING on an attempt (retry) keeps that attempt's own subtasks,
            # because a retry re-runs one execution, not the turn.
            chain_subtasks = list_chain_answer_subtasks(
                str(attempt.get("root_attempt_id") or attempt.get("attempt_id") or "")
            )
            recall_subtasks = chain_subtasks or subtasks
            handlers = {
                "EXPLAIN_ATTEMPT_FAILURE": self._answer_explain_attempt_failure,
                "LIST_ORIGINAL_ENTITIES": self._answer_list_original_entities,
                "RETRY_ATTEMPT": self._answer_retry_attempt,
                "REPEAT_ORIGINAL_REQUEST": self._answer_repeat_original_request,
                "CORRECT_PREVIOUS_RESPONSE": self._answer_correct_previous_response,
            }
            handler = handlers.get(intent)
            if handler is None:
                # CONTINUE_ATTEMPT (Repair 10, Mnemosyne review): no dedicated typed-attempt
                # handling exists -- deferring to the existing checkpoint resume mechanism
                # (core.agent_runtime.proceed_intent_support) rather than duplicating it here.
                # Recorded honestly as a FALLBACK: this classified against `attempt`, but that
                # attempt is never actually used to answer the turn (the checkpoint mechanism owns
                # the answer), so recording resolution_confidence=0.9/fallback_used=False here
                # would persist a "successful resolution" that was never used.
                record_followup_resolution(
                    session_id=session_id, resolved_attempt_id=str(attempt["attempt_id"]),
                    resolution_intent=intent,
                    resolution_reason="CONTINUE_ATTEMPT has no dedicated handler -- deferred to checkpoint resume, not resolved here",
                    resolution_confidence=0.0, fallback_used=True,
                    follow_up_turn_id=self._canonical_user_turn_id(source_context),
                )
                return None
            record_followup_resolution(
                session_id=session_id, resolved_attempt_id=str(attempt["attempt_id"]),
                resolution_intent=intent, resolution_reason=reason, resolution_confidence=0.9,
                follow_up_turn_id=self._canonical_user_turn_id(source_context),
            )
            return handler(
                attempt,
                recall_subtasks if intent in _CHAIN_RECALL_INTENTS else subtasks,
                session_id=session_id,
                source_context=source_context,
                effective_input=effective_input,
            )
        except Exception:
            # NEVER SILENT. This bare catch turned a hard TypeError -- a caller passing the
            # wrong arity to the resolver -- into "there is no follow-up here", with no log
            # line anywhere. A follow-up lane that declines because it CRASHED is
            # indistinguishable from one that declines because nothing was outstanding, and
            # the turn falls through to generation either way: the exact shape this audit is
            # about, one layer up. Measured cost: a retry red presenting as an unexplained
            # zero fetches behind an assertion message that said the opposite.
            _log.exception(
                "follow-up resolution raised; the turn falls through to ordinary handling "
                "(session=%s, intent=%s)",
                session_id,
                intent,
            )
            return None

    def _resolve_routing_failure_followup(
        self, session_id: str, *, source_context: dict[str, object] | None
    ) -> dict | None:
        """The latest typed ROUTING failure in this session, excluding this turn's own.

        Routing failures are upstream of attempts: the row exists only when a model
        call itself failed, which is precisely the turn the attempt renderer cannot
        describe (no subtasks ever ran)."""
        try:
            from core.turn_routing import latest_routing_failure

            return latest_routing_failure(
                session_id,
                exclude_turn_id=self._canonical_user_turn_id(source_context),
            )
        except Exception:
            return None

    def _arm_routing_retry_redispatch(
        self,
        routing_row: dict,
        *,
        session_id: str,
        effective_input: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """Arm the retry generation: stamp the linkage, never answer from here.

        Returns None so the turn falls through to ordinary execution; the follow-up call
        site substitutes the ORIGINAL request text for the literal "retry" phrase, and
        the plan mint turns the marker into generation N+1 of the failed plan — linked
        to the original request and evidence, fenced no wider than the original."""
        original_text = str(routing_row.get("user_text") or "").strip()
        plan_id = str(routing_row.get("plan_id") or "").strip()
        if not original_text or not plan_id:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=(
                    "The original request could not be reconstructed exactly, so it was not "
                    "retried. Please resend it."
                ),
                confidence=0.9,
                source_context=source_context,
                reason="routing_followup_retry_no_original",
                failure_text="routing failure row carried no original request text or plan id",
            )
        if isinstance(source_context, dict):
            from core.turn_routing import TURN_ROUTING_RETRY_KEY

            source_context[TURN_ROUTING_RETRY_KEY] = {
                "plan_id": plan_id,
                "user_text": original_text,
                "original_turn_id": str(routing_row.get("turn_id") or ""),
            }
        return None


    @staticmethod
    def _operator_request_text(source_context: dict[str, object] | None) -> str:
        """What the OPERATOR sent this turn, from the canonical TurnRequest on the context.

        A planned sub-turn runs under the PARENT's TurnRequest, so this is the whole request
        even when the attempt row a follow-up resolved is a sub-turn's row carrying one clause.
        Empty when no request is bound, which leaves every caller on its previous behaviour.
        """
        try:
            from core.turn_contract import TURN_REQUEST_KEY

            request = (source_context or {}).get(TURN_REQUEST_KEY)
            return str(getattr(request, "user_text", "") or "")
        except Exception:
            return ""

    def _answer_explain_attempt_failure(
        self, attempt: dict, subtasks: list[dict], *, session_id: str,
        source_context: dict[str, object] | None, effective_input: str,
    ) -> dict:
        from core.agent_runtime.response import record_deterministic_render
        from core.attempt_followup import render_attempt_failure_explanation

        # The request the explanation names is the FAILED attempt's -- its root turn's text when
        # the resolved row is a planned sub-turn's, else its own snapshot -- never the follow-up
        # phrase this turn carries. Measured (owner transcript 2026-09-10 23:20, FINDINGS
        # F14.5): "Your request was: 'why?'" -- the operator's CURRENT text was quoted as the
        # request that failed.
        original_request = ""
        try:
            from core.runtime_continuity import get_runtime_attempt

            root_id = str(attempt.get("root_attempt_id") or "").strip()
            root = get_runtime_attempt(root_id) if root_id and root_id != str(attempt.get("attempt_id") or "") else None
            original_request = str(
                (root or {}).get("original_request_snapshot")
                or attempt.get("original_request_snapshot")
                or ""
            ).strip()
        except Exception:
            original_request = str(attempt.get("original_request_snapshot") or "").strip()
        rendered = render_attempt_failure_explanation(
            attempt, subtasks, original_request=original_request
        )
        # These bytes are the durable ledger read back, not generation. Record them so the
        # output seam can tell the runtime's own record apart from prose that resembles it --
        # without this the explanation is convicted by the live-value guard for quoting the
        # operator's own request back to them (C9).
        record_deterministic_render(
            source_context, rendered, route="attempt_followup_explain_failure"
        )
        return self._fast_path_result(
            session_id=session_id, user_input=effective_input, response=rendered, confidence=0.9,
            source_context=source_context, reason="attempt_followup_explain_failure",
        )

    def _answer_list_original_entities(
        self, attempt: dict, subtasks: list[dict], *, session_id: str,
        source_context: dict[str, object] | None, effective_input: str,
    ) -> dict:
        from core.attempt_followup import render_original_entities

        rendered = render_original_entities(subtasks)
        return self._fast_path_result(
            session_id=session_id, user_input=effective_input, response=rendered, confidence=0.9,
            source_context=source_context, reason="attempt_followup_list_entities",
        )

    def _answer_retry_attempt(
        self, attempt: dict, subtasks: list[dict], *, session_id: str,
        source_context: dict[str, object] | None, effective_input: str,
        resolution_intent: str = "RETRY_ATTEMPT",
    ) -> dict:
        """Step 10: exact retry, idempotent per parent attempt.

        A fast pre-check (`find_child_attempt`) avoids entering the retry machinery at all in the
        common case; the actual correctness guarantee (Repair 2, Mnemosyne review) is
        `create_runtime_attempt`'s real database uniqueness constraint on (parent_attempt_id,
        trigger_user_turn_id, resolution_intent) -- a process-local lock alone was proven
        insufficient across two OS processes. `RetryAlreadyInProgressError` still means "a same-process
        thread already holds this parent's lock."
        """
        from core.runtime_continuity import find_child_attempt

        trigger_user_turn_id = self._canonical_user_turn_id(source_context)
        existing_child = find_child_attempt(str(attempt["attempt_id"]))
        if existing_child is not None and existing_child.get("lifecycle_state") in ("RECEIVED", "PLANNED", "RUNNING"):
            return self._fast_path_result(
                session_id=session_id, user_input=effective_input,
                response="A retry of that request is already in progress. Please wait for it to finish.",
                confidence=0.9, source_context=source_context, reason="attempt_followup_retry_inflight",
            )
        try:
            from core.attempt_retry import RetryAlreadyInProgressError, execute_attempt_retry

            result = execute_attempt_retry(
                attempt, subtasks, session_id=session_id,
                checkpoint_id=self._runtime_checkpoint_id(source_context),
                trigger_user_turn_id=trigger_user_turn_id, resolution_intent=resolution_intent,
                source_context=source_context,
            )
        except RetryAlreadyInProgressError:
            return self._fast_path_result(
                session_id=session_id, user_input=effective_input,
                response="A retry of that request is already in progress. Please wait for it to finish.",
                confidence=0.9, source_context=source_context, reason="attempt_followup_retry_inflight",
            )
        except ValueError:
            # Repair 4: create_runtime_attempt refuses an empty trigger_user_turn_id rather than
            # silently creating an undeduplicated retry -- this should not happen once the turn is
            # wired from `_run_once_inner`, but a caller that genuinely has no canonical turn (a
            # test, an unusual entry path) must fail honestly, not fabricate a retry.
            return self._fast_path_result(
                session_id=session_id, user_input=effective_input,
                response="This turn has no resolvable identity to retry against. Please resend your request.",
                confidence=0.7, source_context=source_context, reason="attempt_followup_retry_no_turn_identity",
                failure_text="trigger_user_turn_id was empty",
            )
        except Exception as exc:
            _log.exception(
                "attempt retry execution raised (attempt_id=%s, trigger_turn=%s)",
                attempt.get("attempt_id"), trigger_user_turn_id,
            )
            return self._fast_path_result(
                session_id=session_id, user_input=effective_input,
                response="The retry could not be executed. The original result has been preserved.",
                confidence=0.9, source_context=source_context, reason="attempt_followup_retry_failed",
                failure_text=f"retry execution raised {type(exc).__name__}: {exc}",
            )
        return self._fast_path_result(
            session_id=session_id, user_input=effective_input, response=result["rendered"],
            confidence=0.9, source_context=source_context, reason="attempt_followup_retry",
            failure_text=self._attempt_failure_text(result["attempt"]),
        )

    def _answer_repeat_original_request(
        self, attempt: dict, subtasks: list[dict], *, session_id: str,
        source_context: dict[str, object] | None, effective_input: str,
    ) -> dict:
        """"Check the original message and answer it." -- reconstruct from valid persisted
        results when the attempt already succeeded; otherwise behave like an exact retry (Step 9's
        own instruction: reconstruct, or start a linked retry when required data failed or is
        stale)."""
        if str(attempt.get("lifecycle_state") or "") == "SUCCEEDED":
            from core.attempt_followup import render_reconstructed_answer

            rendered = render_reconstructed_answer(attempt, subtasks, source_context=source_context)
            return self._fast_path_result(
                session_id=session_id, user_input=effective_input, response=rendered, confidence=0.9,
                source_context=source_context, reason="attempt_followup_repeat_original",
            )
        return self._answer_retry_attempt(
            attempt, subtasks, session_id=session_id, source_context=source_context,
            effective_input=effective_input, resolution_intent="REPEAT_ORIGINAL_REQUEST",
        )

    def _answer_correct_previous_response(
        self, attempt: dict, subtasks: list[dict], *, session_id: str,
        source_context: dict[str, object] | None, effective_input: str,
    ) -> dict:
        """"That is not what I asked" / "fix the previous answer" -- grounds the correction in
        what the runtime actually did, rather than guessing what the person meant. Thinner than
        the other four intents: it states the runtime facts and invites the person to name what
        was wrong, rather than attempting to infer and fix an unspecified error."""
        from core.attempt_followup import render_attempt_failure_explanation, render_reconstructed_answer

        if str(attempt.get("lifecycle_state") or "") == "SUCCEEDED":
            facts = render_reconstructed_answer(attempt, subtasks, source_context=source_context)
        else:
            facts = render_attempt_failure_explanation(
                attempt, subtasks, original_request=self._operator_request_text(source_context)
            )
        rendered = (
            f"Here is exactly what your request produced: {facts} "
            "If that's not what you meant, say specifically what should be different and I'll "
            "target that instead of guessing."
        )
        # Same law as the explain seam: the wrapper re-trips the same guard on the same
        # ledger-sourced facts, so a fix that covered only the builder would still lose this
        # one (C9).
        from core.agent_runtime.response import record_deterministic_render

        record_deterministic_render(
            source_context, rendered, route="attempt_followup_correct_previous"
        )
        return self._fast_path_result(
            session_id=session_id, user_input=effective_input, response=rendered, confidence=0.8,
            source_context=source_context, reason="attempt_followup_correct_previous",
        )

    def _legacy_live_data_lane_covers(
        self,
        *,
        raw_input: str,
        effective_input: str,
        source_context: dict[str, object] | None,
    ):
        """A probe proving the legacy live-data lane serves EVERY node of a conductor plan.

        Passed to `plan_conductor_turn` as `lane_coverage_probe`: the conductor may hand a plan
        whose operations all sit in its legacy vocabulary to `_maybe_answer_live_data_turn` ONLY
        when this returns True. The probe mirrors that lane's own admission gates and then demands
        a per-entity match, because the lane's whole-text extraction provably loses entities the
        conductor's clause-scoped expanders find (a run-on weather clause yields no weather
        subtask there). Conservative by construction: any mismatch, any missing subject, any
        exception reads as "not covered" and the conductor keeps the turn -- the worst outcome is
        the conductor serving a turn the lane could also have served, never a dropped clause.
        """

        def _norm(value: object) -> str:
            return str(value or "").strip().casefold()

        def probe(plan) -> bool:
            from core.execution_requirements import requirements_for
            from core.live_data_plan import build_live_data_plan

            requirements = requirements_for(effective_input, source_context=source_context)
            if requirements.answer_mode != "LIVE_DATA":
                return False
            if self._named_live_data_entity_count(raw_input, requirements.allowed_toolsets) < 2:
                return False
            legacy = build_live_data_plan(
                raw_input,
                plan_id=f"coverage-probe-{plan.plan_id}",
                attempt_id=f"coverage-probe-{plan.plan_id}",
                source_context=dict(source_context or {}),
            )
            if legacy is None or len(legacy.subtasks) < 2:
                return False
            weather_subjects = {
                _norm(task.arguments.get("location")) or _norm(task.entity)
                for task in legacy.subtasks
                if task.operation == "weather_lookup"
            }
            market_subjects = {
                _norm(task.arguments.get("asset_key")) or _norm(task.entity)
                for task in legacy.subtasks
                if task.operation == "market_quote"
            }
            nodes_by_id = {node.node_id: node for node in plan.nodes}

            def node_covered(node, seen: frozenset[str]) -> bool:
                if node.node_id in seen:
                    return False
                if node.operation == "weather_lookup":
                    subject = _norm(node.arguments.get("location")) or _norm(
                        node.arguments.get("entity")
                    )
                    return bool(subject) and subject in weather_subjects
                if node.operation == "market_quote":
                    subject = _norm(node.arguments.get("asset_key")) or _norm(
                        node.arguments.get("entity")
                    )
                    return bool(subject) and subject in market_subjects
                if node.operation == "comparison":
                    # The legacy render derives comparison lines from its own outcomes, so a
                    # comparison is covered exactly when everything it compares is.
                    deps = tuple(node.depends_on or ())
                    if not deps or any(dep not in nodes_by_id for dep in deps):
                        return False
                    return all(
                        node_covered(nodes_by_id[dep], seen | {node.node_id}) for dep in deps
                    )
                return False

            return all(node_covered(node, frozenset()) for node in plan.nodes)

        return probe

    def _maybe_answer_conductor_turn(
        self,
        *,
        effective_input: str,
        raw_input: str = "",
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """Answer a message that spans several domains, or None to leave the turn as it was.

        None is the common path and stays cheap: `turn_may_hold_several_requests` rejects a
        single-request message inside `plan_conductor_turn` before any model call is made.

        Failure BEFORE the claim returns None -- no plan, an unparseable plan, a plan that invents
        a word the user did not write, a graph that is not a DAG, a plan the existing lanes already
        serve. The worst outcome of that half misbehaving is the behaviour the runtime already had.

        Failure AFTER the claim does not. `reduce_execution_report` is the cutover: once it has
        produced a claimed `ProductDecision`, this turn belongs to the conductor and every exit --
        normal return, composition fault, dispatch fault, an exception three frames down -- produces
        a claimed result. Handing the turn onward at that point would let a narrower lane answer a
        message whose bookkeeping the runtime already knows is wrong, and the wrongness would never
        surface. That is not hypothetical: a `TypeError` from a single keyword argument used to hit
        a blanket `except Exception: return None` and silently retire the conductor.
        """
        if bool((source_context or {}).get("planned_subturn")):
            return None
        from core.conductor.product_decision import ConductorClaimGate

        claim_gate = ConductorClaimGate()
        try:
            from core.agent_runtime.turn_planner_hook import (
                build_conductor_ask_model,
                build_pinned_paid_turn_scope,
                build_planner_ask_model,
                build_semantic_proof_ask_model,
            )
            from core.conductor import compose_answer, plan_conductor_turn, run_conductor_plan
            from core.conductor.evidence import bind_evidence_on_node_completion
            from core.conductor.planner import (
                CONDUCTOR_LANE_ID,
                declined_conductor_proposal,
                demands_this_plan_cannot_execute,
            )
            from core.conductor.receipts import plan_receipt
            from core.conductor.registry import NodeContext
            from core.conductor.scheduler import DEFAULT_PLAN_DEADLINE_S
            from core.provider_call_deadline import bind_provider_deadline

            # One owner, one clock: planning and node execution are phases of the same conductor
            # turn.  The provider deadline is deliberately earlier, leaving cleanup time for the
            # adapter to raise, the router to emit model.call_failed, and the API to close the turn
            # trace.  A manifest's generous 180s timeout may never extend this 75s turn.
            conductor_turn_deadline = runtime_active_clock.monotonic() + DEFAULT_PLAN_DEADLINE_S
            # The dict the CALLER owns, kept before the rebinding below. `bind_provider_deadline`
            # documents itself as returning "a context copy", so from the next statement on
            # `source_context` names a copy -- and evidence written to a copy is exactly the defect
            # being repaired here, one level above the `NodeContext` copy. What this turn observed
            # has to land on the context the final seam will read.
            turn_context = source_context if isinstance(source_context, dict) else None
            source_context = bind_provider_deadline(
                source_context,
                turn_deadline_monotonic=conductor_turn_deadline,
                reason="conductor plan deadline",
            )

            # PLANNER PHASE SUB-BUDGET. Planning is unpaid supporting work that runs FIRST;
            # without a bound of its own a cold planner model consumed the front of the 75 s
            # turn and the answer's generation inherited the remainder (measured on 79a9b357,
            # turn 9419658a: the Berlin Wall node's PROVIDER_TIMEOUT after two cold planner
            # calls). The planner's asks run under a capped COPY — bind-only-shortens, so the
            # node phase below keeps the FULL turn deadline — and a planner that blows its cap
            # makes `plan_conductor_turn` decline through its existing exception path, handing
            # the turn to the ordinary lanes with their own budget.
            from core.conductor.scheduler import PLANNER_PHASE_CAP_S

            planner_phase_context = bind_provider_deadline(
                source_context,
                turn_deadline_monotonic=runtime_active_clock.monotonic() + PLANNER_PHASE_CAP_S,
                reason="conductor planner phase cap",
            )
            # Planned against the RAW text: `core.input_normalizer` collapses newlines, and a
            # clause boundary the user typed is information the planner should not have to guess at.
            # One server-built scope is shared by every answer-producing reasoning node.  The
            # planner remains unpaid: charging merely to classify whether a plan is useful would
            # make the pin a recurring internal-spend switch.  Building one scope per node would
            # turn the intended per-turn ceiling into a per-step ceiling.
            paid_scope = build_pinned_paid_turn_scope(self, source_context)
            # TWO seams, because these are two different questions with two different contracts.
            # Passing one callable for both is what made the semantic proposer dead: the shared
            # artifact had no notion of which question was being asked, so the second call returned
            # the first one's clause array and every frame was refused as malformed. Measured at
            # bb14a695: one provider invocation, byte-identical reply, zero frames, and 134 green
            # tests -- because every one of them injected a stand-in proposer instead.
            #
            # Both run on whatever lane the turn is already on: local stays local. When either is
            # absent or fails, capture abstains rather than guessing a role.
            # TASK-STATE PRESERVATION (FINDINGS F15): a bare re-ask of the obligation this
            # session holds ("check it on the internet" after an allocation whose ticker
            # could not be resolved) plans the RECORDED request again, exactly as the
            # live-data lane re-asks its own. The raw text stays what the user wrote; the
            # plan text is the request it refers to.
            conductor_text = str(raw_input or effective_input)
            try:
                from core.live_data_continuation import continuation_inherits_live_data

                inherited_text = continuation_inherits_live_data(
                    conductor_text, source_context=turn_context
                )
            except Exception:
                inherited_text = ""
            if inherited_text and inherited_text.strip() and inherited_text.strip() != conductor_text.strip():
                conductor_text = inherited_text.strip()
            plan = plan_conductor_turn(
                conductor_text,
                ask_model=build_planner_ask_model(self, planner_phase_context),
                propose_semantics=build_semantic_proof_ask_model(self, planner_phase_context),
                # A lane-served-ops plan may be handed to the legacy live-data lane only on
                # per-entity proof of coverage; without it the conductor keeps the turn so no
                # clause can be silently dropped by the narrower lane's extraction.
                lane_coverage_probe=self._legacy_live_data_lane_covers(
                    raw_input=conductor_text,
                    effective_input=effective_input,
                    source_context=turn_context,
                ),
            )
            if plan is None:
                # M3 slice 3: the conductor's decline is typed (the planner refused
                # or could not parse this text) — never a silent None.
                self._record_lane_proposal(
                    turn_context if turn_context is not None else source_context,
                    declined_conductor_proposal("plan_conductor_turn returned no plan"),
                )
                return None

            # M3 slice 3 — the typed claim against the SPINE's canonical set. The
            # binding is span overlap (node clause_span x unit start/end over the
            # same original text), and units no node covers are NAMED. Recorded on
            # the TURN's context (turn_context), the dict the caller owns — not the
            # rebound deadline copy.
            from core.agent_runtime.answer_coverage import demand_units as _m3c_units
            from core.conductor.planner import conductor_lane_proposal

            _c4_proposal = conductor_lane_proposal(
                plan, _m3c_units(str(raw_input or effective_input))
            )
            self._record_lane_proposal(
                turn_context if turn_context is not None else source_context,
                _c4_proposal,
            )
            # M4 SLICE 4 — consult before EXECUTE (completing registry-wide
            # coverage): if an earlier-ranked lane (frontdoor, typed live-data)
            # already owns every unit this conductor plan binds, the kernel
            # refuses the serve; the conductor declines with the typed reason
            # and the turn falls through. Fail-open like the live-data consult.
            _c4_allowed, _c4_superseder = self._kernel_allows_lane(
                turn_context if turn_context is not None else source_context,
                lane_id=_c4_proposal.lane_id,
                unit_ids=_c4_proposal.obligations_claimed,
            )
            if not _c4_allowed:
                self._record_lane_proposal(
                    turn_context if turn_context is not None else source_context,
                    declined_conductor_proposal(
                        f"superseded by {_c4_superseder} (consult-before-serve)"
                    ),
                )
                return None
            # P0 MIXED-DEMAND (served) — the finalize law applied to a COMPOSITE
            # PLANNER, at the last moment it can still decline for free.
            #
            # Measured over the real HTTP surface at 3ad2ca6c, local lane
            # qwen2.5:7b, against a real two-line file on disk:
            #
            #   "Return exactly the second line of notes.txt. Calculate 37 x 19.
            #    Decide whether that line describes walking."
            #     -> "37*19 = 703.  Could not be answered: ..."
            #        route conductor_multi_intent_plan; plan 3 nodes, 1 succeeded;
            #        obligations u1 indeterminate, u2 satisfied, u3 indeterminate;
            #        execution_truth ran_tool=false — and the runtime attempt was
            #        recorded SUCCEEDED.
            #
            # The catalog grants a composite planner an unconditional whole-turn
            # claim on the premise that it "owns a COMPLETE unit plan". This plan
            # did not: the file-read and judgement demands each overlapped ONLY
            # unresolved nodes at PLAN TIME (one `agent_node_started` was ever
            # emitted), so the conductor took a turn it had already computed it
            # could not finish, and preempted the composite that answers it in
            # full.
            #
            # TWO conditions, and both are load-bearing:
            #
            # * the demand must have NO executable node at all — not merely an
            #   unresolved node beside a working one. The conductor deliberately
            #   ships a truthful PARTIAL rather than falling back (see
            #   `test_a_mixed_turn_ships_its_partial_instead_of_falling_back`), and
            #   a plan of eight nodes with six unresolved is still the right lane
            #   for the demands it CAN serve. Standing down on any unresolved node
            #   would delete that contract.
            # * the demand must be one the CANONICAL REGISTRY says another lane
            #   owns. A demand nothing in the runtime covers ("call my mum") must
            #   keep reaching the conductor's truthful unavailability answer,
            #   because handing it to a model instead buys a fabrication in place
            #   of an honest "no".
            try:
                from core.agent_runtime.demand_ownership import (
                    demand_coverage,
                    execution_unit_spans,
                    registered_lane_for,
                )

                # This hand-off exists so the DEMAND-OWNED MIXED lane can run a demand
                # the conductor's plan cannot. That lane only takes a turn the catalog
                # reads as mixed, so on a non-mixed turn there is nobody to hand off TO
                # and declining simply throws the conductor's better answer away.
                #
                # Measured: "What is weather in Rome also tell me how much gold I can
                # buy" is NOT mixed -- one lane (live_info_fast_path) covers both units.
                # The conductor resolved the weather node and left the fx_quote node
                # unresolved, because the clause carries no currency pair; the registry
                # then named live_info_fast_path as the owner and the conductor stood
                # down. The turn fell to the typed live-data plan lane -- the SAME
                # live-data family that had just failed to resolve the clause (its lane
                # id is deliberately not spelled here; it lives in
                # core.live_data_plan.LIVE_DATA_LANE_ID) -- and the truthful
                # "Could not be answered: ... how much gold" the conductor had composed
                # was lost. Nothing was gained by the hand-off.
                #
                # The mixed case the guard was built for is untouched: the incident turn
                # (file read + arithmetic + judgement) reads mixed=True and still
                # declines, so a conductor plan can never swallow a demand the
                # mixed-demand lane owns.
                if not demand_coverage(str(raw_input or effective_input)).mixed:
                    raise _ConductorHandoffNotApplicableError

                # The EXECUTION grain, not the mint grain: this asks "would another
                # lane run this demand?", and the lane that would is the demand-owned
                # plan, which dispatches execution units. On the mint grain a
                # continuation fragment ("and one app.py file.") becomes its own slot
                # and reads as a workspace file reference — a demand nobody asked for,
                # which would strip a healthy conductor claim.
                _unservable = [
                    (unit_id, text, owner)
                    for unit_id, text in demands_this_plan_cannot_execute(
                        plan,
                        execution_unit_spans(str(raw_input or effective_input)),
                    )
                    if (owner := registered_lane_for(text))
                ]
            except Exception:
                # Accounting may never break the lane it accounts for: an
                # unavailable registry leaves the pre-existing claim untouched.
                _unservable = []
            if _unservable:
                _named = "; ".join(
                    f"{unit_id} ({text!r}) is owned by {owner}"
                    for unit_id, text, owner in _unservable
                )
                self._record_lane_proposal(
                    turn_context if turn_context is not None else source_context,
                    declined_conductor_proposal(
                        "plan cannot execute demand another registered lane covers: "
                        f"{_named}"
                    ),
                )
                return None
            self._emit_runtime_event(
                source_context,
                event_type="conductor_plan_created",
                message=f"Conductor plan {plan.plan_id}: {len(plan.nodes)} nodes",
                plan_id=plan.plan_id,
                plan=plan.to_dict(),
            )

            # P0 SERVED-EVIDENCE BINDING -- open the grounding lifecycle at CLAIM time.
            #
            # The canonical M1 authority, consulted -- never re-derived. On this path
            # nothing else asks it with the turn's context before the plan runs (the
            # lane-coverage probe only consults it for plans the legacy lane could serve,
            # and the seal only reaches it after every byte is written), so a mixed
            # multi-demand turn with live observations opened its lifecycle at SEAL time
            # -- after every generation call -- and the binding its observations minted
            # had no row to land on. One read here, on the CALLER-owned dict: the frozen
            # record M1 keeps makes repeat consultations free, and a turn M1 does not
            # mark current-information still opens nothing (the DIRECT/timeless law).
            try:
                from core.execution_requirements import requirements_for

                requirements_for(
                    str(effective_input or ""), source_context=turn_context
                )
            except Exception:
                pass

            def _run_tool_intent(payload: dict) -> object:
                """Every filesystem-touching node runs through the one permission-gated seam."""
                return self._execute_tool_intent(
                    payload,
                    task_id=plan.plan_id,
                    session_id=session_id,
                    source_context=dict(source_context or {}),
                    hive_activity_tracker=self.hive_activity_tracker,
                )

            node_generation = build_conductor_ask_model(self, source_context, paid_scope=paid_scope)
            outcomes = run_conductor_plan(
                plan,
                context=NodeContext(
                    session_id=session_id,
                    source_context=dict(source_context or {}),
                    run_tool_intent=_run_tool_intent,
                    # P0 SERVED-EVIDENCE BINDING. The emitter is wrapped so each observation
                    # node's result MINTS AND BINDS the turn's evidence set the moment it
                    # lands -- incrementally, because a generation node in a later dependency
                    # wave answers WITH that evidence, and a binding that post-dates every
                    # generation call is a `proves="prompt_entry"` claim no prompt ever saw.
                    # The wrapper stamps the binding envelope onto this same working context
                    # (`build_conductor_ask_model` copies it at call time), so the answering
                    # calls are recorded carrying the exact bound evidence set.
                    emit_node_event=bind_evidence_on_node_completion(
                        self._agent_node_emitter(session_id, source_context),
                        source_context=source_context,
                        plan=plan,
                        query=str(raw_input or effective_input),
                    ),
                    # A node that reasons over the message's own facts needs a model. Injected for
                    # the same reason the tool seam is: absent means such a node fails with a
                    # stated reason, never that it invents its answer.
                    run_generation=node_generation,
                    run_structured_generation=lambda system, prompt, schema: node_generation(
                        system, prompt, json_schema=dict(schema)),
                ),
                # Planning consumed part of this turn's budget.  Do not reset the 75s clock at
                # node dispatch or a slow planner plus a slow node becomes a 90s turn.
                plan_deadline_s=max(0.001, conductor_turn_deadline - runtime_active_clock.monotonic()),
            )
            # What this plan actually OBSERVED, onto the turn's real context. Published here rather
            # than by the nodes because `NodeContext` above is handed `dict(source_context or {})` --
            # a shallow copy -- so a node writing evidence would write it to a detached dict. Two
            # live market quotes really were fetched and the turn still reported "I didn't run any
            # live lookup on this turn", because nothing the conductor did was visible to the guard
            # that decides whether the answer is licensed. See `core.conductor.evidence`.
            from core.conductor.evidence import (
                bind_conductor_evidence,
                publish_conductor_computations,
                publish_conductor_observations,
                publish_conductor_stable_knowledge,
            )

            publish_conductor_observations(turn_context, outcomes)
            # The plan's deterministic computations publish on their OWN channel: they are not
            # lookups (see `conductor_observations`' contract), but a gated mixed turn needs them
            # as support or one sibling's ungrounded prose takes the arithmetic down with it.
            publish_conductor_computations(turn_context, outcomes)
            # The plan's stable-knowledge renders publish on a channel that is deliberately NOT
            # support (F43): the publication gate consumes it as a per-claim exemption on the
            # plan's typed authority, never as a source, so the model's own text cannot certify
            # itself and a live sibling's gate stops drowning the knowledge clause with it.
            # The generation seam wrote its authorship verdicts into SOURCE_CONTEXT (the dict
            # `build_conductor_ask_model` closed over); carry them into the working copy the
            # publisher joins against, the same direction the observation channel is carried
            # below — absent this, the join sees no verdicts and fails closed every turn.
            if turn_context is not None and isinstance(source_context, dict):
                _authorship_log = source_context.get("conductor_generation_authorship")
                if _authorship_log:
                    turn_context["conductor_generation_authorship"] = _authorship_log
            publish_conductor_stable_knowledge(turn_context, outcomes)
            try:
                from core.grounding_lifecycle import (
                    adopt_lifecycle_id as _adopt_for_computations,
                )
                from core.grounding_lifecycle import (
                    record_computed_values,
                )

                _adopt_for_computations(turn_context)
                record_computed_values(
                    turn_context,
                    entries=(turn_context or {}).get("runtime_computed_values"),
                )
            except Exception:
                pass
            try:
                from core.grounding_lifecycle import (
                    adopt_lifecycle_id as _adopt_for_knowledge,
                )
                from core.grounding_lifecycle import (
                    record_stable_knowledge,
                )

                _adopt_for_knowledge(turn_context)
                record_stable_knowledge(
                    turn_context,
                    entries=(turn_context or {}).get("runtime_stable_knowledge"),
                )
            except Exception:
                # Fail-closed in EFFECT (no record, so no exemption — the knowledge line is
                # withheld exactly as before this channel existed) but never silent: a
                # recorder that quietly died would read as "the exemption never applies"
                # in every future turn, which is a misleading success state.
                _log.exception(
                    "stable-knowledge channel recording failed; publication exemption "
                    "unavailable for this turn"
                )
            # The binding of record for the WHOLE plan, on the turn's real context. The
            # incremental emitter wrapper already bound each observation as it landed (so
            # later generation calls carried the set); this re-bind from the final outcomes
            # is idempotent by content (stable digests) and covers plans whose observations
            # all completed before any generation node ran.
            from core.grounding_lifecycle import adopt_lifecycle_id

            adopt_lifecycle_id(turn_context)
            bind_conductor_evidence(
                turn_context, outcomes, query=str(raw_input or effective_input)
            )
            if turn_context is not None and isinstance(source_context, dict):
                # Keep the working copy consistent with the turn, so anything further down this
                # method sees the same evidence the caller now holds.
                source_context["runtime_tool_observations"] = list(
                    turn_context.get("runtime_tool_observations") or []
                )
            # P0 SERVED-EVIDENCE BINDING -- authorship. A generation node's text IS served
            # bytes: composed verbatim into the answer by `compose_answer`. Served usage is
            # never recorded for these calls (they cross `_invoke_manifest`, not `resolve`),
            # so without this recorder the publication gate read a model-written paragraph as
            # runtime-composed bytes it had no jurisdiction over -- the exact shape a gate
            # exists to catch. Truthful in both directions: a plan whose every generation
            # node failed serves no model prose and raises nothing.
            try:
                from core.conductor.registry import operation_spec as _op_spec

                if any(
                    outcome is not None
                    and bool(getattr(outcome, "succeeded", False))
                    and bool(getattr(_op_spec(outcome.node.operation), "needs_generation", False))
                    for outcome in outcomes
                ):
                    from core.grounding_lifecycle import record_model_authorship

                    adopt_lifecycle_id(turn_context)
                    if turn_context is not None:
                        record_model_authorship(turn_context)
            except Exception:
                pass
            receipt = plan_receipt(
                outcomes, plan_id=plan.plan_id, parallel_preferred=plan.parallel_preferred
            )
            concurrency = receipt["concurrency"]
            self._emit_runtime_event(
                source_context,
                event_type="conductor_plan_completed",
                message=(
                    f"Conductor plan {plan.plan_id}: {receipt['succeeded_count']}/{len(outcomes)} "
                    f"succeeded, max_concurrent={concurrency['max_concurrent']}"
                ),
                plan_id=plan.plan_id,
                receipt=receipt,
            )

            # THE PRODUCT-TRUTH CUTOVER. One reduction, immediately after execution, and every
            # downstream stage reads its result rather than forming its own opinion. Four rival
            # verdicts used to live past this point -- `composed.complete`, the obligation
            # coverage, a `CompletionVerdict`, and an `answered_count >= unserved_count` ratio --
            # and a fifth downstream turned a non-empty string into `FULFILLED` in the receipt.
            from core.conductor.product_decision import (
                INTEGRITY_FAILURE_RESPONSE,
                ExecutionReport,
                ProductDisposition,
                emergency_decision_payload,
                reduce_execution_report,
                total_failure_response,
            )

            decision = reduce_execution_report(
                ExecutionReport(
                    bound_plan=plan.bound_plan,
                    node_outcomes=tuple(outcomes),
                    planned_node_ids=tuple(node.node_id for node in plan.nodes),
                    requirement_nodes=dict(plan.requirement_nodes),
                )
            )
            # The decision travels WITH the turn on the context every downstream reader already
            # has. Fulfilment is a fact about requirements: a confidence float cannot carry it, and
            # overloading the route name would make every existing reader of that name wrong.
            decision_payload = decision.to_dict()
            if isinstance(source_context, dict):
                source_context["conductor_disposition"] = decision.disposition.value
                source_context["conductor_product_decision"] = dict(decision_payload)
            if turn_context is not None:
                turn_context["conductor_disposition"] = decision.disposition.value
                turn_context["conductor_product_decision"] = dict(decision_payload)

            if not decision.claimed:
                # NOTHING_CAPTURED, and no node produced anything either. This layer has no opinion,
                # which is the ONE state that still permits another lane to answer. Reached before
                # the gate is entered, so it is a legal decline rather than an un-claim.
                return None
            claim_gate.enter(decision)
        except Exception as exc:
            # PRE-CLAIM only. `enter` is the last statement in the block above, so nothing here can
            # be undoing a commitment -- the post-claim domain is the handler below.
            #
            # Declining is legal here and it is NOT silent. The version this replaces swallowed a
            # `TypeError` for as long as it took somebody to notice the conductor had stopped
            # existing, and while writing the tests for this seam a recursion did exactly the same
            # thing again. An emitted event costs nothing and is the difference between "the
            # conductor chose not to claim" and "the conductor has been broken since Tuesday".
            self._emit_runtime_event(
                source_context,
                event_type="conductor_declined",
                message=f"Conductor declined before claiming: {type(exc).__name__}: {exc}",
                error_class=type(exc).__name__,
            )
            return None

        # ------------------------------------------------------------------ POST-CLAIM
        # From here there is no `return None` and no fall-through to another lane. A composition or
        # dispatch defect is a fact about THIS turn's accounting, and the reader is told so. The
        # blanket `except Exception: return None` this replaces sent every conductor turn silently
        # to the ordinary lane for as long as it took somebody to notice the conductor had stopped
        # existing -- a TypeError from one keyword argument, invisible.
        try:
            if decision.disposition is ProductDisposition.INTEGRITY_FAILURE:
                self._emit_runtime_event(
                    source_context,
                    event_type="conductor_integrity_failure",
                    message=f"Conductor plan {plan.plan_id}: requirement accounting is broken",
                    plan_id=plan.plan_id,
                    decision=decision_payload,
                )
                return self._conductor_product_result(
                    decision,
                    session_id=session_id,
                    user_input=effective_input,
                    response=INTEGRITY_FAILURE_RESPONSE,
                    confidence=0.0,
                    source_context=source_context,
                    reason="conductor_integrity_failure",
                )

            composed = compose_answer(plan, outcomes, decision)
            text = composed.text.strip()
            if text:
                # The composed answer is what the reader gets, so this is the moment the
                # conductor's execution record becomes an attestation about served demands.
                _record_conductor_demand_receipts(plan, outcomes, request=effective_input)
                # The obligation this turn put on the table, so a follow-up has a subject: the
                # same record the live-data lane keeps (`remember_live_data_obligation`), keyed
                # on the market assets this plan quoted. A later "check it on the internet" or
                # "and solana?" rebinds to THIS request (FINDINGS F15).
                try:
                    from core.runtime_continuity import remember_live_data_obligation

                    quoted = [
                        str(node.arguments.get("entity") or node.arguments.get("asset_key") or "").strip()
                        for node in plan.nodes
                        if str(getattr(node, "operation", "") or "") == "market_quote"
                    ]
                    quoted = [name for name in quoted if name]
                    if quoted:
                        from core.conductor.operations import allocation_purchase_roles

                        allocation = allocation_purchase_roles(conductor_text)
                        remember_live_data_obligation(
                            session_id,
                            operation="allocation" if allocation is not None else "market_quote",
                            slots=[target.text for target in allocation.targets] if allocation is not None else quoted,
                            request_text=conductor_text,
                            absorbed_text=str(raw_input or effective_input),
                            source_turn_id=self._client_turn_id(source_context),
                        )
                except Exception:
                    pass
            if decision.disposition is not ProductDisposition.FULFILLED:
                # A claimed turn that did not fulfil every ask closes as such (FINDINGS F15):
                # the door reads this mark when it finalizes the attempt.
                try:
                    from core.agent_runtime.response import _mark_turn_unfulfilled

                    _mark_turn_unfulfilled(
                        turn_context if isinstance(turn_context, dict) else source_context,
                        f"conductor disposition {decision.disposition.value}",
                    )
                except Exception:
                    pass
            if not text:
                # A claimed turn with nothing rendered still owes the reader the truth about what
                # was asked. Falling through here would answer a different question and silently
                # drop the rest, which is what the deleted answered/unserved ratio did.
                text = total_failure_response(decision)
            return self._conductor_product_result(
                decision,
                session_id=session_id,
                user_input=effective_input,
                response=text,
                confidence=0.88 if decision.disposition is ProductDisposition.FULFILLED else 0.6,
                source_context=source_context,
                reason=CONDUCTOR_LANE_ID,
            )
        except BaseException as exc:
            failed = claim_gate.integrity_failure(exc)
            try:
                return self._conductor_product_result(
                    failed,
                    session_id=session_id,
                    user_input=effective_input,
                    response=INTEGRITY_FAILURE_RESPONSE,
                    confidence=0.0,
                    source_context=source_context,
                    reason="conductor_integrity_failure",
                )
            except BaseException:
                # The dispatch seam itself is broken. Still not None: a claimed turn returns a
                # claimed result. Assembled from a dict literal so nothing left here can fail --
                # and the decision is serialized by `emergency_decision_payload`, which cannot
                # raise, because the version of this that called `failed.to_dict()` inline WAS a
                # remaining call that could take this path down, directly under a comment saying
                # there was none. The payload carries the verdict the claim gate already made; it
                # does not form a new one. Bound at the top of the claimed block rather than
                # imported here: an import IS a call, and this handler may hold none.
                return {
                    "response": INTEGRITY_FAILURE_RESPONSE,
                    "confidence": 0.0,
                    "reason": "conductor_integrity_failure",
                    "conductor_product_decision": emergency_decision_payload(failed),
                    "task_outcome": "failed",
                }

    def _conductor_product_result(
        self,
        decision: Any,
        *,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict:
        """Dispatch a turn the conductor has CLAIMED, carrying its decision verbatim.

        The decision rides on the result under one key, and `terminal_fulfillment_outcome` reads
        the exact `RuntimeTaskOutcome` out of it. One copy, so the persisted status cannot drift
        from the disposition that produced it -- and no derivation from the response text, which is
        how a turn that served nothing came to be recorded as fulfilled because its apology was
        non-empty.
        """
        result = self._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=confidence,
            source_context=source_context,
            reason=reason,
        )
        if isinstance(result, dict):
            result["conductor_product_decision"] = decision.to_dict()
        return result

    @staticmethod
    def _ambiguity_type_of(exc: BaseException) -> str:
        return type(exc).__name__

    def _record_unresolved_adjudication(
        self,
        source_context: dict[str, object] | None,
        *,
        mode: str,
        attempts: int,
        detail: str,
    ) -> None:
        """Record an UNRESOLVED adjudication on the turn and emit its runtime event.

        The state is `unresolved` with its mode named; it is a record for the served trace and
        the finalization seam's second layer, never a substitute for the ask-back the caller
        serves. Recording can fail without changing the outcome.
        """
        if isinstance(source_context, dict):
            source_context["ambiguity_adjudication"] = {
                "state": "unresolved",
                "mode": str(mode),
                "attempts": int(attempts),
            }
        # This failure returns a whole-turn ask-back without answering generation.
        # Record that work outcome before echoed request words reach the text ladder.
        try:
            from core.conductor import obligation_ledger

            active = obligation_ledger.active_set()
            if active is not None:
                for demand in obligation_ledger.demand_obligations(*active):
                    obligation_ledger.record_slice_dispatch(
                        *active,
                        unit_id=str(demand.get("unit_id") or ""),
                        subtask_id="entity-ambiguity-adjudication",
                        operation="entity_ambiguity",
                        state="FAILED",
                        failure_reason=str(mode),
                    )
        except Exception:
            _log.exception("unresolved adjudication demand evidence was not recorded")
        try:
            if mode == "precheck_error":
                message = (
                    f"Entity-ambiguity pre-check faulted ({detail}); the turn asks back "
                    "instead of answering."
                )
            elif mode == "raised":
                message = (
                    f"Entity-ambiguity adjudication raised on {int(attempts)} attempt(s); the "
                    "turn asks back instead of answering."
                )
            else:
                message = (
                    "Entity-ambiguity adjudication did not complete after "
                    f"{int(attempts)} replies without a verdict; the turn asks back instead "
                    "of answering."
                )
            self._emit_runtime_event(
                source_context,
                event_type="ambiguity_adjudication_unresolved",
                message=message,
                details={"state": "unresolved", "mode": str(mode), "attempts": int(attempts)},
            )
        except Exception:
            pass

    def _maybe_clarify_ambiguous_entity(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """Serve a clarification instead of an answer when the question's entity is ambiguous.

        One plain knowledge question reaches here after every typed lane declined it — the
        shape where the served Springfield defect lived. The adjudication is a bounded
        policy-author call (`core.entity_ambiguity`'s JSON contract) judged WITH the
        session's recent turns, because prior context can resolve an entity the bare
        question leaves open. Three states, and the third is not the second:

        * AMBIGUOUS — the answering generation never runs; a clarification naming the
          referents ships.
        * UNAMBIGUOUS — the turn proceeds exactly as it always did.
        * UNRESOLVED — the probe timed out, returned garbage, or raised, twice, or the
          eligibility pre-check itself faulted. A failed check is NOT a green light:
          measured on a65768ab, fail-open published an invented Springfield number the
          moment adjudication failed. The turn serves an ask-back that says the check
          did not complete; no answering generation runs, so no entity-specific claim
          -- numeric or not -- can be published under an unadjudicated referent. This
          is scoped to adjudication FAILURE, never to ordinary questions whose check
          succeeded, and a clean no-eligible-author home stays on its ordinary path.
        """

        from core.entity_ambiguity import (
            AMBIGUITY_JSON_SCHEMA,
            AMBIGUITY_SYSTEM_PROMPT,
            build_probe_user_message,
            parse_ambiguity_verdict,
            render_clarification,
            render_unresolved_clarification,
            single_plain_know_question,
        )

        question = str(effective_input or "").strip()
        try:
            # The referent authorities (follow-up intents, the live-data continuation) are
            # consulted inside the jurisdiction test: a turn about work already on the table
            # is never adjudicated for entity ambiguity (FINDINGS F15).
            if not single_plain_know_question(question, source_context=source_context):
                return None
            prior_events: list = []
            try:
                from core.chat_session_identity import canonical_chat_session_id
                from core.memory.entries import recent_conversation_events

                prior_events = list(
                    recent_conversation_events(
                        canonical_chat_session_id(str(session_id or "")), limit=4
                    )
                    or ()
                )
            except Exception:
                prior_events = []
            from core.provider_call_deadline import bind_provider_deadline

            probe_context = bind_provider_deadline(
                source_context,
                # Measured: the FIRST judgment call on a freshly loaded author thinks for
                # 20-30 s; every later one answers in ~4 s. 20 s fail-opened the first
                # Springfield probe straight into the inventing answer (served, run-a),
                # so the budget is the measured cold ceiling plus margin.
                turn_deadline_monotonic=runtime_active_clock.monotonic() + 35.0,
                reason="ambiguity probe deadline",
            )
            from core.agent_runtime.turn_planner_hook import (
                build_conductor_ask_model,
                conductor_generation_candidates,
            )

            # ABSENT INFRASTRUCTURE IS NOT A FAILED ADJUDICATION. If no policy-eligible
            # author exists, no judgment call is possible and the probe says so BEFORE
            # attempting: the turn proceeds to its existing path, whose own gates (the
            # authorship gate refuses uncertified writers) still govern the answer.
            # Equating the two states is the mirror of the measured defect — it broke
            # every model-less harness turn the moment fail-open became fail-closed.
            try:
                from core.agent_runtime.audit_routing import (
                    resolve_routing_mode as _resolve_mode,
                )
                from core.agent_runtime.audit_routing import (
                    select_audit_manifests as _select_manifests,
                )
                from core.final_answer_authorship import (
                    FINAL_ANSWER_ROLE as _ROLE,
                )
                from core.final_answer_authorship import (
                    decide_final_answer_author as _decide,
                )

                _manifests, _ = _select_manifests(self, probe_context, _resolve_mode(probe_context))
                _eligible = any(
                    _decide(
                        request_text=question,
                        author_role=_ROLE,
                        requested_manifest=_m,
                        requested_model=str(getattr(_m, "provider_id", "") or ""),
                        allow_escalation=False,
                    ).eligible
                    for _m in conductor_generation_candidates(list(_manifests or []))
                )
            except Exception as _infra_error:
                # The pre-check itself faulted -- adjudication did NOT run. This is not the
                # clean absent-infrastructure case (no eligible author: the answer path's own
                # gates still refuse uncertified writers, so proceeding is safe); it is an
                # UNKNOWN, and the contract for an unknown is the same as for every other
                # unresolved adjudication: the turn asks back instead of answering. The
                # earlier design let the turn proceed and relied on finalization withholding
                # introduced NUMBERS -- a fence that lets "Springfield's mayor is John Smith"
                # through untouched, because the invention it guards against is numeric and
                # the invention that matters is the ENTITY SELECTION. No generation runs, so
                # no entity-specific claim of any shape can be published.
                _eligible = None
                self._record_unresolved_adjudication(
                    source_context,
                    mode="precheck_error",
                    attempts=0,
                    detail=self._ambiguity_type_of(_infra_error),
                )
                return self._fast_path_result(
                    session_id=session_id,
                    user_input=question,
                    response=render_unresolved_clarification(question, attempts=0),
                    confidence=0.5,
                    source_context=source_context,
                    reason="ambiguity_adjudication_unresolved",
                    classification_details={
                        "ambiguity": "unresolved", "attempts": 0, "mode": "precheck_error"
                    },
                )
            if not _eligible:
                return None

            # The judgment is bought from the POLICY-ELIGIBLE author, not the planner's
            # default: measured, the planner's qwen2.5:7b named Springfield's referents
            # and then answered {"ambiguous": false} — a self-contradiction only the
            # weaker model produced — while the certified author judged every control
            # correctly. The generation builder's pre-call policy filter, deadline
            # honesty and token ceiling are exactly the guarantees a probe needs; its
            # authorship-log side effect cannot pollute anything (the probe runs only
            # on turns the conductor already declined, so no knowledge node shares
            # the turn to join against).
            ask = build_conductor_ask_model(
                self, probe_context, reasoning_mode="disabled",
                default_json_schema=AMBIGUITY_JSON_SCHEMA,
            )
            probe_message = build_probe_user_message(question, prior_events)
            verdict = None
            attempts = 0
            raised = False
            for _ in (1, 2):
                attempts += 1
                try:
                    verdict = parse_ambiguity_verdict(
                        ask(AMBIGUITY_SYSTEM_PROMPT, probe_message)
                    )
                except Exception:
                    # A raised probe (deadline, provider fault) is a failed attempt with
                    # the verdict UNKNOWN — not a pass-through. The outer except must not
                    # turn it into "answer as if adjudicated": that is the measured
                    # defect (a65768ab, PROVIDER_TIMEOUT -> invented Springfield number).
                    verdict = None
                    raised = True
                if verdict is not None:
                    break
                # One retry: an empty, malformed or raised first attempt usually yields
                # a clean verdict on the second — the prompt is cached and the call is
                # cheap. Only a SECOND failure is UNRESOLVED.
            if verdict is None:
                # UNRESOLVED, whichever way it happened. A raised probe (deadline, provider
                # fault) and an author that REPLIED twice without a verdict are the same fact
                # for the reader: the runtime did not establish that the question has one
                # answer, so it does not publish one as if it had. The reply says exactly that
                # and asks. This is the module's documented three-state contract
                # (`core.entity_ambiguity`: UNRESOLVED "serves an ask-back that says the check
                # did not complete"); the replied-without-verdict mode previously diverged from
                # it by proceeding to the answering generation under a numeric-only fence.
                mode = "raised" if raised else "replied_without_verdict"
                self._record_unresolved_adjudication(
                    source_context, mode=mode, attempts=attempts, detail=""
                )
                return self._fast_path_result(
                    session_id=session_id,
                    user_input=question,
                    response=render_unresolved_clarification(question, attempts=attempts),
                    confidence=0.5,
                    source_context=source_context,
                    reason="ambiguity_adjudication_unresolved",
                    classification_details={
                        "ambiguity": "unresolved", "attempts": attempts, "mode": mode
                    },
                )
            if not verdict.ambiguous:
                return None
            return self._fast_path_result(
                session_id=session_id,
                user_input=question,
                response=render_clarification(verdict, question),
                confidence=0.9,
                source_context=source_context,
                reason="ambiguity_clarification_ask",
                classification_details={"ambiguity": "ambiguous", "attempts": attempts},
            )
        except Exception:
            return None

    def _maybe_answer_planned_turn(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict | None:
        """Answer every request in a multi-part message, or None to run the turn as it always was.

        None is the common path and must stay free: `turn_may_hold_several_requests` inside
        `plan_turn` rejects a single-request message before any model call, so "what is the price of
        bitcoin right now?" still reaches its deterministic 0.3s lookup untouched.

        Every failure -- no plan, a plan that invents content, a planner provider that is down, a
        merge that comes back empty -- returns None and the turn proceeds exactly as it did before
        this existed. This can only add the ability to answer several requests.
        """
        if bool((source_context or {}).get("planned_subturn")):
            # A sub-turn is one request by construction. Planning it again would recurse.
            return None
        if dict((source_context or {}).get("raw_output_contract") or {}).get(
            "structured_labels"
        ):
            return None
        try:
            from core.agent_runtime.turn_planner import merge_outcomes, plan_turn, run_plan
            from core.agent_runtime.turn_planner_hook import (
                build_planner_ask_model,
                build_planner_run_one,
                build_request_is_servable,
            )

            tasks = plan_turn(
                effective_input,
                ask_model=build_planner_ask_model(self, source_context),
                # Split ONLY when every part is an independent lookup a lane can serve. A
                # general-knowledge message answers better in one context: measured, splitting
                # "their engine sizes" and "what tires does it come with" away from their subject
                # produced "I don't have any context about what they refers to".
                request_is_servable=build_request_is_servable(),
            )
            if len(tasks) < 2:
                return None
            outcomes = run_plan(
                tasks,
                run_one=build_planner_run_one(
                    self, session_id=session_id, source_context=source_context
                ),
                # Sequential, deliberately. A planned sub-turn is a WHOLE turn -- a full generation,
                # not a cheap tool call -- so fanning three of them at one local model put three
                # simultaneous generations into Ollama. Measured 2026-08-05 on the three-part BMW
                # message: the first answered and the other two died at the 60s read timeout with
                # "qwen3:8b ... did not return a usable reply". Run sequentially the same three cost
                # roughly 3x24s and all three answer, which is the trade that matters: a person who
                # asks three things wants three answers, not one answer sooner.
                #
                # `local_model_admission`'s gate (wired in `build_planner_run_one`) bounds this
                # against OTHER traffic in the daemon; it cannot help here, since its ceiling of 4
                # is above a three-request wave. Concurrency can come back per-wave once a sub-turn
                # can declare whether its lane is local before it runs.
                max_workers=1,
            )
            merged = merge_outcomes(outcomes)
            if not merged.strip():
                return None
            # A plan where nothing at all could be answered is not better than the ordinary path;
            # let the normal turn try rather than shipping a page of failure notes.
            if not any(outcome.ok for outcome in outcomes):
                return None
            _record_planned_turn_demand_receipts(outcomes, request=str(effective_input or ""))
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=merged,
                confidence=0.86,
                source_context=source_context,
                reason="planned_multi_request_turn",
            )
        except Exception:
            return None

    def _handle_turn_frontdoor(
        self,
        *,
        raw_user_input: str,
        effective_input: str,
        normalized_input: str,
        source_surface: str,
        session_id: str,
        source_context: dict[str, object] | None,
        persona: Any,
        interpreted: Any,
        access_policy: Any | None = None,
        semantic_preflight: Any | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            normalized_input=normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
            persona=persona,
            interpreted=interpreted,
            access_policy=access_policy,
            maybe_handle_preference_command_fn=maybe_handle_preference_command,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )
        if semantic_preflight is not None:
            kwargs["semantic_preflight"] = semantic_preflight
        # D4: the SECOND ENTRANCE. `finalize_answer` holds the closure sweep centrally, but a
        # sweep is a no-op without a bound demand set, and the set was minted in exactly one
        # place — `_r3_open_turn_execution`, which only `run_once` calls. Measured by RED-1:
        # a turn entering here served `closure_verdict={}`, so the central placement bought
        # nothing on this door. Arming the door here (never re-arming a turn that already
        # carries a set) is what makes the property hold on the entrance as well as the exit.
        armed = _arm_demand_set_for_entrance(raw_user_input)
        try:
            outcome = agent_turn_frontdoor.handle_turn_frontdoor(self, **kwargs)
        finally:
            if armed is not None:
                _release_entrance_demand_set()
        if armed is not None:
            _certify_entrance_turn(outcome, armed, source_context)
        return outcome

    def _execute_grounded_turn(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpreted: Any,
        persona: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_turn_reasoning.execute_grounded_turn(
            self,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id=session_id,
            source_context=source_context,
            adapt_user_input_fn=adapt_user_input,
            ingest_media_evidence_fn=ingest_media_evidence,
            build_media_context_snippets_fn=build_media_context_snippets,
            orchestrate_parent_task_fn=orchestrate_parent_task,
            build_plan_fn=build_plan,
            render_response_fn=render_response,
            explicit_planner_style_requested_fn=explicit_planner_style_requested,
            should_use_planner_renderer_fn=should_use_planner_renderer,
            request_relevant_holders_fn=request_relevant_holders,
            dispatch_query_shard_fn=dispatch_query_shard,
            build_generalized_query_fn=build_generalized_query,
            feedback_engine_module=feedback_engine,
            policy_engine_module=policy_engine,
            from_task_result_fn=from_task_result,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
        )

    def _chat_surface_cache_or_memory_source(self, model_execution: Any) -> bool:
        return agent_memory_runtime.chat_surface_cache_or_memory_source(model_execution)

    def _chat_surface_model_final_text(self, model_execution: Any) -> str:
        return agent_memory_runtime.chat_surface_model_final_text(model_execution)

    def _chat_surface_honest_degraded_response(
        self,
        model_execution: Any,
        *,
        user_input: str = "",
        interpretation: Any | None = None,
    ) -> str:
        return agent_memory_runtime.chat_surface_honest_degraded_response(
            self,
            model_execution,
            user_input=user_input,
            interpretation=interpretation,
        )

    def _maybe_handle_credit_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None = None,
    ) -> dict | None:
        return agent_fast_command_surface.maybe_handle_credit_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
            transfer_credits_fn=transfer_credits,
            get_credit_balance_fn=get_credit_balance,
            escrow_credits_for_task_fn=escrow_credits_for_task,
            session_hive_state_fn=session_hive_state,
            runtime_session_id_fn=runtime_session_id,
        )

    def _fast_path_result(
        self,
        *,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
        classification_details: dict[str, Any] | None = None,
        runtime_event_details: dict[str, Any] | None = None,
        failure_text: str = "",
        route_prefix: str = "deterministic",
    ) -> dict:
        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "user_input": user_input,
            "response": response,
            "confidence": confidence,
            "source_context": source_context,
            "reason": reason,
            "append_conversation_event_fn": append_conversation_event,
            "audit_logger_module": audit_logger,
        }
        if route_prefix != "deterministic":
            kwargs["route_prefix"] = route_prefix
        if classification_details is not None:
            kwargs["classification_details"] = classification_details
        if runtime_event_details is not None:
            kwargs["runtime_event_details"] = runtime_event_details
        if failure_text:
            kwargs["failure_text"] = failure_text
        if reason in _UNFULFILLED_FAST_PATH_REASONS or failure_text:
            # A fast-path result whose reason names a non-fulfilment (FINDINGS F15) marks the
            # turn so the door closes its attempt PARTIAL_SUCCESS, never SUCCEEDED.
            try:
                from core.agent_runtime.response import _mark_turn_unfulfilled

                _mark_turn_unfulfilled(source_context, f"{reason}" if not failure_text else f"{reason}: {failure_text[:100]}")
            except Exception:
                pass
        return agent_fast_command_surface.fast_path_result(
            self,
            **kwargs,
        )

    def _action_fast_path_result(
        self,
        *,
        task_id: str,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
        success: bool,
        details: dict[str, object] | None = None,
        mode_override: str | None = None,
        task_outcome: str | None = None,
        learned_plan: Plan | None = None,
        workflow_summary: str = "",
    ) -> dict:
        return agent_fast_command_surface.action_fast_path_result(
            self,
            task_id=task_id,
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=confidence,
            source_context=source_context,
            reason=reason,
            success=success,
            details=details,
            mode_override=mode_override,
            task_outcome=task_outcome,
            learned_plan=learned_plan,
            workflow_summary=workflow_summary,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
            explicit_planner_style_requested_fn=explicit_planner_style_requested,
        )

    def _apply_interaction_transition(self, session_id: str, result: ChatTurnResult) -> None:
        agent_orchestrator_runtime.apply_interaction_transition(
            self,
            session_id,
            result,
            session_hive_state_fn=session_hive_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )

    def _maybe_handle_hive_frontdoor(
        self,
        *,
        raw_user_input: str,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
        return agent_hive_followups.maybe_handle_hive_frontdoor(
            self,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_runtime_command(
        self,
        user_input: str,
        *,
        session_id: str,
    ) -> tuple[bool, str, bool, dict[str, Any] | None]:
        return agent_hive_runtime.maybe_handle_hive_runtime_command(
            self,
            user_input,
            session_id=session_id,
        )

    def _recover_hive_runtime_command_input(self, user_input: str) -> str:
        return agent_hive_runtime.recover_hive_runtime_command_input(
            self,
            user_input,
            looks_like_semantic_hive_request_fn=looks_like_semantic_hive_request,
        )

    def _hive_tracker_needs_bridge_fallback(self, response: str) -> bool:
        return agent_hive_runtime.hive_tracker_needs_bridge_fallback(response)

    def _looks_like_hive_prompt_control_command(self, user_input: str) -> bool:
        return agent_hive_runtime.looks_like_hive_prompt_control_command(user_input)

    def _maybe_handle_hive_bridge_fallback(
        self,
        user_input: str,
        *,
        session_id: str,
        tracker_response: str,
    ) -> dict[str, Any] | None:
        return agent_hive_runtime.maybe_handle_hive_bridge_fallback(
            self,
            user_input,
            session_id=session_id,
            tracker_response=tracker_response,
        )

    def _store_hive_topic_selection_state(
        self,
        session_id: str,
        topics: list[dict[str, Any]],
    ) -> None:
        agent_hive_runtime.store_hive_topic_selection_state(
            session_id,
            topics,
            session_hive_state_fn=session_hive_state,
            update_session_hive_state_fn=update_session_hive_state,
        )

    def _sync_public_presence(
        self,
        *,
        status: str,
        source_context: dict[str, object] | None = None,
    ) -> None:
        agent_presence_runtime.sync_public_presence(
            self,
            status=status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name,
            audit_log_fn=audit_logger.log,
        )

    def _start_public_presence_heartbeat(self) -> None:
        agent_presence_runtime.start_public_presence_heartbeat(
            self,
            thread_factory=threading.Thread,
        )

    def _start_idle_commons_loop(self) -> None:
        agent_presence_runtime.start_idle_commons_loop(
            self,
            thread_factory=threading.Thread,
        )

    def _public_presence_heartbeat_loop(self) -> None:
        agent_presence_runtime.public_presence_heartbeat_loop(
            self,
            sleep_fn=time.sleep,
        )

    def _idle_commons_loop(self) -> None:
        agent_presence_runtime.idle_commons_loop(
            self,
            sleep_fn=time.sleep,
            audit_log_fn=audit_logger.log,
        )

    def _maybe_run_idle_commons_once(self) -> None:
        agent_presence_runtime.maybe_run_idle_commons_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            audit_log_fn=audit_logger.log,
        )

    def _maybe_execute_queued_curiosity_once(self) -> dict | None:
        return agent_presence_runtime.maybe_execute_queued_curiosity_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            audit_log_fn=audit_logger.log,
        )

    def stop_background_runtime_threads(self) -> dict[str, bool]:
        """Stop the idle-commons and presence loops (runtime shutdown)."""
        return agent_presence_runtime.stop_background_runtime_threads(self)

    def _maybe_run_autonomous_hive_research_once(self) -> None:
        agent_presence_runtime.maybe_run_autonomous_hive_research_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            pick_signal_fn=pick_autonomous_research_signal,
            research_topic_fn=research_topic_from_signal,
            audit_log_fn=audit_logger.log,
        )

    def _mark_user_activity(self) -> None:
        with self._activity_lock:
            self._last_user_activity_ts = time.time()

    @contextlib.contextmanager
    def _turn_in_flight(self):
        """The span of one served turn, for the background lanes' idle gate.

        Marking activity at turn START was not enough: measured on the s51 rig (dad61973),
        a 163 s research turn looked idle after 60 s and the curiosity consumer ran a topic
        beside it. A turn in flight is activity for its whole span, and the idle clock
        restarts when it ENDS, so background work waits for the user's turn, never the
        other way round.
        """
        with self._activity_lock:
            self._turns_in_flight = int(getattr(self, "_turns_in_flight", 0) or 0) + 1
            self._last_user_activity_ts = time.time()
        try:
            yield
        finally:
            with self._activity_lock:
                self._turns_in_flight = max(0, int(getattr(self, "_turns_in_flight", 1) or 1) - 1)
                self._last_user_activity_ts = time.time()

    def _idle_commons_session_id(self) -> str:
        return agent_presence_runtime.idle_commons_session_id(
            get_local_peer_id_fn=signer_mod.get_local_peer_id,
        )

    def _normalize_public_presence_status(self, status: str) -> str:
        return agent_presence_runtime.normalize_public_presence_status(self, status)

    def _idle_public_presence_status(self) -> str:
        return agent_presence_runtime.idle_public_presence_status(
            load_preferences_fn=load_preferences,
        )

    def _maybe_handle_hive_research_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_research_followup(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            session_hive_state_fn=session_hive_state,
            clear_hive_interaction_state_fn=clear_hive_interaction_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _maybe_resume_active_hive_task(
        self,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_resume_active_hive_task(
            self,
            lowered,
            session_id=session_id,
            source_context=source_context,
            hive_state=hive_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _extract_hive_topic_hint(self, text: str) -> str:
        return agent_hive_followups.extract_hive_topic_hint(text)


def main() -> int:
    parser = argparse.ArgumentParser(prog="vool-agent")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--persona", default="default")
    parser.add_argument("--input", default="")
    parser.add_argument("--json", action="store_true", help="Print full response payload as JSON.")
    args = parser.parse_args()

    from core.runtime_bootstrap import bootstrap_runtime_mode


    backend_name = str(args.backend)
    device = str(args.device)
    boot = bootstrap_runtime_mode(
        mode="agent",
        force_policy_reload=True,
        resolve_backend=backend_name == "auto" or device == "auto",
    )

    if backend_name == "auto" or device == "auto":
        selection = boot.backend_selection
        if selection is None:
            raise RuntimeError("Runtime bootstrap did not resolve a backend selection.")
        backend_name = backend_name if backend_name != "auto" else selection.backend_name
        device = device if device != "auto" else selection.device

    agent = VoolAgent(
        backend_name=backend_name,
        device=device,
        persona_id=str(args.persona),
    )
    agent.start()
    if not str(args.input or "").strip():
        print("Vool agent started. Provide --input for one-shot execution.")
        return 0

    result = agent.run_once(str(args.input))
    # R-8 (K-11): one-shot CLI serves the SEALED commit bytes -- commit-first,
    # mirroring vool_chat.py; raw response text only as typed no-answer frame.
    _cli_commit = (
        result.get("vool_response_commit")
        if isinstance(result.get("vool_response_commit"), dict)
        else {}
    )
    _cli_text = str(_cli_commit.get("canonical_content") or "") or str(
        result.get("response") or ""
    ).strip()
    if bool(args.json):
        print(json.dumps({**result, "served_bytes": _cli_text}, indent=2, sort_keys=True))
    else:
        print(_cli_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
