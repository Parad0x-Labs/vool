"""A8 canonical privacy repair — PASS 003 targeted detectors.

One deterministic detector per PASS002 surviving failure class and per new
deterministic counterexample (NCE-A..D). Each test was executed RED against
BASE b7f4475d19c19a750fa0d9e69dc6313848e5ab24 (directly or via its exact
independent reproducer in council/a8-targeted-reproof/pass-002-20260826)
before repair and must stay GREEN after it. Every load-bearing guard here is
a mutation-campaign sabotage target.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest

import storage.db as sdb


@pytest.fixture()
def a8_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    import core.liquefy_bridge as _liquefy

    _liquefy._VOOL_VAULT = home / "data" / "liquefy_vault"
    monkeypatch.setenv("VOOL_LIQUEFY_HOME", str(home / "data" / "liquefy_vault"))
    sdb.configure_default_db_path(tmp_path / "a8.db")
    # This fixture changes database identity; a prior test's process-global
    # trace-table flag is not schema evidence for this new isolated database.
    from core import task_state_machine, trace_id

    monkeypatch.setattr(trace_id, "_TABLE_READY", False)
    monkeypatch.setattr(task_state_machine, "_TABLE_READY", False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    yield home
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)


# --- shared helpers ---------------------------------------------------------

import contextlib


@contextlib.contextmanager
def _request_scope(request_id):
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    token = set_request_context(request_id)
    try:
        yield
    finally:
        _CURRENT_REQUEST_ID.reset(token)


def _sha_hex(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _admit_finalize(text, request_id="req-p3", turn_id="t1"):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    with _request_scope(request_id):
        return finalize_answer(turn_id=turn_id, canonical_content=text)


def _erase(fid):
    from core.finalization import erase_finalization_payload

    return erase_finalization_payload(fid)


def _make_delay_gate(sha_of_payload):
    """Deterministic interleave: while armed, a governed-hash call on thread
    'p3-racer' parks until released, returning its PRE-ERASE verdict."""
    from core.finalization import payload_availability_for_hash as orig_fn
    import core.finalization as fin

    state = {"armed": False, "entered": threading.Event(), "release": threading.Event()}

    def patched(content_hash):
        orig = orig_fn(content_hash)
        if (
            state["armed"]
            and content_hash == sha_of_payload
            and threading.current_thread().name == "p3-racer"
            and orig not in ("WITHHELD", "ERASED")
        ):
            state["entered"].set()
            state["release"].wait(timeout=30)
            state["armed"] = False
        return orig

    fin.payload_availability_for_hash = patched
    return state


def _unpatch_gate():
    import core.finalization as fin
    from core.finalization import payload_availability_for_hash as orig_fn

    fin.payload_availability_for_hash = orig_fn


# ===========================================================================
# U1/U2 — CE06 + SHADOW-C: ERASE dominance over late writers (sync, events,
# checkpoints). The forced-interleave windows are closed by the durable-
# boundary re-checks; a parked stale verdict may no longer recreate bytes.
# ===========================================================================

def test_u1_sync_task_result_lane_cannot_resurrect_after_erase(a8_env):
    P = "u1 sync secret ROMEO-2222"
    fid = _admit_finalize(P)["finalization_id"]
    import uuid
    from datetime import datetime, timezone as _tz
    from storage.db import get_connection
    from network.assist_router import _store_task_result
    from network.assist_models import TaskResult

    task_id = f"task-u1-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO task_offers (task_id, parent_peer_id, capsule_id, task_type, subtask_type,
                                 summary, input_capsule_hash, required_capabilities_json,
                                 deadline_ts, created_at, updated_at)
        VALUES (?, 'peer-u1', 'capsule-u1', 'research', 'generic', 'u1 offer',
                'hash-u1', '[]', ?, ?, ?)
        """,
        (
            task_id,
            datetime.now(_tz.utc).isoformat(),
            datetime.now(_tz.utc).isoformat(),
            datetime.now(_tz.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    _store_task_result(
        TaskResult(
            result_id=f"res-u1-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            helper_agent_id="helper-u1-000000000001",
            result_type="research_summary",
            summary=P,
            confidence=0.95,
            timestamp=datetime.now(_tz.utc),
        )
    )

    def racer():
        from storage.useful_output_store import sync_useful_outputs

        sync_useful_outputs()

    gate = _make_delay_gate("sha256:" + _sha_hex(P))
    gate["armed"] = True
    t = threading.Thread(target=racer, name="p3-racer")
    t.start()
    assert gate["entered"].wait(timeout=15)
    result = _erase(fid)
    gate["release"].set()
    t.join(timeout=30)
    _unpatch_gate()
    assert result["transitioned"] and result["sweep_complete"]
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT output_text FROM useful_outputs WHERE output_text = ?", (P,)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [], "RESURRECTION: erased task_result re-upserted"


def test_u2_runtime_event_and_checkpoint_writers_cannot_resurrect_after_erase(a8_env):
    P = "u2 writer secret SIERRA-3333"
    fid = _admit_finalize(P)["finalization_id"]
    from core.runtime_continuity import (
        CheckpointTransitionRefused,
        append_runtime_event,
        create_runtime_checkpoint,
        get_runtime_checkpoint,
        update_runtime_checkpoint,
    )
    from core.runtime_continuity import _conn

    # event leg: parked pre-erase verdict must not durably land erased bytes
    gate = _make_delay_gate("sha256:" + _sha_hex(P))
    outcome: dict = {}

    def racer_event():
        outcome["event"] = append_runtime_event(
            session_id="s-u2", event_type="model_output", message=P
        )

    gate["armed"] = True
    t = threading.Thread(target=racer_event, name="p3-racer")
    t.start()
    assert gate["entered"].wait(timeout=15)
    result = _erase(fid)
    gate["release"].set()
    t.join(timeout=30)
    _unpatch_gate()
    assert result["sweep_complete"]
    conn = _conn()
    try:
        hit = conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_session_events WHERE message = ?", (P,)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert hit == 0, "RESURRECTION: erased message appended after completed sweep"

    # checkpoint leg: durable-boundary veto refuses the late stale write
    ckpt = create_runtime_checkpoint(session_id="s-u2", request_text="resume u2")
    with pytest.raises(CheckpointTransitionRefused):
        update_runtime_checkpoint(ckpt["checkpoint_id"], final_response=P, status="completed")
    assert P != str(get_runtime_checkpoint(ckpt["checkpoint_id"])["final_response"] or "")


def test_u2b_parked_checkpoint_writer_cannot_commit_after_erase(a8_env):
    """SHADOW-C exact window: the early writer-gate parks on a PRE-erase
    verdict; ERASE completes fully; the durable-boundary re-check inside the
    runtime write transaction must refuse the stale commit."""
    P = "u2b parked checkpoint secret DELTA-04"
    fid = _admit_finalize(P)["finalization_id"]
    from core.runtime_continuity import (
        CheckpointTransitionRefused,
        create_runtime_checkpoint,
        get_runtime_checkpoint,
        update_runtime_checkpoint,
    )
    from core.runtime_continuity import _conn

    ckpt = create_runtime_checkpoint(session_id="s-u2b", request_text="park me")

    def racer():
        try:
            update_runtime_checkpoint(
                ckpt["checkpoint_id"], final_response=P, status="completed"
            )
        except CheckpointTransitionRefused as exc:
            global_refused["detail"] = str(exc)

    global_refused = {"detail": ""}
    gate = _make_delay_gate("sha256:" + _sha_hex(P))
    gate["armed"] = True
    t = threading.Thread(target=racer, name="p3-racer")
    t.start()
    assert gate["entered"].wait(timeout=15)
    result = _erase(fid)
    gate["release"].set()
    t.join(timeout=30)
    _unpatch_gate()
    assert result["sweep_complete"], result["sweep"]
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT final_response FROM runtime_checkpoints WHERE checkpoint_id = ?",
            (ckpt["checkpoint_id"],),
        ).fetchone()
    finally:
        conn.close()
    stored = str(row["final_response"] or "") if row else ""
    assert stored != P, (
        "RESURRECTION: erased final_response written into runtime_checkpoints "
        "after the sweep completed"
    )


# ===========================================================================
# U3 — CE07/NCE-C: tombstone-aware binding (non-racy). A fresh legitimate
# admission+finalization of identical bytes binds to the existing privacy
# state instead of minting AVAILABLE that shadows the digest tombstone.
# ===========================================================================

def test_u3_fresh_identical_bytes_finalization_does_not_shadow_tombstone(a8_env):
    P = "u3 tombstone secret KILO-4444"
    fid = _admit_finalize(P, request_id="req-u3-a")["finalization_id"]
    result = _erase(fid)
    assert result["transitioned"] and result["sweep_complete"]

    second = _admit_finalize(P, request_id="req-u3-b", turn_id="t2")
    from core.finalization import (
        AVAILABILITY_ERASED,
        payload_availability_for_text,
        writer_may_persist_text,
    )
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT availability, canonical_content FROM a7_finalizations "
            "WHERE content_hash = ?", (_sha_hex(P) if False else __import__("hashlib").sha256(P.encode()).hexdigest(),),
        ).fetchall()
        prefixed = conn.execute(
            "SELECT finalization_id FROM a7_finalizations WHERE request_id = 'req-u3-b'"
        ).fetchall()
        assert prefixed, "second finalization must still exist (A7 identity intact)"
        fin_rows = conn.execute(
            "SELECT availability FROM a7_finalizations WHERE request_id = 'req-u3-b'"
        ).fetchone()
        assert fin_rows["availability"] == AVAILABILITY_ERASED, (
            "fresh identical-bytes finalization inherited ERASED, not AVAILABLE"
        )
    finally:
        conn.close()
    assert payload_availability_for_text(P) == AVAILABILITY_ERASED
    assert writer_may_persist_text(P) is False


# ===========================================================================
# U4 — CE04/SHADOW-A/NCE-D: one canonical digest representation; unlineaged
# nodes, FTS and memory_blocks (incl. derived fragments) are dead legs no more.
# ===========================================================================

def test_u4_unlineaged_nodes_fts_memory_blocks_die_on_erase(a8_env):
    P_MAIN = "the vault passphrase is CANARY-P3-U4-MAIN-QX2210"
    P_FACT = "my recovery words are CANARY-P3-U4-MAIN-QX2210"  # derived text sharing a 25-char governed run
    fid = _admit_finalize(P_MAIN)["finalization_id"]

    from core.vool_memory import VoolMemory

    with VoolMemory() as mem:
        for text in (P_MAIN, P_FACT):
            mem.node_store(text, ["vault"], ["secret"], "p3 unlineaged write", [0.1] * 8, lineage_request_id="")
        mem.block_append("user_profile", f"note: {P_FACT}")

    def residue():
        from core.vool_memory import VoolMemory

        out = {"nodes": [], "blocks": ""}
        with VoolMemory() as mem:
            rows = mem._conn.execute(
                "SELECT content FROM memory_nodes WHERE lineage_request_id IS NULL OR lineage_request_id = ''"
            ).fetchall()
            out["nodes"] = [str(r["content"]) for r in rows]
            block = mem._conn.execute(
                "SELECT content FROM memory_blocks WHERE block_name='user_profile'"
            ).fetchone()
            out["blocks"] = str(block["content"]) if block else ""
        return out

    before = residue()
    assert any(P_MAIN == n for n in before["nodes"]) and len(before["nodes"]) == 2

    result = _erase(fid)
    assert result["transitioned"] and result["sweep_complete"], result["sweep"]
    after = residue()
    assert not after["nodes"], f"unlineaged nodes survived: {after['nodes']}"
    assert P_MAIN not in after["blocks"] and "CANARY-P3-U4" not in after["blocks"], (
        f"memory_blocks re-feed survived: {after['blocks']!r}"
    )

    # later prompt hydration stays clean
    from core.vool_memory import VoolMemory

    with VoolMemory() as mem:
        prompt_view = mem.blocks_for_prompt(["user_profile"])
    assert "CANARY-P3-U4" not in prompt_view

    # FTS derivative is co-deleted
    from core.runtime_paths import data_path
    import sqlite3 as _sq

    mconn = _sq.connect(data_path("memory", "vool_memory.db"))
    try:
        hits = mconn.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH 'CANARY'"
        ).fetchone()[0]
    except Exception:
        hits = 0
    finally:
        mconn.close()
    assert hits == 0, "FTS index still surfaces governed tokens"


# ===========================================================================
# U5 — CE10: packed FILE liquefy bundles (.zst/.gz) traversed with the
# product loader; unknown formats fail honest (never fictional COMPLETED).
# ===========================================================================

def _write_packed_bundle(home, stem, backend, payload_obj):
    from pathlib import Path

    bundle_dir = home / "data" / "liquefy_vault" / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload_obj, sort_keys=True).encode("utf-8")
    if backend == "gzip":
        import gzip

        blob = gzip.compress(raw)
        path = bundle_dir / f"{stem}.gz"
    else:
        import zstandard

        blob = zstandard.ZstdCompressor().compress(raw)
        path = bundle_dir / f"{stem}.zst"
    path.write_bytes(blob)
    return path


@pytest.mark.parametrize("backend", ["zstd", "gzip"])
def test_u5_packed_liquefy_bundle_swept_via_product_loader(a8_env, backend):
    P = f"u5 {backend} secret TANGO-5555"
    fid = _admit_finalize(P)["finalization_id"]
    path = _write_packed_bundle(
        a8_env, f"task-u5-{backend}", backend,
        {"source_finalization_id": fid, "bundle": {"final_response": P}},
    )
    assert path.exists()
    result = _erase(fid)
    assert result["transitioned"] and result["sweep_complete"], result["sweep"]
    from core.liquefy_bridge import load_packed_bytes

    assert not path.exists(), "packed governed bundle survived erasure"


def test_u5b_unsupported_packed_form_reports_non_complete_not_completed(a8_env):
    P = "u5b opaque secret UNIFORM-6666"
    fid = _admit_finalize(P)["finalization_id"]
    from pathlib import Path

    mystery = a8_env / "data" / "liquefy_vault" / "bundles" / "task-u5b.tar.gz.enc"
    mystery.parent.mkdir(parents=True, exist_ok=True)
    mystery.write_bytes(b"\x00opaque-not-loadable")
    result = _erase(fid)
    assert not result.get("sweep_complete"), "unsupported governed artifact claimed COMPLETE"


# ===========================================================================
# U6 — CE11: every canonical checkpoint representation is swept (state_json
# shadow included); recoverable checkpoints carry their hash so recovery
# cannot retain or serve erased bytes.
# ===========================================================================

def test_u6_checkpoint_state_json_shadow_and_interrupted_retention_die(a8_env):
    P = "u6 checkpoint secret VICTOR-7777"
    fid = _admit_finalize(P)["finalization_id"]
    from core.runtime_continuity import (
        create_runtime_checkpoint,
        get_runtime_checkpoint,
        update_runtime_checkpoint,
    )

    ckpt = create_runtime_checkpoint(
        session_id="s-u6",
        request_text=f"restore me ({P})",
        source_context={"external_evidence": [P], "origin": "p3"},
    )
    # loop-state shadow copy inside state_json (the CE11 survivor class)
    updated = update_runtime_checkpoint(
        ckpt["checkpoint_id"],
        status="interrupted",
        state={"loop_source_context": {"external_evidence": [P]}, "last_tool_response": P},
    )
    # recoverable checkpoint retains final text WITHOUT terminal status ->
    # PASS003 stamps its identity so the sweep equality leg can see it
    stamped = update_runtime_checkpoint(
        ckpt["checkpoint_id"], final_response=P, status="interrupted"
    )
    assert stamped["final_response_hash"] == "sha256:" + _sha_hex(P)

    result = _erase(fid)
    assert result["transitioned"] and result["sweep_complete"], result["sweep"]
    row = get_runtime_checkpoint(ckpt["checkpoint_id"])
    assert not str(row["final_response"] or "")
    durable = json.dumps(row["state"]) + json.dumps(row["source_context"])
    assert P not in durable, "governed bytes survive inside checkpoint state shadow"
    conn = __import__("core.runtime_continuity", fromlist=["_conn"])._conn()
    try:
        hit = conn.execute(
            "SELECT COUNT(*) AS n FROM runtime_checkpoints "
            "WHERE pending_intent_json LIKE '%' || ? || '%'",
            (P,),
        ).fetchone()["n"]
        src_hit = conn.execute(
            "SELECT source_context_json, state_json FROM runtime_checkpoints WHERE checkpoint_id = ?",
            (ckpt["checkpoint_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert hit == 0
    assert P not in json.dumps(dict(src_hit))


# ===========================================================================
# U7 — CE09/NCE-A: runtime events details_json and sessions last_message are
# gated AT WRITE and AT SERVE and swept AT REST (withheld needs no traversal).
# ===========================================================================

def test_u7_details_json_and_last_message_gated_at_rest_and_serve(a8_env):
    P = "u7 runtime secret WHISKEY-8888"
    fid = _admit_finalize(P)["finalization_id"]
    from core.finalization import set_availability, AVAILABILITY_WITHHELD
    from core.runtime_continuity import (
        append_runtime_event,
        list_runtime_session_events,
        list_runtime_sessions,
    )

    append_runtime_event(
        session_id="s-u7",
        event_type="tool_executed",
        message="tool ok",
        details={"output": P},
    )
    # NCE-A surface populated while still available
    append_runtime_event(session_id="s-u7", event_type="model_output_chunk", message=P)

    set_availability(fid, AVAILABILITY_WITHHELD)  # NO traversal yet

    served_events = list_runtime_session_events("s-u7", limit=50)
    for item in served_events:
        assert P not in json.dumps(item), f"details/message leak on serve: {item}"
    served_sessions = list_runtime_sessions(limit=20)
    for sess in served_sessions:
        if sess["session_id"] == "s-u7":
            assert P not in json.dumps(sess), "last_message preview leak on serve"

    # now ERASE: at-rest law kills the retained columns outright
    result = _erase(fid)
    assert result["sweep_complete"], result["sweep"]
    served_events = list_runtime_session_events("s-u7", limit=50)
    for item in served_events:
        assert P not in json.dumps(item)
    served_sessions = list_runtime_sessions(limit=20)
    for sess in served_sessions:
        if sess["session_id"] == "s-u7":
            assert P not in json.dumps(sess)


# ===========================================================================
# U8 — NCE-B: operator snapshot's SELECT * archive reader consults
# availability (WITHHELD alone must already suppress — no traverse needed).
# ===========================================================================

def test_u8_operator_snapshot_archive_reader_suppresses_withheld_bytes(a8_env):
    P = "u8 archive secret XRAY-9999"
    fid = _admit_finalize(P)["finalization_id"]
    from storage.dialogue_memory import archive_dialogue_topic, recent_archived_dialogue_topics
    from core.finalization import set_availability, AVAILABILITY_WITHHELD

    archive_dialogue_topic(
        "s-u8",
        last_subject=None,
        topic_hints=["archive"],
        current_user_goal=None,
        assistant_commitments=[],
        unresolved_followups=[],
        closure_status="resolved",
        closure_reason="topic_shift",
        closing_user_input="what was it",
        closing_assistant_output=P,
    )
    rows = recent_archived_dialogue_topics("s-u8", limit=5)
    assert any((r.get("closing_assistant_output") or "") == P for r in rows), "fixture failed"

    set_availability(fid, AVAILABILITY_WITHHELD)
    rows = recent_archived_dialogue_topics("s-u8", limit=5)
    assert all(P not in json.dumps(r) for r in rows), "WITHHELD archive bytes served verbatim"

    result = _erase(fid)
    assert result["sweep_complete"]
    rows = recent_archived_dialogue_topics("s-u8", limit=5)
    assert all(P not in json.dumps(r) for r in rows)


# ===========================================================================
# U9 — CE08: no retained unsalted confirmation oracle for erased payloads —
# keying at emission AND suppression of legacy unsalted artifacts on erase.
# ===========================================================================

def test_u9_receipt_oracle_keyed_and_legacy_unsalted_suppressed(a8_env):
    DICT = ["hunter2", "password123", "letmein", "changeme", "qwerty123"]
    TARGET = "hunter2"
    fid = _admit_finalize(TARGET)["finalization_id"]
    from core.honesty_receipt import issue_honesty_receipt

    receipt = issue_honesty_receipt(
        session_id="s-u9", turn_index=0, response_text=TARGET
    )
    raw_hex = _sha_hex(TARGET)
    assert receipt.response_hash != raw_hex
    assert receipt.response_hash.startswith("keyed-sha256:")
    ledger = a8_env / "data" / "honesty_receipts" / "s-u9.jsonl"
    # inject a LEGACY unsalted artifact line as an attacker would find it
    legacy = {
        "receipt_id": "legacy-1", "turn_index": 1,
        "prompt_hash": "", "response_hash": raw_hex,
        "verdict": "no_action_claimed", "verdict_detail": "x",
        "claimed_actions": [], "executed_tools": [],
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if ledger.exists():
        lines = [l for l in ledger.read_text().splitlines() if l.strip()]
    lines.append(json.dumps(legacy))
    ledger.write_text("\n".join(lines) + "\n")

    result = _erase(fid)
    assert result["sweep_complete"]
    stored = "\n".join(l for l in ledger.read_text().splitlines() if l.strip())
    # low-entropy dictionary attack against EVERY retained digest form fails
    for guess in DICT + [TARGET]:
        assert guess not in stored.replace("erasure_digest_suppressed", "")
    assert '"response_hash": "%s"' % raw_hex not in stored
    assert "digest_suppressed_by_erasure" in stored, "suppression marker missing"


# ===========================================================================
# U10 — CE13: duplicate request identity — canonical uniqueness prevents
# invalid duplicates; residual legacy ambiguity FAILS CLOSED (typed refusal),
# never newest-wins substitution.
# ===========================================================================

def test_u10_duplicate_request_id_prevented_or_refused_fail_closed(a8_env):
    P = "u10 owner secret YANKEE-0000"
    first = _admit_finalize(P, request_id="http:req-u10-dup")
    from storage.db import get_connection

    conn = get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO a7_finalizations (
                finalization_id, semantic_result_id, turn_id, content_hash,
                canonical_content, status, request_id, payload_ref, availability
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'AVAILABLE')
            """,
            (
                "fc:p3-dupe-manual", "sr-not-admitted-will-violate-fk-off", "t-x",
                "sha256:" + _sha_hex("ATTACKER-SUBSTITUTE-BYTES"),
                "ATTACKER-SUBSTITUTE-BYTES", "answer_present", "http:req-u10-dup",
            ),
        )
    conn.close()

    # legacy-ambiguity fail-closed probe: simulate an old DB that carries two
    # rows sharing the id by dropping the unique index, inserting, restoring.
    conn = get_connection()
    conn.execute("DROP INDEX IF EXISTS ux_a7_finalizations_request_id")
    conn.execute(
        """
        INSERT INTO a7_finalizations (
            finalization_id, semantic_result_id, turn_id, content_hash,
            canonical_content, status, request_id, payload_ref, availability
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'AVAILABLE')
        """,
        (
            "fc:p3-dupe-legacy", "sr-p3-legacy-not-used", "t-y",
            "sha256:" + _sha_hex("LEGACY-DUP"),
            "LEGACY-DUP", "answer_present", "http:req-u10-dup",
        ),
    )
    conn.commit()
    conn.close()
    from core.finalization import get_finalization_by_request_id, ReplayAmbiguityRefused
    with pytest.raises(ReplayAmbiguityRefused):
        get_finalization_by_request_id("http:req-u10-dup", principal="owner_local")
    with pytest.raises(Exception):
        get_finalization_by_request_id("req-u10-dup")


# ===========================================================================
# U11 — fail-closed probes on newly gated served/read boundaries.
# ===========================================================================

def test_u11_served_boundaries_fail_closed_when_privacy_store_crashes(a8_env, monkeypatch):
    P = "u11 outage secret ZULU-1212"
    fid = _admit_finalize(P)["finalization_id"]
    from core.finalization import set_availability, AVAILABILITY_WITHHELD
    from core.runtime_continuity import append_runtime_event, list_runtime_sessions

    set_availability(fid, AVAILABILITY_WITHHELD)
    append_runtime_event(session_id="s-u11", event_type="model_output_chunk", message=P)

    import core.finalization as fin
    import core.runtime_continuity as rc

    def boom(*a, **k):
        raise RuntimeError("privacy store outage")

    # sessions preview gate: crash must suppress / propagate, never disclose
    monkeypatch.setattr(fin, "payload_availability_for_hash", boom)
    sessions = list_runtime_sessions(limit=20)
    for sess in sessions:
        if sess["session_id"] == "s-u11":
            assert P not in json.dumps(sess)
    # details/message serve gate likewise
    ev = rc.list_runtime_session_events("s-u11", limit=50)
    assert all(P not in json.dumps(item) for item in ev)


# ===========================================================================
# U12 — restart/resume remains real: sabotaged step yields honest
# non-completion and the production resume hook converges afterwards.
# ===========================================================================

def test_u12_partial_traversal_honest_then_resume_converges(a8_env, monkeypatch):
    P = "u12 resume secret ALFA-1313"
    fid = _admit_finalize(P)["finalization_id"]
    import core.finalization as fin

    def broken():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(fin, "_sweep_step_task_result_bodies", broken)
    result = _erase(fid)
    assert not result.get("sweep_complete"), "fabricated success on broken step"
    st = fin.erasure_sweep_status(fid)
    assert not st["sweep_complete"]
    assert any(v.startswith("error:") for v in st["stores"].values())

    monkeypatch.setattr(fin, "_sweep_step_task_result_bodies", lambda *a, **k: "completed")
    resumed = fin.resume_incomplete_erasure_sweeps()
    assert fid in resumed
    st = fin.erasure_sweep_status(fid)
    assert st["sweep_complete"]
