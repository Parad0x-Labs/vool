from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from core.memory_first_router import MemoryFirstRouter
from core.model_escalation_policy import EscalationReason, PaidModelMode, PaidModelPolicy
from core.model_handoff_capsule import CapsuleItem, build_model_handoff_capsule
from core.model_orchestration import ModelOrchestrator
from core.model_orchestration_state import ModelOrchestrationState
from core.model_spend_ledger import SpendLimits
from core.runtime_paths import configure_runtime_home
from storage.model_provider_manifest import ModelProviderManifest


@pytest.fixture(autouse=True)
def _reset_runtime_home():
    yield
    configure_runtime_home(None)


def _capsule(tmp_path, suffix: str):
    configure_runtime_home(tmp_path / "runtime")
    source = tmp_path / "parser.py"
    return build_model_handoff_capsule(
        task_id=f"task-{suffix}",
        turn_id=f"turn-{suffix}",
        subtask_id=f"subtask-{suffix}",
        user_goal="Fix parser",
        blocked_subtask="Diagnose parser ambiguity",
        rules=("No unrelated changes",),
        plan_state=("Failure reproduced locally",),
        expected_output_schema={"required": ["diagnosis", "proposed_files"]},
        forbidden_operations=("edit files",),
        verification_criteria=("Run parser tests locally",),
        items=(CapsuleItem("source", "source_excerpt", "def parse(x): return x", "parser.py:1", str(source)),),
        approved_workspace_roots=(str(tmp_path),),
    ), source


def _policy() -> PaidModelPolicy:
    return PaidModelPolicy(
        mode=PaidModelMode.LOCAL_FIRST_ASK,
        per_call_max_usd=0.10,
        per_task_max_usd=0.20,
        daily_max_usd=20.0,
        monthly_max_usd=100.0,
        model_allowlist=("provider/specialist",),
        permitted_data_categories=("source_excerpt",),
    )


def test_daily_to_heavy_to_daily_local_switch(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    events: list[tuple[str, dict]] = []
    orchestrator = ModelOrchestrator(event_sink=lambda event_type, details: events.append((event_type, details)))
    run_id = orchestrator.start_local(
        task_id=f"task-{suffix}", turn_id=f"turn-{suffix}", subtask_id="repo-review",
        session_id=f"session-{suffix}", lane="LOCAL_DAILY", model_id="daily-model",
        reason="ordinary_execution",
    )
    orchestrator.switch_local(
        run_id, from_model="daily-model", to_model="heavy-model", to_lane="LOCAL_HEAVY",
        reason="repository_wide_reasoning",
    )
    orchestrator.switch_local(
        run_id, from_model="heavy-model", to_model="daily-model", to_lane="LOCAL_DAILY",
        reason="decision_complete_edits_remain",
    )
    current = orchestrator.store.current(run_id)
    assert current["state"] == ModelOrchestrationState.LOCAL_RUNNING
    assert current["active_lane"] == "LOCAL_DAILY"
    assert [event[0] for event in events].count("model.switch_completed") == 2


def test_paid_approval_validates_result_and_returns_local(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    capsule, source = _capsule(tmp_path, suffix)
    events: list[tuple[str, dict]] = []
    orchestrator = ModelOrchestrator(event_sink=lambda event_type, details: events.append((event_type, details)))
    run_id = orchestrator.start_local(
        task_id=capsule.task_id, turn_id=capsule.turn_id, subtask_id=capsule.subtask_id,
        session_id=f"session-{suffix}", lane="LOCAL_DAILY", model_id="daily-model",
        reason="ordinary_execution",
    )
    escalation = orchestrator.evaluate_paid(
        run_id,
        capsule=capsule,
        local_model_id="daily-model",
        model_id="provider/specialist",
        reason=EscalationReason.LOCAL_MODEL_CAPABILITY,
        policy=_policy(),
        openrouter_configured=True,
        network_allowed=True,
        data_categories=("source_excerpt",),
        estimated_low_usd=0.03,
        estimated_high_usd=0.08,
        hard_cap_usd=0.10,
    )
    assert escalation.summary()["payload"]["paths"] == [str(source.resolve())]
    authorized = orchestrator.authorize_paid(
        escalation,
        limits=SpendLimits(per_call_usd=0.10, per_task_usd=0.20, daily_usd=20.0, monthly_usd=100.0),
        user_approved=True,
    )
    assert authorized is not None
    completed = orchestrator.complete_paid(
        authorized,
        result={
            "task_id": capsule.task_id,
            "turn_id": capsule.turn_id,
            "subtask_id": capsule.subtask_id,
            "diagnosis": "Add an explicit parser branch.",
            "proposed_files": [str(source)],
            "proposed_changes": ["Update parser.py"],
        },
        actual_usd=0.041,
        allowed_files=(str(source),),
        remaining_work="edits",
        context_tokens=4000,
        heavy_available=True,
    )
    assert completed["accepted"] is True, completed
    assert completed["return_lane"] == "LOCAL_DAILY"
    assert completed["handoff"]["paid_provider_active"] is False
    assert orchestrator.store.current(run_id)["state"] == ModelOrchestrationState.LOCAL_RESUMED
    assert [event[0] for event in events][-2:] == ["model.returning_local", "model.local_resumed"]


def test_paid_denial_returns_local_without_reservation_or_reprompt(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    capsule, _ = _capsule(tmp_path, suffix)
    orchestrator = ModelOrchestrator()
    run_id = orchestrator.start_local(
        task_id=capsule.task_id, turn_id=capsule.turn_id, subtask_id=capsule.subtask_id,
        session_id=f"session-{suffix}", lane="LOCAL_DAILY", model_id="daily-model", reason="ordinary_execution",
    )
    escalation = orchestrator.evaluate_paid(
        run_id, capsule=capsule, local_model_id="daily-model", model_id="provider/specialist",
        reason=EscalationReason.LOCAL_MODEL_REPEATED_FAILURE, policy=_policy(),
        openrouter_configured=True, network_allowed=True, data_categories=("source_excerpt",),
        estimated_low_usd=0.03, estimated_high_usd=0.08, hard_cap_usd=0.10,
    )
    assert orchestrator.authorize_paid(
        escalation,
        limits=SpendLimits(per_call_usd=0.10, per_task_usd=0.20, daily_usd=20.0, monthly_usd=100.0),
        user_approved=False,
    ) is None
    assert orchestrator.store.current(run_id)["state"] == ModelOrchestrationState.LOCAL_RUNNING


def test_router_requires_and_consumes_call_bound_paid_authorization(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    capsule, _ = _capsule(tmp_path, suffix)
    orchestrator = ModelOrchestrator()
    run_id = orchestrator.start_local(
        task_id=capsule.task_id, turn_id=capsule.turn_id, subtask_id=capsule.subtask_id,
        session_id=f"session-{suffix}", lane="LOCAL_DAILY", model_id="daily-model", reason="ordinary_execution",
    )
    policy = PaidModelPolicy(
        mode=PaidModelMode.AUTO_UNDER_LIMIT,
        per_call_max_usd=0.10,
        per_task_max_usd=0.20,
        daily_max_usd=20.0,
        monthly_max_usd=100.0,
        model_allowlist=("provider/specialist",),
        permitted_data_categories=("source_excerpt",),
    )
    escalation = orchestrator.evaluate_paid(
        run_id, capsule=capsule, local_model_id="daily-model", model_id="provider/specialist",
        reason=EscalationReason.LOCAL_MODEL_CAPABILITY, policy=policy,
        openrouter_configured=True, network_allowed=True, data_categories=("source_excerpt",),
        estimated_low_usd=0.03, estimated_high_usd=0.08, hard_cap_usd=0.10,
    )
    authorized = orchestrator.authorize_paid(
        escalation,
        limits=SpendLimits(per_call_usd=0.10, per_task_usd=0.20, daily_usd=20.0, monthly_usd=100.0),
        user_approved=False,
    )
    assert authorized is not None
    manifest = ModelProviderManifest(
        provider_name="openrouter-byok", model_name="provider/specialist", source_type="http",
        adapter_type="openai_compatible", license_name="Provider", license_reference="user-managed",
        weight_location="external", runtime_dependency="openrouter", capabilities=["summarize"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        metadata={"deployment_class": "cloud", "cost_class": "paid_cloud"},
    )
    # The router-facing reservation names the lane it authorizes (the owner-pick path stamps it).
    authorized = replace(authorized, provider_id=manifest.provider_id)
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.run_text_task.return_value = ModelResponse(output_text="bounded diagnosis")
    router = MemoryFirstRouter()
    task = SimpleNamespace(task_id=capsule.task_id)

    with mock.patch.object(router.registry, "build_adapter", return_value=adapter) as build_adapter:
        _, blocked, blocked_error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="diagnosis", prompt="bounded capsule"),
            output_mode="plain_text",
            task=task,
            source_context={},
        )
        _, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="diagnosis", prompt="bounded capsule"),
            output_mode="plain_text",
            task=task,
            source_context={"authorized_paid_call": authorized},
        )

    assert blocked is None
    assert blocked_error == "paid_call_not_authorized_or_reserved"
    assert error is None
    assert response is not None
    assert response.model_call_id == authorized.model_call_id
    assert build_adapter.call_count == 1
