"""One physical provider response is charged once, however often its settlement is replayed.

Finding #8 (0.7.3 item 6): a reservation that had already closed sent every further settlement to
the supplemental-charge path, which minted a fresh random row id each time. Settling the same
$0.01 response twice booked $0.02. The same path ran when settlement itself had committed and only
the receipt after it failed, so a receipt error alone doubled the charge.

The rule these tests pin:
* a settlement names the physical invocation it settles; the same invocation settles once,
  durably, across restarts and concurrent callers;
* a different invocation under the same reservation is a real paid retry and is still recorded;
* a settlement that names no invocation on a closed reservation cannot be told apart from a
  replay, so it is recorded AND flagged, never dropped;
* an unknown outcome keeps the reserved ceiling held.

NO REAL CALL IS MADE ANYWHERE HERE. The real spend ledger and the real settlement code run on a
private database; the provider is a local recorder.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from core import paid_call_reservation as pcr
from core.model_spend_ledger import SpendLimits, get_spend_reservation, reserve_spend

LIMITS = SpendLimits(per_call_usd=0.25, per_task_usd=1.00, daily_usd=5.00, monthly_usd=25.00)
CENT = 0.01


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


def _reserve(model_call_id: str, *, usd: float = 0.25, task_id: str = "task-1"):
    reserve_spend(
        model_call_id=model_call_id,
        task_id=task_id,
        subtask_id="owner_explicit_model_pick",
        model_id="claude-sonnet-4-5",
        maximum_usd=usd,
        limits=LIMITS,
    )
    capsule = SimpleNamespace(task_id=task_id)
    escalation = SimpleNamespace(model_id="claude-sonnet-4-5", capsule=capsule)
    return SimpleNamespace(model_call_id=model_call_id, escalation=escalation)


def _rows() -> list[dict]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT model_call_id, subtask_id, reserved_usd, actual_usd, status FROM model_spend_reservations "
                "ORDER BY created_ts, model_call_id"
            ).fetchall()
        ]
    finally:
        conn.close()


def _charged() -> float:
    return round(sum(float(row["actual_usd"]) for row in _rows()), 8)


def _held() -> float:
    return round(
        sum(float(row["reserved_usd"]) for row in _rows() if row["status"] in {"reserved", "billing_ambiguous"}),
        8,
    )


# --- replay of one physical response -------------------------------------------------------------


def test_the_same_response_settled_twice_is_charged_once():
    auth = _reserve("call-replay")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    assert _charged() == pytest.approx(CENT)
    assert len(_rows()) == 1
    assert get_spend_reservation("call-replay").status == "settled"


def test_a_replay_survives_a_restart():
    from storage.db import reset_default_connection

    auth = _reserve("call-restart")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    reset_default_connection()
    pcr._ATTEMPTS.clear()
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    assert _charged() == pytest.approx(CENT)


def test_concurrent_replays_of_one_response_charge_once():
    auth = _reserve("call-race")
    barrier = threading.Barrier(8)

    def _settle():
        barrier.wait()
        pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")

    threads = [threading.Thread(target=_settle) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert _charged() == pytest.approx(CENT)
    assert len(_rows()) == 1


def test_a_replayed_settlement_emits_no_second_receipt(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(pcr, "_emit_answer_spend_receipt", lambda event, *a, **k: seen.append(event))
    auth = _reserve("call-receipt")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a", call_role="answer_generation")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a", call_role="answer_generation")
    assert seen == ["paid_call.settled"]


# --- a real paid retry is still recorded --------------------------------------------------------


def test_a_second_physical_call_under_a_closed_reservation_is_still_charged():
    auth = _reserve("call-retry")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-b")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-b")
    assert _charged() == pytest.approx(2 * CENT)
    rows = _rows()
    assert len(rows) == 2
    from core.model_spend_ledger import SUPPLEMENTAL_SUBTASK_ID

    assert rows[1]["subtask_id"] == SUPPLEMENTAL_SUBTASK_ID
    assert pcr.unverified_paid_charges() == []


def test_a_retry_after_an_ambiguous_attempt_counts_its_cost_once():
    auth = _reserve("call-ambiguous")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")  # attempt 1: outcome unknown
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-b")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-b")
    row = get_spend_reservation("call-ambiguous")
    assert row.status == "billing_ambiguous"
    assert row.actual_usd == pytest.approx(CENT)
    assert row.reserved_usd == pytest.approx(0.25), "the unknown attempt keeps its ceiling held"


def test_a_settlement_without_an_invocation_is_recorded_and_flagged_not_dropped():
    auth = _reserve("call-anonymous")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT)
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT)
    # Indistinguishable from a paid retry: the money is recorded, so the caps see it...
    assert _charged() == pytest.approx(2 * CENT)
    # ...and the row that may be a replay is named for review.
    flagged = pcr.unverified_paid_charges()
    assert len(flagged) == 1
    assert flagged[0]["model_call_id"] == "call-anonymous"
    assert flagged[0]["actual_usd"] == pytest.approx(CENT)


# --- crash between settle and record ------------------------------------------------------------


def test_a_receipt_failure_after_settlement_does_not_post_a_second_charge(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("receipt store unavailable")

    monkeypatch.setattr(pcr, "_emit_answer_spend_receipt", _boom)
    auth = _reserve("call-receipt-crash")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a", call_role="answer_generation")
    assert _charged() == pytest.approx(CENT)
    assert len(_rows()) == 1


def test_a_receipt_failure_without_an_invocation_still_charges_once(monkeypatch):
    monkeypatch.setattr(pcr, "_terminalize_paid_generation_orchestration", lambda *a, **k: 1 / 0)
    auth = _reserve("call-terminal-crash")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT)
    assert _charged() == pytest.approx(CENT)
    assert len(_rows()) == 1


def test_a_crash_inside_the_settlement_commits_nothing_and_the_retry_settles_once(monkeypatch):
    """The invocation record and the charge are one transaction: a failure between them leaves
    neither, so the same response settling again is charged exactly once."""
    from core import model_spend_ledger
    from storage import db

    auth = _reserve("call-mid-crash")
    real_get_connection = db.get_connection
    armed = {"on": True}

    class _Interrupting:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if armed["on"] and "INSERT INTO model_spend_invocations" in sql:
                armed["on"] = False
                raise RuntimeError("power cut between the charge and its invocation record")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(model_spend_ledger, "get_connection", lambda *a, **k: _Interrupting(real_get_connection(*a, **k)))
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    assert _charged() == 0.0
    assert get_spend_reservation("call-mid-crash").status == "reserved", "the hold is kept, not lost"
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    assert _charged() == pytest.approx(CENT)
    assert len(_rows()) == 1


# --- unknown outcome -----------------------------------------------------------------------------


def test_an_unknown_outcome_keeps_the_reserved_ceiling_and_replays_change_nothing():
    auth = _reserve("call-unknown")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id="response-a")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id="response-a")
    row = get_spend_reservation("call-unknown")
    assert row.status == "billing_ambiguous"
    assert row.reserved_usd == pytest.approx(0.25)
    assert _charged() == 0.0


def test_an_unknown_outcome_on_a_closed_reservation_is_held_not_ignored(monkeypatch):
    monkeypatch.setattr(pcr, "spend_limits", lambda: LIMITS)
    auth = _reserve("call-closed-unknown")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id="response-b")
    assert _charged() == pytest.approx(CENT)
    assert _held() == pytest.approx(LIMITS.per_call_usd), "a second call with no usage holds a call's ceiling"
    pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id="response-b")
    assert _held() == pytest.approx(LIMITS.per_call_usd)


def test_a_held_unknown_charge_blocks_the_next_reservation_like_any_other(monkeypatch):
    monkeypatch.setattr(pcr, "spend_limits", lambda: LIMITS)
    for index in range(3):
        auth = _reserve(f"call-{index}")
        pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id=f"first-{index}")
        pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id=f"second-{index}")
    # Three cents charged and three call ceilings held: the task cannot fit another full call.
    with pytest.raises(PermissionError, match="per_task_spend_cap_exceeded"):
        _reserve("call-blocked")


def test_an_unknown_outcome_on_a_closed_reservation_holds_the_calls_own_ceiling(monkeypatch):
    """A large call reserved well above the per-call floor: its unknown second response holds that ceiling."""
    big = SpendLimits(per_call_usd=10.0, per_task_usd=100.0, daily_usd=100.0, monthly_usd=100.0)
    reservation = reserve_spend(
        model_call_id="call-large",
        task_id="task-large",
        subtask_id="owner_explicit_model_pick",
        model_id="claude-sonnet-4-5",
        maximum_usd=7.05,
        limits=big,
    )
    monkeypatch.setattr(pcr, "spend_limits", lambda: LIMITS)  # today's floor is $0.25
    auth = SimpleNamespace(
        model_call_id="call-large",
        reservation=reservation,
        escalation=SimpleNamespace(model_id="claude-sonnet-4-5", capsule=SimpleNamespace(task_id="task-large")),
    )
    pcr.settle_owner_pick_paid_call(auth, actual_usd=0.90, invocation_id="response-a")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id="response-b")
    assert _charged() == pytest.approx(0.90)
    assert _held() == pytest.approx(7.05)


def test_a_settlement_that_read_a_stale_status_keeps_a_concurrent_unknown_attempt_held(monkeypatch):
    """The failed attempt turned the row ambiguous after the settlement read it as reserved."""
    from core import model_spend_ledger

    auth = _reserve("call-stale")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    stale = SimpleNamespace(status="reserved", actual_usd=0.0)
    monkeypatch.setattr(model_spend_ledger, "get_spend_reservation", lambda _call_id: stale)
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-b")
    monkeypatch.undo()
    row = get_spend_reservation("call-stale")
    assert row.status == "billing_ambiguous"
    assert row.reserved_usd == pytest.approx(0.25), "the unknown attempt's hold is not cleared"
    assert row.actual_usd == pytest.approx(CENT)


@pytest.mark.parametrize("snapshot", [float("nan"), None, "7.05"])
def test_an_unreadable_reservation_snapshot_still_holds_the_per_call_floor(monkeypatch, snapshot):
    monkeypatch.setattr(pcr, "spend_limits", lambda: LIMITS)
    auth = _reserve("call-odd")
    auth.reservation = SimpleNamespace(reserved_usd=snapshot)
    pcr.settle_owner_pick_paid_call(auth, actual_usd=CENT, invocation_id="response-a")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=None, invocation_id="response-b")
    assert _held() == pytest.approx(LIMITS.per_call_usd)
