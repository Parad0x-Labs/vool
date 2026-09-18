"""Start-price admission, durable queue identity and continuation under the money law."""

import time
from types import SimpleNamespace

import pytest

from core import runtime_continuity as queue
from core.usepod import price_wait as wait
from core.usepod import routing
from tests.usepod.test_usepod_money_law import law as law
from tests.usepod.test_usepod_routing_policy import ORIGIN, _snap
from tests.usepod.test_usepod_routing_policy import _row as original_row


def _row(*args, **kwargs):
    return original_row(*args, centralized=(("synthetic-central", 20000000, 50000000),), **kwargs)


@pytest.fixture
def market(law, monkeypatch):
    snapshots = [_snap(_row("model-one", market=(800000, 4000000)), fetched_at=time.time())]
    monkeypatch.setattr(wait.discovery, "configured_origin", lambda: ORIGIN)
    monkeypatch.setattr(
        wait.discovery, "resolve_credential", lambda: SimpleNamespace(fingerprint="account-one", origin=ORIGIN)
    )
    monkeypatch.setattr(wait.pricing, "current_snapshot", lambda **kw: SimpleNamespace(snapshot=snapshots[0]))
    monkeypatch.setattr(wait.pricing, "fetch_marketplace_snapshot", lambda **kw: snapshots[0])
    monkeypatch.setattr("core.usepod.spend_approval.prepaid_spend_readiness", lambda model: {"available": True})
    wait._POLLS.clear()
    return snapshots


def target():
    return wait.save_target(model_id="model-one", max_input_usdc="0.4", max_output_usdc="2", poll_minutes=1)


def test_wait_checks_both_axes_and_preserves_cancellation_and_snapshot(market):
    bound = target()
    target_facts = wait.queued_target("model-one")
    row = queue.enqueue_message(session_id="wait:test", payload={"text": "do my task", "price_wait": target_facts})
    assert queue.price_wait_sessions() == ["wait:test"]
    assert not wait.check_queued(target_facts)["ready"]
    market[0] = _snap(_row("model-one", market=(300000, 3000000)), fetched_at=time.time())
    wait._POLLS.clear()
    assert not wait.check_queued(target_facts)["ready"]  # cheap input alone is insufficient
    market[0] = _snap(_row("model-one", market=(300000, 1000000)), fetched_at=time.time())
    wait._POLLS.clear()
    assert wait.check_queued(target_facts)["ready"]
    assert routing.load_route_state().bounds["model-one"].approval_id == bound.approval_id
    assert queue.cancel_queue_item(row["queue_item_id"], session_id="wait:test")
    assert queue.claim_next_message("wait:test", expected_queue_item_id=row["queue_item_id"]) is None


def test_running_task_finishes_after_price_spike_but_next_task_must_wait(market):
    target()
    facts = wait.queued_target("model-one")
    row = queue.enqueue_message(session_id="wait:continuation", payload={"text": "fix the code", "price_wait": facts})
    queue.claim_next_message("wait:continuation", expected_queue_item_id=row["queue_item_id"])
    state = routing.load_route_state()
    low = _snap(_row("model-one", market=(300000, 1000000)), fetched_at=time.time())
    high = _snap(_row("model-one", market=(900000, 5000000)), fetched_at=time.time())
    with wait.running_task("wait:continuation", "fix the code", queue_item_id=row["queue_item_id"]):
        first = wait.authorize_running_dispatch(state, low, model_id="model-one")
        later = wait.authorize_running_dispatch(state, high, model_id="model-one")
        assert later.max_output_microunits_per_million > first.max_output_microunits_per_million
    with pytest.raises(routing.RouteUnavailableError):
        wait.authorize_running_dispatch(state, high, model_id="model-one")
    assert state.bounds["model-one"].max_output_microunits_per_million == 2000000


def test_wrong_task_account_or_changed_target_cannot_use_admission(market, monkeypatch):
    target()
    facts = wait.queued_target("model-one")
    row = queue.enqueue_message(session_id="wait:bound", payload={"text": "work", "price_wait": facts})
    queue.claim_next_message("wait:bound")
    with wait.running_task("wait:other", "work", queue_item_id=row["queue_item_id"]):
        with pytest.raises(routing.RouteUnavailableError):
            wait.authorize_running_dispatch(routing.load_route_state(), market[0], model_id="model-one")
    monkeypatch.setattr(
        wait.discovery, "resolve_credential", lambda: SimpleNamespace(fingerprint="switched", origin=ORIGIN)
    )
    assert wait.check_queued(facts)["state"] == "target_or_account_changed"


def test_price_polling_is_bounded_and_never_changes_the_approved_target(market, monkeypatch):
    bound = target()
    calls = []
    monkeypatch.setattr(wait.pricing, "fetch_marketplace_snapshot", lambda **kw: calls.append(kw) or market[0])
    first = wait.check("model-one")
    second = wait.check("model-one")
    assert first == second and len(calls) == 1
    assert routing.load_route_state().bounds["model-one"] == bound
    assert not first["ready"]


def _live(market, monkeypatch, *, tolerance=10):
    from adapters.base_adapter import ModelRequest
    wait.save_target(model_id="model-one", max_input_usdc="0.4", max_output_usdc="2",
                     poll_minutes=1, active_price_tolerance_percent=tolerance)
    facts = wait.queued_target("model-one")
    row = queue.enqueue_message(session_id="wait:live", payload={"text": "continue work", "price_wait": facts})
    queue.claim_next_message("wait:live")
    ticks = [1000.0]
    monkeypatch.setattr(wait.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(wait, "_sleep", lambda seconds: ticks.__setitem__(0, ticks[0] + seconds))
    request = ModelRequest(task_kind="chat", prompt="exact next request", cancel_check=lambda: False)
    events = []
    monkeypatch.setattr(wait, "emit_runtime_event", lambda ctx, **event: events.append(event))
    return row, ticks, request, events


def test_live_pause_keeps_exact_continuation_and_excludes_wait_from_execution_deadline(market, monkeypatch):
    from core import runtime_active_clock as clock
    from core.provider_call_deadline import bind_provider_deadline, deadline_expired
    row, ticks, request, events = _live(market, monkeypatch)
    state = routing.load_route_state()
    low = _snap(_row("model-one", market=(300000, 1000000)), fetched_at=time.time())
    high = _snap(_row("model-one", market=(450000, 2200000)), fetched_at=time.time())
    market[0] = _snap(_row("model-one", market=(440000, 2200000)), fetched_at=time.time())
    with wait.running_task("wait:live", "continue work", queue_item_id=row["queue_item_id"]):
        deadline = bind_provider_deadline({}, turn_deadline_monotonic=clock.monotonic()+5, cleanup_margin_seconds=0)
        request.cancel_check = lambda: deadline_expired(deadline)
        completed_actions = ["file already written"]
        wait.authorize_running_dispatch(state, low, model_id="model-one", request=request)
        result = wait.authorize_running_dispatch(state, high, model_id="model-one", request=request)
        assert completed_actions == ["file already written"]
        assert request.prompt == "exact next request"
        assert result.max_input_microunits_per_million == 440000
        assert result.max_output_microunits_per_million == 2200000
        assert ticks[0] >= 1060 and not deadline_expired(deadline)
        ticks[0] += 6
        assert deadline_expired(deadline)  # work still has a real execution timeout
    assert [e["event_type"] for e in events] == ["usepod_price_paused", "usepod_price_resumed"]
    assert "0.45 input" in events[0]["message"]
    assert routing.load_route_state().bounds["model-one"].max_input_microunits_per_million == 400000


@pytest.mark.parametrize("change", ["stop", "account", "budget"])
def test_paused_call_cannot_resume_after_stop_or_authority_change(market, monkeypatch, change):
    row, ticks, request, events = _live(market, monkeypatch, tolerance=0)
    state = routing.load_route_state()
    low = _snap(_row("model-one", market=(300000, 1000000)), fetched_at=time.time())
    high = _snap(_row("model-one", market=(500000, 3000000)), fetched_at=time.time())
    def sleep(seconds):
        ticks[0] += seconds
        if change == "stop": request.cancel_check = lambda: True
        elif change == "account":
            monkeypatch.setattr(wait.discovery, "resolve_credential", lambda: SimpleNamespace(fingerprint="different", origin=ORIGIN))
        else:
            monkeypatch.setattr("core.usepod.spend_approval.prepaid_spend_readiness", lambda model: {"available": False})
    monkeypatch.setattr(wait, "_sleep", sleep)
    with wait.running_task("wait:live", "continue work", queue_item_id=row["queue_item_id"]):
        wait.authorize_running_dispatch(state, low, model_id="model-one", request=request)
        with pytest.raises(routing.RouteUnavailableError):
            wait.authorize_running_dispatch(state, high, model_id="model-one", request=request)
    assert [e["event_type"] for e in events] == ["usepod_price_paused"]


@pytest.mark.parametrize("value", [-1, True, 1.5, 1001])
def test_invalid_tolerance_cannot_be_saved(market, value):
    with pytest.raises(ValueError):
        wait.save_target(model_id="model-one", max_input_usdc="1", max_output_usdc="4",
                         active_price_tolerance_percent=value)
