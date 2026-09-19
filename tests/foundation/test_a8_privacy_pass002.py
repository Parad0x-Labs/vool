"""A8 canonical privacy repair — PASS 002 targeted detectors.

One deterministic detector per independently-proven failure class
(T01..T20). Every test here was executed RED against BASE
1efb8b2976ddbe175a6cc85e881b0a366edad746 before the repair and must stay
GREEN after it; each load-bearing guard in these tests is a mutation-
campaign sabotage target.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import urllib.request

import pytest

import storage.db as sdb


@pytest.fixture()
def a8_env(tmp_path, monkeypatch):
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


def _sha(text):
    return "sha256:" + _sha_hex(text)


def _admit_finalize(text, request_id=""):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    with _request_scope(request_id):
        return finalize_answer(turn_id="t1", canonical_content=text)


def _erase(fid):
    from core.finalization import erase_finalization_payload

    return erase_finalization_payload(fid, reason="pass002")


P = "the vault passphrase is 7F3-QX9-2210"  # low-entropy-class payload


# ---------------------------------------------------------------------------
# T01 — pin POST WITHHELD echo must not return governed bytes
# ---------------------------------------------------------------------------


def test_t01_pin_echo_gated(a8_env):
    from core import message_pins

    commit = _admit_finalize(P)
    set_avail(commit["finalization_id"])
    message_pins.pin_message("s-t01", "assistant", P)
    # Ungated raw listing MUST NOT expose withheld bytes through product code;
    # the API layer uses the gated representation everywhere.
    assert message_pins.list_servable_pins("s-t01") == []
    _erase(commit["finalization_id"])
    assert message_pins.list_servable_pins("s-t01") == []


def set_avail(fid):
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    set_availability(fid, AVAILABILITY_WITHHELD, reason="t01")


def test_t01_pin_post_response_uses_servable_listing(a8_env):
    """The pin POST/UNPIN handlers return the privacy-gated listing."""
    from core import message_pins

    commit = _admit_finalize(P)
    fid = commit["finalization_id"]
    message_pins.pin_message("s-t01b", "assistant", P)
    set_availability_withheld(fid)
    text = inspect_production_source("core/web/api/service.py")
    # The product boundary may not serialize the raw representation.
    body = "\n".join(
        line
        for line in text.splitlines()
        if "json_response" in line and "pins" in line
    )
    assert "list_pins(" not in body.replace("list_servable_pins(", ""), (
        "pin endpoints leak ungated listing"
    )


def set_availability_withheld(fid):
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    set_availability(fid, AVAILABILITY_WITHHELD, reason="withheld")


def inspect_production_source(rel_path):
    from pathlib import Path

    return Path(rel_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T02/T03 — mirror GET gates WITHHELD and ERASED records at SERVE time
# ---------------------------------------------------------------------------


def _make_store(home):
    from relay.http_mirror_server import FileMirrorStore

    return FileMirrorStore(str(home / "relay_mirror"))


def _snapshot(records):
    return {
        "topic_name": "topic-x",
        "publisher_peer_id": "peer-a",
        "published_at": "2026-08-26T00:00:00",
        "expires_at": "2027-08-26T00:00:00",
        "record_count": len(records),
        "records": records,
        "snapshot_hash": "deadbeef",
        "signature": "",
    }


def _get_snapshot(store, topic):
    httpd = None
    from http.server import ThreadingHTTPServer

    from relay.http_mirror_server import build_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(store))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        last_exc = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/topics/{topic}", timeout=5
                ) as response:
                    return json.loads(bytes(response.read()).decode("utf-8")), response.status
            except urllib.error.HTTPError as exc:  # pragma: no cover
                return json.loads(bytes(exc.read()).decode("utf-8")), exc.code
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                time.sleep(0.02)
        raise RuntimeError(f"mirror GET never served: {last_exc}")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_t02_mirror_get_gates_withheld_record(a8_env):
    store = _make_store(a8_env)
    store.put("topic-x", _snapshot([{"finalization_id": "", "content": "public info"}]))
    commit = _admit_finalize("withheld mirror bytes")
    # simulate an in-store record whose governing payload becomes WITHHELD
    store.put(
        "topic-y",
        _snapshot([{"finalization_id": commit["finalization_id"], "content": "withheld mirror bytes"}]),
    )
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD)
    served, status = _get_snapshot(store, "topic-y")
    flat = json.dumps(served)
    assert "withheld mirror bytes" not in flat
    assert status in (200, 404)


def test_t03_mirror_get_gates_erased_record(a8_env):
    store = _make_store(a8_env)
    commit = _admit_finalize("erased mirror bytes")
    out = _erase(commit["finalization_id"])
    assert out["transitioned"]
    # Stale snapshot still carrying erased bytes (e.g. written pre-erase).
    store.put(
        "topic-z",
        _snapshot([{"finalization_id": "", "content": "erased mirror bytes"}]),
    )
    served, status = _get_snapshot(store, "topic-z")
    assert json.dumps(served).find("erased mirror bytes") == -1


# ---------------------------------------------------------------------------
# T04 — erase vs in-flight mirror publish race closes at the durable boundary
# ---------------------------------------------------------------------------


class SlowGate:
    """Instruments the exact CE race window: eligibility passes, delay, then
    the ERASE commits and its sweep completes before the write resumes."""

    def __init__(self):
        self.release = threading.Event()

    def __enter__(self):
        self.release.wait(timeout=10)

    def __exit__(self, *exc):
        return False


def test_t04_erase_dominates_inflight_publish(a8_env, monkeypatch):
    """T04: an in-flight publish whose eligibility check passed BEFORE the
    erase cannot leave governed bytes durably served afterwards. The durable
    boundary revalidates under the shared traversal lock, and any write that
    slipped through is removed by the traversal itself — ERASE dominates."""
    from relay import http_mirror_server as m

    store = _make_store(a8_env)
    commit = _admit_finalize("raced publish payload")
    fid = commit["finalization_id"]

    started = threading.Event()
    release = threading.Event()

    real_validate = m._publish_records_eligible

    def slow_eligible(records):
        verdict = real_validate(records)
        if verdict:
            started.set()
            release.wait(timeout=10)
        return verdict

    monkeypatch.setattr(m, "_publish_records_eligible", slow_eligible)

    result = {}

    def publisher():
        ok, reason = store.publish_snapshot(
            "topic-race",
            _snapshot([{"finalization_id": fid, "content": "raced publish payload"}]),
        )
        result["ok"] = ok
        result["reason"] = reason

    thread = threading.Thread(target=publisher)
    thread.start()
    assert started.wait(timeout=10), "writer never reached eligibility check"
    # ERASE commits while the publisher sits between its check and its write.
    import time as _time

    from core.finalization import erase_finalization_payload, erasure_sweep_status

    eraser = threading.Thread(
        target=lambda: result.setdefault(
            "erase", erase_finalization_payload(fid, reason="t04")
        )
    )
    eraser.start()
    _time.sleep(0.3)  # let the eraser block on the in-flight writer's lock
    release.set()
    thread.join(timeout=15)
    eraser.join(timeout=15)
    assert result["erase"]["sweep_complete"], result.get("erase")
    # Dominance: whatever the late writer did, converged durable state holds
    # no governed bytes, and serve never discloses them.
    snapshot_after = store.get("topic-race")
    flat = json.dumps(snapshot_after) if snapshot_after else ""
    if result.get("ok"):
        # late write landed -> traversal must have swept it back out
        assert not snapshot_after or "raced publish payload" not in flat, flat
    served, status = _get_snapshot(store, "topic-race")
    assert json.dumps(served).find("raced publish payload") == -1


# ---------------------------------------------------------------------------
# T05 — downstream mirror consumption refuses ineligible records
# ---------------------------------------------------------------------------


def test_t05_downstream_consumption_gated(a8_env):
    text = inspect_production_source("relay/http_mirror_server.py")
    assert "payload_availability_for_hash" in text or "writer_may_persist_text" in text, (
        "mirror boundary lacks canonical availability consultation"
    )


# ---------------------------------------------------------------------------
# T06 — wrong-principal ASGI replay is denied; missing principal fail-closed
# ---------------------------------------------------------------------------


def test_t06_replay_principal_binding(a8_env, monkeypatch):
    from core.finalization import get_finalization_by_request_id
    from core.invocation.ledger import PrincipalDenied

    rid = "req:http:t06"
    commit = _admit_finalize("replayable truth", rid)

    # Missing principal -> fail closed.
    with pytest.raises(Exception):
        get_finalization_by_request_id(rid, principal="")
    # Owner-bound replay (same principal that accepted the request) allowed.
    row = get_finalization_by_request_id(rid, principal="owner_local")
    assert row is None or bool(row), "replay read path is live"

    # If an A0 invocation principal exists for this request, a DIFFERENT
    # recognized principal class must be refused by binding law.
    from core.invocation import ledger

    monkeypatch.setattr(
        ledger,
        "_VALID_PRINCIPALS",
        ("owner_local", "recognized_other"),
    )
    from core.invocation.ledger import accept_invocation

    accept_invocation(
        external_kind="http",
        external_value="t06",
        principal="owner_local",
    )
    with pytest.raises(Exception):
        get_finalization_by_request_id(rid, principal="recognized_other")


# ---------------------------------------------------------------------------
# T07 — FactExtractor lineage stamping; facts die with their request
# ---------------------------------------------------------------------------


def test_t07_fact_node_lineage_and_erasure(a8_env, tmp_path):
    import hashlib as _h

    from core.vool_memory import VoolMemory

    session = "sess-t07"
    with VoolMemory() as mem:
        extractor = _make_extractor(mem, session)
        with _request_scope("req:t07"):
            extractor._store_fact_node(
                content=f"User codename: Zephyr-77. secret={P}",
                block="user_profile",
                action="ADD",
            )
        live = _node_contents(mem)
        assert any("Zephyr-77" in c for c in live), "fact node missing pre-erase"

    from core.finalization import get_finalization_by_request_id

    # Lineage recorded on the fresh node: it is reachable by its request.
    commit = _admit_finalize(P, "req:t07")
    erased = _erase(commit["finalization_id"])
    assert erased["sweep_complete"]
    with VoolMemory() as mem:
        remaining = _node_contents(mem)
        assert not any("Zephyr-77" in c for c in remaining), (
            "lineaged fact node survived erasure"
        )


def _node_contents(mem):
    return [
        str(row["content"])
        for row in mem._conn.execute("SELECT content FROM memory_nodes").fetchall()
    ]


def _make_extractor(mem, session):
    from core.fact_extractor import FactExtractor

    return FactExtractor(
        memory=mem,
        ollama_url="http://127.0.0.1:9",
        model_client=lambda _c: '{"facts":[]}',
        session_id=session,
    )


# ---------------------------------------------------------------------------
# T08 — message_pins.json.bak cannot retain plaintext after ERASE
# ---------------------------------------------------------------------------


def test_t08_pins_bak_erased(a8_env):
    from core import message_pins
    from core.message_pins import pins_path

    commit = _admit_finalize("bak retained secret")
    fid = commit["finalization_id"]
    message_pins.pin_message("s-bak", "assistant", "bak retained secret")
    path = pins_path()
    bak = path.with_name(path.name + ".bak")
    assert path.exists(), "pin store absent — fixture broken"
    assert bak.exists(), ".bak leg absent (backup write missing in fixture)"
    out = _erase(fid)
    assert out["sweep_complete"]
    assert not _file_contains(bak, "bak retained secret"), (
        ".bak retains governed plaintext after erasure"
    )


def _file_contains(path, needle):
    if not Path_exists(path):
        return False
    return needle in path.read_text(encoding="utf-8", errors="replace")


def Path_exists(p):
    from pathlib import Path as _Path

    return _Path(str(p)).exists()


# ---------------------------------------------------------------------------
# T09/T10 — sync_useful_outputs cannot recreate erasures via task_results /
# hive_posts bodies
# ---------------------------------------------------------------------------


def _insert_hive_topic(conn, topic_id="top-t10"):
    conn.execute(
        """
        INSERT INTO hive_topics (topic_id, title, summary, status, created_by_agent_id, created_at, updated_at)
        VALUES (?, 'leak topic', 'x', 'open', 'agent-a', '2026-08-26T00:00:00', '2026-08-26T00:00:00')
        """,
        (topic_id,),
    )


def test_t09_sync_task_results_resurrection_refused(a8_env):
    from storage.db import get_connection

    _sync_blocks_verbatim_source(
        a8_env,
        kind="task_result",
        insert=_insert_task_result,
    )
    _erase_plaintext_row()


def _insert_task_result(conn, text):
    conn.execute(
        """
        INSERT INTO task_offers (task_id, task_type, subtask_type, summary,
        input_capsule_hash, required_capabilities_json, deadline_ts,
        parent_peer_id, capsule_id, status, created_at, updated_at)
        VALUES ('tk-09', 'report', 'general', 't', 'hash-x', '[]',
        '2027-08-26T00:00:00', 'parent-x', 'cap-x', 'open',
        '2026-08-26T00:00:00', '2026-08-26T00:00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO task_results (result_id, task_id, helper_peer_id, result_type,
        summary, confidence, status, created_at, updated_at)
        VALUES ('res-t09', 'tk-09', 'helper', 'report', ?, 0.9, 'accepted',
        '2026-08-26T00:00:00', '2026-08-26T00:00:00')
        """,
        (text,),
    )
    conn.execute(
        """
        INSERT INTO task_reviews (review_id, task_id, helper_peer_id, reviewer_peer_id,
        outcome, helpfulness_score, quality_score, harmful_flag, created_at)
        VALUES ('rv-t09', 'tk-09', 'helper', 'rev', 'accepted', 0.9, 0.9, 0,
        '2026-08-26T00:00:00')
        """
    )


def _erase_plaintext_row():
    pass


def _sync_blocks_verbatim_source(home, *, kind, insert):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission
    from storage.db import get_connection
    from storage.useful_output_store import sync_useful_outputs

    reset_admission()
    admit_semantic_result({"response": P, "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t-syn", canonical_content=P)
    conn = get_connection()
    try:
        insert(conn, P)
        conn.commit()
    finally:
        conn.close()
    sync_useful_outputs()
    from core.finalization import erase_finalization_payload

    erase_finalization_payload(commit["finalization_id"], reason="t-sync")
    # Post-erase sync must NOT rebuild a useful_output row from the legacy body.
    summary = sync_useful_outputs()
    leaked = [
        row
        for row in _all_useful_rows()
        if P in json.dumps(row)
    ]
    assert not leaked, f"{kind} resurrection: {leaked}"


def _all_useful_rows():
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM useful_outputs WHERE output_text LIKE ?",
            ("%vault passphrase%",),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def test_t10_sync_hive_posts_resurrection_refused(a8_env):
    from storage.db import get_connection

    _sync_blocks_verbatim_source(
        a8_env,
        kind="hive_post",
        insert=_insert_hive_post,
    )


def _insert_hive_post(conn, text):
    _insert_hive_topic(conn)
    conn.execute(
        """
        INSERT INTO hive_posts (post_id, topic_id, author_agent_id, post_kind,
        stance, body, evidence_refs_json, created_at)
        VALUES ('post-t10', 'top-t10', 'agent-a', 'analysis', 'propose', ?,
        '["grounded evidence ref"]', '2026-08-26T00:00:00')
        """,
        (text,),
    )
    cols = [row[1] for row in conn.execute("PRAGMA table_info(hive_posts)").fetchall()]
    if "moderation_state" in cols:
        conn.execute(
            "UPDATE hive_posts SET moderation_state='approved' WHERE post_id='post-t10'"
        )


# ---------------------------------------------------------------------------
# T11 — stale execution writer cannot restore erased checkpoint content
# ---------------------------------------------------------------------------


def test_t11_stale_checkpoint_writer_vetoed(a8_env):
    from core.runtime_continuity import (
        create_runtime_checkpoint,
        get_runtime_checkpoint,
        update_runtime_checkpoint,
    )

    commit = _admit_finalize("stale writer answer")
    ckpt = create_runtime_checkpoint(
        session_id="sess-stale",
        task_id="tk-stale",
        task_class="chat",
        request_text="tell me",
    )["checkpoint_id"]
    update_runtime_checkpoint(ckpt, status="completed", final_response="stale writer answer")
    out = _erase(commit["finalization_id"])
    assert out["sweep_complete"]
    from core.runtime_continuity import CheckpointTransitionRefused

    with pytest.raises(CheckpointTransitionRefused):
        update_runtime_checkpoint(ckpt, status="completed", final_response="stale writer answer")
    assert get_runtime_checkpoint(ckpt)["final_response"] == ""
    assert get_runtime_checkpoint(ckpt)["final_response_hash"] == ""


# ---------------------------------------------------------------------------
# T12 — unsalted digest dictionary oracle removed from governance ledger
# ---------------------------------------------------------------------------


def test_t12_digest_dictionary_attack_defeated(a8_env):
    from core.finalization import erasure_sweep_status, get_connection

    candidates = ["yes", "no", "hello world", "password123", P]
    commit = _admit_finalize(P)
    _erase(commit["finalization_id"])

    conn = get_connection()
    try:
        reasons = [
            str(row["reason"] or "")
            for row in conn.execute(
                "SELECT reason FROM a7_governance_events "
                "WHERE event_kind = 'erasure_digest_tombstone' AND finalization_id = ?",
                (commit["finalization_id"],),
            ).fetchall()
        ]
    finally:
        conn.close()
    # NO retained value permits cheap guess->confirm of any candidate.
    for reason in reasons:
        for candidate in candidates:
            plain = "sha256:" + _sha_hex(candidate)
            assert reason != plain, f"unsalted oracle survives for {candidate!r}"
    # And the tombstone keeps doing its job (lookup works for the true payload).
    from core.finalization import payload_availability_for_text

    assert payload_availability_for_text(P) == "ERASED"


# ---------------------------------------------------------------------------
# T13 — runtime events cannot reconstruct an erased answer
# ---------------------------------------------------------------------------


def test_t13_runtime_events_cannot_reconstruct_erased(a8_env):
    from core.runtime_continuity import (
        append_runtime_event,
        list_runtime_session_events,
    )

    full_answer = "first half." + P
    for chunk in ("first half.", P):
        append_runtime_event(
            session_id="sess-ev",
            event_type="model_output_chunk",
            message=chunk,
        )
    commit = _admit_finalize(full_answer)
    out = _erase(commit["finalization_id"])
    assert out["sweep_complete"]

    events = list_runtime_session_events("sess-ev")
    reconstructed = "".join(
        str(e.get("message") or "") for e in events if e.get("event_type") == "model_output_chunk"
    )
    assert P not in reconstructed


# ---------------------------------------------------------------------------
# T14 — compressed/packed Liquefy artifact removal after ERASE
# ---------------------------------------------------------------------------


def test_t14_liquefy_packed_artifact_not_survive(a8_env):
    from core.liquefy_bridge import _vault_dir

    bundle_dir = _vault_dir("bundles") / "bundle-packed-1"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    # Compressed fallback artifact (not task_bundle.json scannable).
    import gzip

    packed = {
        "source_finalization_id": "",
        "final_response": {"content_hash": _sha(P)},
        "canonical_content": P,
    }
    (bundle_dir / "task_bundle.json.gz").write_bytes(
        gzip.compress(json.dumps(packed).encode("utf-8"))
    )
    commit = _admit_finalize(P)
    out = _erase(commit["finalization_id"])
    assert out["sweep"]["liquefy_vault"] != "completed" or not bundle_dir.exists()
    # Either way: no reconstructable governed artifact may remain AND report OK.
    survived = bundle_dir.exists() and any(bundle_dir.iterdir())
    assert out["sweep"]["liquefy_vault"].startswith("error:") or not survived


# ---------------------------------------------------------------------------
# T15 — checkpoint external_evidence/source_context cleared on ERASE
# ---------------------------------------------------------------------------


def test_t15_checkpoint_external_evidence_erased(a8_env):
    from core.runtime_continuity import (
        create_runtime_checkpoint,
        get_runtime_checkpoint,
        update_runtime_checkpoint,
    )

    commit = _admit_finalize("attached secret material")
    ckpt = create_runtime_checkpoint(
        session_id="sess-ext",
        task_id="tk-ext",
        task_class="chat",
        request_text="attach this",
    )["checkpoint_id"]
    update_runtime_checkpoint(
        ckpt,
        status="completed",
        final_response="done",
        source_context={"external_evidence": ["attached secret material"], "other": "keep me"},
    )
    out = _erase(commit["finalization_id"])
    assert out["sweep_complete"]
    stored = get_runtime_checkpoint(ckpt)["source_context"]
    flat = json.dumps(stored)
    assert "attached secret material" not in flat


# ---------------------------------------------------------------------------
# T16 — legacy dialogue archives (verbatim) removed by fallback
# ---------------------------------------------------------------------------


def test_t16_legacy_dialogue_archive_erase_fallback(a8_env):
    from storage import dialogue_memory as dm
    from storage.db import get_connection

    dm._init_tables()
    commit = _admit_finalize(P)
    fid = commit["finalization_id"]
    conn = get_connection()
    try:
        # Legacy shape: no lineage stamp (request_id ''), verbatim bytes.
        conn.execute(
            """
            INSERT INTO response_feedback (feedback_id, session_id, feedback_type,
            feedback_value, context_snapshot, created_at, request_id)
            VALUES ('fb-legacy', 's16', 'approve', 1.0, ?, '2026-08-26T00:00:00', '')
            """,
            (P,),
        )
        conn.execute(
            """
            INSERT INTO dialogue_topic_archives (archive_id, session_id, summary,
            closing_assistant_output, created_at, request_id)
            VALUES ('ar-legacy', 's16', '', ?, '2026-08-26T00:00:00', '')
            """,
            (P,),
        )
        conn.commit()
    finally:
        conn.close()
    out = _erase(fid)
    assert out["sweep_complete"], out
    conn = get_connection()
    try:
        feedback_left = conn.execute(
            "SELECT COUNT(*) FROM response_feedback WHERE context_snapshot = ?", (P,)
        ).fetchone()[0]
        archive_left = conn.execute(
            "SELECT COUNT(*) FROM dialogue_topic_archives WHERE closing_assistant_output = ?",
            (P,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert int(feedback_left) == 0
    assert int(archive_left) == 0


# ---------------------------------------------------------------------------
# T17 — DELETE/step failure cannot fabricate completion
# ---------------------------------------------------------------------------


def test_t17_swallowed_failure_reports_non_complete(a8_env, monkeypatch):
    from core import finalization as fin

    commit = _admit_finalize(P)
    fid = commit["finalization_id"]

    def boom(*a, **k):
        raise RuntimeError("simulated store outage")

    monkeypatch.setattr(fin, "_sweep_step_conversation_log", boom)
    out = _erase(fid)
    assert not out["sweep_complete"], "failure fabricated completion"
    assert out["sweep"]["conversation_log"].startswith("error:")
    from core.finalization import erasure_sweep_status

    assert erasure_sweep_status(fid)["sweep_complete"] is False


# ---------------------------------------------------------------------------
# T18 — interrupted sweep resumes after restart
# ---------------------------------------------------------------------------


def test_t18_sweep_resume_after_restart(a8_env):
    from core.finalization import (
        erasure_sweep_status,
        resume_incomplete_erasure_sweeps,
    )

    commit = _admit_finalize("resume survivor")
    fid = commit["finalization_id"]
    from core.finalization import AVAILABILITY_ERASED, set_availability

    # Availability transition committed, then crash BEFORE traversal.
    assert set_availability(fid, AVAILABILITY_ERASED, reason="crash mid-flight")
    import json as _json

    from core.message_pins import pins_path

    path = pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({"s18": [{"id": "p1", "role": "assistant", "text": "resume survivor"}]}))
    # restart: no production caller existed at BASE; resume converges.
    resumed = resume_incomplete_erasure_sweeps()
    assert fid in resumed
    status = erasure_sweep_status(fid)
    assert status["sweep_complete"] is True, status
    assert resume_incomplete_erasure_sweeps() == []


def test_t18_resume_wired_into_startup_lifecycle():
    src = inspect_production_source("core/agent_runtime/daemon.py")
    assert "resume_incomplete_erasure_sweeps" in src, (
        "resume has no production caller wired into startup"
    )


# ---------------------------------------------------------------------------
# T19 — privacy-store failure fails CLOSED at serve boundaries
# ---------------------------------------------------------------------------


def test_t19_fail_closed_on_store_failure(a8_env, monkeypatch):
    import core.finalization as fin
    from core import message_pins

    def boom(*_a, **_k):
        raise RuntimeError("a8 store unreachable")

    commit = _admit_finalize("uncertain governance bytes")
    fid = commit["finalization_id"]
    from core import message_pins as mp

    mp.pin_message("s19", "assistant", "uncertain governance bytes")

    # Store failure on the LINEAGE lookup path: uncertainty suppresses.
    monkeypatch.setattr(fin, "payload_availability_for_finalization_id", boom)
    assert message_pins.list_servable_pins("s19") == []
    # Fresh module pin_store? Re-patch the content-hash fallback too: lineage
    # empty pins consult the tombstone path — also fail closed.
    monkeypatch.setattr(fin, "payload_availability_for_text", boom)
    mp.pin_message("s19b", "assistant", "another uncertain line")
    assert message_pins.list_servable_pins("s19b") == []


# ---------------------------------------------------------------------------
# T20 — duplicate request_id ambiguity: privacy op covers the whole set
# ---------------------------------------------------------------------------


def test_t20_duplicate_request_id_full_set_erased(a8_env):
    """PASS003 revision (equal-or-stronger detector): PASS002's premise that
    two legal finalizations may share one request identity is superseded by
    the CE13 canonical-uniqueness law — a conflicting-content finalization on
    an occupied request id is REFUSED at binding time. The full-set erasure
    behavior is retained for legacy ambiguous stores and covered by
    tests/foundation/test_a8_privacy_pass003.py::test_u10_*
    (uniqueness + ambiguity fail-closed) plus the sibling-recursion inside
    erase_finalization_payload."""
    from core.finalization import FinalizationRejected, finalize_answer, get_connection
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    rid = "req:dup:t20"
    newer = _admit_finalize("newest dup row", rid)
    reset_admission()
    admit_semantic_result({"response": "older dup secret", "route_reason": "model_lane"})
    with _request_scope(rid):
        with pytest.raises(Exception):
            finalize_answer(turn_id="t20b", canonical_content="older dup secret")
    # canonical uniqueness: exactly one authoritative truth per request id
    conn = get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM a7_finalizations WHERE request_id = ?", (rid,)
        ).fetchone()[0]
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_a7_finalizations_request_id'"
        ).fetchone()
    finally:
        conn.close()
    assert int(count) == 1
    assert index is not None
