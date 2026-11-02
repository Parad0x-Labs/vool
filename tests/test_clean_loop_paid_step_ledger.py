"""A clean-loop step on the owner's paid cloud model reserves, settles and closes on the real ledger.

The money review of the cloud brain found each step's orchestration run left PAID_RUNNING for good
and no paid_call.* receipt emitted, because the step's call role was unknown to the reservation
module. These tests drive the real spend ledger and orchestration store in a temp home.

NO REAL CALL IS MADE ANYWHERE HERE: the reservation is made and settled directly; no adapter,
transport or key is involved.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core import paid_call_reservation as pcr
from core.cloud_providers import config_for
from storage.model_provider_manifest import ModelProviderManifest

MESSAGES = [{"role": "system", "content": "s" * 4000}, {"role": "user", "content": "whats on my desktop"}]


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    pcr._ATTEMPTS.clear()
    yield
    pcr._ATTEMPTS.clear()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _paid_manifest() -> ModelProviderManifest:
    cfg = config_for("anthropic")
    return ModelProviderManifest(
        provider_name="anthropic-byok",
        model_name="claude-sonnet-4-5",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        capabilities=["summarize"],
        runtime_config={"base_url": cfg.base_url, "api_path": "/chat/completions", "credential_key": cfg.credential_slot},
        metadata={"cost_class": "paid_cloud"},
    )


def test_a_settled_paid_step_completes_its_run_and_emits_receipts():
    from core.clean_loop import brain
    from core.model_orchestration_state import ModelOrchestrationStore
    from core.model_spend_ledger import get_spend_reservation

    events: list[str] = []
    with mock.patch("core.runtime_task_events.emit_runtime_event", lambda ctx, **kw: events.append(kw["event_type"])):
        authorization, task, context = brain._reserve_step(
            _paid_manifest(), {"_owner_local": True, "turn_id": "turn-x", "session_id": "s"}, MESSAGES
        )
        assert get_spend_reservation(authorization.model_call_id).status == "reserved"
        assert task.task_id == "turn-x" and context["authorized_paid_call"] is authorization
        pcr.settle_owner_pick_paid_call(
            authorization, actual_usd=0.004, source_context=context, call_role="clean_loop_step", invocation_id="inv-1"
        )
    reservation = get_spend_reservation(authorization.model_call_id)
    assert reservation.status == "settled" and reservation.actual_usd == pytest.approx(0.004)
    assert ModelOrchestrationStore().current(authorization.escalation.run_id)["state"] == "COMPLETED"
    assert events == ["paid_call.reserved", "paid_call.settled"]


def test_a_remote_turn_never_reserves_the_owners_paid_model():
    from core.clean_loop import brain

    with pytest.raises(RuntimeError, match="not_owner_local"):
        brain._reserve_step(_paid_manifest(), {"_owner_local": False, "turn_id": "t"}, MESSAGES)


def test_steps_without_a_turn_id_share_one_task_id_across_the_loop():
    from core.clean_loop import brain

    first = brain._reserve_step(_paid_manifest(), {"_owner_local": True}, MESSAGES, fallback_task_id="clean-loop-a")
    second = brain._reserve_step(_paid_manifest(), {"_owner_local": True}, MESSAGES, fallback_task_id="clean-loop-a")
    assert first[1].task_id == second[1].task_id == "clean-loop-a"
