"""A8 final privacy freeze — independent SERVED reproof on real HTTP.

Base a6c8e3c4 carries A8 pass-001..004 (all 72 dedicated detectors green).
This suite is the independent reproof the build order demands: every
governed-payload surface is driven over REAL sockets (main API + meet
network + mirror) with isolated state, and every mandatory freeze
invariant is asserted against served bytes — not store internals.

Detector map (freeze checklist):
  F01  hive posts GET never serves WITHHELD governed bytes (meet HTTP)
  F02  hive posts GET never serves WITHHELD derivative quotes (>=24 run)
  F03  control-plane status never serves WITHHELD task-result summaries
  F04  memory entries GET never serves ERASED bytes in the crash window
       (sweep step failed; resume not yet run) — serve gate must hold
  F05  chat history serves neither WITHHELD nor ERASED bytes (confirm)
  F06  runtime events + receipts + sessions stay clean (confirm)
  F07  task recovery checkpoints stay clean (confirm)
  F08  replay: principal-scoped + UNAVAILABLE_BY_POLICY terminal (confirm)
  F09  model context hydration excludes governed bytes (confirm)
  F10  duplicate request IDs cannot preserve an older payload (confirm)
  F11  no unsalted digest oracle on any served surface after ERASE
  F12  incomplete erasure resumes safely after restart and completes
  F13  terminal privacy outcome stays terminal across restart
  F14  late writers cannot resurrect governed bytes through meet (hive
       post create after WITHHOLD must refuse the bytes)

RED expectations at base: F01–F04, F14 (meet/task/memory surfaces carry
no serve-time gate). F05–F13 confirm genuinely-present invariants.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import storage.db as sdb

P_EXACT = "the vault passphrase is 7F3-QX9-2210"
P_QUOTE = (
    "Analysis follows: " + P_EXACT + " — treat as the canonical finding."
)  # derivative: embeds the exact governed payload
SECRET_RUN = "7F3-QX9-2210"  # >=? no — 12 chars; the >=24 law uses P_EXACT itself


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


# --- governed payload minting (production seams, pass-002 vocabulary) -------

import contextlib


@contextlib.contextmanager
def _request_scope(request_id):
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    token = set_request_context(request_id)
    try:
        yield
    finally:
        _CURRENT_REQUEST_ID.reset(token)


_turn_seq = [0]


def _admit_finalize(text, request_id=""):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    _turn_seq[0] += 1
    turn = f"t{_turn_seq[0]}"
    admit_semantic_result({"response": text, "route_reason": "model_lane"}, turn_id=turn)
    with _request_scope(request_id):
        return finalize_answer(turn_id=turn, canonical_content=text)


def _withhold(fid):
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="freeze")


def _erase(fid):
    from core.finalization import erase_finalization_payload

    return erase_finalization_payload(fid, reason="freeze")


# --- real HTTP servers -------------------------------------------------------

_uid = [0]


def _next_port_token():
    _uid[0] += 1
    return _uid[0]


class _MainAPIHandler(BaseHTTPRequestHandler):
    """The production VoolAPIHandler shape, driving the real dispatch."""

    def _runtime(self):
        from core.web.api.runtime import RuntimeServices

        return RuntimeServices(display_name="VOOL")

    def do_GET(self) -> None:
        from core.web.api.service import dispatch_get

        parsed = urlparse(self.path)
        response = dispatch_get(
            path=parsed.path,
            query=parse_qs(parsed.query),
            runtime=self._runtime(),
            model_name="vool",
            client_host="127.0.0.1",
        )
        self._write_response(response)

    def do_POST(self) -> None:
        from core.web.api.service import dispatch_post

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        response = dispatch_post(
            path=urlparse(self.path).path,
            body=body,
            headers=dict(self.headers.items()),
            runtime=self._runtime(),
            model_name="vool",
            workspace_root_provider=lambda: None,
            client_host="127.0.0.1",
        )
        self._write_response(response)

    def _write_response(self, response) -> None:
        self.send_response(int(response.status))
        self.send_header("Content-Type", str(response.content_type))
        for header, value in dict(response.headers or {}).items():
            self.send_header(str(header), str(value))
        payload = response.body or b""
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


class _MeetHandler(BaseHTTPRequestHandler):
    """Real-socket meet dispatch (the public hive network surface)."""

    def do_GET(self) -> None:
        from apps.meet_and_greet_server import dispatch_request as meet_dispatch

        parsed = urlparse(self.path)
        status, payload = meet_dispatch(
            "GET", parsed.path, parse_qs(parsed.query), {}, None
        )
        self._write(status, payload)

    def do_POST(self) -> None:
        from apps.meet_and_greet_server import dispatch_request as meet_dispatch

        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        status, payload = meet_dispatch("POST", parsed.path, {}, body, None)
        self._write(status, payload)

    def _write(self, status, payload) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        pass


@contextlib.contextmanager
def _served(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(base: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(base + path, headers={"Host": f"127.0.0.1:{base.rsplit(':', 1)[1]}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _post(base: str, path: str, body: dict) -> tuple[int, str]:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


# --- hive helpers ------------------------------------------------------------

AGENT = "agent-freeze-000000000001"
TOPIC_SUMMARY = "Freeze reproof topic summary"


def _create_topic(meet_base: str) -> str:
    status, body = _post(
        meet_base,
        "/v1/hive/topics",
        {
            "created_by_agent_id": AGENT,
            "title": "A8 freeze reproof topic",
            "summary": TOPIC_SUMMARY,
        },
    )
    assert status == 200, body
    payload = json.loads(body)
    result = payload.get("result") if isinstance(payload, dict) else None
    return str((result or {}).get("topic_id") or "")


# ---------------------------------------------------------------------------
# F01 — WITHHELD exact-copy hive post body must never serve (meet HTTP)
# ---------------------------------------------------------------------------


def test_f01_hive_post_withheld_exact_bytes_never_served(a8_env):
    commit = _admit_finalize(P_EXACT, request_id="freeze-f01")
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        status, body = _post(
            meet,
            "/v1/hive/posts",
            {"topic_id": topic, "author_agent_id": AGENT, "body": P_EXACT},
        )
        assert status == 200, body
        # available now: served is fine
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200
        assert P_EXACT in served
        # WITHHOLD -> the same GET must not disclose
        _withhold(commit["finalization_id"])
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200
        assert P_EXACT not in served, "F01: WITHHELD bytes served on meet hive posts GET"


# ---------------------------------------------------------------------------
# F02 — WITHHELD derivative quote (payload embedded in a longer body)
# ---------------------------------------------------------------------------


def test_f02_hive_post_withheld_derivative_never_served(a8_env):
    commit = _admit_finalize(P_EXACT, request_id="freeze-f02")
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        _status, _body = _post(
            meet,
            "/v1/hive/posts",
            {"topic_id": topic, "author_agent_id": AGENT, "body": P_QUOTE},
        )
        assert _status == 200, _body
        _withhold(commit["finalization_id"])
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200
        assert P_EXACT not in served, "F02: WITHHELD derivative quote served on meet"


# ---------------------------------------------------------------------------
# F03 — WITHHELD task-result summary via control-plane status
# ---------------------------------------------------------------------------


def _plant_task_result(summary: str, result_id: str = "res-freeze-1") -> None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json,
                max_helpers, priority, deadline_ts, status, created_at, updated_at
            ) VALUES ('task-freeze-1', 'peer-freeze-00000000001', 'cap-1', 'research',
                      'summary', 'offer summary', 'h', '[]', '{}', 1, 'normal',
                      '2026-09-03T00:00:00', 'open', '2026-09-02T00:00:00', '2026-09-02T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO task_results (
                result_id, task_id, helper_peer_id, result_type, summary, result_hash,
                confidence, evidence_json, abstract_steps_json, risk_flags_json,
                status, created_at, updated_at
            ) VALUES (?, 'task-freeze-1', 'helper-freeze-0000000001', 'analysis', ?, '',
                      0.9, '[]', '[]', '[]', 'submitted', '2026-09-02T00:00:00', '2026-09-02T00:00:00')
            """,
            (result_id, summary),
        )
        conn.commit()
    finally:
        conn.close()


def _plant_task_offer_summary(summary: str) -> None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json,
                max_helpers, priority, deadline_ts, status, created_at, updated_at
            ) VALUES ('task-freeze-3', 'peer-freeze-00000000001', 'cap-1', 'research',
                      'summary', ?, 'h', '[]', '{}', 1, 'normal',
                      '2026-09-03T00:00:00', 'open', '2026-09-02T00:00:00', '2026-09-02T00:00:00')
            """,
            (summary,),
        )
        conn.commit()
    finally:
        conn.close()


def test_f03_task_offer_summary_withheld_never_served(a8_env):
    # the served task surface on the meet network is the OFFER set
    # (/v1/tasks/queue, /v1/tasks/{id}); a governed answer quoted in an
    # offer brief must never serve after WITHHOLD
    _plant_task_offer_summary(P_QUOTE)
    commit = _admit_finalize(P_EXACT, request_id="freeze-f03")
    _withhold(commit["finalization_id"])
    with _served(_MeetHandler) as meet:
        for path in ("/v1/tasks/queue", "/v1/tasks/task-freeze-3"):
            status, served = _get(meet, path)
            assert status == 200, path
            assert P_EXACT not in served, f"F03: WITHHELD offer summary served on {path}"


# ---------------------------------------------------------------------------
# F04 — ERASED bytes in the crash window: memory entries must be gated at
#       serve time until the resume sweep completes
# ---------------------------------------------------------------------------


P_MEMO = "PROJECT ATLAS RENAME DECISION MEMO 8842 FINALIZED"


def test_f04_memory_entries_erased_crash_window_gated(a8_env, monkeypatch):
    commit = _admit_finalize(P_MEMO, request_id="freeze-f04")
    from core.context_namespace import ensure_chat_namespace
    from core.memory import entries as mem_entries

    chat = "openclaw:freeze-f04"
    ensure_chat_namespace(chat)
    with _request_scope("freeze-f04"):
        ok = mem_entries.record_memory_entry(
            P_MEMO,
            category="fact",
            session_id=chat,
            source="assistant",
            confidence=0.9,
            keywords=None,
            share_scope=None,
            scope="chat",
            authority="confirmed_memory",
            fact_key=None,
            expires_at=None,
            review_after=None,
        )
        assert ok
    # sanity: the row is there and carries lineage (chat policy resolves)
    rows = mem_entries.list_memory_entries(chat_id=chat, limit=50)
    assert any(str(r.get("text") or "") == P_MEMO for r in rows)

    # ERASE with the memory sweep step forced to crash (simulated crash mid-
    # traversal): serve-time must still suppress the row.
    from core import finalization as fin

    def _boom(*_a, **_k):
        raise RuntimeError("simulated crash in memory sweep")

    monkeypatch.setattr(fin, "_sweep_step_memory_jsonl", _boom)
    result = _erase(commit["finalization_id"])
    assert result.get("transitioned") is True
    assert result.get("sweep_complete") is False

    # library lane (model context / memory recall consume this reader)
    rows = mem_entries.list_memory_entries(chat_id=chat, limit=50)
    assert not any(
        str(r.get("text") or "") == P_MEMO for r in rows
    ), "F04a: ERASED bytes readable from memory entries during crash window"
    # served lane
    with _served(_MainAPIHandler) as base:
        status, served = _get(base, "/api/memory/entries?limit=200")
        assert status == 200
        assert P_MEMO not in served, "F04b: ERASED bytes served on memory entries during crash window"

    # resume (restart law): completes the sweep, bytes leave the store
    monkeypatch.undo()
    from core.finalization import resume_incomplete_erasure_sweeps

    resumed = resume_incomplete_erasure_sweeps()
    assert len(resumed) >= 1
    rows = mem_entries.list_memory_entries(chat_id=chat, limit=50)
    assert not any(str(r.get("text") or "") == P_MEMO for r in rows)


# ---------------------------------------------------------------------------
# F05 — chat history: WITHHELD then ERASED never served (confirm present)
# ---------------------------------------------------------------------------


def _seed_history_row(session: str, user_text: str, assistant_text: str) -> None:
    from core.persistent_memory import append_conversation_event

    append_conversation_event(
        session_id=session,
        user_input=user_text,
        assistant_output=assistant_text,
    )


def test_f05_history_withheld_and_erased_never_served(a8_env):
    session = "openclaw:freeze-f05"
    _seed_history_row(session, "what is the passphrase?", P_EXACT)
    commit = _admit_finalize(P_EXACT, request_id="freeze-f05")
    with _served(_MainAPIHandler) as base:
        status, served = _get(base, f"/api/chat/history?session={session}")
        assert status == 200
        assert P_EXACT in served  # AVAILABLE serves (baseline sanity)
        _withhold(commit["finalization_id"])
        status, served = _get(base, f"/api/chat/history?session={session}")
        assert P_EXACT not in served, "F05a: WITHHELD bytes served on history"
        _erase(commit["finalization_id"])
        status, served = _get(base, f"/api/chat/history?session={session}")
        assert P_EXACT not in served, "F05b: ERASED bytes served on history"


# ---------------------------------------------------------------------------
# F06 — runtime events / receipts / sessions stay clean (confirm present)
# ---------------------------------------------------------------------------


def test_f06_runtime_surfaces_withheld_never_served(a8_env):
    from core.runtime_task_events import emit_runtime_event

    session = "openclaw:freeze-f06"
    emit_runtime_event(
        {"session_id": session, "runtime_session_id": session, "cancel_turn_id": "t-f06"},
        event_type="assistant_message",
        message=f"Answer: {P_EXACT}",
        details={"summary": f"Answer: {P_EXACT}"},
    )
    commit = _admit_finalize(P_EXACT, request_id="freeze-f06")
    _withhold(commit["finalization_id"])
    with _served(_MainAPIHandler) as base:
        for path in (
            f"/api/runtime/events?session={session}",
            "/api/runtime/sessions",
            "/api/runtime/receipts",
        ):
            status, served = _get(base, path)
            assert status == 200, path
            assert P_EXACT not in served, f"F06: WITHHELD bytes served on {path}"


# ---------------------------------------------------------------------------
# F07 — task recovery checkpoint stays clean (confirm present)
# ---------------------------------------------------------------------------


def test_f07_task_recovery_checkpoint_gated(a8_env):

    from core.runtime_continuity import _conn

    conn = _conn()
    digest = hashlib.sha256(P_EXACT.encode("utf-8")).hexdigest()
    with conn:
        conn.execute(
            """
            INSERT INTO runtime_checkpoints (
                checkpoint_id, session_id, status, step_count, request_text,
                final_response, final_response_hash, created_at, updated_at
            ) VALUES (?, ?, 'interrupted', 1, 'q', ?, ?, ?, ?)
            """,
            ("ckpt-freeze-1", "openclaw:freeze-f07", P_EXACT, digest, "2026-09-02T00:00:00", "2026-09-02T00:00:00"),
        )
    conn.close()
    commit = _admit_finalize(P_EXACT, request_id="freeze-f07")
    _withhold(commit["finalization_id"])
    with _served(_MainAPIHandler) as base:
        status, served = _post(
            base,
            "/api/task/recovery",
            {"session_id": "openclaw:freeze-f07", "checkpoint_id": "ckpt-freeze-1", "action": "cancel"},
        )
        # Either a clean 200 with suppressed bytes, or the A8 stale-writer
        # fence refusing the transition — both must disclose ZERO bytes.
        assert P_EXACT not in served, "F07: WITHHELD checkpoint bytes served on task recovery"
        if status != 200:
            assert "refused" in served or "error" in served


# ---------------------------------------------------------------------------
# F08 — replay: principal-scoped, terminal UNAVAILABLE_BY_POLICY (confirm)
# ---------------------------------------------------------------------------


def test_f08_replay_principal_scoped_and_terminal(a8_env):
    from core.finalization import REPLAY_UNAVAILABLE_BY_POLICY, replay_finalized_answer
    from core.invocation.ledger import PrincipalDenied

    commit = _admit_finalize(P_EXACT, request_id="freeze-f08")
    # absent / unrecognized principal is refused before any verdict
    with pytest.raises(PrincipalDenied):
        replay_finalized_answer(principal="", semantic_result_id=commit["semantic_result_id"])
    served = replay_finalized_answer(
        principal="owner_local", semantic_result_id=commit["semantic_result_id"]
    )
    assert served["canonical_content"] == P_EXACT
    _erase(commit["finalization_id"])
    served = replay_finalized_answer(
        principal="owner_local", semantic_result_id=commit["semantic_result_id"]
    )
    assert served["replay_outcome"] == REPLAY_UNAVAILABLE_BY_POLICY
    assert served["canonical_content"] == ""
    # terminal: still refused after "restart" (fresh call == fresh process read)
    served2 = replay_finalized_answer(
        principal="owner_local", semantic_result_id=commit["semantic_result_id"]
    )
    assert served2["replay_outcome"] == REPLAY_UNAVAILABLE_BY_POLICY


# ---------------------------------------------------------------------------
# F09 — model context hydration excludes governed bytes (confirm)
# ---------------------------------------------------------------------------


def test_f09_model_context_hydration_excludes_governed(a8_env):
    session = "openclaw:freeze-f09"
    _seed_history_row(session, "what is the passphrase?", P_EXACT)
    commit = _admit_finalize(P_EXACT, request_id="freeze-f09")
    _withhold(commit["finalization_id"])
    from core.persistent_memory import augment_history_from_session_log

    hydrated = augment_history_from_session_log(
        [], session_id=session, user_text="next question please"
    )
    flat = json.dumps(hydrated)
    assert P_EXACT not in flat, "F09: WITHHELD bytes entered model context hydration"


# ---------------------------------------------------------------------------
# F10 — duplicate request IDs cannot preserve an older payload (confirm)
# ---------------------------------------------------------------------------


def test_f10_duplicate_request_ids_both_governed(a8_env):
    from core.finalization import finalize_answer, payload_availability_for_text
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    rid = "dup-freeze-f10"
    newer = _admit_finalize("newer secret payload A8-F10-new", request_id=rid)
    # (a) canonical uniqueness: a conflicting-content finalization on the
    # occupied request id is REFUSED — the ambiguity can never be minted
    from core.final_response_store import FinalizationRejected

    reset_admission()
    admit_semantic_result({"response": "older secret payload A8-F10-old", "route_reason": "model_lane"})
    with _request_scope(rid):
        with pytest.raises(FinalizationRejected):
            finalize_answer(turn_id="t-f10-old", canonical_content="older secret payload A8-F10-old")

    # (b) legacy ambiguous store (pre-uniqueness shape): two AVAILABLE rows
    # share the id; erasing the newest governs the older sibling too
    from core.finalization import get_connection

    # legacy-ambiguity probe (pass003 u10 precedent): simulate an old store
    # that predates the unique index by dropping it, planting the older
    # AVAILABLE row, restoring the index.
    conn = get_connection()
    try:
        conn.execute("DROP INDEX IF EXISTS ux_a7_finalizations_request_id")
        conn.execute(
            """
            INSERT INTO a7_finalizations (
                finalization_id, semantic_result_id, turn_id, content_hash,
                canonical_content, status, request_id, payload_ref, availability, created_at
            ) VALUES ('fid-f10-old', 'sr-f10-old', 't-f10-old', ?, ?,
                      'answer_present', ?, NULL, 'AVAILABLE', '2026-09-02T00:00:00')
            """,
            (
                "sha256:" + hashlib.sha256(b"older secret payload A8-F10-old").hexdigest(),
                "older secret payload A8-F10-old",
                rid,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    session = "openclaw:freeze-f10"
    _seed_history_row(session, "q1", "older secret payload A8-F10-old")
    _seed_history_row(session, "q2", "newer secret payload A8-F10-new")
    result = _erase(newer["finalization_id"])
    assert result.get("transitioned") is True
    # the OLDER row sharing the id must be governed too
    verdict = payload_availability_for_text("older secret payload A8-F10-old")
    assert verdict in ("WITHHELD", "ERASED"), f"F10: older duplicate stayed {verdict}"
    with _served(_MainAPIHandler) as base:
        status, served = _get(base, f"/api/chat/history?session={session}")
        assert status == 200
        assert "older secret payload A8-F10-old" not in served
        assert "newer secret payload A8-F10-new" not in served


# ---------------------------------------------------------------------------
# F11 — no unsalted digest oracle after ERASE on served surfaces
# ---------------------------------------------------------------------------


def test_f11_no_unsalted_digest_oracle_served(a8_env):
    plain_hash = hashlib.sha256(P_EXACT.encode("utf-8")).hexdigest()
    session = "openclaw:freeze-f11"
    _seed_history_row(session, "q", P_EXACT)
    commit = _admit_finalize(P_EXACT, request_id="freeze-f11")
    _erase(commit["finalization_id"])
    with _served(_MainAPIHandler) as base:
        for path in (
            f"/api/chat/history?session={session}",
            "/api/chat/sessions",
            "/api/runtime/receipts",
            "/api/runtime/control-plane/status",
        ):
            status, served = _get(base, path)
            assert status == 200, path
            assert plain_hash not in served, f"F11: unsalted digest oracle served on {path}"
            assert P_EXACT not in served, f"F11: plaintext served on {path}"
    # the governance ledger itself must not retain the unsalted digest
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT reason FROM a7_governance_events WHERE reason LIKE '%' || ? || '%'",
            (plain_hash,),
        ).fetchall()
        assert not rows, "F11: unsalted pre-erase hash retained in governance events"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# F12 — incomplete erasure resumes safely after restart (idempotent, honest)
# ---------------------------------------------------------------------------


def test_f12_resume_completes_and_is_idempotent(a8_env, monkeypatch):
    from core import finalization as fin

    commit = _admit_finalize(P_EXACT, request_id="freeze-f12")
    _plant_task_result(P_EXACT, result_id="res-freeze-f12")

    calls = {"n": 0}
    _orig = fin._sweep_step_task_result_bodies

    def _flaky(content_hash, plaintext=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before task bodies sweep")
        return _orig(content_hash, plaintext)

    monkeypatch.setattr(fin, "_sweep_step_task_result_bodies", _flaky)
    result = _erase(commit["finalization_id"])
    assert result.get("sweep_complete") is False
    # crash window: the row is still in the store
    from storage.db import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT summary FROM task_results WHERE result_id = 'res-freeze-f12'"
    ).fetchone()
    assert row is not None and str(row["summary"] or "") == P_EXACT
    conn.close()

    resumed = fin.resume_incomplete_erasure_sweeps()
    assert len(resumed) >= 1
    conn = get_connection()
    row = conn.execute(
        "SELECT summary FROM task_results WHERE result_id = 'res-freeze-f12'"
    ).fetchone()
    conn.close()
    assert row is None or str(row["summary"] or "") != P_EXACT, "F12: resume did not remove the body"
    # idempotent: a second resume is a no-op, not a failure
    fin.resume_incomplete_erasure_sweeps()


# ---------------------------------------------------------------------------
# F13 — terminal privacy outcome stays terminal (no transition back)
# ---------------------------------------------------------------------------


def test_f13_terminal_privacy_outcome_terminal(a8_env):
    from core.finalization import AVAILABILITY_AVAILABLE, AVAILABILITY_ERASED, set_availability

    commit = _admit_finalize(P_EXACT, request_id="freeze-f13")
    _erase(commit["finalization_id"])
    assert not set_availability(
        commit["finalization_id"], AVAILABILITY_AVAILABLE, reason="un-erase"
    )
    assert not set_availability(
        commit["finalization_id"], AVAILABILITY_ERASED, reason="re-erase"
    )


# ---------------------------------------------------------------------------
# F15 — voolbook feed never serves WITHHELD governed bytes (meet HTTP)
# ---------------------------------------------------------------------------


def _plant_voolbook_post(content: str) -> None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO voolbook_profiles (
                peer_id, handle, canonical_handle, display_name, bio, avatar_seed,
                profile_url, twitter_handle, post_count, claim_count, glory_score,
                status, joined_at, last_active_at, updated_at
            ) VALUES ('peer-f15', 'freezebot', 'freezebot', 'FreezeBot', '', '', '', '',
                      1, 0, 0, 'active', '2026-09-02T00:00:00', '2026-09-02T00:00:00', '2026-09-02T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO voolbook_posts (
                post_id, peer_id, handle, content, post_type, parent_post_id,
                hive_post_id, topic_id, link_url, link_title, upvotes, reply_count,
                status, created_at, updated_at
            ) VALUES ('nb-f15', 'peer-f15', 'freezebot', ?, 'social', NULL, NULL, NULL,
                      '', '', 0, 0, 'active', '2026-09-02T00:00:00', '2026-09-02T00:00:00')
            """,
            (content,),
        )
        conn.commit()
    finally:
        conn.close()


def test_f15_voolbook_feed_withheld_never_served(a8_env):
    _plant_voolbook_post(P_QUOTE)
    commit = _admit_finalize(P_EXACT, request_id="freeze-f15")
    _withhold(commit["finalization_id"])
    with _served(_MeetHandler) as meet:
        status, served = _get(meet, "/v1/voolbook/feed")
        assert status == 200
        assert P_EXACT not in served, "F15: WITHHELD bytes served on voolbook feed"
        # id-addressed governed post reduces to honest 404 (mirror law)
        status, served = _get(meet, "/v1/voolbook/post/nb-f15")
        assert status == 404
        assert P_EXACT not in served, "F15: WITHHELD bytes served on voolbook post"


# ---------------------------------------------------------------------------
# F16 — hive topic summary never serves WITHHELD governed bytes (meet HTTP)
# ---------------------------------------------------------------------------


def test_f16_hive_topic_summary_withheld_never_served(a8_env):
    commit = _admit_finalize(P_EXACT, request_id="freeze-f16")
    with _served(_MeetHandler) as meet:
        status, body = _post(
            meet,
            "/v1/hive/topics",
            {
                "created_by_agent_id": AGENT,
                "title": "A8 freeze reproof topic f16",
                "summary": "Context: " + P_EXACT + " end of context.",
            },
        )
        assert status == 200, body
        topic = str((json.loads(body).get("result") or {}).get("topic_id") or "")
        _withhold(commit["finalization_id"])
        status, served = _get(meet, f"/v1/hive/topics/{topic}")
        assert status == 200
        assert P_EXACT not in served, "F16: WITHHELD bytes served in hive topic summary"


# ---------------------------------------------------------------------------
# F14 — late writers cannot resurrect governed bytes through the meet surface
# ---------------------------------------------------------------------------


def test_f14_hive_post_after_withhold_refuses_governed_bytes(a8_env):
    commit = _admit_finalize(P_EXACT, request_id="freeze-f14")
    _withhold(commit["finalization_id"])
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        status, _body = _post(
            meet,
            "/v1/hive/posts",
            {"topic_id": topic, "author_agent_id": AGENT, "body": P_QUOTE},
        )
        # The write must be refused (or stored in a never-servable state) —
        # never durably served afterwards.
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200
        assert P_EXACT not in served, "F14: late writer resurrected WITHHELD bytes on meet"


_RESTART_PROBE_SOURCE = '"""Fresh-interpreter (new runtime epoch) probe: reads every served store\nagainst the SAME state dir and prints findings as JSON. Asserted by the\nfreeze suite in the parent process."""\nimport json\nimport os\nimport sys\n\nfrom pathlib import Path\n\n# The fresh interpreter must import THIS tree — the shared venv carries an\n# editable install pinned to the main checkout, and script mode would resolve\n# the foreign (possibly ungated) modules instead of the frozen candidate.\nsys.path.insert(0, os.environ["A8_PROBE_ROOT"])\n\nfrom core.runtime_paths import configure_runtime_home\n\n# SAME order as the product fixture: runtime home FIRST (it derives a default\n# db path), then the explicit db override, then continuity.\nconfigure_runtime_home(Path(os.environ["A8_PROBE_HOME"]))\nimport storage.db as sdb\n\nsdb.configure_default_db_path(os.environ["A8_PROBE_DB"])\nfrom core.runtime_continuity import configure_runtime_continuity_db_path\nfrom storage.db import active_default_db_path\n\nconfigure_runtime_continuity_db_path(active_default_db_path())\n\nfindings = {"posts": [], "offers": [], "topics": [], "voolbook": []}\nfrom storage.brain_hive_store import list_recent_posts\nfrom storage.task_offer_store import list_open_task_offers, get_task_offer\nfrom storage.brain_hive_store import get_topic\n\nfor p in list_recent_posts(limit=200):\n    findings["posts"].append(str(p.get("body") or ""))\nfor o in list_open_task_offers(limit=100):\n    findings["offers"].append(str(o.get("summary") or ""))\n    got = get_task_offer(str(o.get("task_id") or ""))\n    if got:\n        findings["offers"].append(str(got.get("summary") or ""))\nif os.environ.get("A8_PROBE_TOPIC"):\n    t = get_topic(os.environ["A8_PROBE_TOPIC"])\n    if t:\n        findings["topics"].append(str(t.get("summary") or ""))\ntry:\n    from storage.voolbook_store import list_feed\n\n    for post in list_feed(limit=100):\n        findings["voolbook"].append(post.content)\nexcept Exception as exc:\n    findings["voolbook_error"] = str(exc)\nprint("FINDINGS:" + json.dumps(findings))\n'


# ---------------------------------------------------------------------------
# F17 — RESTART / OLD RUNTIME EPOCH: a fresh interpreter against the same
#       state serves no governed bytes on any meet surface
# ---------------------------------------------------------------------------


def test_f17_fresh_epoch_restart_serves_nothing_governed(a8_env):
    import subprocess
    import sys as _sys

    commit = _admit_finalize(P_EXACT, request_id="freeze-f17")
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        status, _body = _post(
            meet,
            "/v1/hive/posts",
            {"topic_id": topic, "author_agent_id": AGENT, "body": P_QUOTE},
        )
        assert status == 200, _body
        _plant_task_offer_summary("Brief: " + P_EXACT + " end brief.")
        _withhold(commit["finalization_id"])
        # erase a second governed payload fully (sweep included)
        commit2 = _admit_finalize("second governed epoch secret 4481", request_id="freeze-f17b")
        _erase(commit2["finalization_id"])

        env = {
            **os.environ,
            "A8_PROBE_DB": str(sdb.active_default_db_path()),
            "A8_PROBE_HOME": str(a8_env),
            "A8_PROBE_TOPIC": topic,
            "A8_PROBE_ROOT": str(Path(__file__).resolve().parents[2]),
        }
        probe_path = a8_env.parent / "a8_restart_probe.py"
        probe_path.write_text(_RESTART_PROBE_SOURCE, encoding="utf-8")
        proc = subprocess.run(
            [_sys.executable, str(probe_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr[-800:]
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("FINDINGS:")]
        assert line, proc.stdout[-500:] + proc.stderr[-500:]
        findings = json.loads(line[-1][len("FINDINGS:") :])
        for surface, values in findings.items():
            if surface.endswith("_error"):
                continue
            for value in values or []:
                assert P_EXACT not in str(value), (
                    f"F17: fresh-epoch restart served WITHHELD bytes on {surface}"
                )
                assert "second governed epoch secret 4481" not in str(value), (
                    f"F17: fresh-epoch restart served ERASED bytes on {surface}"
                )


# ---------------------------------------------------------------------------
# F18 — RACE: posts created concurrently with a WITHHOLD transition never
#       end up durably served (traversal lock + fences hold under contention)
# ---------------------------------------------------------------------------


def test_f18_concurrent_writers_vs_withhold_never_serve(a8_env):
    commit = _admit_finalize(P_EXACT, request_id="freeze-f18")
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        state = {"withheld": False, "leak_after": False}

        def _hammer():
            for _ in range(12):
                _post(
                    meet,
                    "/v1/hive/posts",
                    {"topic_id": topic, "author_agent_id": AGENT, "body": P_QUOTE},
                )
                status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
                # serving is legal while the payload is still AVAILABLE; the
                # violation is bytes served AFTER the transition has won
                if state["withheld"] and status == 200 and P_EXACT in served:
                    state["leak_after"] = True
                    return

        threads = [threading.Thread(target=_hammer) for _ in range(3)]
        for t in threads:
            t.start()
        _withhold(commit["finalization_id"])
        state["withheld"] = True
        for t in threads:
            t.join(timeout=30)
        # final verdict after the transition has definitively won
        status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert status == 200
        assert P_EXACT not in served, "F18: concurrent-writer race served governed bytes"
        assert not state["leak_after"], "F18: governed bytes served after WITHHOLD won mid-race"
