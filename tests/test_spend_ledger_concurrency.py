"""The spend ledger under concurrent turns: serve the work, and bind the caps atomically.

Two measured defects are pinned here.

1. **Legitimate work was refused under contention.** 25 concurrent reservations for a normal turn
   (12k prompt / 900 completion, ~$0.0495 each) against a $5.00 daily cap — a total of $1.24, so
   the caps were nowhere near binding — lost 1-2 of them to
   ``sqlite3.OperationalError("database is locked")``. The lock came out of ``storage.db``, not
   out of any transaction: opening several connections at once against a database not yet in WAL
   mode made each one attempt the journal-mode conversion, which SQLite answers with SQLITE_BUSY
   *immediately* instead of through the busy handler, so ``connect(timeout=...)`` never covered
   it. Failing closed is the safe direction, but a user running a few turns at once saw their own
   paid lane refuse them for a reason unrelated to spend.

2. **The call-count cap was not atomic.** ``daily_call_cap_available()`` read the counter and
   ``record_escalation()`` wrote it, on either side of the transaction that reserves the dollars.
   Concurrent turns therefore all read the same pre-call count and every one of them passed a cap
   that should have admitted one. The USD ledger already serialized correctly on BEGIN IMMEDIATE;
   the count did not.

NO REAL CALL IS MADE ANYWHERE HERE — no adapter, no transport, no key. These drive the ledger.
"""
from __future__ import annotations

import gc
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.model_spend_ledger import SpendLimits, calls_today, reserve_spend

# A normal turn at Anthropic's published Claude Sonnet rate: 12k in, 900 out.
NORMAL_TURN_USD = 0.0495
CONCURRENCY = 25


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)
    # sqlite connections opened by the deliberately short-lived worker threads contain
    # internal reference cycles. Without an explicit collection at this fixture boundary,
    # macOS can carry roughly 50 descriptors per race into the next test and exceed the normal
    # 256-descriptor terminal limit before cyclic GC happens on its own.
    gc.collect()


def _race(limits: SpendLimits, *, amount: float, n: int = CONCURRENCY, prefix: str = "call"):
    """Fire ``n`` reservations at once and sort the outcomes into granted / refused / errored."""

    def one(i: int) -> tuple[str, str]:
        try:
            reserve_spend(
                model_call_id=f"{prefix}-{i}",
                task_id=f"task-{i}",
                subtask_id="s",
                model_id="anthropic/claude-sonnet-4",
                maximum_usd=amount,
                limits=limits,
            )
            return ("granted", "")
        except PermissionError as exc:
            return ("refused", str(exc))
        except Exception as exc:  # a lock error lands here, and must not
            return ("errored", f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(one, range(n)))
    return (
        [r for r in out if r[0] == "granted"],
        [r for r in out if r[0] == "refused"],
        [r for r in out if r[0] == "errored"],
    )


# --- 1. contention must not refuse work the caps would allow ---------------------------------


def test_concurrent_normal_turns_are_all_served_under_a_cap_they_do_not_approach() -> None:
    limits = SpendLimits(per_call_usd=0.25, per_task_usd=100.0, daily_usd=5.00, monthly_usd=100.0)
    granted, refused, errored = _race(limits, amount=NORMAL_TURN_USD)

    assert not errored, f"contention must not surface as an error: {errored[:3]}"
    assert not refused, f"the caps were nowhere near binding: {refused[:3]}"
    assert len(granted) == CONCURRENCY


def test_a_cold_database_does_not_lose_connections_to_the_wal_conversion() -> None:
    """The journal-mode race directly: open N connections at once on an untouched database."""
    from storage.db import get_connection, reset_default_connection

    def one(_i: int) -> str:
        reset_default_connection()  # force a genuinely fresh connection per thread
        try:
            conn = get_connection()
            try:
                conn.execute("SELECT 1")
                return ""
            finally:
                conn.close()
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        failures = [f for f in pool.map(one, range(CONCURRENCY)) if f]
    assert not failures, f"opening connections concurrently must not fail: {failures[:3]}"


# --- 2. a genuine cap still refuses, with a spend reason ---------------------------------------


def test_concurrent_turns_over_the_dollar_cap_are_refused_for_spend_not_for_locking() -> None:
    limits = SpendLimits(per_call_usd=0.25, per_task_usd=100.0, daily_usd=0.50, monthly_usd=100.0)
    granted, refused, errored = _race(limits, amount=0.10, prefix="over")

    assert not errored, f"a cap refusal must not arrive as a lock error: {errored[:3]}"
    assert len(granted) == 5, "exactly $0.50 worth of $0.10 calls fit"
    assert len(refused) == CONCURRENCY - 5
    assert {r[1] for r in refused} == {"daily_spend_cap_exceeded"}


# --- 3. the call-count cap is atomic -----------------------------------------------------------


@pytest.mark.parametrize("cap", [1, 3, 5])
def test_a_call_count_cap_admits_exactly_its_cap_under_concurrency(cap: int) -> None:
    limits = SpendLimits(
        per_call_usd=0.25,
        per_task_usd=1e6,
        daily_usd=1e6,
        monthly_usd=1e6,
        daily_call_cap=cap,
    )
    granted, refused, errored = _race(limits, amount=0.01, prefix=f"cap{cap}")

    assert not errored, f"the count cap must refuse, not error: {errored[:3]}"
    assert len(granted) == cap, "the count cap must not be beatable by concurrency"
    assert len(refused) == CONCURRENCY - cap
    assert {r[1] for r in refused} == {"daily_call_cap_exceeded"}
    assert calls_today() == cap


def test_the_call_count_cap_is_checked_inside_the_reserving_transaction() -> None:
    """Sequentially: the Nth call is admitted and the N+1th is not, off the ledger alone.

    No counter file is written here at all — the cap binds on the ledger's own rows, which is
    what makes it inseparable from the dollar reservation.
    """
    limits = SpendLimits(
        per_call_usd=0.25, per_task_usd=1e6, daily_usd=1e6, monthly_usd=1e6, daily_call_cap=2
    )
    for i in range(2):
        reserve_spend(
            model_call_id=f"seq-{i}", task_id="t", subtask_id="s", model_id="m",
            maximum_usd=0.01, limits=limits,
        )
    assert calls_today() == 2
    with pytest.raises(PermissionError, match="daily_call_cap_exceeded"):
        reserve_spend(
            model_call_id="seq-over", task_id="t", subtask_id="s", model_id="m",
            maximum_usd=0.01, limits=limits,
        )
    assert calls_today() == 2, "a refused reservation must not consume a slot"


def test_a_retry_charge_row_does_not_consume_a_call_slot() -> None:
    """A supplemental charge records money already spent, not a new call being admitted."""
    from core.model_spend_ledger import SUPPLEMENTAL_SUBTASK_ID

    ungated = SpendLimits(per_call_usd=0.25, per_task_usd=1e6, daily_usd=1e6, monthly_usd=1e6)
    reserve_spend(
        model_call_id="retry-charge", task_id="t", subtask_id=SUPPLEMENTAL_SUBTASK_ID,
        model_id="m", maximum_usd=0.01, limits=ungated,
    )
    assert calls_today() == 0, "a retry charge is a charge, not a call"

    capped = SpendLimits(
        per_call_usd=0.25, per_task_usd=1e6, daily_usd=1e6, monthly_usd=1e6, daily_call_cap=1
    )
    reserve_spend(
        model_call_id="real-call", task_id="t", subtask_id="s", model_id="m",
        maximum_usd=0.01, limits=capped,
    )
    assert calls_today() == 1


def test_an_absent_call_cap_leaves_the_count_ungated() -> None:
    """Callers that only care about dollars are unaffected by the new field."""
    limits = SpendLimits(per_call_usd=0.25, per_task_usd=1e6, daily_usd=1e6, monthly_usd=1e6)
    assert limits.daily_call_cap is None
    granted, _refused, errored = _race(limits, amount=0.01, prefix="ungated")
    assert not errored
    assert len(granted) == CONCURRENCY


# --- 4. a broken counter still fails CLOSED ----------------------------------------------------


def test_a_corrupt_counter_store_refuses_every_paid_pick(tmp_path) -> None:
    from core import cloud_escalation_policy as cep
    from core import paid_call_reservation as pcr

    store = cep._store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text('{"policy": {"mode": "auto", "daily_cap": 25}, "usage": {', encoding="utf-8")

    assert cep.counter_store_is_corrupt() is True
    assert pcr._daily_call_cap() == 0, "an unreadable counter means no budget, not the default"
    assert pcr.daily_call_cap_available() is False
    with pytest.raises(PermissionError, match="daily_call_cap_exceeded"):
        reserve_spend(
            model_call_id="corrupt", task_id="t", subtask_id="s", model_id="m",
            maximum_usd=0.01, limits=pcr.spend_limits(),
        )


def test_a_healthy_counter_store_still_admits() -> None:
    """The refusal above is the corruption, not a blanket refusal."""
    from core import cloud_escalation_policy as cep
    from core import paid_call_reservation as pcr

    store = cep._store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"policy": {"mode": "auto", "daily_cap": 25}}), encoding="utf-8")

    assert cep.counter_store_is_corrupt() is False
    assert pcr._daily_call_cap() is None
    assert pcr.daily_call_cap_available() is True
    reserve_spend(
        model_call_id="healthy", task_id="t", subtask_id="s", model_id="m",
        maximum_usd=0.01, limits=pcr.spend_limits(),
    )
    assert calls_today() == 1


def test_an_absent_counter_store_is_not_treated_as_corrupt() -> None:
    """A fresh install has spent nothing; it must not be locked out."""
    from core import cloud_escalation_policy as cep

    assert not cep._store_path().exists()
    assert cep.counter_store_is_corrupt() is False


# --- 5. the real entry point, end to end ------------------------------------------------------


@pytest.fixture
def anthropic_lane(monkeypatch):
    """Register the real Anthropic BYOK manifest from a DUMMY key, exactly as the UI does.

    NO CALL IS MADE: only the reservation path runs here, which never touches the transport.
    """
    from unittest import mock

    import core.runtime_provider_defaults as rpd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-DUMMY-NOT-A-REAL-KEY")
    with mock.patch("core.credential_store.has_credential", lambda slot: slot == "llm.cloud.anthropic"):
        provider_id = rpd.activate_provider_byok(
            "anthropic", env={"ANTHROPIC_API_KEY": "sk-ant-DUMMY-NOT-A-REAL-KEY"}
        )
    assert provider_id, "the Anthropic BYOK lane must register from a key"

    from storage.model_provider_manifest import list_provider_manifests

    return next(
        m for m in list_provider_manifests(enabled_only=True) if m.provider_name == "anthropic-byok"
    )


@pytest.mark.parametrize("allowed_reservations", [1, 4])
def test_concurrent_owner_picks_respect_money_without_a_call_quota(
    anthropic_lane, allowed_reservations, monkeypatch,
) -> None:
    """The production path admits only the dollars available, even with many racing chats."""
    from types import SimpleNamespace

    from core import cloud_escalation_policy as cep
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    cep.save_policy(cep.CloudEscalationPolicy(mode="off", daily_cap=0))
    monkeypatch.setattr("core.paid_call_reservation.spend_limits", lambda: SpendLimits(
        per_call_usd=0.25, per_task_usd=1.0, daily_usd=0.25 * allowed_reservations,
        monthly_usd=100.0,
    ))

    def one(i: int):
        try:
            return reserve_owner_pick_paid_call(
                manifest=anthropic_lane,
                task=SimpleNamespace(task_id=f"task-{i}", task_summary="explain this repository"),
                source_context={"_owner_local": True},
            )
        except Exception:  # a refusal must never surface as an exception to the turn
            return "ERROR"

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        out = list(pool.map(one, range(CONCURRENCY)))

    assert "ERROR" not in out, "a refused pick must return None, never raise"
    authorized = [a for a in out if a is not None]
    assert len(authorized) == allowed_reservations
    assert calls_today() == allowed_reservations
    assert sum(a.reservation.reserved_usd for a in authorized) <= 0.25 * allowed_reservations


def test_an_unwritable_ledger_refuses_rather_than_granting(tmp_path) -> None:
    from storage.db import configure_default_db_path, reset_default_connection

    db_path = tmp_path / "data" / "unwritable.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    configure_default_db_path(db_path)
    reset_default_connection()
    db_path.mkdir(parents=True, exist_ok=True)  # a directory where the DB file must go

    limits = SpendLimits(per_call_usd=0.25, per_task_usd=1e6, daily_usd=1e6, monthly_usd=1e6)
    with pytest.raises(Exception) as caught:
        reserve_spend(
            model_call_id="unwritable", task_id="t", subtask_id="s", model_id="m",
            maximum_usd=0.01, limits=limits,
        )
    assert caught.value is not None, "an unwritable ledger must raise, never grant"
