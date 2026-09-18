"""A8 canonical privacy authority — pass-001 proof suite.

Covers the final adjudicated builder scope (FINAL_BUILDER_SCOPE.md +
MUTATION_REQUIREMENTS.md): served-boundary gates (history, pins, receipts,
replay, recovery), lineage stamps, erasure traversal + ledger, resurrection
fences, digest laws, legacy semantics, migration honesty, and the
stale-epoch fence. Every test here is a sabotage target for the mutation
campaign: removing the guard it pins must turn it RED.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import storage.db as sdb
from core.finalization import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_ERASED,
    AVAILABILITY_LEGACY_UNKNOWN,
    AVAILABILITY_WITHHELD,
    REPLAY_UNAVAILABLE_BY_POLICY,
    erase_finalization_payload,
    erasure_sweep_status,
    finalize_answer,
    get_finalization_by_content,
    get_finalization_by_request_id,
    get_finalization_by_semantic_id,
    payload_availability_for_hash,
    replay_finalized_answer,
    resume_incomplete_erasure_sweeps,
    set_availability,
)
from core.final_response_store import store_final_response
from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission
from core.semantic.semantic_admissions import set_request_context


PRODUCTION_ROOTS = ("core", "apps", "network", "relay", "storage", "channels", "retrieval", "sandbox", "tools", "adapters", "ops")


@pytest.fixture()
def a8_env(tmp_path, monkeypatch):
    """Isolated store: temp DB (a7 + continuity) + temp VOOL_HOME (JSONLs,
    pins, semantic memory, receipts, adaptation corpora, mirror snapshots)."""
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    sdb.configure_default_db_path(tmp_path / "a8.db")
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    reset_admission()
    yield home
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)


def _finalize(text="canonical answer bytes"):
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id="t", canonical_content=text)


def _finalize_under_request(text, request_id):
    """Finalize with a bound A0 request id (lineage anchor for derivatives)."""
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    with _request_scope(request_id):
        return finalize_answer(turn_id="t", canonical_content=text)



import contextlib


@contextlib.contextmanager
def _request_scope(request_id):
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    token = set_request_context(request_id)
    try:
        yield
    finally:
        _CURRENT_REQUEST_ID.reset(token)


def _sha(text):
    return "sha256:" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _ensure_namespace(session):
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(session, grant_confirmed_profile=True)


def _append_event(home, session, user, assistant, request_id=""):
    log = home / "data" / "conversation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": "2026-08-26T00:00:00",
        "session_id": session,
        "user": user,
        "assistant": assistant,
    }
    if request_id:
        row["request_id"] = request_id
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _history(runtime, session):
    from core.web.api.service import dispatch_get

    response = dispatch_get(
        path="/api/chat/history",
        query={"session": [session]},
        runtime=runtime,
        model_name="test",
        client_host="127.0.0.1",
    )
    return json.loads(bytes(response.body).decode("utf-8")) if response.body else {}


def _runtime():
    from core.web.api.runtime import RuntimeServices

    return RuntimeServices(display_name="VOOL")


# ---------------------------------------------------------------------------
# Availability vocabulary + raw readers + principal scope
# ---------------------------------------------------------------------------


def test_legacy_unknown_vocabulary_transitions_lawfully(a8_env):
    commit = _finalize("pre-window truth")
    fid = commit["finalization_id"]
    conn = sdb.get_connection()
    try:
        conn.execute(
            "UPDATE a7_finalizations SET availability = ? WHERE finalization_id = ?",
            (AVAILABILITY_LEGACY_UNKNOWN, fid),
        )
        conn.commit()
    finally:
        conn.close()
    # Governance contact: LEGACY_UNKNOWN -> WITHHELD -> ERASED is lawful.
    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="contact")
    assert set_availability(fid, AVAILABILITY_ERASED, reason="erase")
    # LEGACY_UNKNOWN never upgrades to AVAILABLE (fabricated governability).
    commit2 = _finalize("another legacy row")
    fid2 = commit2["finalization_id"]
    conn = sdb.get_connection()
    try:
        conn.execute(
            "UPDATE a7_finalizations SET availability = ? WHERE finalization_id = ?",
            (AVAILABILITY_LEGACY_UNKNOWN, fid2),
        )
        conn.commit()
    finally:
        conn.close()
    assert not set_availability(fid2, AVAILABILITY_AVAILABLE, reason="fabricate")


def test_raw_readers_suppress_unavailable_bytes(a8_env):
    commit = _finalize("withheld secret answer")
    fid = commit["finalization_id"]
    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="policy")
    row = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert row is not None and row["canonical_content"] == ""
    by_content = get_finalization_by_content("withheld secret answer")
    assert by_content is not None and by_content["canonical_content"] == ""
    # ERASED rows likewise never expose bytes through the raw readers.
    assert set_availability(fid, AVAILABILITY_ERASED, reason="policy")
    row = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert row["canonical_content"] == ""


def test_replay_is_principal_scoped_fail_closed(a8_env):
    commit = _finalize("scoped truth bytes")
    # Absent principal: typed refusal, no bytes.
    with pytest.raises(Exception) as missing:
        replay_finalized_answer(semantic_result_id=commit["semantic_result_id"])
    assert "principal" in str(missing.value).lower()
    with pytest.raises(Exception):
        get_finalization_by_request_id("req:http:anything")
    # Unrecognized principal class: refused (ledger vocabulary reused).
    with pytest.raises(Exception):
        replay_finalized_answer(
            semantic_result_id=commit["semantic_result_id"], principal="attacker"
        )
    # Lawful principal: replay serves committed bytes.
    ok = replay_finalized_answer(
        semantic_result_id=commit["semantic_result_id"], principal="owner_local"
    )
    assert ok["canonical_content"] == "scoped truth bytes"


# ---------------------------------------------------------------------------
# Served boundary: history (M-04)
# ---------------------------------------------------------------------------


def test_history_available_verified_then_erased_suppressed(a8_env):
    rid = "req:http:hist-1"
    commit = _finalize_under_request("the canonical answer", rid)
    session = "sess-a8-hist"
    _append_event(a8_env, session, "question", "the canonical answer", request_id=rid)

    served = _history(_runtime(), session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[0]["content"] == "the canonical answer"
    assert assistant[0]["a7"]["status"] == "verified"
    assert assistant[0]["a7"]["finalization_id"] == commit["finalization_id"]

    result = erase_finalization_payload(
        commit["finalization_id"], reason="user erasure", governance_actor="owner_local"
    )
    assert result["transitioned"] and result["sweep_complete"]

    served = _history(_runtime(), session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    # The traversal removed the governed transcript event: no bytes, ever.
    assert not any(m.get("content") == "the canonical answer" for m in served["messages"])
    # Crash-window defense: if the event row survives an incomplete sweep, the
    # serve-time gate still blanks it with the typed outcome.
    import core.finalization as _fin

    _append_event(a8_env, session, "question", "the canonical answer", request_id=rid)
    served = _history(_runtime(), session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[0]["content"] == ""
    assert assistant[0]["a7"]["status"] == "unavailable_by_policy"
    del _fin


def test_history_withheld_suppressed_lineage_and_legacy(a8_env):
    rid = "req:http:hist-2"
    commit = _finalize_under_request("withheld canonical bytes", rid)
    session = "sess-a8-wh"
    _append_event(a8_env, session, "q", "withheld canonical bytes", request_id=rid)
    assert set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD)
    served = _history(_runtime(), session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[0]["content"] == ""
    assert assistant[0]["a7"]["availability"] == AVAILABILITY_WITHHELD

    # Legacy (unlineaged) turn: WITHHELD verdict still found via content hash;
    # ERASED verdict found via the digest tombstone (the a7 hash is salted).
    legacy_session = "sess-a8-legacy"
    _append_event(a8_env, legacy_session, "q2", "legacy erased answer")
    legacy = _finalize("legacy erased answer")
    set_availability(legacy["finalization_id"], AVAILABILITY_WITHHELD)
    served = _history(_runtime(), legacy_session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[0]["content"] == ""
    set_availability(legacy["finalization_id"], AVAILABILITY_ERASED)
    served = _history(_runtime(), legacy_session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    # ERASED verdict (digest tombstone) suppresses even before the sweep runs.
    assert assistant and assistant[0]["content"] == ""
    assert assistant[0]["a7"]["status"] == "unavailable_by_policy"
    erase_finalization_payload(legacy["finalization_id"], reason="erase")
    served = _history(_runtime(), legacy_session)
    assert not any(
        m.get("content") == "legacy erased answer" for m in served["messages"]
    )


def test_history_honest_legacy_label_when_no_governance_record(a8_env):
    session = "sess-a8-plain"
    _append_event(a8_env, session, "q", "an old answer nobody governs")
    served = _history(_runtime(), session)
    assistant = [m for m in served["messages"] if m["role"] == "assistant"]
    assert assistant and assistant[0]["content"] == "an old answer nobody governs"
    assert assistant[0]["a7"]["status"] == "legacy_unverified"


# ---------------------------------------------------------------------------
# Erasure traversal over derivative stores (M-06, M-08, M-09, M-11)
# ---------------------------------------------------------------------------


def _seed_all_derivative_stores(home, request_id, canonical_text, finalization_id):
    from core.memory.files import user_heuristics_path

    _ensure_namespace("sess-sweep")
    _append_event(home, "sess-sweep", "q", canonical_text, request_id=request_id)
    # dialogue rows (stamped from the bound request context)
    with _request_scope(request_id):
        from storage.dialogue_memory import record_dialogue_turn

        record_dialogue_turn(
            "sess-sweep",
            raw_input="q",
            normalized_input="q",
            reconstructed_input="q",
            topic_hints=["t"],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
        )
        from core.memory import entries as memory_entries

        memory_entries.add_memory_fact(
            "fact derived from the turn",
            category="fact",
            session_id="sess-sweep",
            source="auto_dialogue",
        )
        from core.memory.learning import update_session_summary

        update_session_summary(
            session_id="sess-sweep", user_input="q", assistant_output=canonical_text
        )
        from core.context_retrieval import store_turn

        store_turn("sess-sweep", "q", canonical_text, access_policy=None, source_context=None)
    # legacy final-truth lane + useful outputs
    store_final_response(
        "task-sweep",
        canonical_text,
        canonical_text,
        "completed",
        0.9,
        finalization_id=finalization_id,
        request_id=request_id,
    )
    from storage.useful_output_store import sync_useful_outputs

    sync_useful_outputs()
    # pin (stamped at write by content lookup)
    from core import message_pins

    message_pins.pin_message("sess-sweep", "assistant", canonical_text)
    # checkpoint carrying the payload hash
    conn = sdb.get_connection()
    try:
        conn.execute(
            "INSERT INTO runtime_checkpoints (checkpoint_id, session_id, request_text, "
            "status, final_response, final_response_hash, created_at, updated_at) "
            "VALUES (?, 'sess-sweep', 'q', 'interrupted', ?, ?, ?, ?)",
            (
                f"cp-{uuid.uuid4().hex[:8]}",
                canonical_text,
                _sha(canonical_text),
                "2026-08-26T00:00:00",
                "2026-08-26T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    # liquefy bundle referencing the finalization
    from core.liquefy_bridge import _vault_dir

    bundle_dir = _vault_dir("bundles") / "task-sweep"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "task_bundle.json").write_text(
        json.dumps(
            {
                "trace_id": "task-sweep",
                "source_finalization_id": finalization_id,
                "final_response": {"content_hash": _sha(canonical_text)},
            }
        ),
        encoding="utf-8",
    )
    # adaptation corpus example with lineage
    corpora = home / "data" / "adaptation" / "corpora"
    corpora.mkdir(parents=True, exist_ok=True)
    (corpora / "corp.jsonl").write_text(
        json.dumps(
            {
                "instruction": "q",
                "output": canonical_text,
                "source": "conversation",
                "metadata": {"session_id": "sess-sweep", "request_id": request_id},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # local mirror snapshot carrying the payload bytes
    mirror_dir = home / "relay_mirror"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    (mirror_dir / "telegram.json").write_text(
        json.dumps(
            {
                "topic_name": "telegram",
                "records": [{"record_id": "r1", "content": canonical_text}],
                "record_count": 1,
            }
        ),
        encoding="utf-8",
    )


def test_erasure_traversal_reaches_every_governed_derivative(a8_env):
    rid = "req:http:sweep-1"
    commit = _finalize_under_request("sweep me completely", rid)
    fid = commit["finalization_id"]
    _seed_all_derivative_stores(a8_env, rid, "sweep me completely", fid)

    # sanity: the derivatives exist before erasure
    from core.memory.files import conversation_log_path, load_jsonl

    assert any(r.get("request_id") == rid for r in load_jsonl(conversation_log_path()))

    result = erase_finalization_payload(fid, reason="user erasure")
    assert result["transitioned"]
    assert result["sweep_complete"], result["sweep"]

    from core.memory.files import (
        memory_entries_path,
        session_summaries_path,
    )

    rows = load_jsonl(conversation_log_path())
    assert not any(r.get("request_id") == rid for r in rows)
    assert not any(r.get("assistant") == "sweep me completely" for r in rows)
    assert not any(
        r.get("request_id") == rid for r in load_jsonl(memory_entries_path())
    )
    assert not any(
        r.get("request_id") == rid for r in load_jsonl(session_summaries_path())
    )
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM dialogue_turns WHERE request_id = ?", (rid,)
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM finalized_responses WHERE parent_task_id = 'task-sweep'"
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM useful_outputs WHERE finalization_id = ?", (fid,)
        ).fetchone()["c"] == 0
        checkpoint = conn.execute(
            "SELECT final_response, final_response_hash FROM runtime_checkpoints"
        ).fetchone()
        assert checkpoint["final_response"] == "" and checkpoint["final_response_hash"] == ""
    finally:
        conn.close()
    from core import message_pins

    assert message_pins.list_pins("sess-sweep") == []
    from core.liquefy_bridge import _vault_dir

    assert not (_vault_dir("bundles") / "task-sweep" / "task_bundle.json").exists()
    corpus = a8_env / "data" / "adaptation" / "corpora" / "corp.jsonl"
    assert "sweep me completely" not in corpus.read_text(encoding="utf-8")
    mirror = a8_env / "relay_mirror" / "telegram.json"
    assert not mirror.exists()
    # semantic nodes hard-deleted
    from core.vool_memory import VoolMemory

    with VoolMemory() as mem:
        remaining = mem._conn.execute(
            "SELECT COUNT(*) c FROM memory_nodes WHERE lineage_request_id = ?", (rid,)
        ).fetchone()["c"]
    assert remaining == 0
    # traversal ledger observable, complete
    status = erasure_sweep_status(fid)
    assert status["erased"] and status["sweep_complete"]
    assert status["stores"]["conversation_log"] == "completed"


def test_lineage_stamped_at_write_time(a8_env):
    rid = "req:http:lineage-1"
    commit = _finalize_under_request("lineage target", rid)
    _ensure_namespace("sess-lin")
    _append_event(a8_env, "sess-lin", "q", "lineage target", request_id=rid)
    with _request_scope(rid):
        from storage.dialogue_memory import record_dialogue_turn

        record_dialogue_turn(
            "sess-lin",
            raw_input="q",
            normalized_input="q",
            reconstructed_input="q",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=1.0,
            quality_flags=[],
        )
        from core.memory.learning import update_session_summary

        update_session_summary(
            session_id="sess-lin", user_input="q", assistant_output="lineage target"
        )
    from core import message_pins

    message_pins.pin_message("sess-lin", "assistant", "lineage target")
    from core.memory.files import conversation_log_path, load_jsonl, session_summaries_path

    assert any(r.get("request_id") == rid for r in load_jsonl(conversation_log_path()))
    assert any(
        r.get("request_id") == rid for r in load_jsonl(session_summaries_path())
    )
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM dialogue_turns WHERE request_id = ?", (rid,)
        ).fetchone()["c"] > 0
    finally:
        conn.close()
    pins = message_pins.list_pins("sess-lin")
    assert pins and pins[0]["finalization_id"] == commit["finalization_id"]


# ---------------------------------------------------------------------------
# Resurrection fences (M-07) + traversal ledger honesty (M-10)
# ---------------------------------------------------------------------------


def test_sync_useful_outputs_cannot_resurrect_erased_source(a8_env):
    commit = _finalize("resurrection candidate")
    fid = commit["finalization_id"]
    store_final_response(
        "task-res", "resurrection candidate", "resurrection candidate", "completed", 0.9,
        finalization_id=fid,
    )
    from storage.useful_output_store import sync_useful_outputs

    sync_useful_outputs()
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM useful_outputs WHERE finalization_id = ?", (fid,)
        ).fetchone()["c"] == 1
    finally:
        conn.close()
    erase_finalization_payload(fid, reason="erase")
    sync_useful_outputs()  # the proven resurrection attempt
    # Stale-writer simulation: a finalized_responses row for the erased payload
    # reappears (e.g. written by a pre-erasure process). Sync must NOT let it
    # re-derive a useful_output.
    store_final_response(
        "task-res", "resurrection candidate", "resurrection candidate", "completed", 0.9,
        finalization_id=fid,
    )
    sync_useful_outputs()
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM useful_outputs WHERE finalization_id = ?", (fid,)
        ).fetchone()["c"] == 0
    finally:
        conn.close()
    # WITHHELD sources are skipped (and their derived rows removed) too.
    commit2 = _finalize("withheld candidate")
    fid2 = commit2["finalization_id"]
    store_final_response(
        "task-res2", "withheld candidate", "withheld candidate", "completed", 0.9,
        finalization_id=fid2,
    )
    sync_useful_outputs()
    set_availability(fid2, AVAILABILITY_WITHHELD)
    sync_useful_outputs()
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM useful_outputs WHERE finalization_id = ?", (fid2,)
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_no_sweep_complete_without_actual_traversal(a8_env, monkeypatch):
    commit = _finalize("partial traversal target")
    fid = commit["finalization_id"]
    _append_event(a8_env, "sess-partial", "q", "partial traversal target")
    import core.finalization as fin

    def _boom(finalization_id, content_hash):
        raise RuntimeError("simulated crash mid-sweep")

    monkeypatch.setattr(fin, "_sweep_step_useful_outputs", _boom)
    result = erase_finalization_payload(fid, reason="erase")
    assert result["transitioned"]
    assert not result["sweep_complete"]
    status = erasure_sweep_status(fid)
    assert not status["sweep_complete"]
    assert status["stores"]["useful_outputs"].startswith("error:")
    # The canonical row is erased (bytes suppressed) — and resumption completes.
    monkeypatch.undo()
    resumed = resume_incomplete_erasure_sweeps()
    assert fid in resumed
    assert erasure_sweep_status(fid)["sweep_complete"]


def test_resume_is_idempotent_after_completion(a8_env):
    commit = _finalize("idempotent target")
    fid = commit["finalization_id"]
    erase_finalization_payload(fid, reason="erase")
    assert resume_incomplete_erasure_sweeps() == []
    assert erasure_sweep_status(fid)["sweep_complete"]


# ---------------------------------------------------------------------------
# Mirror gate + DELETE route (M-08)
# ---------------------------------------------------------------------------


class _FakeMirrorAdapter:
    def __init__(self):
        self.published = {}

    def fetch_snapshot(self, topic):
        return self.published.get(topic)

    def publish_snapshot(self, topic, payload):
        self.published[topic] = payload
        return True


def test_mirror_publish_refuses_unavailable_payload(a8_env):
    commit = _finalize("mirror secret")
    set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD)
    from relay.channel_outbound import append_outbound_post

    ok, record = append_outbound_post(
        platform="telegram",
        content="mirror secret",
        task_id="task-m",
        session_id="sess-m",
        source_context=None,
        adapter=_FakeMirrorAdapter(),
    )
    assert not ok and record.get("reason") == "payload_unavailable_by_policy"
    # AVAILABLE payload still publishes (and carries lineage).
    commit2 = _finalize("mirror fine")
    ok2, record2 = append_outbound_post(
        platform="telegram",
        content="mirror fine",
        task_id="task-m2",
        session_id="sess-m2",
        source_context=None,
        adapter=_FakeMirrorAdapter(),
        finalization_id=commit2["finalization_id"],
    )
    assert ok2 and record2.get("finalization_id") == commit2["finalization_id"]


def test_mirror_delete_route_removes_local_snapshot(a8_env):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from relay.http_mirror_server import FileMirrorStore, MirrorServerConfig, build_handler

    store = FileMirrorStore(str(a8_env / "mirror"))
    store.put("topic-a", {"topic_name": "topic-a", "records": [], "record_count": 0})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(store))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/topics/topic-a", method="DELETE"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
        assert store.get("topic-a") is None
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# Receipts free-text gate (M-14) + pins gate (M-15) + recovery gate
# ---------------------------------------------------------------------------


def _write_receipt(home, session, response_text):
    ledger = home / "data" / "honesty_receipts"
    ledger.mkdir(parents=True, exist_ok=True)
    safe = hashlib.sha256(session.encode()).hexdigest()[:24]
    receipt = {
        "receipt_id": "hr-1",
        "session_id": session,
        "turn_index": 0,
        "issued_at": 1.0,
        "prev_hash": "",
        "prompt_hash": "00",
        "response_hash": hashlib.sha256(response_text.encode()).hexdigest(),
        "claimed_actions": ["wrote file notes.md"],
        "executed_tools": [],
        "verdict": "clean",
        "verdict_detail": "claim backed by a real write_file execution",
        "content_hash": "ab",
        "signer_peer_id": "",
        "signature": "",
        "schema": "vool.honesty_receipt.v1",
    }
    (ledger / f"{safe}.jsonl").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    return receipt


def test_receipts_free_text_gated_on_availability(a8_env):
    session = "sess-receipts"
    commit = _finalize("receipt payload truth")
    _write_receipt(a8_env, session, "receipt payload truth")

    from core.web.api.service import dispatch_get

    def _serve():
        response = dispatch_get(
            path="/api/runtime/receipts",
            query={"session": [session]},
            runtime=_runtime(),
            model_name="test",
            client_host="127.0.0.1",
        )
        return json.loads(bytes(response.body).decode("utf-8"))

    payload = _serve()["receipts"][0]
    assert payload["verdict_detail"] != ""
    assert payload["claimed_actions"] == ["wrote file notes.md"]
    erase_finalization_payload(commit["finalization_id"], reason="erase")
    payload = _serve()["receipts"][0]
    assert payload["verdict_detail"] == ""
    assert payload["claimed_actions"] == []
    # Evidence fields remain (receipts stay receipts).
    assert payload["content_hash"] == "ab"
    assert payload["receipt_id"] == "hr-1"


def test_pins_served_only_when_available(a8_env):
    from core import message_pins

    commit = _finalize("pin me while available")
    fid = commit["finalization_id"]
    message_pins.pin_message("sess-pins", "assistant", "pin me while available")
    served = message_pins.list_servable_pins("sess-pins")
    assert served and served[0]["finalization_id"] == fid
    # WITHHELD (not swept): serve-gate suppresses even though the pin row exists.
    set_availability(fid, AVAILABILITY_WITHHELD)
    assert message_pins.list_servable_pins("sess-pins") == []
    # Legacy pin with no governance record: served (honest legacy).
    message_pins.pin_message("sess-pins2", "assistant", "old legacy pin text")
    served2 = message_pins.list_servable_pins("sess-pins2")
    assert served2 and served2[0]["text"] == "old legacy pin text"


def test_recovery_blanks_erased_final_response(a8_env):
    from core.web.api.service import dispatch_post

    commit = _finalize("recoverable payload")
    fid = commit["finalization_id"]
    conn = sdb.get_connection()
    try:
        conn.execute(
            "INSERT INTO runtime_checkpoints (checkpoint_id, session_id, request_text, "
            "status, final_response, final_response_hash, created_at, updated_at) "
            "VALUES ('cp-rec', 'sess-rec', 'q', 'interrupted', ?, ?, ?, ?)",
            ("recoverable payload", commit["content_hash"], "2026-08-26T00:00:00", "2026-08-26T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    erase_finalization_payload(fid, reason="erase")
    response = dispatch_post(
        path="/api/task/recovery",
        body={"session_id": "sess-rec", "checkpoint_id": "cp-rec", "action": "cancel"},
        headers={},
        runtime=_runtime(),
        model_name="test",
        client_host="127.0.0.1",
        workspace_root_provider=lambda: str(a8_env),
    )
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["ok"] is True
    assert payload["checkpoint"]["final_response"] == ""
    assert payload["checkpoint"]["final_response_hash"] == ""


# ---------------------------------------------------------------------------
# Replay wire outcome (served) + hydration gate + delete rebind
# ---------------------------------------------------------------------------


def test_identical_replay_carries_typed_outcome_on_wire(a8_env):
    from core.invocation.ledger import accept_invocation
    from core.web.api.service import dispatch_post

    body = {"message": "wire outcome probe"}
    import hashlib as _h

    digest = "sha256:" + _h.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()
    accepted = accept_invocation(
        external_kind="http",
        external_value="wire-probe-1",
        principal="owner_local",
        raw_digest=digest,
    )
    assert accepted["outcome"] == "ACCEPTED_FIRST"
    commit = _finalize_under_request("wire probe answer", accepted["request_id"])

    def _replay():
        response = dispatch_post(
            path="/api/chat",
            body=body,
            headers={},
            runtime=_runtime(),
            model_name="test",
            client_host="127.0.0.1",
            request_id="wire-probe-1",
            workspace_root_provider=lambda: str(a8_env),
        )
        return json.loads(bytes(response.body).decode("utf-8"))

    payload = _replay()
    assert payload["message"]["content"] == "wire probe answer"
    assert payload["replay_outcome"] == "AVAILABLE"
    erase_finalization_payload(commit["finalization_id"], reason="erase")
    payload = _replay()
    assert payload["message"]["content"] == ""
    assert payload["replay_outcome"] == REPLAY_UNAVAILABLE_BY_POLICY
    assert payload["vool_response_commit"]["finalization_id"] == commit["finalization_id"]


def test_hydration_excludes_unavailable_payload(a8_env):
    from core.persistent_memory import augment_history_from_session_log

    rid = "req:http:hyd-1"
    commit = _finalize_under_request("hydrate then erase", rid)
    _append_event(a8_env, "sess-hyd", "q", "hydrate then erase", request_id=rid)
    history = augment_history_from_session_log(
        [], session_id="sess-hyd", user_text="next question"
    )
    assert any(m["content"] == "hydrate then erase" for m in history)
    erase_finalization_payload(commit["finalization_id"], reason="erase")
    history = augment_history_from_session_log(
        [], session_id="sess-hyd", user_text="next question"
    )
    assert not any(m["content"] == "hydrate then erase" for m in history)
    # WITHHELD also excluded.
    rid2 = "req:http:hyd-2"
    commit2 = _finalize_under_request("hydrate withheld", rid2)
    _append_event(a8_env, "sess-hyd2", "q", "hydrate withheld", request_id=rid2)
    set_availability(commit2["finalization_id"], AVAILABILITY_WITHHELD)
    history = augment_history_from_session_log(
        [], session_id="sess-hyd2", user_text="next question"
    )
    assert not any(m["content"] == "hydrate withheld" for m in history)


def test_delete_conversation_session_rebinds_to_availability_authority(a8_env):
    from core.memory.entries import delete_conversation_session

    rid = "req:http:del-1"
    commit = _finalize_under_request("delete me via chat delete", rid)
    fid = commit["finalization_id"]
    _ensure_namespace("sess-del")
    _append_event(a8_env, "sess-del", "q", "delete me via chat delete", request_id=rid)
    with _request_scope(rid):
        from storage.dialogue_memory import record_dialogue_turn

        record_dialogue_turn(
            "sess-del",
            raw_input="q",
            normalized_input="q",
            reconstructed_input="q",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=1.0,
            quality_flags=[],
        )
    delete_conversation_session("sess-del")
    row = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert row["availability"] == AVAILABILITY_ERASED
    assert erasure_sweep_status(fid)["sweep_complete"]
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM dialogue_turns WHERE session_id = 'sess-del'"
        ).fetchone()["c"] == 0
    finally:
        conn.close()
    from core.memory.files import conversation_log_path, load_jsonl

    assert not any(r.get("session_id") == "sess-del" for r in load_jsonl(conversation_log_path()))


def test_delivery_sweep_excludes_unavailable_rows(a8_env):
    from core.finalization import sweep_attempted_unknown_deliveries

    withheld = _finalize("withheld delivery candidate")
    set_availability(withheld["finalization_id"], AVAILABILITY_WITHHELD)
    conn = sdb.get_connection()
    try:
        conn.execute(
            "UPDATE a7_finalizations SET delivery_status = 'ATTEMPTED_UNKNOWN' "
            "WHERE finalization_id IN (?, ?)",
            (withheld["finalization_id"], "fc-nonexistent"),
        )
        conn.commit()
    finally:
        conn.close()
    candidates = sweep_attempted_unknown_deliveries()
    fids = [row["finalization_id"] for row in candidates]
    assert withheld["finalization_id"] not in fids


# ---------------------------------------------------------------------------
# Epoch fence (M-12) + digest laws (M-11) + external vocabulary (M-13)
# ---------------------------------------------------------------------------


def test_stale_epoch_writer_cannot_mint_available_bytes(a8_env):
    from core.finalization import _bind_durably
    from core.invocation.ledger import FenceRefused
    from core.semantic.semantic_admissions import set_execution_context

    stale_identity = {
        "execution_id": "exec-does-not-exist",
        "generation": 0,
        "runtime_epoch": "epoch-of-a-dead-process",
    }
    from core.semantic.semantic_admissions import _EXECUTION_IDENTITY

    reset_admission()
    admit_semantic_result({"response": "stale bytes", "route_reason": "model_lane"})
    from core.finalization import current_semantic_result_id

    cited_sr = current_semantic_result_id()
    token = set_execution_context(stale_identity)
    try:
        commit = {
            "finalization_id": "fc:stale-attempt",
            "semantic_result_id": cited_sr,
            "turn_id": "t",
            "content_hash": _sha("stale bytes"),
            "canonical_content": "stale bytes",
            "status": "answer_present",
            "request_id": "",
        }
        with pytest.raises(FenceRefused):
            _bind_durably(commit)
    finally:
        _EXECUTION_IDENTITY.reset(token)
    conn = sdb.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM a7_finalizations"
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_knowledge_tombstone_retains_no_content_hash(a8_env):
    from storage.knowledge_index import add_tombstone

    add_tombstone("shard-1", "sha256:guessable", 3, "withdrawn")
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT content_hash FROM knowledge_tombstones WHERE shard_id = 'shard-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row["content_hash"] == ""


def test_digest_tombstone_survives_row_salting(a8_env):
    commit = _finalize("tombstone target")
    pre_hash = commit["content_hash"]
    erase_finalization_payload(commit["finalization_id"], reason="erase")
    # The a7 row's hash is salted; the governance tombstone still resolves the
    # PRE-ERASE digest deterministically (legacy surfaces' gate).
    assert payload_availability_for_hash(pre_hash) == AVAILABILITY_ERASED


def test_external_erasure_confirmed_vocabulary_absent():
    # M-13 pre-commit pin: the vocabulary may not ship without an evidence
    # class mirroring the delivery-evidence law. Grep-law over production.
    import subprocess
    import sys
    from pathlib import Path as _P

    repo = _P(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            "EXTERNAL_ERASURE_CONFIRMED",
            *[str(repo / item) for item in PRODUCTION_ROOTS],
        ],
        capture_output=True,
        text=True,
    )
    hits = [line for line in result.stdout.splitlines() if line.strip()]
    assert hits == [], f"EXTERNAL_ERASURE_CONFIRMED shipped without evidence law: {hits}"
    del sys, subprocess


# ---------------------------------------------------------------------------
# Migration honesty (law 11) + fresh writes after upgrade
# ---------------------------------------------------------------------------


def test_legacy_rows_migrate_to_honest_legacy_unknown(tmp_path):
    db = tmp_path / "legacy-a8.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE a7_finalizations (
            finalization_id TEXT PRIMARY KEY,
            semantic_result_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            canonical_content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'answer_present',
            delivery_status TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE finalized_responses (
            parent_task_id TEXT PRIMARY KEY,
            raw_synthesized_text TEXT,
            rendered_persona_text TEXT,
            status_marker TEXT,
            confidence_score REAL,
            anchored_signature TEXT
        );
        """
    )
    legacy_bytes = "old world answer"
    conn.execute(
        "INSERT INTO a7_finalizations (finalization_id, semantic_result_id, turn_id, content_hash, canonical_content)"
        " VALUES ('fc-old-1', 'sr:old', 't', ?, ?)",
        (_sha(legacy_bytes), legacy_bytes),
    )
    conn.execute(
        "INSERT INTO finalized_responses VALUES ('task-old', 'old raw', 'old rendered', 'completed', 0.9, NULL)"
    )
    conn.commit()
    conn.close()

    sdb.configure_default_db_path(db)
    try:
        from storage.migrations import run_migrations

        run_migrations()
        fresh = sdb.get_connection()
        try:
            row = fresh.execute(
                "SELECT availability FROM a7_finalizations WHERE finalization_id = 'fc-old-1'"
            ).fetchone()
            # Honest uncertainty — never governed-as-AVAILABLE by schema default.
            assert row["availability"] == AVAILABILITY_LEGACY_UNKNOWN
            # No fabricated lineage on legacy rows.
            fr = fresh.execute(
                "SELECT finalization_id, request_id FROM finalized_responses WHERE parent_task_id = 'task-old'"
            ).fetchone()
            assert fr["finalization_id"] == "" and fr["request_id"] == ""
            # No fabricated external/erasure claims.
            events = fresh.execute(
                "SELECT COUNT(*) c FROM a7_governance_events"
            ).fetchone()
            assert events["c"] == 0
        finally:
            fresh.close()
        # Fresh A8-governed writes work after the upgrade (and stay AVAILABLE —
        # the one-time marker prevents re-stamping).
        commit = _finalize("post-upgrade truth")
        assert commit["binding_outcome"] == "ACCEPTED_FIRST"
        fresh = sdb.get_connection()
        try:
            row = fresh.execute(
                "SELECT availability FROM a7_finalizations WHERE finalization_id = ?",
                (commit["finalization_id"],),
            ).fetchone()
            assert row["availability"] == AVAILABILITY_AVAILABLE
        finally:
            fresh.close()
        run_migrations()  # idempotent: marker holds
        fresh = sdb.get_connection()
        try:
            row = fresh.execute(
                "SELECT availability FROM a7_finalizations WHERE finalization_id = ?",
                (commit["finalization_id"],),
            ).fetchone()
            assert row["availability"] == AVAILABILITY_AVAILABLE
        finally:
            fresh.close()
    finally:
        sdb.configure_default_db_path(None)


# ---------------------------------------------------------------------------
# Race semantics: erase wins on every lineage-reached surface
# ---------------------------------------------------------------------------


def test_erase_wins_over_serve_and_restart_deterministically(a8_env):
    rid = "req:http:race-1"
    commit = _finalize_under_request("race target payload", rid)
    fid = commit["finalization_id"]
    _append_event(a8_env, "sess-race", "q", "race target payload", request_id=rid)
    runtime = _runtime()
    # serve (AVAILABLE), erase, serve again, "restart" (fresh connection pool)
    first = _history(runtime, "sess-race")
    assert any(m["content"] == "race target payload" for m in first["messages"])
    erase_finalization_payload(fid, reason="erase")
    for _ in range(3):
        served = _history(runtime, "sess-race")
        assert not any(m["content"] == "race target payload" for m in served["messages"])
    replay = replay_finalized_answer(
        semantic_result_id=commit["semantic_result_id"], principal="owner_local"
    )
    assert replay["replay_outcome"] == REPLAY_UNAVAILABLE_BY_POLICY
    assert replay["canonical_content"] == ""


def test_withheld_then_erased_ordering_is_monotone_in_serving(a8_env):
    rid = "req:http:order-1"
    commit = _finalize_under_request("ordering payload", rid)
    _append_event(a8_env, "sess-ord", "q", "ordering payload", request_id=rid)
    runtime = _runtime()

    def assistant_content():
        served = _history(runtime, "sess-ord")
        return [m["content"] for m in served["messages"] if m["role"] == "assistant"]

    assert assistant_content() == ["ordering payload"]
    set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD)
    assert assistant_content() == [""]
    erase_finalization_payload(commit["finalization_id"], reason="erase")
    assert assistant_content() == []
    # No unauthorized reversal out of ERASED.
    assert not set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD)
    assert not set_availability(commit["finalization_id"], AVAILABILITY_AVAILABLE)
    assert assistant_content() == []
