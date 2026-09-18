"""HTTP API + CLI wiring for the safe bug reporter (not an orphan module).

Endpoints are driven through the same dispatch entrypoint every other endpoint uses
(direct dispatch_post/dispatch_get calls, per tests/test_cloud_status_endpoint.py), with
the full guard suite inherited: JSON content-type, origin, loopback, unknown-field
rejection. The GitHub transport is monkeypatched at the adapter module boundary -- no
real network, no real issue.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

SECRET = "ghp_" + "F1e2D3c4B5a6Z9y8X7w6V5u4"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _fake_transport(monkeypatch):
    """GitHub is completely mocked for every API test in this file."""
    from core.bug_report import github_adapter

    calls: list[dict] = []

    def transport(method, url, *, data, headers, timeout):
        calls.append({"method": method, "url": url, "data": data, "headers": dict(headers)})
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 21, "html_url": "https://github.com/o/r/issues/21"}).encode()

    monkeypatch.setattr(github_adapter, "DEFAULT_TRANSPORT", transport)
    return calls


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _post(path: str, body, headers=None, client_host: str = "127.0.0.1"):
    return dispatch_post(
        path=path,
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )


def _get(path: str, query=None):
    return dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool")


def _draft_body():
    return {
        "expected": "answer served",
        "actual": f"crash after key {SECRET}",
        "repro_steps": ["start the daemon", "send one turn"],
        "error_text": '  File "core/x.py", line 4, in f\nValueError: boom',
        "category": "crash",
        "lanes": ["turn_frontdoor_deterministic"],
        "tools": ["web.fetch"],
        "models": ["llama-3.1-8b"],
        "log_sources": [{"name": "daemon.log", "lines": ["2026-09-01T00:00:00 INFO up"]}],
        "destination_repo": "example-owner/example-repo",
        "title": "crash on turn",
    }


def test_draft_endpoint_creates_sanitized_local_draft() -> None:
    resp = _post("/api/bug-report/draft", _draft_body())
    assert resp.status == 200
    payload = json.loads(resp.body)
    assert payload["ok"] is True
    report_id = payload["report_id"]
    assert report_id.startswith("br_")
    body_text = json.dumps(payload)
    assert SECRET not in body_text
    assert "[redacted" in body_text
    assert payload["draft"]["fingerprint"]
    assert payload["draft"]["destination_repo"] == "example-owner/example-repo"


def test_draft_endpoint_rejects_unknown_fields() -> None:
    body = _draft_body()
    body["messages"] = [{"role": "user", "content": "hi"}]
    resp = _post("/api/bug-report/draft", body)
    assert resp.status == 400
    assert "messages" in json.loads(resp.body)["error"]


def test_draft_endpoint_requires_json_content_type() -> None:
    resp = _post("/api/bug-report/draft", _draft_body(), headers={"content-type": "text/plain"})
    assert resp.status == 415


def test_draft_endpoint_refuses_cross_origin() -> None:
    resp = _post("/api/bug-report/draft", _draft_body(), headers={
        "content-type": "application/json",
        "origin": "https://evil.example",
    })
    assert resp.status == 403


def test_draft_endpoint_refuses_non_loopback_client() -> None:
    resp = _post("/api/bug-report/draft", _draft_body(), client_host="203.0.113.9")
    assert resp.status == 403


def test_full_flow_over_the_api(_fake_transport) -> None:
    calls = _fake_transport
    created = json.loads(_post("/api/bug-report/draft", _draft_body()).body)
    report_id = created["report_id"]
    assert calls == []  # nothing left the machine at draft time

    previewed = json.loads(_post("/api/bug-report/preview", {"report_id": report_id}).body)
    payload_sha = previewed["payload_sha256"]
    assert previewed["issue"]["title"]
    assert "bug-report-fingerprint:" in previewed["issue"]["body"]
    assert SECRET not in previewed["issue"]["body"]

    # submit before approval is refused
    early = _post("/api/bug-report/submit", {"report_id": report_id})
    assert early.status == 409
    assert calls == []

    approved = _post("/api/bug-report/approve", {
        "report_id": report_id,
        "payload_sha256": payload_sha,
        "confirm": True,
    })
    assert approved.status == 200

    submitted = json.loads(_post("/api/bug-report/submit", {"report_id": report_id}).body)
    assert submitted["status"] == "submitted"
    assert submitted["issue_url"].endswith("/issues/21")
    create = next(c for c in calls if c["method"] == "POST" and c["url"].endswith("/issues"))
    assert json.loads(create["data"]) == previewed["issue"]

    status = json.loads(_get("/api/bug-report/status", {"report_id": [report_id]}).body)
    assert status["state"] == "submitted"
    assert status["consent"]["payload_sha256"] == payload_sha

    receipts = json.loads(_get("/api/bug-report/receipts").body)
    assert len(receipts) == 1
    assert receipts[0]["payload_sha256"] == payload_sha
    assert SECRET not in json.dumps(receipts)


def test_preview_endpoint_supports_field_removal() -> None:
    created = json.loads(_post("/api/bug-report/draft", _draft_body()).body)
    report_id = created["report_id"]
    full = json.loads(_post("/api/bug-report/preview", {"report_id": report_id}).body)
    trimmed = json.loads(_post("/api/bug-report/preview", {
        "report_id": report_id,
        "remove_fields": ["logs"],
        "remove_attachments": ["logs-daemon.log"],
    }).body)
    assert trimmed["payload_sha256"] != full["payload_sha256"]
    assert "daemon.log" not in trimmed["issue"]["body"]
    assert trimmed["total_bytes"] < full["total_bytes"]


def test_status_endpoint_refuses_path_traversal_ids() -> None:
    resp = _get("/api/bug-report/status", {"report_id": ["../../etc/passwd"]})
    assert resp.status in (400, 404)
    resp2 = _post("/api/bug-report/submit", {"report_id": "../.."})
    assert resp2.status in (400, 404)


def test_approve_endpoint_requires_confirm() -> None:
    created = json.loads(_post("/api/bug-report/draft", _draft_body()).body)
    report_id = created["report_id"]
    previewed = json.loads(_post("/api/bug-report/preview", {"report_id": report_id}).body)
    resp = _post("/api/bug-report/approve", {
        "report_id": report_id,
        "payload_sha256": previewed["payload_sha256"],
        "confirm": False,
    })
    assert resp.status == 400


# --- CLI wiring -----------------------------------------------------------------

def test_cli_registers_bug_report_subcommands() -> None:
    from apps.vool_cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "bug-report", "draft",
        "--expected", "e", "--actual", "a",
        "--repro", "step one",
        "--destination", "example-owner/example-repo",
    ])
    assert args.command == "bug-report"
    assert args.action == "draft"
    assert args.destination == "example-owner/example-repo"

    args2 = parser.parse_args(["bug-report", "status", "br_deadbeefcafe"])
    assert args2.action == "status"

    args3 = parser.parse_args(["bug-report", "receipts"])
    assert args3.action == "receipts"


def test_cli_draft_to_submit_roundtrip(tmp_path, monkeypatch, capsys) -> None:
    from apps import vool_cli
    from core.bug_report import github_adapter

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)

    def transport(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 3, "html_url": "https://github.com/o/r/issues/3"}).encode()

    monkeypatch.setattr(github_adapter, "DEFAULT_TRANSPORT", transport)

    rc = vool_cli.main([
        "bug-report", "draft",
        "--expected", "works", "--actual", "crashes",
        "--repro", "run one turn",
        "--error-text", '  File "core/x.py", line 1, in f\nValueError: boom',
        "--category", "crash",
        "--destination", "example-owner/example-repo",
        "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    report_id = out["report_id"]

    rc = vool_cli.main(["bug-report", "preview", report_id, "--json"])
    assert rc == 0
    sha = json.loads(capsys.readouterr().out)["payload_sha256"]

    rc = vool_cli.main(["bug-report", "approve", report_id, "--sha", sha, "--confirm", "--json"])
    assert rc == 0
    capsys.readouterr()  # drain the approve output before the submit capture

    rc = vool_cli.main(["bug-report", "submit", report_id, "--json"])
    assert rc == 0
    submitted = json.loads(capsys.readouterr().out)
    assert submitted["status"] == "submitted"

    rc = vool_cli.main(["bug-report", "receipts", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and rows[0]["report_id"] == report_id
    runtime_paths.configure_runtime_home(None)
