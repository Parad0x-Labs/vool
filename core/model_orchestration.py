from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.model_escalation_policy import (
    EscalationReason,
    PaidModelPolicy,
    PaidPolicyDecision,
    evaluate_paid_escalation,
)
from core.model_handoff_capsule import (
    ModelHandoffCapsule,
    build_local_return_handoff,
    select_return_local_lane,
    validate_paid_result,
)
from core.model_orchestration_state import ModelOrchestrationState, ModelOrchestrationStore
from core.model_spend_ledger import SpendLimits, SpendReservation, reserve_spend, settle_spend

EventSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class PaidEscalation:
    run_id: str
    capsule: ModelHandoffCapsule
    model_id: str
    local_model_id: str
    estimated_low_usd: float
    estimated_high_usd: float
    hard_cap_usd: float
    decision: PaidPolicyDecision

    def summary(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "reason": self.decision.reason,
            "expected_cost_range_usd": [self.estimated_low_usd, self.estimated_high_usd],
            "hard_cap_usd": self.hard_cap_usd,
            "payload": self.capsule.payload_summary(),
            "privacy": "Only the listed capsule items leave this device.",
            "continue_locally": True,
        }


@dataclass(frozen=True)
class AuthorizedPaidCall:
    escalation: PaidEscalation
    reservation: SpendReservation
    model_call_id: str
    # Concrete provider manifest identity when the caller resolved one. The orchestration policy
    # itself binds model_id; this additive field lets spend receipts name the exact route too.
    provider_id: str = ""


class ModelOrchestrator:
    def __init__(self, *, store: ModelOrchestrationStore | None = None, event_sink: EventSink | None = None) -> None:
        self.store = store or ModelOrchestrationStore()
        self.event_sink = event_sink

    def start_local(
        self,
        *,
        task_id: str,
        turn_id: str,
        subtask_id: str,
        session_id: str,
        lane: str,
        model_id: str,
        reason: str,
    ) -> str:
        self._emit("model.selection_started", task_id=task_id, turn_id=turn_id, subtask_id=subtask_id, reason=reason)
        run_id = self.store.create_run(
            task_id=task_id,
            turn_id=turn_id,
            subtask_id=subtask_id,
            session_id=session_id,
            lane=lane,
            model_id=model_id,
            reason=reason,
        )
        self.store.transition(
            run_id,
            ModelOrchestrationState.LOCAL_RUNNING,
            source_model=model_id,
            target_model=model_id,
            reason="local_execution_started",
            active_lane=lane,
        )
        self._emit("model.selected", run_id=run_id, task_id=task_id, turn_id=turn_id, subtask_id=subtask_id, to_lane=lane, model_id=model_id, locality="local")
        return run_id

    def switch_local(self, run_id: str, *, from_model: str, to_model: str, to_lane: str, reason: str) -> None:
        self._emit("model.switch_started", run_id=run_id, from_model=from_model, model_id=to_model, to_lane=to_lane, reason=reason)
        self.store.transition(
            run_id,
            ModelOrchestrationState.LOCAL_VERIFYING,
            source_model=from_model,
            target_model=from_model,
            reason="local_handoff_capsule_ready",
            verification_result="handoff_validated",
        )
        self.store.transition(
            run_id,
            ModelOrchestrationState.LOCAL_SELECTED,
            source_model=from_model,
            target_model=to_model,
            reason=reason,
            active_lane=to_lane,
        )
        self.store.transition(
            run_id,
            ModelOrchestrationState.LOCAL_RUNNING,
            source_model=to_model,
            target_model=to_model,
            reason="local_execution_resumed",
            active_lane=to_lane,
        )
        self._emit("model.switch_completed", run_id=run_id, model_id=to_model, to_lane=to_lane, locality="local")

    def evaluate_paid(
        self,
        run_id: str,
        *,
        capsule: ModelHandoffCapsule,
        local_model_id: str,
        model_id: str,
        reason: EscalationReason,
        policy: PaidModelPolicy,
        openrouter_configured: bool,
        network_allowed: bool,
        data_categories: tuple[str, ...],
        estimated_low_usd: float,
        estimated_high_usd: float,
        hard_cap_usd: float,
        approval_source: str = "",
    ) -> PaidEscalation:
        self.store.transition(
            run_id,
            ModelOrchestrationState.ESCALATION_EVALUATING,
            source_model=local_model_id,
            target_model=model_id,
            reason=reason,
            context_capsule_ref=capsule.capsule_id,
            spend_estimate_usd=estimated_high_usd,
        )
        decision = evaluate_paid_escalation(
            policy,
            reason=reason,
            model_id=model_id,
            openrouter_configured=openrouter_configured,
            network_allowed=network_allowed,
            estimated_max_usd=hard_cap_usd,
            data_categories=data_categories,
            approval_source=approval_source,
        )
        self._emit("model.escalation_evaluated", run_id=run_id, reason=reason, action=decision.action, model_id=model_id)
        escalation = PaidEscalation(
            run_id=run_id,
            capsule=capsule,
            model_id=model_id,
            local_model_id=local_model_id,
            estimated_low_usd=estimated_low_usd,
            estimated_high_usd=estimated_high_usd,
            hard_cap_usd=hard_cap_usd,
            decision=decision,
        )
        if decision.action == "continue_local":
            self.store.transition(
                run_id,
                ModelOrchestrationState.LOCAL_SELECTED,
                source_model=local_model_id,
                target_model=local_model_id,
                reason=decision.reason,
                policy_decision=decision.action,
            )
            self.store.transition(
                run_id,
                ModelOrchestrationState.LOCAL_RUNNING,
                source_model=local_model_id,
                target_model=local_model_id,
                reason="continue_local",
            )
            return escalation
        self.store.transition(
            run_id,
            ModelOrchestrationState.ESCALATION_PROPOSED,
            source_model=local_model_id,
            target_model=model_id,
            reason=decision.reason,
            context_capsule_ref=capsule.capsule_id,
            spend_estimate_usd=hard_cap_usd,
            policy_decision=decision.action,
        )
        next_state = (
            ModelOrchestrationState.AWAITING_USER_APPROVAL
            if decision.requires_approval
            else ModelOrchestrationState.PAID_REQUEST_PREPARING
        )
        self.store.transition(
            run_id,
            next_state,
            source_model=local_model_id,
            target_model=model_id,
            reason=decision.reason,
            context_capsule_ref=capsule.capsule_id,
            spend_estimate_usd=hard_cap_usd,
            policy_decision=decision.action,
        )
        self._emit("model.escalation_proposed", run_id=run_id, **escalation.summary())
        return escalation

    def authorize_paid(
        self,
        escalation: PaidEscalation,
        *,
        limits: SpendLimits,
        user_approved: bool,
    ) -> AuthorizedPaidCall | None:
        current = self.store.current(escalation.run_id)
        if current is None:
            raise KeyError(escalation.run_id)
        if current["state"] == ModelOrchestrationState.AWAITING_USER_APPROVAL:
            if not user_approved:
                self.store.transition(
                    escalation.run_id,
                    ModelOrchestrationState.LOCAL_SELECTED,
                    source_model=escalation.local_model_id,
                    target_model=escalation.local_model_id,
                    reason="user_denied_paid_escalation",
                    policy_decision="denied",
                )
                self.store.transition(
                    escalation.run_id,
                    ModelOrchestrationState.LOCAL_RUNNING,
                    source_model=escalation.local_model_id,
                    target_model=escalation.local_model_id,
                    reason="continue_local_without_reprompt",
                )
                self._emit("model.escalation_denied", run_id=escalation.run_id, reason="user_denied")
                return None
            self.store.transition(
                escalation.run_id,
                ModelOrchestrationState.PAID_REQUEST_PREPARING,
                source_model=escalation.local_model_id,
                target_model=escalation.model_id,
                reason="explicit_user_approval",
                policy_decision="approved_by_user",
            )
            self._emit("model.escalation_approved", run_id=escalation.run_id, model_id=escalation.model_id)
        elif not escalation.decision.allowed:
            raise PermissionError("paid escalation is not authorized")
        model_call_id = f"model-call-{uuid.uuid4().hex}"
        reservation = reserve_spend(
            model_call_id=model_call_id,
            task_id=escalation.capsule.task_id,
            subtask_id=escalation.capsule.subtask_id,
            model_id=escalation.model_id,
            maximum_usd=escalation.hard_cap_usd,
            limits=limits,
        )
        self.store.transition(
            escalation.run_id,
            ModelOrchestrationState.PAID_RUNNING,
            source_model=escalation.local_model_id,
            target_model=escalation.model_id,
            reason="spend_reserved",
            context_capsule_ref=escalation.capsule.capsule_id,
            spend_estimate_usd=escalation.hard_cap_usd,
            policy_decision="authorized",
            metadata={"model_call_id": model_call_id, "reservation_id": reservation.reservation_id},
        )
        self._emit("model.paid_started", run_id=escalation.run_id, model_call_id=model_call_id, model_id=escalation.model_id, estimated_cost=escalation.hard_cap_usd, locality="cloud")
        return AuthorizedPaidCall(escalation=escalation, reservation=reservation, model_call_id=model_call_id)

    def complete_paid(
        self,
        call: AuthorizedPaidCall,
        *,
        result: dict[str, Any],
        actual_usd: float,
        allowed_files: tuple[str, ...],
        remaining_work: str,
        context_tokens: int,
        heavy_available: bool,
    ) -> dict[str, Any]:
        settled = settle_spend(call.model_call_id, actual_usd=actual_usd)
        self.store.transition(
            call.escalation.run_id,
            ModelOrchestrationState.PAID_VALIDATING,
            source_model=call.escalation.model_id,
            target_model=call.escalation.model_id,
            reason="paid_response_received",
            actual_spend_usd=settled.actual_usd,
        )
        valid, errors = validate_paid_result(result, capsule=call.escalation.capsule, allowed_files=allowed_files)
        if not valid:
            self.store.transition(
                call.escalation.run_id,
                ModelOrchestrationState.PAID_FAILED,
                source_model=call.escalation.model_id,
                target_model=call.escalation.local_model_id,
                reason="paid_result_validation_failed",
                actual_spend_usd=settled.actual_usd,
                verification_result=",".join(errors),
            )
            self._emit("model.paid_failed", run_id=call.escalation.run_id, model_call_id=call.model_call_id, actual_cost=actual_usd, errors=list(errors))
        else:
            self._emit("model.paid_completed", run_id=call.escalation.run_id, model_call_id=call.model_call_id, actual_cost=actual_usd)
        self.store.transition(
            call.escalation.run_id,
            ModelOrchestrationState.RETURNING_LOCAL,
            source_model=call.escalation.model_id,
            target_model=call.escalation.local_model_id,
            reason="paid_subtask_finished" if valid else "paid_result_rejected",
            actual_spend_usd=settled.actual_usd,
            verification_result="accepted" if valid else "rejected",
        )
        lane = select_return_local_lane(
            remaining_work=remaining_work,
            context_tokens=context_tokens,
            heavy_available=heavy_available,
        )
        self._emit("model.returning_local", run_id=call.escalation.run_id, to_lane=lane, model_id=call.escalation.local_model_id)
        self.store.transition(
            call.escalation.run_id,
            ModelOrchestrationState.LOCAL_RESUMED,
            source_model=call.escalation.model_id,
            target_model=call.escalation.local_model_id,
            reason="local_provider_restored",
            actual_spend_usd=settled.actual_usd,
            verification_result="paid_result_accepted" if valid else "paid_result_rejected",
            active_lane=lane,
        )
        self._emit("model.local_resumed", run_id=call.escalation.run_id, to_lane=lane, model_id=call.escalation.local_model_id, locality="local")
        handoff = build_local_return_handoff(
            decision=str(result.get("diagnosis") or result.get("decision") or "") if valid else "",
            proposed_changes=tuple(str(item) for item in list(result.get("proposed_changes") or [])) if valid else (),
            constraints=tuple(call.escalation.capsule.rules),
            verification_steps=tuple(call.escalation.capsule.verification_criteria),
            unresolved_concerns=errors,
        )
        return {"accepted": valid, "errors": errors, "return_lane": lane, "handoff": handoff, "actual_usd": settled.actual_usd}

    def _emit(self, event_type: str, **details: Any) -> None:
        if self.event_sink is not None:
            self.event_sink(
                event_type,
                {key: value for key, value in details.items() if value is not None and value != ""},
            )


__all__ = ["AuthorizedPaidCall", "ModelOrchestrator", "PaidEscalation"]
