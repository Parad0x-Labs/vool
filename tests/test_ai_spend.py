"""AI spending: the limits the person sets are the ones the spend ledger enforces, the amounts say how
they are known (the provider's bill, VOOL's estimate, or held), and VOOL warns before a limit is used.

Reservations go through the real ledger (core.model_spend_ledger.reserve_spend) with the real limits
(core.paid_call_reservation.spend_limits), in an isolated VOOL home.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from core import ai_spend
from core import model_spend_ledger as ledger
from core.paid_call_reservation import spend_limits


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    yield
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _reserve(amount: float, *, task: str = "", now: float | None = None) -> ledger.SpendReservation:
    return ledger.reserve_spend(model_call_id="model-call-" + uuid.uuid4().hex, task_id=task or uuid.uuid4().hex,
                                subtask_id="s-" + uuid.uuid4().hex[:6], model_id="paid/model",
                                maximum_usd=amount, limits=spend_limits(), now=now)


def test_a_daily_limit_the_person_sets_refuses_the_call_that_would_pass_it():
    ok, saved = ai_spend.set_limits({"daily": 0.5})
    assert ok and saved["limits"] == {"daily": 0.5}
    assert spend_limits().daily_usd == 0.5
    first = _reserve(0.2)
    ledger.settle_spend(first.model_call_id, actual_usd=0.2, cost_basis="published_price")
    _reserve(0.2)  # still open: held at its ceiling, counted against the day
    with pytest.raises(PermissionError, match="daily_spend_cap_exceeded"):
        _reserve(0.2)
    # Raised: the same call now fits.
    assert ai_spend.set_limits({"daily": 1.0})[0]
    _reserve(0.2)


def test_a_per_answer_limit_above_the_task_ceiling_lifts_the_task_ceiling_with_it():
    default_task = spend_limits().per_task_usd
    assert ai_spend.set_limits({"per_call": default_task + 1})[0]
    limits = spend_limits()
    assert limits.per_call_usd == default_task + 1 and limits.per_task_usd >= limits.per_call_usd


def test_a_damaged_or_out_of_range_limits_file_never_lifts_a_limit():
    defaults = spend_limits()
    path = ai_spend._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for raw in ("not json", json.dumps([1, 2]), json.dumps({"daily": "inf", "monthly": -5, "per_call": 1e9}),
                json.dumps({"daily": True})):
        path.write_text(raw, encoding="utf-8")
        assert ai_spend.person_limits() == {}
        assert spend_limits() == defaults


@pytest.mark.parametrize(("values", "error"), [
    ({"daily": 0}, "out_of_range"),
    ({"daily": "abc"}, "out_of_range"),
    ({"monthly": 20_000}, "out_of_range"),
    ({"per_call": 3, "daily": 2}, "answer_above_period"),
    ({"daily": 30, "monthly": 20}, "day_above_month"),
    ({"weekly": 3}, "unknown_limit"),
])
def test_a_bad_limit_writes_nothing(values, error):
    assert ai_spend.set_limits({"daily": 4, "monthly": 40})[0]
    ok, said = ai_spend.set_limits(values)
    assert not ok and said["error"] == error
    assert ai_spend.person_limits() == {"daily": 4.0, "monthly": 40.0}


def test_clearing_a_limit_returns_it_to_the_default():
    default_daily = spend_limits().daily_usd
    assert ai_spend.set_limits({"daily": 1.25})[0]
    assert ai_spend.set_limits({"daily": None})[0]
    assert spend_limits().daily_usd == default_daily and ai_spend.person_limits() == {}


def test_billed_estimated_and_held_are_told_apart_and_all_count_against_the_limit():
    billed = _reserve(0.10)
    ledger.settle_spend(billed.model_call_id, actual_usd=0.04, cost_basis=ledger.BILLED_BASIS)
    estimated = _reserve(0.10)
    ledger.settle_spend(estimated.model_call_id, actual_usd=0.03, cost_basis="published_price")
    _reserve(0.05)  # open
    unknown = _reserve(0.06)
    ledger.settle_spend(unknown.model_call_id, actual_usd=None)  # the provider said nothing: held
    today = ai_spend.overview()["today"]
    assert today["billed_usd"] == pytest.approx(0.04)
    assert today["estimated_usd"] == pytest.approx(0.03)
    assert today["held_usd"] == pytest.approx(0.11)
    assert today["counted_usd"] == pytest.approx(0.18)
    assert today["calls"] == 4


def test_a_row_settled_on_a_bill_and_then_an_estimate_is_not_shown_as_billed():
    call = _reserve(0.2)
    ledger.settle_spend(call.model_call_id, actual_usd=None)
    row = ledger.settle_spend(call.model_call_id, actual_usd=0.01, add_to_ambiguous=True, invocation_id="a",
                              cost_basis=ledger.BILLED_BASIS, billing_ambiguous=True)
    assert row.cost_basis == ledger.BILLED_BASIS
    final = ledger.settle_spend(call.model_call_id, actual_usd=0.02, invocation_id="b", cost_basis="published_price")
    assert final.cost_basis == "published_price"
    assert ai_spend.overview()["today"]["billed_usd"] == 0


def test_the_warning_comes_before_the_limit_and_says_when_it_is_reached():
    assert ai_spend.set_limits({"per_call": 1.0, "daily": 1.0})[0]
    first = _reserve(0.7)
    ledger.settle_spend(first.model_call_id, actual_usd=0.7, cost_basis="published_price")
    assert ai_spend.overview()["alerts"] == []
    second = _reserve(0.15)
    ledger.settle_spend(second.model_call_id, actual_usd=0.15, cost_basis="published_price")
    standing = ai_spend.overview()
    assert standing["today"]["state"] == "near"
    assert standing["alerts"] == [{"window": "today", "state": "near", "counted_usd": pytest.approx(0.85), "limit_usd": 1.0}]
    third = _reserve(0.15)
    ledger.settle_spend(third.model_call_id, actual_usd=0.15, cost_basis="published_price")
    assert ai_spend.overview()["today"]["state"] == "reached"
    with pytest.raises(PermissionError):
        _reserve(0.01)


def test_yesterdays_spend_is_not_today_but_is_this_month():
    now = time.time()
    day_start = now - now % 86400
    if day_start - 3600 < ledger._day_and_month_start(now)[1]:
        pytest.skip("first hour of the month: yesterday is last month")
    old = _reserve(0.2, now=day_start - 3600)
    ledger.settle_spend(old.model_call_id, actual_usd=0.2, cost_basis="published_price")
    standing = ai_spend.overview()
    assert standing["today"]["counted_usd"] == 0 and standing["month"]["counted_usd"] == pytest.approx(0.2)


def test_one_chats_spend_is_read_through_the_run_it_belongs_to():
    from core.model_orchestration_state import ModelOrchestrationStore

    store = ModelOrchestrationStore()
    task = "task-" + uuid.uuid4().hex
    call = ledger.reserve_spend(model_call_id="model-call-" + uuid.uuid4().hex, task_id=task, subtask_id="sub-1",
                                model_id="paid/model", maximum_usd=0.1, limits=spend_limits())
    store.create_run(task_id=task, turn_id="turn-1", subtask_id="sub-1", session_id="chat-a", lane="local",
                     model_id="local", reason="owner_explicit_paid_model_pick")
    ledger.settle_spend(call.model_call_id, actual_usd=0.05, cost_basis="published_price")
    other = _reserve(0.1)
    ledger.settle_spend(other.model_call_id, actual_usd=0.07, cost_basis="published_price")
    assert ai_spend.overview(session_id="chat-a")["session"]["counted_usd"] == pytest.approx(0.05)
    assert ai_spend.overview(session_id="chat-b")["session"]["counted_usd"] == 0


def test_an_answers_spend_is_read_from_its_own_settlement_receipts():
    events = [
        {"event_type": "paid_call.settled", "model_call_id": "a", "actual_usd": 0.01, "cost_basis": "provider_reported"},
        {"event_type": "paid_call.settled", "model_call_id": "a", "actual_usd": 0.01, "cost_basis": "provider_reported"},
        {"event_type": "paid_call.settled", "model_call_id": "b", "actual_usd": 0.02, "cost_basis": "published_price"},
        {"event_type": "paid_call.billing_ambiguous", "model_call_id": "c", "actual_usd": None, "held_usd": 0.05},
        {"event_type": "tool_executed"},
    ]
    assert ai_spend.answer_spend(events) == {"calls": 3, "billed_usd": 0.01, "estimated_usd": 0.02, "held_usd": 0.05}
    assert ai_spend.answer_spend([{"event_type": "tool_executed"}]) is None
