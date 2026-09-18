"""R2B2C — DURABLE BACKGROUND EFFECT EVIDENCE.

R2b2a1 gave the three relay daemons legitimate named effect scopes, and the
R2b2a1 doc states the retention truth plainly: "scope-local … handed to no
one at close, not durable". A relay send's authorized/started/terminal
account dies with the daemon process. Nothing can answer what a background
lane attempted, whether its transport ran, or how it ended — not during the
run (the scope never closes), and not after one (restart wipes the ledger).

WHAT THIS FILE PINS (the objective's ten named cases, then the laws)

* receipts today die in memory (the control that proves the defect first)
* successful relay send  → durable authorized+started+succeeded, linked
* failed send            → durable terminal failed
* cancellation           → durable terminal cancelled
* retry                  → new attempt linked to the SAME logical effect
* concurrent daemons     → distinct daemon instances, never crossed
* restart/query          → evidence reconstructed fresh from the store
* duplicate persistence  → idempotent (identical event, one row)
* retention overflow     → hard cap with explicit dropped/truncated accounting
* corrupt store          → transport continues per policy, failure observable,
                           no success falsified
* secret-bearing exception → type-token reasons only, no webhook tokens/URLs

and: background evidence invents NO turn identity (no request_id/turn_id/
session keys at all — absence is provable), the three relay daemon scopes are
the only wired scopes (AST pin), and the journal's reason sanitizer redacts
unsafe text as the last line before disk.

New API is imported INSIDE tests: at the base commit every case fails on its
own line, not on one collection error.
"""
from __future__ import annotations

import ast
import concurrent.futures
import pathlib
import sqlite3
import urllib.error
import urllib.request
from contextlib import suppress as contextlib_suppress
from contextvars import copy_context

import pytest

from core.effect_gateway import named_background_effect_scope
from core.remote_fetch_policy import open_remote_url

WEBHOOK = "https://discord.com/api/webhooks/1095/TOKENVALUE7/secrets"


# --------------------------------------------------------------------------- harness


@pytest.fixture(autouse=True)
def _clean_background_evidence():
    """Each test starts with no background-effect evidence and a fresh journal
    (the restart simulation resets it too). The conftest storage fixture has
    already pointed the default DB at the isolated test database. Every step
    is base-tolerant: at the pre-R2b2c commit none of this exists, and each
    test must still fail on ITS OWN line, not in this fixture. Each statement
    is guarded individually and the commit ALWAYS runs (the R2b2c-amendment
    lesson: a suppressed failure skipping the commit lets the pooled close()
    roll the deletes back, and rows leak into the next test)."""
    from storage.db import get_connection

    with contextlib_suppress(Exception):
        conn = get_connection()
        try:
            for statement in (
                "DELETE FROM background_effect_journal_v1",
                "DELETE FROM background_effect_witness_v1",
                "DELETE FROM background_effect_checkpoint_v1",
                "DELETE FROM event_log_v2 WHERE category = 'background_effect'",
                "DELETE FROM event_hash_chain WHERE event_id LIKE 'bge:%'",
            ):
                with contextlib_suppress(sqlite3.Error):
                    conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
    from core import effect_gateway as eg

    with contextlib_suppress(AttributeError):
        eg.reset_background_effect_journal()
    yield
    with contextlib_suppress(AttributeError):
        eg.reset_background_effect_journal()


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self.status = status


def _evidence(**filters) -> dict:
    from core.effect_gateway import background_effect_evidence

    return background_effect_evidence(**filters)


def _events(**filters) -> list[dict]:
    return list(_evidence(**filters).get("events") or ())


def _lifecycles(effect_id: str = "") -> list[str]:
    return [str(e.get("lifecycle")) for e in _events(effect_id=effect_id) if e.get("kind") == "effect"]


def _one_send(monkeypatch, *, fail: BaseException | None = None) -> str:
    """One relay-shaped send through the real door under a durable scope. A
    failing transport raises through the door as always; this helper holds
    the raised exception (the tests assert the durable record of it)."""
    if fail is None:
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
        )
    else:
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda request, timeout=None, **kw: (_ for _ in ()).throw(fail)
        )
    with named_background_effect_scope("relay.test", durable=True):
        try:
            open_remote_url(WEBHOOK, timeout=5)
        except BaseException as exc:  # the door re-raises; the account is what matters here
            assert isinstance(exc, type(fail)) if fail else True
        from core.effect_gateway import effect_receipts

        entries = effect_receipts()
        assert entries, "the durable scope kept its in-memory account too"
        return str(entries[0].get("effect_id") or "")


def _require_durable_chain(effect_id: str, expected: list[str]) -> None:
    """The named invariant: the durable account shows the full ordered chain
    for one effect. Sabotage tests reuse this under `pytest.raises`."""
    seen = _lifecycles(effect_id)
    assert seen == expected, f"durable evidence for {effect_id}: expected {expected}, shows {seen}"


# ------------------------------------------------- 0. the control: receipts die today


def test_background_receipts_today_die_in_memory(monkeypatch):
    """The defect, stated first. A NON-durable named scope (every other
    background lane today) sends successfully, closes — and there is nothing
    durable anywhere. Durability is the three relay scopes' wiring, not an
    ambient property of every scope."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
    )
    with named_background_effect_scope("relay.nondurable"):
        open_remote_url(WEBHOOK, timeout=5)
    report = _evidence()
    assert report["events"] == (), "a non-durable scope must leave no durable evidence"


# ------------------------------------------------------------ 1. successful relay send


def test_successful_relay_send_is_durable_while_the_scope_is_open(monkeypatch):
    """Req 1/2/3/4. The run_forever scope NEVER closes, so evidence must be
    durable AS IT OCCURS: queryable mid-flight, linked under one effect_id,
    carrying scope and daemon instance — and NO turn identity keys at all."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
    )
    with named_background_effect_scope("relay.test", durable=True):
        open_remote_url(WEBHOOK, timeout=5)
        report = _evidence()  # mid-flight: the scope is still open

    events = [e for e in report["events"] if e.get("kind") == "effect"]
    _require_durable_chain(events[0]["effect_id"], ["authorized", "started", "succeeded"])
    by_id = {e["effect_id"] for e in events}
    assert len(by_id) == 1, "one logical effect, one durable id"
    first = events[0]
    assert first["scope_name"] == "relay.test"
    assert str(first.get("scope_instance") or "").startswith("bgi:"), (
        "the daemon instance must be preserved, not just the scope name"
    )
    assert first["effect_class"] == "network_fetch"
    assert first["decision"] == "allowed"
    assert first["recorded_at"] and first.get("stored_at"), "both timestamps preserved"
    for event in report["events"]:
        blob = repr(event)
        for absent in ("turn_id", "request_id", "session"):
            assert absent not in blob, (
                f"background evidence must not fabricate {absent}: {blob}"
            )


# ------------------------------------------------------------------ 2. failed send


def test_failed_relay_send_is_durable_and_linked(monkeypatch):
    """Req 4. The failure is part of the durable chain, not lost with the
    process."""
    effect_id = _one_send(monkeypatch, fail=ConnectionRefusedError("send refused"))
    _require_durable_chain(effect_id, ["authorized", "started", "failed"])
    failed = next(e for e in _events(effect_id=effect_id) if e["lifecycle"] == "failed")
    assert "ConnectionRefusedError" in failed["reason"]
    assert "refused" not in failed["reason"].lower() or "Error" in failed["reason"]


# ----------------------------------------------------------------- 3. cancellation


def test_cancelled_relay_send_is_durable(monkeypatch):
    """Req 4. Cancellation is a terminal in the durable account too."""
    import concurrent.futures as cf

    effect_id = _one_send(monkeypatch, fail=cf.CancelledError())
    _require_durable_chain(effect_id, ["authorized", "started", "cancelled"])


# ------------------------------------------------------------------------ 4. retry


def test_retry_gets_a_new_attempt_linked_to_the_same_durable_effect(monkeypatch):
    """Req 5. The durable account carries both attempts under one effect_id."""
    calls: list[int] = []

    def _flaky(request, timeout=None, **kw):
        calls.append(len(calls))
        if len(calls) == 1:
            raise ConnectionRefusedError("first refused")
        return _Resp(204)

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    with named_background_effect_scope("relay.test", durable=True):
        with pytest.raises(ConnectionRefusedError):
            open_remote_url(WEBHOOK, timeout=5)
        from core.effect_gateway import effect_receipts

        first = {e["effect_id"] for e in effect_receipts()}.pop()
        open_remote_url(WEBHOOK, timeout=5, retry_of=first)

    _require_durable_chain(
        first, ["authorized", "started", "failed", "started", "succeeded"]
    )
    attempts = sorted(
        e["attempt"] for e in _events(effect_id=first) if e.get("kind") == "effect"
    )
    assert attempts == [0, 1, 1, 2, 2], f"retry keeps one logical id: {attempts}"


# --------------------------------------------------------------- 5. concurrent daemons


def test_concurrent_daemons_keep_distinct_instances(monkeypatch):
    """Req 2/5. Two workers of the SAME scope name run at once; each durable
    event names its own daemon instance and the chains never cross."""
    import threading

    barrier = threading.Barrier(2, timeout=10)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None, **kw: (barrier.wait(), _Resp(204))[1],
    )
    errors: list[BaseException] = []

    def _daemon() -> None:
        try:
            with named_background_effect_scope("relay.test", durable=True):
                open_remote_url(WEBHOOK, timeout=5)
        except BaseException as exc:  # pragma: no cover - surfaced via list
            errors.append(exc)

    ctx_a, ctx_b = copy_context(), copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(ctx_a.run, _daemon)
        pool.submit(ctx_b.run, _daemon)

    assert not errors, errors
    events = [e for e in _events() if e.get("kind") == "effect"]
    instances = {e["scope_instance"] for e in events}
    assert len(instances) == 2, f"two daemon instances, saw {instances}"
    ids = {e["effect_id"] for e in events}
    assert len(ids) == 2
    for effect_id in ids:
        _require_durable_chain(effect_id, ["authorized", "started", "succeeded"])


# ------------------------------------------------------------------ 6. restart/query


def test_restart_reconstructs_the_same_evidence(monkeypatch):
    """Req 7. After the scope closes and the journal is reset (a new process
    owns no memory of it), the store answers with the same evidence."""
    effect_id = _one_send(monkeypatch)
    from core import effect_gateway as eg

    eg.reset_background_effect_journal()
    report = _evidence()

    assert report["persistence_failures"] == 0
    _require_durable_chain(effect_id, ["authorized", "started", "succeeded"])
    one = _evidence(effect_id=effect_id)
    assert [e["lifecycle"] for e in one["events"] if e.get("kind") == "effect"] == [
        "authorized",
        "started",
        "succeeded",
    ]
    scoped = _evidence(scope_name="relay.test")
    assert scoped["events"], "scope filter answers"
    assert _evidence(scope_name="relay.other")["events"] == ()


# ------------------------------------------------------------ 7. duplicate persistence


def test_duplicate_persistence_is_idempotent(monkeypatch):
    """Req 5. Re-persisting the identical transition is a no-op: the event id
    is deterministic for (effect, attempt, lifecycle) and the store keeps one
    row."""
    effect_id = _one_send(monkeypatch)
    before = _evidence()["kept"]
    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()
    entry = next(e for e in _events(effect_id=effect_id) if e.get("kind") == "effect")
    # the journal's record() takes the LEDGER entry shape; rebuild it from the
    # durable fields it preserves
    ledger_entry = {
        "effect_id": entry["effect_id"],
        "attempt": entry["attempt"],
        "lifecycle": entry["lifecycle"],
        "effect_class": entry["effect_class"],
        "decision": entry["decision"],
        "reason": entry["reason"],
        "host": entry["host"],
        "mode": entry.get("mode", ""),
        "decided_by": entry.get("decided_by", ""),
        "recorded_at": entry["recorded_at"],
    }
    assert journal.record(
        ledger_entry, scope_name="relay.test", scope_instance=entry["scope_instance"]
    )
    assert journal.record(
        ledger_entry, scope_name="relay.test", scope_instance=entry["scope_instance"]
    )
    after = _evidence()
    assert after["kept"] == before, "a duplicate writes no second row"


# ------------------------------------------------------------- 8. retention overflow


def test_retention_overflow_bounds_the_account_and_says_what_was_dropped(monkeypatch):
    """Req 6. A hard cap on durable background-effect events; the account row
    states exactly how many were dropped; the query surfaces it."""
    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()
    journal.cap = 6  # two sends' worth of detail, no more

    sends = 3  # 9 lifecycle events
    for index in range(sends):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda request, timeout=None, **kw: _Resp(204),
        )
        with named_background_effect_scope(f"relay.test{index}", durable=True):
            open_remote_url(f"{WEBHOOK}/{index}", timeout=5)

    report = _evidence()
    effect_rows = [e for e in report["events"] if e.get("kind") == "effect"]
    assert report["kept"] <= journal.cap, (
        f"the durable account is bounded at {journal.cap}, kept {report['kept']}"
    )
    assert report["dropped"] >= sends * 3 - journal.cap, (
        "the dropped count must account for everything past the cap"
    )
    assert report["truncated"] is True
    assert report["cap"] == journal.cap
    assert effect_rows, "the newest evidence survives the bound"


# ------------------------------------------------------------------- 9. corrupt store


def test_corrupt_store_never_authorizes_or_falsifies(monkeypatch):
    """Req 8. Persistence failing does not stop or widen the transport (policy
    governs, as before), is explicitly observable, and fabricates no success.
    R2b2c amendment: the journal owns its storage seam now
    (`_journal_connection`) — that is where an unavailable store is injected."""
    real_send: list[int] = []

    def _ok(request, timeout=None, **kw):
        real_send.append(len(real_send))
        return _Resp(204)

    monkeypatch.setattr(urllib.request, "urlopen", _ok)

    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()

    def _corrupt(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(journal, "_journal_connection", _corrupt)
    with named_background_effect_scope("relay.test", durable=True):
        open_remote_url(WEBHOOK, timeout=5)  # must run per policy regardless

    assert real_send, "a persistence failure must not block the transport"
    from core.effect_gateway import background_effect_persistence_status

    status = background_effect_persistence_status()
    assert status["failures"] >= 3, "every lost lifecycle write is counted"
    assert status["last_error"], "the failure is observable, not swallowed"
    report = _evidence()
    assert report["events"] == (), "a corrupt store fabricates no evidence"
    assert report["persistence_failures"] >= 3
    # and a FAILED send under the same corruption is never falsified:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None, **kw: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    with named_background_effect_scope("relay.test", durable=True):
        with pytest.raises(ConnectionRefusedError):
            open_remote_url(WEBHOOK, timeout=5)
    assert _evidence()["events"] == (), "no success, no failure, nothing invented"


# ------------------------------------------------------- 10. secret-bearing exception


def test_secret_bearing_exception_leaves_nothing_durable(monkeypatch):
    """Req 9. The webhook URL carries its token in the path; a leaky exception
    message carries it too. The durable payload keeps the host and a
    type-derived token only."""
    leaky = urllib.error.URLError(
        f"POST {WEBHOOK} failed (Authorization: Bearer TOKENVALUE7)"
    )
    effect_id = _one_send(monkeypatch, fail=leaky)
    _require_durable_chain(effect_id, ["authorized", "started", "failed"])
    blob = repr(_events())
    for forbidden in ("TOKENVALUE7", "webhooks", "Bearer", "discord.com/api"):
        assert forbidden not in blob, f"durable evidence leaked {forbidden!r}"
    hosts = {e.get("host") for e in _events(effect_id=effect_id)}
    assert hosts == {"discord.com"}, "hostname only — the path is the secret"


def test_reason_sanitizer_redacts_unsafe_text_before_disk():
    """Req 9, last line of defense: an entry whose reason text is not a safe
    token (a URL, a token fragment) is redacted BEFORE the store."""
    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()
    entry = {
        "effect_id": "eff:test-9999",
        "attempt": 1,
        "lifecycle": "failed",
        "effect_class": "network_fetch",
        "decision": "allowed",
        "reason": f"GET {WEBHOOK} exploded with TOKENVALUE7",
        "host": "discord.com",
        "mode": "",
        "decided_by": "test",
        "recorded_at": "2026-08-31T00:00:00+00:00",
    }
    assert journal.record(entry, scope_name="relay.test", scope_instance="bgi:t")
    persisted = [
        e for e in _events(effect_id="eff:test-9999") if e.get("kind") == "effect"
    ]
    assert persisted and persisted[0]["reason"] != entry["reason"]
    assert "TOKENVALUE7" not in repr(persisted)


# ------------------------------------------------------------ wiring: three relays only


def test_exactly_the_three_relay_daemon_scopes_are_wired_durable():
    """Req 10, structural. relay.discord, relay.telegram, relay.telegram_chat
    open their scopes durable=True; no other module passes durable=True.
    (SyntaxWarning is silenced while walking: at least one unrelated module
    in the tree carries a pre-existing invalid escape.)"""
    import warnings

    wired = []
    for path in sorted(pathlib.Path(".").rglob("*.py")):
        if "__pycache__" in str(path) or "tests/" in str(path):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "named_background_effect_scope"):
                continue
            durable = any(
                kw.arg == "durable"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            name = ""
            if node.args and isinstance(node.args[0], ast.Constant):
                name = str(node.args[0].value)
            if durable:
                wired.append(f"{path}:{name}")
    assert sorted(wired) == sorted(
        [
            "relay/bridge_workers/discord_bridge.py:relay.discord",
            "relay/bridge_workers/telegram_bridge.py:relay.telegram",
            "relay/bridge_workers/telegram_chat_bridge.py:relay.telegram_chat",
        ]
    ), f"durability is wired for exactly the three relay daemons, saw {wired}"


# --------------------------------------------------------------------------- sabotage
#
# Each sabotage injects the named defect AT RUNTIME and asserts the invariant
# helper CATCHES it (raises). If a defect can slip past, the named test above
# has no teeth — and this file fails.


def test_sabotage_lost_terminal_is_caught(monkeypatch):
    """Inject: the journal drops terminal entries. The durable-chain invariant
    must fire."""
    from core.effect_gateway import LIFECYCLE_TERMINALS, _background_effect_journal

    journal = _background_effect_journal()
    real = journal.record

    def loses_terminals(entry, **kw):
        if str(entry.get("lifecycle")) in LIFECYCLE_TERMINALS:
            return True
        return real(entry, **kw)

    monkeypatch.setattr(journal, "record", loses_terminals)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
    )
    with named_background_effect_scope("relay.test", durable=True):
        open_remote_url(WEBHOOK, timeout=5)
        from core.effect_gateway import effect_receipts

        entries = effect_receipts()
        effect_id = str(entries[0]["effect_id"])
    with pytest.raises(AssertionError):
        _require_durable_chain(effect_id, ["authorized", "started", "succeeded"])


def test_sabotage_duplicate_event_is_caught(monkeypatch):
    """Inject: non-deterministic event ids — the same transition persists
    twice. The kept-count invariant must fire."""
    import uuid

    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()

    def dup(event_id, **kw):
        return str(uuid.uuid4())

    monkeypatch.setattr(journal, "_event_id_for", dup)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
    )
    with named_background_effect_scope("relay.test", durable=True):
        open_remote_url(WEBHOOK, timeout=5)
    entry = next(e for e in _events() if e.get("kind") == "effect")
    ledger_entry = {
        "effect_id": entry["effect_id"],
        "attempt": entry["attempt"],
        "lifecycle": entry["lifecycle"],
        "effect_class": entry["effect_class"],
        "decision": entry["decision"],
        "reason": entry["reason"],
        "host": entry["host"],
        "mode": entry.get("mode", ""),
        "decided_by": entry.get("decided_by", ""),
        "recorded_at": entry["recorded_at"],
    }
    journal.record(ledger_entry, scope_name="relay.test", scope_instance=entry["scope_instance"])
    with pytest.raises(AssertionError):
        kept = _evidence()["kept"]
        assert kept == 3, "a duplicate must never create a second row"


def test_sabotage_invented_turn_identity_is_caught(monkeypatch):
    """Inject: the payload names a turn. The no-turn-identity invariant must
    fire."""
    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()
    real = journal._payload_for

    def invents_turn(entry, **kw):
        payload = real(entry, **kw)
        payload["turn_id"] = "turn-forged"
        return payload

    monkeypatch.setattr(journal, "_payload_for", invents_turn)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
    )
    with named_background_effect_scope("relay.test", durable=True):
        open_remote_url(WEBHOOK, timeout=5)
    with pytest.raises(AssertionError):
        blob = repr(_events())
        assert "turn_id" not in blob, "a background effect owns no turn identity"


def test_sabotage_unbounded_retention_is_caught(monkeypatch):
    """Inject: retention enforcement disabled. The hard-bound invariant must
    fire."""
    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()
    journal.cap = 4
    # the amended journal prunes inside the publish transaction — the no-op
    # accepts the connection argument so only the PRUNING is disabled
    monkeypatch.setattr(journal, "_enforce_retention", lambda *a, **kw: None)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(204)
    )
    with named_background_effect_scope("relay.test", durable=True):
        for index in range(3):
            open_remote_url(f"{WEBHOOK}/{index}", timeout=5)
    with pytest.raises(AssertionError):
        kept = _evidence()["kept"]
        assert kept <= 4, "the retention bound is hard"


def test_sabotage_secret_leakage_is_caught(monkeypatch):
    """Inject: the payload builder bypasses the reason sanitizer. The
    redaction invariant must fire."""
    from core.effect_gateway import _background_effect_journal

    journal = _background_effect_journal()
    monkeypatch.setattr(journal, "_safe_reason", lambda reason: str(reason))
    entry = {
        "effect_id": "eff:test-7777",
        "attempt": 1,
        "lifecycle": "failed",
        "effect_class": "network_fetch",
        "decision": "allowed",
        "reason": f"leak {WEBHOOK} TOKENVALUE7",
        "host": "discord.com",
        "mode": "",
        "decided_by": "test",
        "recorded_at": "2026-08-31T00:00:00+00:00",
    }
    journal.record(entry, scope_name="relay.test", scope_instance="bgi:t")
    with pytest.raises(AssertionError):
        blob = repr(_events(effect_id="eff:test-7777"))
        assert "TOKENVALUE7" not in blob, "the sanitizer is the last line before disk"
