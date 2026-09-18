"""Cold-page work must not compile tools or restore historical task contexts."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from core import artifact_readers, chat_attachments, context_namespace, runtime_continuity
from core.artifact_readers import speech_tool
from core.memory import entries
from storage.db import get_connection


def test_cold_attachment_limits_never_launch_a_speech_probe(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("VOOL_READER_TOOLS_DIR", str(tmp_path / "empty-tools"))
    monkeypatch.setattr(speech_tool, "_PROBE_CACHE", {})

    def forbidden_probe(**kwargs):
        calls.append(kwargs)
        raise AssertionError("page initialization must not compile or launch speech")

    monkeypatch.setattr(speech_tool, "probe", forbidden_probe)
    result = chat_attachments.limits_payload()
    assert calls == [], "limits launches the speech compiler/probe before returning"
    assert ".txt" in result["accept"]
    assert ".wav" not in result["accept"]


def test_capability_report_checks_each_decoder_once(monkeypatch):
    calls = []
    original = artifact_readers.ReaderSpec.available

    def counted(spec, **kwargs):
        calls.append(spec.fmt)
        return original(spec, **kwargs)

    monkeypatch.setattr(speech_tool, "speech_available", lambda **kwargs: False)
    monkeypatch.setattr(artifact_readers.ReaderSpec, "available", counted)
    artifact_readers.capability_report()
    assert len(calls) == len(artifact_readers.REGISTRY), calls


def test_passive_speech_snapshot_expires_but_explicit_check_still_probes(monkeypatch):
    import time

    facts = {"authorization": "authorized", "recognizer_available": True,
             "supports_on_device_recognition": True}
    monkeypatch.setattr(speech_tool, "_PROBE_CACHE", {
        "en_US": (time.monotonic(), facts),
        "lt_LT": (time.monotonic() - 60, facts),
    })
    calls = []

    def active_probe(**kwargs):
        calls.append(kwargs)
        return facts

    monkeypatch.setattr(speech_tool, "probe", active_probe)
    assert speech_tool.speech_available(locale="en_US", probe_if_needed=False)
    assert not speech_tool.speech_available(locale="lt_LT", probe_if_needed=False)
    assert not speech_tool.speech_available(locale="de_DE", probe_if_needed=False)
    assert not calls
    assert speech_tool.speech_available(locale="lt_LT")
    assert calls == [{"locale": "lt_LT"}]


def test_sidebar_uses_one_namespace_snapshot_and_preserves_deleted_state(monkeypatch):
    context_namespace.ensure_chat_namespace("visible")
    context_namespace.ensure_chat_namespace("deleted")
    context_namespace.set_chat_namespace_state("deleted", "deleted")
    monkeypatch.setattr(entries, "load_jsonl", lambda _: [
        {"session_id": "visible", "user": "hello", "ts": "2026-09-05"}
        for _ in range(100)
    ] + [{"session_id": "deleted", "user": "private", "ts": "2026-09-05"}])
    monkeypatch.setattr(entries, "load_session_meta", lambda: {
        "deleted": {"title": "must stay deleted", "project_id": "old-project"},
        "visible": {"title": "My chat", "project_id": "current-project"},
    })
    calls = []
    original = entries.load_chat_namespace

    def counted(sid):
        calls.append(sid)
        return original(sid)

    monkeypatch.setattr(entries, "load_chat_namespace", counted)
    result = entries.list_conversation_sessions(limit=100)
    assert calls == [], "sidebar repeats database initialization per message/session"
    assert [row["session_id"] for row in result] == ["visible"]
    assert result[0]["turn_count"] == 100
    assert result[0]["title"] == "My chat"
    assert result[0]["project_id"] == "current-project"


def test_activity_list_never_restores_checkpoint_evidence(monkeypatch):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO runtime_checkpoints (checkpoint_id, session_id, request_text, status, "
                     "step_count, last_tool_name, pending_intent_json, created_at, updated_at) "
                     "VALUES ('cp-page', 'chat-page', 'hello', 'pending_approval', 3, 'workspace.write_file', "
                     "'{}', '2026-09-05', '2026-09-05')")
        conn.execute("INSERT INTO runtime_sessions (session_id, started_at, updated_at, "
                     "status, last_checkpoint_id) VALUES "
                     "('chat-page', '2026-09-05', '2026-09-05', 'pending_approval', 'cp-page')")
        conn.commit()
    finally:
        conn.close()
    calls = []

    def forbidden_restore(*args, **kwargs):
        calls.append(1)
        raise AssertionError("status polling must not restore a task's evidence")

    monkeypatch.setattr(runtime_continuity, "_restore_runtime_checkpoint_json_view", forbidden_restore)
    rows = runtime_continuity.list_runtime_sessions(limit=100)
    row = next(r for r in rows if r["session_id"] == "chat-page")
    assert calls == []
    assert row["resume_available"] is True
    assert row["checkpoint_step_count"] == 3
    assert row["checkpoint_status"] == "pending_approval"


def test_projection_scope_rechecks_revocation_and_closes_its_connection(monkeypatch):
    from core import finalization

    finalization.reset_governance_readiness_for_tests()
    text = "projection canary"
    digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    # A tombstone binds these exact bytes; the real lookup must see it even
    # when another connection commits it midway through the read scope.
    opened = []
    original = finalization.get_connection

    def tracked():
        conn = original()
        opened.append(conn)
        return conn

    monkeypatch.setattr(finalization, "get_connection", tracked)
    with finalization.availability_read_scope():
        assert finalization.writer_may_persist_text(text)
        conn = original()
        try:
            conn.execute("INSERT INTO a7_governance_events "
                         "(event_id, finalization_id, event_kind, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                         ("scope-tombstone", "no-live-row", finalization.EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,
                          digest, "2026-09-05"))
            conn.commit()
        finally:
            conn.close()
        assert not finalization.writer_may_persist_text(text)
        assert finalization.writer_may_persist_text("unrelated control")
    import sqlite3
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert finalization._AVAILABILITY_READ_SCOPE.get() is None


def test_event_listings_open_one_governance_connection_per_call(monkeypatch):
    from core import finalization

    for n in range(40):
        runtime_continuity.append_runtime_event(
            session_id="events-page", event_type="task_progress",
            message=f"step {n} of the plan", details={"stage": str(n), "status": "running"},
        )
    opened = []
    original = finalization.get_connection

    def tracked():
        conn = original()
        opened.append(conn)
        return conn

    monkeypatch.setattr(finalization, "get_connection", tracked)
    for listing in (
        lambda: runtime_continuity.list_recent_runtime_session_events("events-page", limit=200),
        lambda: runtime_continuity.list_runtime_session_events("events-page", after_seq=0, limit=200),
    ):
        opened.clear()
        events = listing()
        assert len(events) == 40
        assert [e["message"] for e in events[:2]] == ["step 0 of the plan", "step 1 of the plan"]
        assert len(opened) <= 1, f"{len(opened)} governance connections for one event listing"
        import sqlite3
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
    assert finalization._AVAILABILITY_READ_SCOPE.get() is None
    # Nested inside a wider projection the listing shares that scope's connection.
    opened.clear()
    with finalization.availability_read_scope():
        runtime_continuity.list_recent_runtime_session_events("events-page", limit=200)
        runtime_continuity.list_recent_runtime_session_events("events-page", limit=200)
    assert len(opened) <= 1


def test_browser_preflight_times_out_cancels_and_coalesces_history():
    source = (Path(__file__).parents[1] / "core/vool_chat_page.py").read_text()
    helper = source[source.index("async function fetchJsonWithin("):source.index("async function runTurn(")]
    activity = source[source.index("let sidebarLifecycleRequest = null;"):source.index("async function loadActivityHistory()")]
    program = helper + activity + r'''
const assert = require('node:assert/strict');
(async () => {
  global.fetch = (url, {signal}) => new Promise((resolve, reject) => {
    const cancel = () => reject(Object.assign(new Error('cancelled'), {name:'AbortError'}));
    if (signal.aborted) cancel(); else signal.addEventListener('abort', cancel, {once:true});
  });
  await assert.rejects(fetchJsonWithin('/state', null, 20), /did not respond/);
  const controller = new AbortController();
  const pending = fetchJsonWithin('/state', controller.signal, 1000);
  controller.abort();
  await assert.rejects(pending, {name:'AbortError'});
  let calls = 0, finish;
  global.fetch = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const a = fetchRuntimeActivity(), b = fetchRuntimeActivity();
  assert.equal(a, b);
  assert.equal(calls, 1);
  finish({ok:true, json:async () => ({sessions:[]})});
  await a;
  assert.equal(runtimeActivityRequest, null);
  let urls = [], finishSummary;
  global.fetch = url => { urls.push(url); return new Promise(resolve => { finishSummary = resolve; }); };
  const firstSummary = fetchSidebarLifecycle(), secondSummary = fetchSidebarLifecycle();
  assert.equal(firstSummary, secondSummary);
  assert.deepEqual(urls, ['/api/runtime/sessions?summary=1']);
  finishSummary({ok:true, json:async () => ({sessions:[{session_id:'summary-only',status:'completed'}]})});
  await firstSummary;
  assert.equal(sidebarLifecycleRequest, null);
  console.log(JSON.stringify({timeout:true, cancellation:true, single_flight:true}));
})().catch(e => {console.error(e); process.exitCode=1;});
'''
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout).values())


def test_late_boot_history_cannot_replace_the_new_user_question():
    source = (Path(__file__).parents[1] / "core/vool_chat_page.py").read_text()
    restore = source[source.index("async function restoreCurrent()"):source.index("// =========================== Live execution UX")]
    program = r'''
const assert = require('node:assert/strict');
let displayedChat = 'same-chat', resolveHistory;
const target = {history:[], run:null};
const chatState = () => target, isDisplayed = () => true;
const historyEntryFromServer = row => row;
let emptyPaints = 0;
const showEmpty = () => {emptyPaints++;};
const renderChat = () => {}, restoreStagedAttachments = () => {};
global.fetch = () => new Promise(resolve => {resolveHistory = resolve;});
''' + restore + r'''
(async () => {
  for (const previous of [[{role:'user',content:'Old task'}], []]) {
    target.run = null; target.history = [];
    const pending = restoreCurrent();
    target.history.push({role:'user',content:'Hi'});
    target.run = {text:'', ended:false};
    resolveHistory({json:async () => ({messages:previous})});
    await pending;
    assert.deepEqual(target.history, [{role:'user',content:'Hi'}]);
    assert.equal(emptyPaints, 0, 'late empty restore erased the live bubble');
  }
  console.log('late history cannot replace the current question');
})().catch(e => {console.error(e); process.exitCode=1;});
'''
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_lifecycle_summary_does_not_hydrate_history_and_still_governs_previews(monkeypatch):
    runtime_continuity.append_runtime_event(
        session_id="sidebar-light", event_type="task_received", message="private preview",
        details={"request_preview": "private request"},
    )
    queries = []
    original = runtime_continuity._conn
    def traced_connection():
        conn = original()
        conn.set_trace_callback(queries.append)
        return conn
    monkeypatch.setattr(runtime_continuity, "_conn", traced_connection)
    monkeypatch.setattr(runtime_continuity, "_servable_event_message", lambda text: "[withheld]")
    rows = runtime_continuity.list_runtime_sessions(limit=100, include_execution_history=False)
    row = next(item for item in rows if item["session_id"] == "sidebar-light")
    assert row["status"] == "running"
    assert row["worker_live"] is False
    assert row["last_message"] == "[withheld]"
    assert row["request_preview"] == "[withheld]"
    assert "execution_history" not in row
    reads = [q.lower() for q in queries if q.lstrip().upper().startswith("SELECT")]
    assert not any("runtime_session_events" in q or "runtime_tool_receipts" in q for q in reads)


@pytest.mark.parametrize("summary", [False, True])
def test_runtime_session_api_keeps_summary_and_detail_contracts_distinct(monkeypatch, summary):
    from unittest.mock import Mock

    from core.web.api import service
    from core.web.api.runtime import RuntimeServices

    reader = Mock(return_value=[{"session_id": "lifecycle", "status": "completed"}])
    monkeypatch.setattr(service, "list_runtime_sessions", reader)
    result = service.dispatch_get(
        path="/api/runtime/sessions", query={"summary": ["1"]} if summary else {},
        runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
    )
    assert result.status == 200
    assert json.loads(result.body)["sessions"][0]["session_id"] == "lifecycle"
    if summary:
        reader.assert_called_once_with(limit=100, include_execution_history=False)
    else:
        reader.assert_called_once_with(limit=100)
