"""R2B2C AMENDMENT — ATOMIC AND HONEST HASH-JOURNAL RETENTION.

The R2b2c claim was incomplete. Four truths it papered over, each proven RED
at `eb9242b6` by the tests named below:

1. NON-ATOMIC COMMIT. `storage.event_log.append_event` commits the visible
   `event_log_v2` row and only THEN calls `append_hashed_event`. A failure
   between the two leaves a visible event with NO hash witness — an account
   that can be read but not attested. (The R2b2c journal rides that seam.)
2. UNBOUNDED WITNESS UNDER A "HARD CAP". Retention deletes `event_log_v2`
   rows but never `event_hash_chain` rows: the visible account claims a
   proven bound while the witness store grows without limit.
3. ORDERING IS NOT CRYPTOGRAPHY. The R2b2c "durable chain" tests assert
   lifecycle ORDERING; none checks event↔witness row parity or verifies the
   hash chain. Ordering surviving tamper proves nothing about attestation.
4. RACING THE SINGLETON. `_background_effect_journal()` lazily constructs
   without a lock: two first-access threads can build two journals, and a
   persistence failure counted on the losing instance is lost accounting.

THE AMENDED CONTRACT, pinned GREEN after the repair:

* every accepted event atomically creates visible row AND witness, or
  neither — one transaction, injected mid-commit failure leaves no orphan;
* identical redelivery is idempotent; a CONFLICTING payload under one
  event_id is a visible refusal (counted, named, nothing written);
* retention is honest: the journal owns a bounded SEGMENTED, CHECKPOINTED
  witness — visible rows and witnesses are pruned in lockstep and a
  checkpoint accumulator (head hash + pruned count) preserves verifiable
  continuity across the pruned boundary. Nothing claims recoverable
  individual history past the cap, and the GLOBAL event hash chain is no
  longer grown by background evidence at all;
* the query exposes visible rows, witness parity and chain verification,
  retained/pruned counts, checkpoint state and persistence failures;
* singleton initialization is locked — one journal, one failure account.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
import urllib.request
from contextvars import copy_context

import pytest

from core.effect_gateway import named_background_effect_scope
from core.remote_fetch_policy import open_remote_url

WEBHOOK = "https://discord.com/api/webhooks/1095/TOKENVALUE7/secrets"
CAP = 6  # two sends' worth of lifecycle detail


# --------------------------------------------------------------------------- harness


@pytest.fixture(autouse=True)
def _clean_background_evidence():
    """Start each test with empty journal tables and a fresh (locked) journal.
    Base-tolerant: at eb9242b6 the amended tables do not exist and the reset
    API has no lock — every test still fails on its OWN line. Every statement
    is guarded individually and the commit ALWAYS runs: a suppressed failure
    must never skip the commit, or the pooled close() rolls the deletes back
    and rows leak into the next test."""
    from contextlib import suppress as contextlib_suppress

    from storage.db import get_connection

    with contextlib_suppress(Exception):
        conn = get_connection()
        try:
            for statement in (
                "DELETE FROM background_effect_journal_v1",
                "DELETE FROM background_effect_witness_v1",
                "DELETE FROM background_effect_checkpoint_v1",
                "DELETE FROM event_log_v2 WHERE category = 'background_effect'",
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
    status = 204


def _send(*, scope: str = "relay.test", url: str = WEBHOOK, retry_of: str = "") -> None:
    open_remote_url(url, timeout=5, retry_of=retry_of) if retry_of else open_remote_url(url, timeout=5)


def _durable_scope(scope: str = "relay.test"):
    return named_background_effect_scope(scope, durable=True)


def _evidence(**filters) -> dict:
    from core.effect_gateway import background_effect_evidence

    return background_effect_evidence(**filters)


def _journal():
    from core.effect_gateway import _background_effect_journal

    return _background_effect_journal()


def _sql_counts() -> dict:
    """Raw store truth, both eras: visible rows, global-chain witnesses for
    background ids, and (post-repair) the journal's own tables."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        def one(query: str, params: tuple = ()) -> int:
            try:
                row = conn.execute(query, params).fetchone()
                return int(row[0]) if row is not None else 0
            except sqlite3.Error:
                return 0

        legacy_visible = one(
            "SELECT COUNT(*) FROM event_log_v2 WHERE category = ?", ("background_effect",)
        )
        global_witness = one(
            "SELECT COUNT(*) FROM event_hash_chain WHERE event_id LIKE 'bge:%'"
        )
        visible = one("SELECT COUNT(*) FROM background_effect_journal_v1")
        witness = one("SELECT COUNT(*) FROM background_effect_witness_v1")
        return {
            "legacy_visible": legacy_visible,
            "global_witness": global_witness,
            "visible": visible,
            "witness": witness,
        }
    finally:
        conn.close()


# ------------------------------- RED 1: a visible event can exist without a witness


def test_mid_commit_failure_leaves_no_visible_row_without_witness(monkeypatch):
    """Contract: row and witness are created together or not at all. Inject a
    failure into the WITNESS-write path — whichever one the current journal
    uses — and require the invariant. At eb9242b6 the visible row commits
    first, so the orphan exists and this is RED."""

    journal = _journal()
    if hasattr(journal, "_append_witness"):
        def _boom(*args, **kwargs):
            raise sqlite3.OperationalError("witness write failed mid-commit")

        monkeypatch.setattr(journal, "_append_witness", _boom)
    else:
        import storage.event_hash_chain as chain

        def _boom(event_id, payload):
            raise sqlite3.OperationalError("witness write failed mid-commit")

        monkeypatch.setattr(chain, "append_hashed_event", _boom)

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    counts = _sql_counts()
    visible = counts["visible"] or counts["legacy_visible"]
    witness = counts["witness"] or counts["global_witness"]
    assert visible == witness, (
        f"an accepted event must carry its witness or not exist: visible={visible}, "
        f"witness={witness} (the orphan IS the unauditable account)"
    )
    from core.effect_gateway import background_effect_persistence_status

    status = background_effect_persistence_status()
    assert status["failures"] >= 1 and status["last_error"], (
        "the refused write is counted and named, never swallowed"
    )


# ------------------- RED 2: the "hard cap" never bounded the witness store at all


def test_retention_bounds_the_witness_in_lockstep_not_the_global_chain(monkeypatch):
    """Contract: past the cap, visible rows AND witnesses are pruned together
    with a checkpoint accumulator; the journal never grows the GLOBAL hash
    chain. At eb9242b6 the global chain holds every background event ever
    written while event_log_v2 sits at its cap — unbounded storage wearing a
    bound's name. RED."""
    journal = _journal()
    journal.cap = CAP
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    for index in range(3):  # 9 lifecycle events against a cap of 6
        with _durable_scope(f"relay.test{index}"):
            _send(url=f"{WEBHOOK}/{index}")

    counts = _sql_counts()
    report = _evidence()
    retained = report["kept"]
    assert retained <= CAP, f"visible account bounded at {CAP}: {retained}"
    assert counts["global_witness"] == 0, (
        f"background evidence must not grow the global hash chain; it holds "
        f"{counts['global_witness']} bge witnesses with no bound of its own"
    )
    witness = report.get("witness") or {}
    assert witness.get("retained_witnesses") == retained, (
        "witnesses are pruned in lockstep with the rows they attest"
    )
    assert witness.get("chain_verified") is True
    checkpoint = report.get("checkpoint") or {}
    assert checkpoint.get("pruned_events") == 9 - retained, (
        "the checkpoint accounts for every pruned event"
    )
    assert report["dropped"] == 9 - retained and report["truncated"] is True


# --------------------- RED 3: the old account never checked parity or the chain


def test_query_reports_witness_parity_and_chain_verification(monkeypatch):
    """Contract: the query exposes cryptographic truth — parity between
    visible rows and witnesses, and recomputation of the hash chain from the
    checkpoint head. At eb9242b6 `background_effect_evidence` returns no
    witness surface at all: ordering was all it could say. RED."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    report = _evidence()
    witness = report.get("witness")
    assert isinstance(witness, dict), "the report must carry a witness account"
    assert witness["parity_ok"] is True
    assert witness["chain_verified"] is True
    assert witness["retained_witnesses"] == report["kept"]
    assert witness.get("algorithm"), "name the witness algorithm honestly"


def test_ordering_survives_a_dead_witness_but_the_witness_does_not(monkeypatch):
    """The R2b2c defect, stated as a test: lifecycle ORDERING is blind to
    attestation — delete every hash witness and the ordered account still
    reads perfectly. Detection must live in the witness surface, and this
    test proves BOTH halves: ordering alone passes (the point), parity does
    not (the repair)."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
        from core.effect_gateway import effect_receipts

        entries = effect_receipts()
        effect_id = str(entries[0]["effect_id"])

    from storage.db import get_connection

    conn = get_connection()
    try:
        with contextlib_suppress_ctx():
            conn.execute("DELETE FROM background_effect_witness_v1")
        with contextlib_suppress_ctx():
            conn.execute("DELETE FROM event_hash_chain WHERE event_id LIKE 'bge:%'")
        conn.commit()
    finally:
        conn.close()

    lifecycles = [
        str(e.get("lifecycle"))
        for e in _evidence(effect_id=effect_id)["events"]
        if e.get("kind") == "effect"
    ]
    assert lifecycles == ["authorized", "started", "succeeded"], (
        "ordering is ordering — it survives a dead witness, which is exactly "
        "why it was never a cryptographic chain"
    )
    report = _evidence()
    witness = report.get("witness") or {}
    assert witness.get("parity_ok") is False, (
        "the witness surface is what detects the attestation loss"
    )


# ---------------------- RED 4: two first-access threads, one journal, one account


def test_singleton_initialization_is_locked_against_concurrent_first_access():
    """Contract: concurrent first access yields ONE shared journal (one
    failure account). The window is made deterministic: the FIRST
    constructor stalls INSIDE `__init__` — the global is provably unassigned
    — while the second thread asks the factory for a journal. At eb9242b6
    the unlocked lazy init hands the second thread its OWN instance; the
    first thread's assignments (and any failure accounting on it) are then
    overwritten. RED."""
    from core import effect_gateway as eg
    from core.effect_gateway import BackgroundEffectJournal

    original_init = BackgroundEffectJournal.__init__
    first_inside = threading.Event()
    release = threading.Event()
    ctor_lock = threading.Lock()
    ctor_count = [0]

    def _stalled_init(self, *args, **kwargs):
        with ctor_lock:
            ctor_count[0] += 1
            nth = ctor_count[0]
        if nth == 1:
            first_inside.set()
            release.wait(timeout=5)  # stall INSIDE __init__: global unassigned
        original_init(self, *args, **kwargs)

    BackgroundEffectJournal.__init__ = _stalled_init
    seen: list[int] = []
    try:
        eg.reset_background_effect_journal()
        first = threading.Thread(target=lambda: seen.append(id(eg._background_effect_journal())))
        first.start()
        assert first_inside.wait(timeout=5), "the first constructor must reach its stall"
        second = threading.Thread(target=lambda: seen.append(id(eg._background_effect_journal())))
        second.start()
        second.join(timeout=5)
        release.set()
        first.join(timeout=5)
    finally:
        BackgroundEffectJournal.__init__ = original_init
        eg.reset_background_effect_journal()

    assert len(seen) == 2 and len(set(seen)) == 1, (
        f"concurrent first access must share ONE journal (one failure "
        f"account); saw {len(set(seen))} distinct instances"
    )


# ---------------------------------------------------------- the amended contract GREEN


def test_every_publish_pairs_its_row_with_a_verifiable_witness(monkeypatch):
    """Parity by construction: after any number of sends, every visible row
    has a witness, the witness set equals the visible set, and each hash
    recomputes from its predecessor."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    for index in range(2):
        with _durable_scope(f"relay.test{index}"):
            _send(url=f"{WEBHOOK}/{index}")
    report = _evidence()
    witness = report["witness"]
    assert witness["parity_ok"] is True
    assert witness["chain_verified"] is True
    assert witness["retained_witnesses"] == report["kept"] == 6
    assert witness["retained_events"] == 6


def test_conflicting_replay_under_one_event_id_fails_visibly(monkeypatch):
    """Same event_id, DIFFERENT payload: refused, counted as a conflict,
    named, and the original record is untouched."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    journal = _journal()
    entry = {
        "effect_id": "eff:forged-0001",
        "attempt": 1,
        "lifecycle": "succeeded",
        "effect_class": "network_fetch",
        "decision": "allowed",
        "reason": "first",
        "host": "discord.com",
        "mode": "",
        "decided_by": "test",
        "recorded_at": "t0",
    }
    assert journal.record(entry, scope_name="relay.test", scope_instance="bgi:a")
    original = _evidence()["events"]  # AFTER the accepted synthetic record
    conflict = dict(entry, reason="changed-after-the-fact")
    assert journal.record(conflict, scope_name="relay.test", scope_instance="bgi:a") is False
    status = journal.persistence_status()
    assert status["conflicts"] >= 1, "a conflicting replay is its own visible class"
    assert "conflict" in str(status["last_error"]).lower()
    assert _evidence()["events"] == original, "the refusal wrote nothing"
    witness = _evidence()["witness"]
    assert witness["parity_ok"] is True and witness["chain_verified"] is True


def test_identical_replay_is_idempotent_row_and_witness(monkeypatch):
    """Same event_id, SAME payload: no second row, no second witness, the
    tail hash does not move."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    journal = _journal()
    before = _evidence()
    payload_events = [e for e in before["events"] if e.get("kind") == "effect"]
    entry = {
        "effect_id": payload_events[0]["effect_id"],
        "attempt": payload_events[0]["attempt"],
        "lifecycle": payload_events[0]["lifecycle"],
        "effect_class": payload_events[0]["effect_class"],
        "decision": payload_events[0]["decision"],
        "reason": payload_events[0]["reason"],
        "host": payload_events[0]["host"],
        "mode": payload_events[0].get("mode", ""),
        "decided_by": payload_events[0].get("decided_by", ""),
        "recorded_at": payload_events[0]["recorded_at"],
    }
    assert journal.record(entry, scope_name="relay.test", scope_instance=payload_events[0]["scope_instance"])
    after = _evidence()
    assert after["kept"] == before["kept"]
    assert after["witness"]["retained_witnesses"] == before["witness"]["retained_witnesses"]
    assert after["witness"]["tail_hash"] == before["witness"]["tail_hash"]


def test_restart_reconstructs_rows_witness_and_checkpoint(monkeypatch):
    """A fresh process (reset singleton, no memory) reads back the same rows,
    the same verified witness state and the same checkpoint accumulator."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    before = _evidence()
    from core import effect_gateway as eg

    eg.reset_background_effect_journal()
    after = _evidence()
    assert after["events"] == before["events"]
    assert after["witness"] == before["witness"]
    assert after["checkpoint"] == before["checkpoint"]
    assert after["witness"]["chain_verified"] is True


def test_checkpoint_accumulator_verifies_across_the_pruned_boundary(monkeypatch):
    """The segmented journal's point: prune history, and the RETAINED chain
    still verifies FROM the checkpoint hash; the oldest retained witness
    links to the accumulator, not to a row that no longer exists."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    journal = _journal()
    journal.cap = CAP
    for index in range(3):  # 9 events, cap 6 → 3 pruned
        with _durable_scope(f"relay.test{index}"):
            _send(url=f"{WEBHOOK}/{index}")
    report = _evidence()
    checkpoint = report["checkpoint"]
    assert checkpoint["pruned_events"] == 3
    assert checkpoint["head_hash"], "the accumulator carries the pruned tail"
    witness = report["witness"]
    assert witness["chain_verified"] is True, (
        "verification must hold across the boundary: retained witnesses "
        "recompute from the checkpoint hash, not from pruned rows"
    )
    from storage.db import get_connection

    conn = get_connection()
    try:
        oldest = conn.execute(
            "SELECT prev_hash FROM background_effect_witness_v1 ORDER BY seq ASC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert oldest and oldest[0] == checkpoint["head_hash"], (
        "the oldest retained witness links to the checkpoint accumulator"
    )


def test_concurrent_publication_loses_no_rows_or_witnesses(monkeypatch):
    """Two daemons publish simultaneously; every transition lands, parity
    holds, the chain verifies, and the failure account is shared."""
    barrier = threading.Barrier(2, timeout=10)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None, **kw: (barrier.wait(), _Resp())[1],
    )
    errors: list[BaseException] = []

    def _daemon(index: int) -> None:
        try:
            with _durable_scope(f"relay.test{index}"):
                for step in range(3):
                    _send(url=f"{WEBHOOK}/{index}/{step}")
        except BaseException as exc:  # pragma: no cover - surfaced via list
            errors.append(exc)

    ctx_a, ctx_b = copy_context(), copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(ctx_a.run, _daemon, 0)
        pool.submit(ctx_b.run, _daemon, 1)
    assert not errors, errors
    report = _evidence()
    assert report["kept"] == 18, f"6 sends × 3 transitions, saw {report['kept']}"
    assert report["witness"]["parity_ok"] is True
    assert report["witness"]["chain_verified"] is True
    assert report["persistence_failures"] == 0


def test_tampering_is_detected_by_the_witness_surface(monkeypatch):
    """Flip a stored payload (visible and witness disagree) and separately
    delete a witness: both are detected — parity and chain verification are
    real checks, not decorations."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE background_effect_journal_v1 SET payload_json = ? "
            "WHERE seq = (SELECT MIN(seq) FROM background_effect_journal_v1)",
            (json.dumps({"kind": "effect", "forged": True}, sort_keys=True),),
        )
        conn.commit()
    finally:
        conn.close()
    first = _evidence()
    assert first["witness"]["parity_ok"] is False, "row/witness payload divergence detected"

    conn = get_connection()
    try:
        conn.execute("DELETE FROM background_effect_witness_v1 WHERE seq = 1")
        conn.commit()
    finally:
        conn.close()
    second = _evidence()
    assert second["witness"]["parity_ok"] is False


def test_witness_payloads_redact_secrets_too(monkeypatch):
    """Redaction holds in EVERY durable store the journal touches — its own
    tables and the legacy ones — no token material anywhere."""
    leaky = urllib.error.URLError(f"POST {WEBHOOK} failed (Bearer TOKENVALUE7)")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None, **kw: (_ for _ in ()).throw(leaky),
    )
    with _durable_scope():
        with pytest.raises(urllib.error.URLError):
            _send()
    from storage.db import get_connection

    conn = get_connection()
    try:
        blob = ""
        for table, clause in (
            ("background_effect_journal_v1", ""),
            ("background_effect_witness_v1", ""),
            ("event_log_v2", "WHERE category = 'background_effect'"),
            ("event_hash_chain", "WHERE event_id LIKE 'bge:%'"),
        ):
            with contextlib_suppress_ctx():
                rows = conn.execute(f"SELECT payload_json FROM {table} {clause}").fetchall()
                blob += repr(rows)
    finally:
        conn.close()
    for forbidden in ("TOKENVALUE7", "webhooks", "Bearer"):
        assert forbidden not in blob, f"secret material {forbidden!r} reached a durable store"


def test_lifecycle_outcomes_remain_durable_and_linked(monkeypatch):
    """The R2b2c regression under the amended journal: full chain, one effect
    id, ordering AND parity together."""
    calls: list[int] = []

    def _flaky(request, timeout=None, **kw):
        calls.append(len(calls))
        if len(calls) == 1:
            raise ConnectionRefusedError("first refused")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    with _durable_scope():
        with pytest.raises(ConnectionRefusedError):
            _send()
        from core.effect_gateway import effect_receipts

        first = str(effect_receipts()[0]["effect_id"])
        _send(retry_of=first)
    report = _evidence(effect_id=first)
    lifecycles = [
        str(e["lifecycle"]) for e in report["events"] if e.get("kind") == "effect"
    ]
    assert lifecycles == ["authorized", "started", "failed", "started", "succeeded"]
    assert report["witness"]["parity_ok"] is True
    assert report["witness"]["chain_verified"] is True


# --------------------------------------------------------------------------- sabotage
#
# Non-vacuous by construction: each injects a defect into the AMENDED
# mechanics and the named invariant must fire.


def test_sabotage_witnessless_publication_is_caught(monkeypatch):
    """Inject: the journal writes visible rows but never witnesses. The
    parity invariant must fire."""
    journal = _journal()
    monkeypatch.setattr(journal, "_append_witness", lambda *a, **kw: None)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    with _durable_scope():
        _send()
    with pytest.raises(AssertionError):
        report = _evidence()
        assert report["witness"]["parity_ok"] is True, "every row needs its witness"
        assert report["witness"]["retained_witnesses"] == report["kept"]


def test_sabotage_frozen_checkpoint_is_caught(monkeypatch):
    """Inject: retention prunes rows but the checkpoint accumulator never
    advances — verification across the boundary must break (the oldest
    retained witness would link to a hash nobody attests)."""
    journal = _journal()
    journal.cap = CAP

    def frozen(*args, **kwargs):
        return None

    monkeypatch.setattr(journal, "_advance_checkpoint", frozen)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp()
    )
    for index in range(3):
        with _durable_scope(f"relay.test{index}"):
            _send(url=f"{WEBHOOK}/{index}")
    with pytest.raises(AssertionError):
        report = _evidence()
        checkpoint = report["checkpoint"]
        assert checkpoint["pruned_events"] == 3, "the account must count its pruning"
        assert report["witness"]["chain_verified"] is True, (
            "a frozen accumulator orphans the retained chain"
        )


def contextlib_suppress_ctx():  # small helper used above
    from contextlib import suppress

    return suppress(Exception)
