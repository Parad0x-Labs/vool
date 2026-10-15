"""Runtime shutdown owns the one-shot presence sync thread.

Before: `stop_background_runtime_threads` joined the idle-commons and heartbeat loops but not the
`vool-public-presence-sync` thread. A sync queued by one runtime ran on after that runtime's
shutdown and wrote its audit rows into whatever storage was active by then: in CI shard 1 it raised
`no such table: event_log_v2` inside a later test, and a later served turn in the same order lost
its evidence binding (409 evidence_missing).

Every test here drives the real bootstrapped runtime from the First-Run Pact rig (real agent, real
`RuntimeServices.shutdown`, real audit storage). Only the network edge is replaced: a gated public
hive bridge that holds `sync_presence` until the test releases it, so "in flight" is a fact, not
a timing guess.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any

import pytest

from core import policy_engine, runtime_paths
from core.agent_runtime import presence
from tests.first_run_pact_rig import pact_rig

_SYNC_EVENTS = ("public_hive_presence_sync_failed", "public_hive_presence_sync_error")


@pytest.fixture(autouse=True)
def background_network_consent(tmp_path, monkeypatch):
    """The operator has turned background network on in the rig's home before its runtime boots.

    A fresh install keeps it off and then no presence sync starts at all (that default is pinned
    in test_fresh_install_network_defaults); the lane these tests stop is the one that consent
    starts. Autouse, so it runs before `pact_rig` boots the runtime in the same home.
    """
    home = tmp_path / "pact-home"  # the home pact_rig boots its runtime in
    old_override = runtime_paths._VOOL_HOME_OVERRIDE
    old_binding = policy_engine._POLICY_CACHE_BINDING
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", None)
    policy_engine.set_operator_policy_values({"network.outbound_enabled": True})
    assert policy_engine.automatic_outbound_enabled() is True
    try:
        yield
    finally:
        runtime_paths.configure_runtime_home(old_override)
        policy_engine._POLICY_CACHE_BINDING = old_binding


class _GatedBridge:
    """The public hive bridge with its presence POST held at a gate (the network edge only)."""

    def __init__(self, inner: Any, *, label: str = "gated", ok: bool = False) -> None:
        self._inner = inner
        self.label = label
        self.ok = ok
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.calls = 0
        self.sync_calls = 0
        self.heartbeat_calls = 0

    def _answer(self) -> dict[str, Any]:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(20.0), "test never released the gate"
        self.returned.set()
        if self.ok:
            return {"ok": True, "status": "registered", "posted_to": ["gate"], "errors": []}
        return {"ok": False, "status": "unreachable", "posted_to": [], "errors": [self.label]}

    def sync_presence(self, **_kwargs: Any) -> dict[str, Any]:
        self.sync_calls += 1
        return self._answer()

    def heartbeat_presence(self, **_kwargs: Any) -> dict[str, Any]:
        self.heartbeat_calls += 1
        return self._answer()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _sync_rows(rig) -> list[tuple[str, dict[str, Any]]]:
    db = rig.home / "data" / "vool_web0_v2.db"
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT event_type, details_json FROM audit_log WHERE event_type IN (?, ?) ORDER BY rowid",
            _SYNC_EVENTS,
        ).fetchall()
    return [(event, json.loads(details or "{}")) for event, details in rows]


def _settled_agent(rig):
    """The rig's real agent after its start-up sync has finished, with the gated bridge fitted."""
    agent = rig.runtime.agent
    with agent._public_presence_lock:
        boot_thread = agent._public_presence_sync_thread
    if boot_thread is not None:
        boot_thread.join(10.0)
        assert not boot_thread.is_alive(), "the start-up presence sync never finished"
    bridge = _GatedBridge(agent.public_hive_bridge)
    agent.public_hive_bridge = bridge
    return agent, bridge


def _sync_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "vool-public-presence-sync" and t.is_alive()]


def test_the_served_runtime_still_publishes_presence_and_shutdown_leaves_no_sync_thread(pact_rig):
    """The real served flow: start + one real /api/chat turn sync presence; shutdown ends the lane."""
    before_threads = set(_sync_threads())
    request_id, _frames, _commit = pact_rig.run_task_turn("Create presence-check.txt containing ok.")
    assert request_id, "the served turn committed"
    pact_rig.runtime.shutdown()
    rows = _sync_rows(pact_rig)
    assert rows, "start-up and the served turn both reached the presence sync and audited it"
    assert all(details.get("status") == "disabled" for _event, details in rows), rows
    leftover = [t for t in _sync_threads() if t not in before_threads]
    assert leftover == [], f"presence sync threads outlived runtime shutdown: {leftover}"


def test_shutdown_joins_the_in_flight_sync_and_drops_the_queued_pass(pact_rig):
    agent, bridge = _settled_agent(pact_rig)
    rows_before = _sync_rows(pact_rig)

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0), "the sync runs on its own thread"
    agent._sync_public_presence(status="idle")  # queued behind the in-flight pass
    thread = agent._public_presence_sync_thread
    assert thread is not None and thread.is_alive()

    gate = threading.Timer(0.3, bridge.release.set)
    gate.start()
    try:
        pact_rig.runtime.shutdown()
    finally:
        gate.cancel()
        bridge.release.set()

    assert not thread.is_alive(), "runtime shutdown returned while its presence sync still ran"
    assert agent._public_presence_sync_thread is None
    assert bridge.calls == 1, "the queued pass did not run after shutdown"
    new_rows = _sync_rows(pact_rig)[len(rows_before):]
    assert [(event, details.get("status")) for event, details in new_rows] == [
        ("public_hive_presence_sync_failed", "unreachable")
    ], "the in-flight pass audited into its own runtime's storage before shutdown returned"


def test_a_stopped_runtime_starts_no_new_sync_thread(pact_rig):
    agent, bridge = _settled_agent(pact_rig)
    pact_rig.runtime.shutdown()
    rows_before = _sync_rows(pact_rig)

    agent._sync_public_presence(status="busy")

    assert agent._public_presence_sync_thread is None
    assert bridge.calls == 0
    assert _sync_rows(pact_rig) == rows_before


def test_a_hung_sync_is_bounded_by_the_join_reported_and_writes_nothing_afterwards(pact_rig, caplog):
    agent, bridge = _settled_agent(pact_rig)
    rows_before = _sync_rows(pact_rig)
    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    thread = agent._public_presence_sync_thread

    with caplog.at_level(logging.WARNING, logger=presence.__name__):
        report = presence.stop_background_runtime_threads(agent, join_timeout=0.2)
        assert report["_public_presence_sync_thread"] is False, "the hung sync is reported, not hidden"
        assert any("still running after" in r.getMessage() for r in caplog.records)

        bridge.release.set()  # the hung POST finally returns after shutdown
        thread.join(10.0)
        assert not thread.is_alive()
        assert any("not written" in r.getMessage() for r in caplog.records)

    assert _sync_rows(pact_rig) == rows_before, "a sync that outlived shutdown wrote an audit row"


def test_start_stop_twice_leaves_no_first_run_sync_able_to_write_or_hold_the_lane(pact_rig):
    agent, first_bridge = _settled_agent(pact_rig)
    rows_before = _sync_rows(pact_rig)
    agent._sync_public_presence(status="busy")
    assert first_bridge.entered.wait(10.0)
    first_thread = agent._public_presence_sync_thread
    report = presence.stop_background_runtime_threads(agent, join_timeout=0.2)
    assert report["_public_presence_sync_thread"] is False

    # Second run of the same agent: its start-up handshake syncs on a fresh, ungated bridge.
    second_bridge = _GatedBridge(first_bridge._inner)
    second_bridge.release.set()
    agent.public_hive_bridge = second_bridge
    agent.start()
    second_thread = agent._public_presence_sync_thread
    assert second_thread is not None and second_thread is not first_thread
    second_thread.join(10.0)
    assert not second_thread.is_alive() and second_bridge.calls == 1

    first_bridge.release.set()  # the first run's hung sync returns into the second run
    first_thread.join(10.0)
    assert not first_thread.is_alive()

    new_rows = _sync_rows(pact_rig)[len(rows_before):]
    assert [details.get("status") for _event, details in new_rows] == [
        "unreachable"
    ], "only the second run's own sync audited; the first run's late return wrote nothing"
    assert agent._public_presence_sync_inflight is False
    assert agent._public_presence_sync_thread is None
    agent._sync_public_presence(status="busy")  # the lane is open for the second run
    third_thread = agent._public_presence_sync_thread
    assert third_thread is not None
    # Open means the second run's next pass reaches the bridge. Shut down only once it has: a
    # pass that has not begun when stop begins is dropped by design (the next test), so shutting
    # down at once would race the pass for the presence lock instead of joining it.
    deadline = time.monotonic() + 10.0
    while second_bridge.calls < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert second_bridge.calls == 2, "the second run's next pass never reached the bridge"
    pact_rig.runtime.shutdown()
    assert not third_thread.is_alive() and second_bridge.calls == 2


def test_a_sync_not_yet_begun_when_stop_begins_makes_no_call_and_writes_nothing(pact_rig, monkeypatch):
    """The other side of that race, held deterministically: the sync's thread is started but has
    not begun its pass when stop begins. The pass is dropped: no bridge call (no network effect
    after stop began), no audit row, and stop still joins the thread inside its bound."""
    agent, bridge = _settled_agent(pact_rig)
    bridge.release.set()
    rows_before = _sync_rows(pact_rig)
    real_worker = presence._public_presence_sync_worker

    def _begins_after_stop(**kwargs):
        deadline = time.monotonic() + 10.0
        while not agent._public_presence_sync_stopped and time.monotonic() < deadline:
            time.sleep(0.001)
        return real_worker(**kwargs)

    monkeypatch.setattr(presence, "_public_presence_sync_worker", _begins_after_stop)
    agent._sync_public_presence(status="busy")
    thread = agent._public_presence_sync_thread
    assert thread is not None and thread.is_alive()

    report = presence.stop_background_runtime_threads(agent, join_timeout=2.0)
    assert report["_public_presence_sync_thread"] is True, "stop joined the dropped sync's thread"
    assert not thread.is_alive()
    assert bridge.calls == 0, "a pass that had not begun when stop began still reached the network"
    assert _sync_rows(pact_rig) == rows_before
    pact_rig.runtime.shutdown()


def test_storage_gone_mid_sync_stays_inside_the_sync_thread(pact_rig, caplog, monkeypatch):
    agent, bridge = _settled_agent(pact_rig)
    escaped: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args.exc_value))

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    thread = agent._public_presence_sync_thread
    # The storage under the runtime goes away while the POST is in flight.
    with sqlite3.connect(pact_rig.home / "data" / "vool_web0_v2.db") as conn:
        conn.execute("DROP TABLE audit_log")

    with caplog.at_level(logging.WARNING, logger=presence.__name__):
        bridge.release.set()
        thread.join(10.0)
    assert not thread.is_alive()
    assert escaped == [], f"the storage error escaped the sync thread: {escaped!r}"
    assert any("could not be written" in r.getMessage() for r in caplog.records)
    pact_rig.runtime.shutdown()


def _hold_the_database(rig) -> sqlite3.Connection:
    """Another writer holds the runtime's SQLite write lock (a real BEGIN IMMEDIATE)."""
    conn = sqlite3.connect(
        rig.home / "data" / "vool_web0_v2.db", timeout=1, isolation_level=None, check_same_thread=False
    )
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _timed_stop(agent, join_timeout: float) -> tuple[dict[str, bool] | None, float | None]:
    """Run stop on a thread of its own so a stop that ignores its bound cannot hang the test."""
    out: dict[str, Any] = {}

    def _stop() -> None:
        started = time.monotonic()
        out["report"] = presence.stop_background_runtime_threads(agent, join_timeout=join_timeout)
        out["elapsed"] = time.monotonic() - started

    stopper = threading.Thread(target=_stop, name="test-presence-stop", daemon=True)
    stopper.start()
    stopper.join(10.0)
    return out.get("report"), out.get("elapsed")


def test_stop_is_bounded_while_another_writer_holds_the_database_and_restart_gets_no_late_row(pact_rig, caplog):
    """The audit's contention probe as a test: the sync's audit write waits on a real SQLite lock."""
    agent, first = _settled_agent(pact_rig)
    rows_before = _sync_rows(pact_rig)
    holder = _hold_the_database(pact_rig)
    held = True
    try:
        first.release.set()
        agent._sync_public_presence(status="busy")
        first_thread = agent._public_presence_sync_thread
        assert first.returned.wait(10.0), "the presence POST returned; the sync now writes its audit row"
        first_thread.join(0.2)
        assert first_thread.is_alive(), "the audit write is waiting on the held database"

        with caplog.at_level(logging.WARNING, logger=presence.__name__):
            report, elapsed = _timed_stop(agent, join_timeout=0.05)
            assert elapsed is not None, "stop never returned while another writer held the database"
            assert elapsed < 0.35, f"stop took {elapsed:.3f}s against a 0.05s join bound"
            assert report["_public_presence_sync_thread"] is False, "the unfinished sync is reported"
            first_thread.join(2.0)
            assert not first_thread.is_alive(), "the waiting write was abandoned while the lock was still held"
            assert any("not written" in r.getMessage() for r in caplog.records)
    finally:
        if held:
            holder.execute("ROLLBACK")
            holder.close()
    held = False

    # The other writer is done; restart on the same home. Only the new run's own sync audits.
    second = _GatedBridge(first._inner, label="second-run")
    second.release.set()
    agent.public_hive_bridge = second
    agent.start()
    second_thread = agent._public_presence_sync_thread
    assert second_thread is not None and second_thread is not first_thread
    second_thread.join(10.0)
    assert not second_thread.is_alive()
    new_rows = _sync_rows(pact_rig)[len(rows_before):]
    assert [details.get("errors") for _event, details in new_rows] == [
        ["second-run"]
    ], "only the restarted run audited; the stopped run's waiting write never landed"
    pact_rig.runtime.shutdown()


def test_a_write_waiting_on_the_database_lands_when_the_lock_frees_inside_the_bound(pact_rig):
    agent, bridge = _settled_agent(pact_rig)
    rows_before = _sync_rows(pact_rig)
    holder = _hold_the_database(pact_rig)
    unlock = threading.Timer(0.3, lambda: (holder.execute("ROLLBACK"), holder.close()))
    try:
        bridge.release.set()
        agent._sync_public_presence(status="busy")
        thread = agent._public_presence_sync_thread
        assert bridge.returned.wait(10.0)
        unlock.start()
        report, elapsed = _timed_stop(agent, join_timeout=2.0)
    finally:
        unlock.join(5.0)
    assert elapsed is not None and elapsed < 2.3, elapsed
    assert report["_public_presence_sync_thread"] is True
    assert not thread.is_alive()
    new_rows = _sync_rows(pact_rig)[len(rows_before):]
    assert [details.get("errors") for _event, details in new_rows] == [["gated"]], (
        "the in-flight write completed in its own runtime's storage before stop returned"
    )


def test_a_late_success_after_stop_and_restart_is_not_recorded_into_the_new_run(pact_rig):
    agent, first = _settled_agent(pact_rig)
    assert agent._public_presence_registered is False, "the rig's hive is disabled: never registered"
    first.ok = True
    rows_before = _sync_rows(pact_rig)
    agent._sync_public_presence(status="busy")
    assert first.entered.wait(10.0)
    first_thread = agent._public_presence_sync_thread
    report = presence.stop_background_runtime_threads(agent, join_timeout=0.2)
    assert report["_public_presence_sync_thread"] is False

    second = _GatedBridge(first._inner, label="second-run")
    second.release.set()
    agent.public_hive_bridge = second
    agent.start()
    second_thread = agent._public_presence_sync_thread
    second_thread.join(10.0)
    assert not second_thread.is_alive()

    first.release.set()  # the stopped run's POST now succeeds, inside the restarted run
    first_thread.join(10.0)
    assert not first_thread.is_alive()
    assert agent._public_presence_registered is False, "a stopped run's late success was recorded as registration"
    new_rows = _sync_rows(pact_rig)[len(rows_before):]
    assert [details.get("errors") for _event, details in new_rows] == [["second-run"]]
    pact_rig.runtime.shutdown()


def test_a_failed_heartbeat_after_stop_attempts_no_re_registration(pact_rig):
    agent, registering = _settled_agent(pact_rig)
    registering.ok = True
    registering.release.set()
    agent._sync_public_presence(status="busy")
    agent._public_presence_sync_thread.join(10.0)
    assert agent._public_presence_registered is True, "a real successful sync registered this run"

    heartbeat = _GatedBridge(registering._inner, label="heartbeat-failed")
    agent.public_hive_bridge = heartbeat
    rows_before = _sync_rows(pact_rig)
    agent._sync_public_presence(status="idle")
    assert heartbeat.entered.wait(10.0)
    thread = agent._public_presence_sync_thread
    report = presence.stop_background_runtime_threads(agent, join_timeout=0.2)
    assert report["_public_presence_sync_thread"] is False
    heartbeat.release.set()  # the heartbeat fails after stop
    thread.join(10.0)
    assert not thread.is_alive()
    assert heartbeat.heartbeat_calls == 1
    assert heartbeat.sync_calls == 0, "a stopped run re-registered through the bridge after its heartbeat failed"
    assert _sync_rows(pact_rig) == rows_before


def test_a_sync_write_into_a_removed_store_is_abandoned_inside_its_thread(pact_rig, caplog, monkeypatch):
    agent, bridge = _settled_agent(pact_rig)
    escaped: list[BaseException] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args.exc_value))
    db = pact_rig.home / "data" / "vool_web0_v2.db"
    parked = pact_rig.home / "parked-store"
    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    thread = agent._public_presence_sync_thread
    parked.mkdir()
    moved = [p for p in db.parent.iterdir() if p.name.startswith(db.name)]
    for path in moved:
        path.rename(parked / path.name)
    try:
        with caplog.at_level(logging.WARNING, logger=presence.__name__):
            bridge.release.set()
            thread.join(10.0)
        assert not thread.is_alive()
        # Other runtime lanes may open the default path again; the sync itself never writes there.
        if db.exists():
            with sqlite3.connect(db) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                late = (
                    conn.execute(
                        "SELECT count(*) FROM audit_log WHERE event_type IN (?, ?)", _SYNC_EVENTS
                    ).fetchone()[0]
                    if "audit_log" in tables
                    else 0
                )
            assert late == 0, "the sync's audit write landed in a store recreated in place of the removed one"
        assert escaped == [], f"the storage error escaped the sync thread: {escaped!r}"
        assert any("store is gone" in r.getMessage() for r in caplog.records)
    finally:
        for path in list(db.parent.iterdir()):
            if path.name.startswith(db.name):
                path.unlink()
        for path in parked.iterdir():
            path.rename(db.parent / path.name)
    pact_rig.runtime.shutdown()
