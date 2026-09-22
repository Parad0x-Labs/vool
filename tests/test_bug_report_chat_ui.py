"""The bug reporter in the REAL app: chat-page UI affordances + app-facing endpoints.

Cause families: the page must offer "Report a problem" from a failed answer and the help
surfaces; the candidates endpoint must derive sanitized turn diagnostics server-side
(the browser never sees raw logs or conversation bodies); draft edits must invalidate
approval; the GitHub API root must be overridable so the browser flow can be proven
against a local fake; and the page JS must stay console-free (no diagnostics in the
console, ever).
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

SECRET = "ghp_" + "T7o6K5i4J3h2G1f0E9d8C7b6A5z4"
PATH_SECRET = "/Users/fixtureuser/secrets/env.sh"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _chat_html() -> str:
    resp = dispatch_get(path="/chat", query={}, runtime=_rt(), model_name="vool")
    assert resp.status == 200
    return resp.body.decode("utf-8")


def _get(path: str, query=None, client_host: str = "127.0.0.1"):
    return dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool", client_host=client_host)


def _post(path: str, body, client_host: str = "127.0.0.1"):
    return dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )


def _seed_failed_turn(session_id: str, turn_id: str) -> None:
    """The real stores: a runtime failure event carrying poison, exactly as a live
    failed turn would leave behind."""
    from core.context_namespace import ensure_chat_namespace
    from core.runtime_task_events import emit_runtime_event

    ensure_chat_namespace(session_id)
    context = {"session_id": session_id, "runtime_session_id": session_id, "cancel_turn_id": turn_id}
    emit_runtime_event(context, event_type="task.started", message="turn started", details={"lane": "live_data_typed_plan"})
    emit_runtime_event(
        context,
        event_type="model_lane_failed",
        message=f"provider rejected token {SECRET} loading env from {PATH_SECRET}",
        details={"lane": "live_data_typed_plan", "tool": "web.fetch", "model": "llama-3.1-8b"},
    )
    emit_runtime_event(
        context,
        event_type="task.failed",
        message="turn failed safely",
        details={"error": f"RuntimeError: auth failed for {SECRET}"},
    )


# --- the page itself ------------------------------------------------------------

def test_chat_page_offers_report_a_problem_from_failed_answers_and_help_surfaces() -> None:
    html = _chat_html()
    assert "Report a problem" in html
    assert 'id="bugReportOverlay"' in html
    assert 'id="reportBtn"' in html
    # the failed-answer surface: the report action is wired into the failed task card path
    assert "reportProblemForFailedRun" in html
    # settings help surface carries the affordance too
    assert html.count("Report a problem") >= 2


def test_chat_page_wires_the_bug_report_api_flow() -> None:
    html = _chat_html()
    for endpoint in (
        "/api/bug-report/candidates",
        "/api/bug-report/draft",
        "/api/bug-report/preview",
        "/api/bug-report/approve",
        "/api/bug-report/submit",
    ):
        assert endpoint in html, f"chat page never calls {endpoint}"
    # approval binds the exact preview hash client-side as well
    assert "payload_sha256" in html


def test_chat_page_js_has_no_console_calls() -> None:
    """Requirement 9: bug-report diagnostics must not reach the browser console.

    The intent is no console METHOD CALLS in the page's JavaScript. A bare substring
    ban on "console." also trips on ordinary user-facing prose that happens to contain
    "no console." (privacy copy in the wallet reveal dialog) -- a false positive that
    failed on the frozen base itself. The assertion keeps its full strength for every
    real console API while letting prose through.
    """
    html = _chat_html()
    # The i18n bootstrap warns only about a missing catalog key; it carries no
    # turn, credential or report payload. All other console calls remain forbidden.
    safe_warning = 'console.warn("[vool-i18n] missing key, English fallback: " + key)'
    assert html.count(safe_warning) == 1
    html = html.replace(safe_warning, "")
    for method in ("log", "debug", "info", "warn", "error", "trace", "table", "dir", "assert", "count", "group", "profile", "time"):
        assert f"console.{method}" not in html, f"the page calls console.{method}"


# --- candidates: sanitized server-side turn diagnostics ----------------------------

def test_candidates_endpoint_returns_sanitized_failed_turns() -> None:
    session = "openclaw:" + "ab12cd34ef"
    _seed_failed_turn(session, "11111111-2222-3333-4444-555555555555")
    resp = _get("/api/bug-report/candidates", {"session": [session]})
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["ok"] is True
    candidates = payload["candidates"]
    assert candidates, "the seeded failed turn must be a candidate"
    blob = json.dumps(payload)
    assert SECRET not in blob
    assert "fixtureuser" not in blob
    top = candidates[0]
    assert top["failed"] is True
    assert "model_lane_failed" in top["failure_types"]
    assert top["lanes"] == ["live_data_typed_plan"]
    assert top["tools"] == ["web.fetch"]
    assert top["models"] == ["llama-3.1-8b"]
    assert top["log_lines"], "runtime events must be offered as a bounded log source"
    assert any("ERROR" in line for line in top["log_lines"])


def test_candidates_exclude_conversation_content() -> None:
    session = "openclaw:" + "ffeeddccbbaa"
    _seed_failed_turn(session, "99999999-8888-7777-6666-555555555555")
    from core.memory.files import append_jsonl, conversation_log_path

    append_jsonl(conversation_log_path(), {
        "session_id": session, "user": "hey reorganize my invoices",
        "assistant": "sure, doing it now", "ts": "2026-09-01T10:00:00+00:00",
        "event_id": "evt_x", "event_sequence": 1,
    })
    resp = _get("/api/bug-report/candidates", {"session": [session]})
    payload = json.loads(resp.body)
    blob = json.dumps(payload)
    assert "invoices" not in blob and "sure, doing it now" not in blob


def test_candidates_endpoint_is_loopback_gated_and_validated() -> None:
    resp = _get("/api/bug-report/candidates", {"session": ["openclaw:ab12"]}, client_host="203.0.113.9")
    assert resp.status == 403
    resp = _get("/api/bug-report/candidates", {})
    assert resp.status == 400


def test_candidate_log_lines_round_trip_into_a_clean_draft() -> None:
    session = "openclaw:" + "77aabbccdde0"
    _seed_failed_turn(session, "12345678-1234-1234-1234-123456789012")
    payload = json.loads(_get("/api/bug-report/candidates", {"session": [session]}).body)
    top = payload["candidates"][0]
    draft_resp = _post("/api/bug-report/draft", {
        "expected": "the turn answers",
        "actual": "the turn failed",
        "repro_steps": ["ask for a live-data lookup"],
        "error_text": top["error_text"],
        "category": "failure",
        "lanes": top["lanes"],
        "tools": top["tools"],
        "models": top["models"],
        "log_sources": [{"name": "runtime-events.log", "lines": top["log_lines"]}],
        "destination_repo": "example-owner/example-repo",
        "title": "live-data turn fails",
    })
    assert draft_resp.status == 200
    draft = json.loads(draft_resp.body)
    blob = json.dumps(draft)
    assert SECRET not in blob and "fixtureuser" not in blob


# --- draft updates invalidate approval ---------------------------------------------

def _make_draft(**overrides):
    body = {
        "expected": "works",
        "actual": "fails",
        "repro_steps": ["one step"],
        "error_text": '  File "core/x.py", line 2, in f\nValueError: boom',
        "category": "crash",
        "lanes": [],
        "tools": [],
        "models": [],
        "log_sources": [],
        "destination_repo": "example-owner/example-repo",
        "title": "boom",
    }
    body.update(overrides)
    resp = _post("/api/bug-report/draft", body)
    assert resp.status == 200
    return json.loads(resp.body)


def test_update_endpoint_edits_fields_and_clears_consent() -> None:
    created = _make_draft()
    report_id = created["report_id"]
    previewed = json.loads(_post("/api/bug-report/preview", {"report_id": report_id}).body)
    approved = _post("/api/bug-report/approve", {
        "report_id": report_id, "payload_sha256": previewed["payload_sha256"], "confirm": True,
    })
    assert approved.status == 200

    updated = _post("/api/bug-report/update", {
        "report_id": report_id,
        "expected": "it works and serves the answer",
        "actual": "it still fails, and now twice",
        "repro_steps": ["one step", "then a second one"],
    })
    assert updated.status == 200
    updated_body = json.loads(updated.body)
    assert updated_body["draft"]["expected"] == "it works and serves the answer"
    assert updated_body["draft"]["consent"] is None  # any edit invalidates approval

    status = json.loads(_get("/api/bug-report/status", {"report_id": [report_id]}).body)
    assert status["consent"] is None

    early = _post("/api/bug-report/submit", {"report_id": report_id})
    assert early.status == 409
    assert "approval" in json.loads(early.body).get("error", json.loads(early.body).get("detail", "")).lower()

    # reapproval against the NEW payload hash works
    new_preview = json.loads(_post("/api/bug-report/preview", {"report_id": report_id}).body)
    assert new_preview["payload_sha256"] != previewed["payload_sha256"]
    reapproved = _post("/api/bug-report/approve", {
        "report_id": report_id, "payload_sha256": new_preview["payload_sha256"], "confirm": True,
    })
    assert reapproved.status == 200


def test_update_endpoint_rejects_unknown_fields_and_bad_ids() -> None:
    created = _make_draft()
    resp = _post("/api/bug-report/update", {"report_id": created["report_id"], "conversation": []})
    assert resp.status == 400
    resp = _post("/api/bug-report/update", {"report_id": "../..", "expected": "x"})
    assert resp.status == 400


def test_update_sanitizes_new_field_values() -> None:
    created = _make_draft()
    resp = _post("/api/bug-report/update", {
        "report_id": created["report_id"],
        "actual": f"crash after key {SECRET} at {PATH_SECRET}",
    })
    assert resp.status == 200
    blob = json.dumps(json.loads(resp.body))
    assert SECRET not in blob and "fixtureuser" not in blob


# --- GitHub API root override (for the local fake in the browser proof) -------------

def test_github_api_root_env_override_directs_the_adapter() -> None:
    from core.bug_report import github_adapter

    sent: list[str] = []

    def fake_transport(method, url, *, data, headers, timeout):
        sent.append(url)
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 5, "html_url": "http://127.0.0.1:9/fake/issues/5"}).encode()

    class _Preview:
        report_id = "br_000000000000"
        payload_sha256 = "0" * 64
        total_bytes = 10
        issue = {"title": "t", "body": "b\nbug-report-fingerprint: " + "0" * 32}
        attachments = ()
        fields_included = ()
        removed_fields = ()
        removed_attachments = ()

    import os

    old = os.environ.get("VOOL_BUG_REPORT_GITHUB_API_ROOT")
    os.environ["VOOL_BUG_REPORT_GITHUB_API_ROOT"] = "http://127.0.0.1:9/fake"
    try:
        result = github_adapter.submit_to_github(
            _Preview(),
            destination="example-owner/example-repo",
            credential_lookup=lambda: ("fake-token", "env:TEST"),
            transport=fake_transport,
        )
    finally:
        if old is None:
            os.environ.pop("VOOL_BUG_REPORT_GITHUB_API_ROOT", None)
        else:
            os.environ["VOOL_BUG_REPORT_GITHUB_API_ROOT"] = old
    assert result.status == "submitted"
    assert sent and all(url.startswith("http://127.0.0.1:9/fake") for url in sent)
