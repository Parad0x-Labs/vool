"""Standard HTTP semantics for the bug-report endpoints (diagnostics-reporting goal).

NEW regressions. The base returned a blanket 409 for EVERY failed submission -- a
network timeout, a missing credential, a GitHub 500 and an unapproved draft all read
as "Conflict". These tests pin the standard meanings:

    200 submitted/duplicate · 401 no credential · 403 destination refused access ·
    404 absent draft/destination · 409 consent conflicts with the bytes ·
    429 throttled · 502 upstream failed · 504 sent-but-unanswered

plus the user-facing paths that were missing entirely: the local export (no GitHub
access still saves a sanitized report), explicit consent revocation, the configured
default destination, and the private-repository explanation with the local-export
alternative.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

SECRET = "ghp_" + "F1e2D3c4B5a6Z9y8X7w6V5u4"
DESTINATION = "example-owner/example-repo"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_BUG_REPORT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _fake_transport(monkeypatch):
    """GitHub is completely mocked; per-test the fixture's queue drives statuses."""
    from core.bug_report import github_adapter

    state = {"posts": 0, "post_status": 201, "post_body": {"number": 5, "html_url": f"https://github.com/{DESTINATION}/issues/5"}}

    def transport(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        state["posts"] += 1
        if state["post_status"] == "timeout":
            raise TimeoutError("read timed out after the request was sent")
        return state["post_status"], json.dumps(state["post_body"]).encode()

    monkeypatch.setattr(github_adapter, "DEFAULT_TRANSPORT", transport)
    return state


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _post(path: str, body, headers=None):
    return dispatch_post(
        path=path,
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )


def _get(path: str, query=None):
    return dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")


def _draft(body=None) -> str:
    payload = {
        "expected": "works",
        "actual": f"fails after token {SECRET} expiry",
        "repro_steps": ["run one turn"],
        "category": "crash",
        "destination_repo": DESTINATION,
        "title": "provider 500",
    }
    payload.update(body or {})
    resp = _post("/api/bug-report/draft", payload)
    assert resp.status == 200, resp.body
    return json.loads(resp.body)["report_id"]


def _approve(report_id: str) -> None:
    preview = _post("/api/bug-report/preview", {"report_id": report_id})
    assert preview.status == 200, preview.body
    sha = json.loads(preview.body)["payload_sha256"]
    approved = _post("/api/bug-report/approve", {"report_id": report_id, "payload_sha256": sha, "confirm": True})
    assert approved.status == 200, approved.body


# --- submit: one status per condition, from typed failure facts ----------------------

def test_submit_succeeds_with_200(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["status"] == "submitted"
    assert body["issue_url"].endswith("/issues/5")
    assert "diagnostic" not in body  # success carries no error envelope


def test_submit_without_credential_is_401_with_the_local_alternative(monkeypatch):
    monkeypatch.delenv("VOOL_BUG_REPORT_GITHUB_TOKEN", raising=False)
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 401
    body = json.loads(resp.body)
    assert body["status"] == "failed"
    assert body["diagnostic"]["code"] == "vool.request.unauthenticated"
    assert "no GitHub credential" in body["diagnostic"]["message"]
    assert body["diagnostic"]["retryable"] is True
    # the no-access user path: a local sanitized copy is offered from typed fact
    assert body["local_export_available"] is True


def test_submit_private_repo_access_refusal_is_403_and_explains_the_local_copy(monkeypatch, _fake_transport):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    _fake_transport["post_status"] = 403
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 403
    body = json.loads(resp.body)
    assert body["diagnostic"]["code"] == "vool.upstream.refused"
    assert body["diagnostic"]["upstream_status"] == 403
    assert "private repository" in body["diagnostic"]["message"]
    assert body["local_export_available"] is True


def test_submit_absent_destination_is_404(monkeypatch, _fake_transport):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    _fake_transport["post_status"] = 404
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 404
    body = json.loads(resp.body)
    assert body["diagnostic"]["code"] == "vool.upstream.refused"
    assert body["diagnostic"]["upstream_status"] == 404


def test_submit_throttled_is_429(monkeypatch, _fake_transport):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    _fake_transport["post_status"] = 429
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 429
    assert json.loads(resp.body)["diagnostic"]["code"] == "vool.upstream.throttled"


def test_submit_upstream_500_is_502_not_409(monkeypatch, _fake_transport):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    _fake_transport["post_status"] = 500
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 502
    body = json.loads(resp.body)
    assert body["diagnostic"]["code"] == "vool.upstream.failed"
    assert body["diagnostic"]["upstream_status"] == 500


def test_submit_timeout_after_post_is_504_with_unknown_outcome(monkeypatch, _fake_transport):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    _fake_transport["post_status"] = "timeout"
    report_id = _draft()
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 504
    body = json.loads(resp.body)
    assert body["diagnostic"]["code"] == "vool.upstream.timeout"
    # unknown outcome is said in plain words, and the retry note is honest
    assert "outcome is unknown" in body["diagnostic"]["message"]


def test_submit_without_approval_is_409_and_names_the_conflict(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    report_id = _draft()
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 409
    body = json.loads(resp.body)
    assert body["status"] == "failed"
    assert body["diagnostic"]["code"] == "vool.request.conflict"


def test_submit_unknown_draft_is_404(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    resp = _post("/api/bug-report/submit", {"report_id": "br_000000000000"})
    assert resp.status == 404
    assert json.loads(resp.body)["diagnostic"]["code"] == "vool.request.not_found"


# --- local export: the no-GitHub-access user still saves the sanitized report --------

def test_export_writes_the_exact_preview_bytes_and_never_uses_the_network(monkeypatch, _fake_transport):
    report_id = _draft()
    preview = _post("/api/bug-report/preview", {"report_id": report_id})
    expected_bytes = json.loads(preview.body)
    resp = _post("/api/bug-report/export", {"report_id": report_id})
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["ok"] is True
    assert body["payload_sha256"] == expected_bytes["payload_sha256"]
    from pathlib import Path

    saved = Path(body["path"]).read_bytes()
    assert saved == json.dumps(expected_bytes["issue"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert _fake_transport["posts"] == 0  # zero egress: no transport call ever happened


def test_export_keeps_its_own_ledger_and_does_not_block_a_later_submission(monkeypatch, _fake_transport):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    report_id = _draft()
    exported = _post("/api/bug-report/export", {"report_id": report_id})
    assert exported.status == 200
    # the submission receipts stay empty: an export is not a submission
    receipts = _get("/api/bug-report/receipts")
    assert json.loads(receipts.body) == []
    # and a later real submission of the same draft is NOT deduped against the export
    _approve(report_id)
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 200
    assert json.loads(resp.body)["status"] == "submitted"


# --- explicit revocation ----------------------------------------------------------------

def test_revoke_destroys_consent_and_submission_is_refused_until_reapproved(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "gh-test-token-not-real")
    report_id = _draft()
    _approve(report_id)
    revoked = _post("/api/bug-report/revoke", {"report_id": report_id})
    assert revoked.status == 200
    assert json.loads(revoked.body)["consent"] is None
    resp = _post("/api/bug-report/submit", {"report_id": report_id})
    assert resp.status == 409
    assert json.loads(resp.body)["status"] == "failed"


# --- the configured default destination ---------------------------------------------------

def test_destination_endpoint_returns_the_builtin_default():
    resp = _get("/api/bug-report/destination")
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["ok"] is True
    assert body["destination"] == "Parad0x-Labs/vool-feedback"
    assert body["builtin"] == "Parad0x-Labs/vool-feedback"


def test_destination_env_override_is_respected(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_DESTINATION", "my-org/my-feedback")
    resp = _get("/api/bug-report/destination")
    body = json.loads(resp.body)
    assert body["destination"] == "my-org/my-feedback"


def test_empty_draft_destination_binds_to_the_configured_default(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_DESTINATION", "my-org/my-feedback")
    report_id = _draft({"destination_repo": ""})
    status = _get("/api/bug-report/status", {"report_id": [report_id]})
    assert status.status == 200
    assert json.loads(status.body)["destination_repo"] == "my-org/my-feedback"


def test_invalid_configured_destination_is_surfaced_not_swallowed(monkeypatch):
    monkeypatch.setenv("VOOL_BUG_REPORT_DESTINATION", "not a repo name")
    resp = _get("/api/bug-report/destination")
    assert resp.status == 200
    body = json.loads(resp.body)
    assert body["configuration_error"]  # the misconfiguration is named
    assert body["destination"] == body["builtin"]  # and the usable default is still returned


# --- invalid request shape -----------------------------------------------------------------

def test_export_rejects_unknown_fields():
    resp = _post("/api/bug-report/export", {"report_id": "br_000000000000", "evil": 1})
    assert resp.status == 400
    assert json.loads(resp.body)["error"].startswith("unknown fields")
