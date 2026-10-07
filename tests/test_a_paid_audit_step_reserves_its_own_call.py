"""A workspace audit on a paid cloud model reserves each of its own calls, sized for that call, and never
strands one.

VOOL-only coordination pass u2, 2026-10-07, on VOOL's own OpenRouter lane: every audit stopped at its first
nomination with `paid_call_not_authorized_or_reserved`, because the stepped audit called `_invoke_manifest`
with no reservation. Reviews of the fix (series L, 2026-10-07): the hold must be sized for the step, must be
handed back on every exit, must emit spend receipts, and a retried step must not reuse a subtask id.
No network call or spend happens here. The reservation tests below use the real spend ledger in a private
home; the provider seam is a recorder.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime import stepped_audit

PAID_MODEL = "z-ai/glm-5.3-flashx"
PAID = SimpleNamespace(
    provider_id=f"openrouter-byok:{PAID_MODEL}", provider_name="openrouter-byok", model_name=PAID_MODEL,
    source_type="http", adapter_type="openai_compatible", runtime_config={"base_url": "https://openrouter.ai/api/v1"},
    metadata={"cost_class": "paid_cloud"}, capabilities=[],
)
LOCAL = SimpleNamespace(
    provider_id="ollama-local:qwen3:8b", provider_name="ollama-local", model_name="qwen3:8b", source_type="http",
    adapter_type="openai_compatible", runtime_config={"base_url": "http://127.0.0.1:11434/v1"},
    metadata={"cost_class": "free_local"},
)
OWNER = {"_owner_local": True, "session_id": "s"}


class _Router:
    def __init__(self, outcome=None) -> None:
        self.contexts: list[dict] = []
        self.outcome = outcome

    def _invoke_manifest(self, **kwargs):
        self.contexts.append(dict(kwargs.get("source_context") or {}))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if isinstance(self.outcome, str):
            return None, None, self.outcome
        return None, SimpleNamespace(output_text='{"findings": []}', usage={}), None


def _step(manifest, *, router=None, context=None, prompt="p", budget=None, task_id=None):
    agent = SimpleNamespace(memory_router=router or _Router())
    budget = budget or stepped_audit.SteppedAuditBudget()
    result = stepped_audit._call_step(
        agent, manifest=manifest, task=SimpleNamespace(task_id=task_id or f"task-{uuid.uuid4().hex}"),
        source_context=dict(context if context is not None else {"session_id": "s", "runtime_event_stream_id": "x"}),
        step="nominate", prompt=prompt, system_prompt="s", max_output_tokens=3000, output_mode="json", budget=budget,
    )
    return agent.memory_router, result, budget


# ---- the reservation is made, per step, and nothing is sent without one --------------------------------


def test_a_paid_audit_step_is_dispatched_under_a_reservation_sized_for_that_step():
    granted = SimpleNamespace(model_call_id="call-1")
    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call", return_value=granted) as reserve:
        router, result, _ = _step(PAID, prompt="x" * 4000, task_id="turn-task")
    sized = reserve.call_args.kwargs["task"]
    assert sized.task_id == "turn-task" and sized.max_output_tokens == 3000 and sized.prompt_tokens >= 1000
    assert reserve.call_args.kwargs["call_role"] == "audit_step"
    ctx = router.contexts[0]
    assert ctx["authorized_paid_call"] is granted and ctx["model_call_role"] == "audit_step"
    assert "runtime_event_stream_id" not in ctx and result.text and not result.error


def test_a_refused_reservation_sends_nothing_and_names_the_gate():
    def refuse(**kwargs):
        kwargs["denial"]["reason"] = "daily_spend_cap_exceeded"

    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call", side_effect=refuse):
        router, result, budget = _step(PAID)
    assert router.contexts == [], "a refused paid step must not reach the provider"
    assert result.error == budget.last_error == "paid_call_refused:daily_spend_cap_exceeded"


def test_a_local_or_verified_free_step_takes_no_reservation():
    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call") as reserve:
        router, _r, _b = _step(LOCAL)
        with mock.patch("core.model_selection_policy.is_verified_free_cloud_manifest", return_value=True):
            router_free, _r2, _b2 = _step(PAID)
    assert reserve.call_count == 0
    assert "authorized_paid_call" not in router.contexts[0] and "authorized_paid_call" not in router_free.contexts[0]


def test_two_paid_steps_reserve_under_distinct_subtask_ids():
    seen: list[str] = []

    def grant(**kwargs):
        seen.append(kwargs["source_context"]["subtask_id"])
        return SimpleNamespace(model_call_id=f"call-{uuid.uuid4().hex}")

    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call", side_effect=grant):
        _step(PAID, task_id="t")
        _step(PAID, task_id="t")
    assert len(seen) == 2 and seen[0] != seen[1]


def test_a_cancelled_turn_reserves_nothing():
    cancel = threading.Event()
    cancel.set()
    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call") as reserve:
        router, result, _ = _step(PAID, context={**OWNER, "cancel_event": cancel})
    assert reserve.call_count == 0 and router.contexts == [] and result.error == "turn_cancelled"


# ---- against the real spend ledger -------------------------------------------------------------------


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    from core import cloud_escalation_policy as cep
    from core import os_consent_gate, runtime_paths
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    cep.save_policy(replace(cep.load_policy(), mode="off", daily_cap=25))
    from core.model_spend_ledger import get_spend_reservation

    yield get_spend_reservation
    os_consent_gate.set_consent_override_for_tests(None)
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _held(router) -> str:
    return str(router.contexts[0]["authorized_paid_call"].model_call_id)


def test_a_router_that_raises_leaves_the_hold_as_possibly_billed(ledger):
    router = _Router(TimeoutError("provider socket closed"))
    _router, result, _ = _step(PAID, router=router, context=dict(OWNER))
    assert result.error == "TimeoutError"
    assert ledger(_held(router)).status == "billing_ambiguous"


@pytest.mark.parametrize("refusal", ["turn_cancelled", "author_not_certified_for_final_answer",
                                     "routing_plan_refused:not_in_plan:x/y"])
def test_a_refusal_before_any_request_hands_the_hold_back(ledger, refusal):
    router = _Router(refusal)
    _router, result, _ = _step(PAID, router=router, context=dict(OWNER))
    assert result.error == refusal
    assert ledger(_held(router)).status != "reserved"


def test_a_failure_before_dispatch_hands_the_hold_back(ledger):
    reserved: list = []
    real_reserve = __import__("core.paid_call_reservation", fromlist=["x"]).reserve_owner_pick_paid_call

    def recording(**kwargs):
        granted = real_reserve(**kwargs)
        reserved.append(granted)
        return granted

    class _Ledger:
        def may_call(self, step):
            return True

        def refusal_for(self, step):
            return ""

        def open_call(self, **_kwargs):
            raise RuntimeError("ledger disk full")

        def close_call(self, *_args, **_kwargs):
            pass

    budget = stepped_audit.SteppedAuditBudget()
    budget.ledger = _Ledger()
    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call", side_effect=recording), \
         pytest.raises(RuntimeError):
        _step(PAID, context=dict(OWNER), budget=budget)
    assert reserved and reserved[0] is not None
    assert ledger(reserved[0].model_call_id).status != "reserved"


def test_a_step_too_large_for_the_per_call_cap_is_refused_before_sending(ledger):
    router = _Router()
    _router, result, _ = _step(PAID, router=router, context=dict(OWNER), prompt="x" * 1_600_000)
    assert router.contexts == []
    assert result.error == "paid_call_refused:per_call_spend_cap_exceeded"


def test_an_audit_step_reservation_emits_a_spend_receipt(ledger):
    events: list[str] = []
    with mock.patch("core.runtime_task_events.emit_runtime_event",
                    side_effect=lambda _ctx, **kw: events.append(str(kw.get("event_type")))):
        _step(PAID, context={**OWNER, "runtime_event_stream_id": "stream"})
    assert "paid_call.reserved" in events, events


def test_a_dense_non_ascii_prompt_is_not_undercounted():
    granted = SimpleNamespace(model_call_id="call-cjk")
    with mock.patch("core.paid_call_reservation.reserve_owner_pick_paid_call", return_value=granted) as reserve:
        _step(PAID, prompt="漢" * 4000)
    assert reserve.call_args.kwargs["task"].prompt_tokens >= 4000


def test_an_error_after_the_answer_releases_the_hold_as_possibly_billed(ledger):
    """The answer came back, then closing the ledger row raised: the call was sent, so the hold must
    not stay `reserved` and must not be handed back as unsent."""

    class _Ledger:
        def may_call(self, step):
            return True

        def refusal_for(self, step):
            return ""

        def open_call(self, **_kwargs):
            return {"row": 1}

        def close_call(self, *_args, **_kwargs):
            raise RuntimeError("ledger disk full")

    router = _Router()
    budget = stepped_audit.SteppedAuditBudget()
    budget.ledger = _Ledger()
    with pytest.raises(RuntimeError):
        _step(PAID, router=router, context=dict(OWNER), budget=budget)
    assert ledger(_held(router)).status == "billing_ambiguous"


def test_a_released_audit_step_closes_its_orchestration_run(ledger):
    from core.model_orchestration_state import ModelOrchestrationState, ModelOrchestrationStore

    router = _Router(TimeoutError("provider socket closed"))
    _step(PAID, router=router, context=dict(OWNER))
    run_id = str(router.contexts[0]["authorized_paid_call"].escalation.run_id)
    current = ModelOrchestrationStore().current(run_id)
    assert current is not None and current.get("state") != ModelOrchestrationState.PAID_RUNNING, current
