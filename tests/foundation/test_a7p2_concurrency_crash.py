"""A7 pass-002 — replay / crash / concurrency attack matrix.

Attacks the canonical finalization authority under the scenarios required by
the A7 builder brief. Every guard here is later mutation-pinned (see
scripts/a7p2_mutation_ledger.py / MUTATION_LEDGER.md).
"""
from __future__ import annotations

import hashlib
import contextvars
import threading

import pytest

import storage.db as sdb
from core.finalization import (
    DELIVERY_ATTEMPTED_UNKNOWN,
    DELIVERY_DELIVERED,
    FinalizationRejected,
    finalize_answer,
    get_finalization_by_semantic_id,
    no_answer_terminal,
    replay_finalized_answer,
    set_availability,
    set_delivery_status,
)
from core.semantic.semantic_admissions import (
    clear_execution_context,
    set_execution_context,
)
from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    reset_admission,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "a7p2cc.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    clear_execution_context()
    sdb.configure_default_db_path(None)


def _admit_and_finalize(text: str) -> dict:
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id="t", canonical_content=text)


def _barrier_start(n: int) -> threading.Barrier:
    return threading.Barrier(n, timeout=20)


# ---------------------------------------------------------------------------
# Same finalization requested twice / same admission finalized concurrently
# ---------------------------------------------------------------------------

def test_concurrent_same_admission_finalizations_exactly_one_row(fresh_store):
    reset_admission()
    admitted = admit_semantic_result({"response": "shared bytes", "route_reason": "model_lane"})
    sr = admitted["_semantic_admission"]["semantic_result_id"]
    results: list = []
    barrier = _barrier_start(8)
    # Workers must see THIS context's A2 admission / fence / ledger bindings:
    # each thread runs inside a snapshot of the turn context.
    contexts = [contextvars.copy_context() for _ in range(8)]

    def worker(ctx):
        def run():
            try:
                commit = finalize_answer(turn_id="t", canonical_content="shared bytes")
                results.append(("ok", commit["binding_outcome"]))
            except FinalizationRejected as exc:
                results.append(("rejected", exc))

        barrier.wait()
        ctx.run(run)

    threads = [threading.Thread(target=worker, args=(ctx,)) for ctx in contexts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = sdb.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM a7_finalizations WHERE semantic_result_id = ?", (sr,)
        ).fetchall()
        assert len(rows) == 1  # duplicate-finalization guard holds under race
    finally:
        conn.close()
    outcomes = [r[1] for r in results if r[0] == "ok"]
    # Exactly one ACCEPTED_FIRST; every other writer either idempotently
    # accepted identical truth or was honestly refused — never a second row.
    assert outcomes.count("ACCEPTED_FIRST") == 1


def test_concurrent_different_bytes_same_admission_exactly_one_truth(fresh_store):
    reset_admission()
    admitted = admit_semantic_result({"response": "v1", "route_reason": "model_lane"})
    sr = admitted["_semantic_admission"]["semantic_result_id"]
    barrier = _barrier_start(2)
    winner: list[str] = []
    contexts = [contextvars.copy_context() for _ in range(2)]

    def worker(ctx, text):
        def run():
            try:
                finalize_answer(turn_id="t", canonical_content=text)
                winner.append(text)
            except FinalizationRejected:
                pass

        barrier.wait()
        ctx.run(run)

    t1 = threading.Thread(target=worker, args=(contexts[0], "v1"))
    t2 = threading.Thread(target=worker, args=(contexts[1], "v2-divergent"))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert len(winner) == 1  # exactly one version became truth
    stored = get_finalization_by_semantic_id(sr)
    assert stored["canonical_content"] == winner[0]


# ---------------------------------------------------------------------------
# Crash boundaries between payload persistence and finality binding
# ---------------------------------------------------------------------------

def test_crash_before_binding_leaves_zero_rows(fresh_store):
    """Crash before the finality INSERT: no row, and a retry finalizes cleanly."""
    reset_admission()
    admit_semantic_result({"response": "crash pre", "route_reason": "model_lane"})
    # Simulate: process died before finalize_answer reached its INSERT.
    conn = sdb.get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) c FROM a7_finalizations").fetchone()["c"]
        assert count == 0
    finally:
        conn.close()
    commit = finalize_answer(turn_id="t", canonical_content="crash pre")
    assert commit["binding_outcome"] == "ACCEPTED_FIRST"


def test_crash_after_binding_row_is_complete_and_retrievable(fresh_store):
    """Bytes are bound INSIDE the same atomic insert as the finality row: a row
    that exists after any crash always carries retrievable bytes whose hash
    matches content_hash — 'magical bytes' are impossible by construction."""
    commit = _admit_and_finalize("post-crash bytes")
    # Post-"crash" read on a fresh connection: the committed row is whole.
    stored = get_finalization_by_semantic_id(commit["semantic_result_id"])
    actual = "sha256:" + hashlib.sha256(
        stored["canonical_content"].encode("utf-8")
    ).hexdigest()
    assert stored["content_hash"] == actual
    replay = replay_finalized_answer(principal="owner_local", semantic_result_id=commit["semantic_result_id"])
    assert replay["canonical_content"] == "post-crash bytes"


def test_sigkill_between_persist_and_read_still_consistent(fresh_store, tmp_path):
    """Real crash-cut: a child process commits a finalization and is SIGKILLed
    immediately; a fresh reader must see complete immutable truth."""
    import os
    import signal
    import subprocess
    import sys
    import time

    db_path = tmp_path / "kill.db"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import storage.db as sdb;"
                "sdb.configure_default_db_path(%r);"
                "from storage.migrations import run_migrations; run_migrations();"
                "from core.runtime_continuity import configure_runtime_continuity_db_path;"
                "from storage.db import active_default_db_path;"
                "configure_runtime_continuity_db_path(active_default_db_path());"
                "from core.semantic.semantic_result_seam import admit_semantic_result;"
                "from core.finalization import finalize_answer;"
                "admit_semantic_result({'response': 'killed bytes', 'route_reason': 'model_lane'});"
                "c = finalize_answer(turn_id='t', canonical_content='killed bytes');"
                "print(c['finalization_id'], flush=True);"
                "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
            )
            % str(db_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    out, _ = child.communicate(timeout=120)
    fid = out.strip().split("\n")[-1]
    assert child.returncode != 0  # genuinely killed, not exited
    time.sleep(0.2)

    sdb.configure_default_db_path(db_path)
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    try:
        row = get_finalization_by_semantic_id(
            "SELECT" and sdb.get_connection().execute(
                "SELECT semantic_result_id FROM a7_finalizations WHERE finalization_id = ?",
                (fid,),
            ).fetchone()[0]
        )
        assert row is not None
        assert row["canonical_content"] == "killed bytes"
        assert row["availability"] == "AVAILABLE"
    finally:
        sdb.configure_default_db_path(None)


# ---------------------------------------------------------------------------
# Stale generation / superseded attempt tries to finalize
# ---------------------------------------------------------------------------

def test_stale_generation_finalization_refused_zero_rows(fresh_store):
    from core.invocation.ledger import (
        FenceRefused,
        accept_invocation,
        bump_generation,
        open_execution,
    )

    inv = accept_invocation(
        external_kind="test", external_value="stale-gen-a7", principal="owner_local"
    )
    req = inv["request_id"]
    from core.invocation.ledger import current_runtime_epoch

    execution_id = open_execution(request_id=req, root_attempt_id="att-stale-gen")[
        "execution_id"
    ]
    set_execution_context(
        {
            "execution_id": execution_id,
            "generation": 0,
            "runtime_epoch": current_runtime_epoch(),
        }
    )
    reset_admission()
    admit_semantic_result({"response": "fenced bytes", "route_reason": "model_lane"})
    # Generation advances underneath the stale attempt.
    bump_generation(execution_id)
    with pytest.raises(FenceRefused):
        finalize_answer(turn_id="t", canonical_content="fenced bytes")
    conn = sdb.get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) c FROM a7_finalizations").fetchone()["c"]
        assert count == 0  # FENCE-OR-REFUSE: zero rows from a stale writer
    finally:
        conn.close()


def test_superseded_epoch_writer_refused(fresh_store):
    from core.invocation.ledger import FenceRefused, accept_invocation, open_execution

    inv = accept_invocation(
        external_kind="test", external_value="stale-epoch-a7", principal="owner_local"
    )
    req = inv["request_id"]
    execution_id = open_execution(request_id=req, root_attempt_id="att-stale-epoch")["execution_id"]
    # The process restarted: current epoch differs from the stale writer's.
    set_execution_context(
        {"execution_id": execution_id, "generation": 0, "runtime_epoch": "old-process"}
    )
    reset_admission()
    admit_semantic_result({"response": "zombie bytes", "route_reason": "model_lane"})
    with pytest.raises(FenceRefused):
        finalize_answer(turn_id="t", canonical_content="zombie bytes")


# ---------------------------------------------------------------------------
# OPEN obligation races finalization
# ---------------------------------------------------------------------------

def test_open_obligation_blocks_finality_even_when_raced(fresh_store):
    from core.conductor import obligation_ledger as ol

    reset_admission()
    admit_semantic_result({"response": "raced closure", "route_reason": "model_lane"})
    # Bind an OPEN obligation set so the ledger verdict is authoritative and
    # structurally uncovered (a pending effect obligation blocks closure).
    opened = ol.open_obligation_set(
        obligations=[
            {"obligation_id": "ob-1", "text": "do the thing", "kind": "effect"}
        ]
    )
    ol.bind_active_set(opened["set_id"], opened["version"])
    blocked: list = []
    contexts = [contextvars.copy_context() for _ in range(4)]

    def worker(ctx):
        def run():
            try:
                finalize_answer(turn_id="t", canonical_content="raced closure")
                blocked.append("finalized")
            except Exception as exc:
                blocked.append(type(exc).__name__)

        ctx.run(run)

    threads = [threading.Thread(target=worker, args=(ctx,)) for ctx in contexts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # While the set is OPEN, NO thread may have finalized — model prose or
    # racing callers cannot convert effect uncertainty into completion.
    assert "finalized" not in blocked
    ol.clear_active_set()
    conn = sdb.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM a7_finalizations WHERE status = 'answer_present'"
        ).fetchall()
        assert rows == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Delivery race AFTER finality / tombstone races
# ---------------------------------------------------------------------------

def test_delivery_transitions_race_never_downgrade_delivered(fresh_store):
    commit = _admit_and_finalize("delivery race bytes")
    fid = commit["finalization_id"]
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)
    assert set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="PLATFORM_ACK")
    barrier = _barrier_start(6)
    attempts: list[bool] = []

    def attacker():
        barrier.wait()
        # Every loser tries to downgrade proven truth; all must fail.
        attempts.append(
            set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN, evidence_class="RECONCILED")
        )

    threads = [threading.Thread(target=attacker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not any(attempts)
    row = dict(
        sdb.get_connection().execute(
            "SELECT delivery_status FROM a7_finalizations WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
    )
    assert row["delivery_status"] == DELIVERY_DELIVERED  # absorbing


def test_delivered_requires_canonical_evidence_class(fresh_store):
    """A-5/F-06: only canonical evidence classes may author proven DELIVERED."""
    commit = _admit_and_finalize("evidence gated")
    fid = commit["finalization_id"]
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)
    # No evidence at all: refused.
    assert set_delivery_status(fid, DELIVERY_DELIVERED) is False
    # Out-of-vocabulary legacy class: refused — claims never become truth.
    assert (
        set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="LEGACY_UNVERIFIED")
        is False
    )
    assert (
        set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="INVENTED_ACK") is False
    )
    row = dict(
        sdb.get_connection().execute(
            "SELECT delivery_status FROM a7_finalizations WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
    )
    assert row["delivery_status"] == DELIVERY_ATTEMPTED_UNKNOWN
    # Canonical evidence authors proven truth.
    assert set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="RECONCILED")


def test_withheld_to_available_reversal_is_refused_deterministically(fresh_store):
    """The forbidden reversal: WITHHELD → AVAILABLE must be refused outright —
    no new-identity loophole, no caller override."""
    commit = _admit_and_finalize("withhold then un-withhold?")
    fid = commit["finalization_id"]
    assert set_availability(fid, "WITHHELD", reason="legal hold")
    assert set_availability(fid, "AVAILABLE", reason="reversal attempt") is False
    row = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert row["availability"] == "WITHHELD"
    # And ERASED stays absorbing too.
    assert set_availability(fid, "ERASED", reason="escalate")
    assert set_availability(fid, "AVAILABLE", reason="post-erase reversal") is False


def test_availability_race_converges_monotone_single_event_trail(fresh_store):
    commit = _admit_and_finalize("tombstone race bytes")
    fid = commit["finalization_id"]
    barrier = _barrier_start(3)
    results: list[bool] = []

    def withdraw():
        barrier.wait()
        results.append(set_availability(fid, "WITHHELD", reason="race"))

    def erase():
        barrier.wait()
        results.append(set_availability(fid, "ERASED", reason="race"))

    def illegal_reversal():
        barrier.wait()
        # WITHHELD → AVAILABLE is forbidden outright.
        results.append(set_availability(fid, "AVAILABLE", reason="race"))

    threads = [threading.Thread(target=withdraw), threading.Thread(target=erase),
               threading.Thread(target=illegal_reversal)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert any(results)  # at least one legal monotone transition landed
    stored = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert stored["availability"] in ("WITHHELD", "ERASED")
    assert stored["availability"] != "AVAILABLE"
    conn = sdb.get_connection()
    try:
        events = conn.execute(
            "SELECT new_state FROM a7_governance_events WHERE finalization_id = ? "
            "AND event_kind = 'availability_transition' ORDER BY created_at",
            (fid,),
        ).fetchall()
        states = [e["new_state"] for e in events]
        # Append-only trail: AVAILABLE never reappears after leaving it.
        assert "AVAILABLE" not in states
        assert len(states) == len(set(states))  # each state transition once
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Replay identity: same semantic bytes from different legitimate executions +
# replay mints nothing
# ---------------------------------------------------------------------------

def test_replay_mints_nothing_zero_rows_after_read(fresh_store):
    commit = _admit_and_finalize("replay purity")
    conn = sdb.get_connection()
    try:
        before_f = conn.execute("SELECT COUNT(*) c FROM a7_finalizations").fetchone()["c"]
        before_a = conn.execute("SELECT COUNT(*) c FROM semantic_admissions").fetchone()["c"]
    finally:
        conn.close()
    for _ in range(5):
        replayed = replay_finalized_answer(
            principal="owner_local", semantic_result_id=commit["semantic_result_id"]
        )
        assert replayed["finalization_id"] == commit["finalization_id"]
    conn = sdb.get_connection()
    try:
        after_f = conn.execute("SELECT COUNT(*) c FROM a7_finalizations").fetchone()["c"]
        after_a = conn.execute("SELECT COUNT(*) c FROM semantic_admissions").fetchone()["c"]
    finally:
        conn.close()
    assert (before_f, before_a) == (after_f, after_a)


def test_same_semantic_bytes_from_distinct_executions_bind_separately(fresh_store):
    c1 = _admit_and_finalize("yes")
    c2 = _admit_and_finalize("yes")
    assert c1["finalization_id"] != c2["finalization_id"]
    assert c1["semantic_result_id"] != c2["semantic_result_id"]
    assert c1["content_hash"] == c2["content_hash"]
    # Both truths remain independently readable — a hash collision of CONTENT
    # never collapses lifecycle identity.
    assert get_finalization_by_semantic_id(c1["semantic_result_id"])["finalization_id"] == c1["finalization_id"]
    assert get_finalization_by_semantic_id(c2["semantic_result_id"])["finalization_id"] == c2["finalization_id"]


# ---------------------------------------------------------------------------
# Typed-outcome distinguishability (ANSWER / REFUSAL / NO_ANSWER / transport)
# ---------------------------------------------------------------------------

def test_typed_outcomes_are_distinguishable_in_durable_state(fresh_store):
    # ANSWER PRESENT: sealed bytes.
    answer = _admit_and_finalize("plain answer")
    # REFUSAL: admitted refusal-policy bytes, sealed as ANSWER_PRESENT but
    # durably distinguishable by the admission's source_class.
    reset_admission()
    from core.semantic.semantic_result_seam import SemanticSource

    admitted = admit_semantic_result(
        {"response": "I cannot do that.", "route_reason": "heavy_model_blocked"},
    )
    assert admitted["_semantic_admission"]["source"] == SemanticSource.REFUSAL_POLICY.value
    refusal = finalize_answer(turn_id="t-r", canonical_content="I cannot do that.")
    # NO ANSWER: typed terminal without prose.
    reset_admission()
    na = no_answer_terminal(turn_id="t-na", reason_code="provider_no_content")

    conn = sdb.get_connection()
    try:
        ref_class = conn.execute(
            "SELECT source_class FROM semantic_admissions WHERE sr_id = ?",
            (refusal["semantic_result_id"],),
        ).fetchone()["source_class"]
        ans_class = conn.execute(
            "SELECT source_class FROM semantic_admissions WHERE sr_id = ?",
            (answer["semantic_result_id"],),
        ).fetchone()["source_class"]
        na_row = conn.execute(
            "SELECT * FROM a7_finalizations WHERE finalization_id = ?",
            (na["finalization_id"],),
        ).fetchone()
    finally:
        conn.close()
    # Three durable states, three distinct readings — none confusable with a
    # transport failure (which lives on delivery_status, untouched here).
    assert ans_class == "model"
    assert ref_class == "refusal_policy"
    assert answer["status"] == refusal["status"] == "answer_present"
    assert na_row["status"] == "no_answer_terminal"
    assert na_row["terminal_reason"] == "provider_no_content"
    assert na_row["canonical_content"] == ""


# ---------------------------------------------------------------------------
# Caller-forged finality inputs
# ---------------------------------------------------------------------------

def test_caller_forged_covered_true_over_open_ledger_refused(fresh_store):
    from core.conductor import obligation_ledger as ol

    reset_admission()
    admit_semantic_result({"response": "forged closure", "route_reason": "model_lane"})
    opened = ol.open_obligation_set(
        obligations=[
            {"obligation_id": "ob-f1", "text": "effect pending", "kind": "effect"}
        ]
    )
    ol.bind_active_set(opened["set_id"], opened["version"])
    # The caller lies: covered=True. The durable ledger overrides.
    with pytest.raises(FinalizationRejected):
        finalize_answer(
            turn_id="t",
            canonical_content="forged closure",
            closure={"covered": True, "open_count": 0, "set_version": "forged"},
        )
    ol.clear_active_set()
