from __future__ import annotations

import threading
import uuid

from core.model_escalation_policy import (
    EscalationReason,
    PaidModelMode,
    PaidModelPolicy,
    evaluate_paid_escalation,
)
from core.model_spend_ledger import SpendLimits, reserve_spend, settle_spend
from core.openrouter_catalog import parse_openrouter_catalog, recommend_openrouter_model


def _policy(mode: PaidModelMode) -> PaidModelPolicy:
    return PaidModelPolicy(
        mode=mode,
        per_call_max_usd=0.10,
        per_task_max_usd=0.15,
        daily_max_usd=1.0,
        monthly_max_usd=5.0,
        model_allowlist=("provider/specialist",),
        permitted_data_categories=("source_excerpt", "error_log"),
    )


def test_tool_or_permission_failures_never_trigger_paid_model() -> None:
    for reason in (EscalationReason.PERMISSION_REQUIRED, EscalationReason.TOOL_MISSING, EscalationReason.TOOL_FAILED):
        decision = evaluate_paid_escalation(
            _policy(PaidModelMode.AUTO_UNDER_LIMIT),
            reason=reason,
            model_id="provider/specialist",
            openrouter_configured=True,
            network_allowed=True,
            estimated_max_usd=0.05,
            data_categories=("source_excerpt",),
        )
        assert decision.allowed is False
        assert decision.action == "continue_local"


def test_ask_mode_requires_user_not_model_approval() -> None:
    kwargs = dict(
        reason=EscalationReason.LOCAL_MODEL_CAPABILITY,
        model_id="provider/specialist",
        openrouter_configured=True,
        network_allowed=True,
        estimated_max_usd=0.05,
        data_categories=("source_excerpt",),
    )
    assert evaluate_paid_escalation(_policy(PaidModelMode.LOCAL_FIRST_ASK), **kwargs, approval_source="model").action == "ask_user"
    approved = evaluate_paid_escalation(_policy(PaidModelMode.LOCAL_FIRST_ASK), **kwargs, approval_source="user")
    assert approved.allowed is True
    assert approved.action == "paid_call"


def test_secret_or_unapproved_data_fails_closed() -> None:
    policy = _policy(PaidModelMode.AUTO_UNDER_LIMIT)
    base = dict(
        reason=EscalationReason.LOCAL_MODEL_REPEATED_FAILURE,
        model_id="provider/specialist",
        openrouter_configured=True,
        network_allowed=True,
        estimated_max_usd=0.05,
    )
    assert evaluate_paid_escalation(policy, **base, contains_secrets=True).reason == "secret_in_payload"
    assert evaluate_paid_escalation(policy, **base, data_categories=("wallet_key",)).reason == "data_category_not_permitted"


def test_atomic_reservations_prevent_concurrent_task_cap_overshoot() -> None:
    task_id = f"task-{uuid.uuid4().hex}"
    limits = SpendLimits(per_call_usd=0.10, per_task_usd=0.15, daily_usd=10.0, monthly_usd=20.0)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def reserve(index: int) -> None:
        barrier.wait()
        try:
            reservation = reserve_spend(
                model_call_id=f"model-call-{uuid.uuid4().hex}",
                task_id=task_id,
                subtask_id=f"subtask-{index}",
                model_id="provider/specialist",
                maximum_usd=0.10,
                limits=limits,
            )
            outcomes.append(reservation.status)
        except PermissionError as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=reserve, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["per_task_spend_cap_exceeded", "reserved"]


def test_settlement_replaces_reservation_with_actual_cost() -> None:
    call_id = f"model-call-{uuid.uuid4().hex}"
    reservation = reserve_spend(
        model_call_id=call_id,
        task_id=f"task-{uuid.uuid4().hex}",
        subtask_id="diagnosis",
        model_id="provider/specialist",
        maximum_usd=0.10,
        limits=SpendLimits(per_call_usd=0.10, per_task_usd=0.20, daily_usd=10.0, monthly_usd=20.0),
    )
    settled = settle_spend(call_id, actual_usd=0.037)
    assert reservation.reserved_usd == 0.10
    assert settled.reserved_usd == 0.0
    assert settled.actual_usd == 0.037
    assert settled.status == "settled"


def test_catalog_uses_timestamped_provider_prices_and_verified_value_per_dollar() -> None:
    payload = {
        "data": [
            {
                "id": "provider/cheap",
                "context_length": 32000,
                "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"},
                "supported_parameters": ["tools"],
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            },
            {
                "id": "provider/reliable",
                "context_length": 64000,
                "pricing": {"prompt": "0.000002", "completion": "0.000004", "request": "0"},
                "supported_parameters": ["tools"],
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            },
        ]
    }
    models = parse_openrouter_catalog(payload, fetched_at="2026-07-14T00:00:00+00:00")
    selected = recommend_openrouter_model(
        models,
        required_context=16000,
        required_parameters=("tools",),
        input_tokens=1000,
        output_tokens=500,
        acceptance_probability={"provider/cheap": 0.1, "provider/reliable": 0.9},
    )
    assert selected is not None
    assert selected.model_id == "provider/reliable"
    assert selected.fetched_at == "2026-07-14T00:00:00+00:00"
