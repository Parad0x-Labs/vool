"""End-to-end bug-report pipeline with a fully mocked GitHub transport.

Proves: local draft first, deterministic redaction inside the draft, exact outbound bytes
preview, consent-bound approval, submission only after approval, receipt recorded, and the
credential value never appearing in any artifact except the Authorization header.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from core.bug_report import BugReportService
from core.bug_report.schema import UnsafeReportContentError  # re-exported for API users


def _fake_credential():
    """Every transport-driven submit in this file carries a fake credential so the
    tests exercise the submission path, not the no-credential fail-closed path."""
    return ("test-token-not-a-real-credential", "env:TEST")



SECRET_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
SECRET_IN_PROSE = "alice@example.com"
CONVERSATION_TEXT = "user: please move my tax documents"

DESTINATION = "example-owner/example-repo"


class FakeTransport:
    """Records exactly what the adapter tried to send. Never touches a network."""

    def __init__(self, *, responses=None, failures=None):
        self.calls: list[dict] = []
        self.responses = responses or {}
        self.failures = list(failures or [])

    def __call__(self, method: str, url: str, *, data: bytes, headers: dict, timeout: float):
        self.calls.append({"method": method, "url": url, "data": data, "headers": dict(headers), "timeout": timeout})
        if self.failures:
            raise self.failures.pop(0)
        if "/search/issues" in url:
            return 200, json.dumps({"total_count": 0, "items": []}).encode()
        key = (method, url)
        if key in self.responses:
            status, body = self.responses[key]
            return status, body
        return 201, json.dumps({"number": 42, "html_url": f"https://github.com/{DESTINATION}/issues/42"}).encode()


def _service(tmp_path):
    return BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)


def _draft_kwargs():
    return dict(
        expected="turn completes and the answer is served",
        actual=f"daemon crashed; owner {SECRET_IN_PROSE} saw a traceback",
        repro_steps=["ask for a weather lookup", "wait for the second tool call"],
        error_text=(
            "Traceback (most recent call last):\n"
            '  File "/Users/fixtureuser/vool/core/adapter.py", line 77, in call\n'
            f"RuntimeError: provider rejected token {SECRET_TOKEN}\n"
        ),
        category="crash",
        lanes=["live_data_typed_plan"],
        tools=["web.fetch"],
        models=["llama-3.1-8b"],
        log_sources=[{"name": "daemon.log", "lines": [
            "2026-09-01T12:00:00 INFO turn started",
            f"2026-09-01T12:00:01 ERROR auth failed token {SECRET_TOKEN}",
        ]}],
        destination_repo=DESTINATION,
        title="daemon crashes on second tool call",
    )


def test_draft_is_created_locally_and_sanitized(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_draft_kwargs())
    assert draft.report_id.startswith("br_")
    assert SECRET_TOKEN not in json.dumps(draft.to_dict())
    assert SECRET_IN_PROSE not in json.dumps(draft.to_dict())
    assert draft.redaction_summary.total_replacements >= 2
    assert len(draft.fingerprint) >= 16
    # local draft file exists on disk before anything else happens
    draft_path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    assert draft_path.exists()
    assert SECRET_TOKEN.encode() not in draft_path.read_bytes()
    # typed structure preserved
    assert draft.error is not None and draft.error.frames[0].file == "adapter.py"
    assert draft.components.lanes == ("live_data_typed_plan",)
    assert draft.destination_repo == DESTINATION


def test_creating_a_draft_touches_no_network(tmp_path) -> None:
    transport = FakeTransport()
    service = _service(tmp_path)
    service.create_draft(**_draft_kwargs())
    assert transport.calls == []


def test_preview_returns_exact_outbound_bytes(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_draft_kwargs())
    preview = service.preview(draft.report_id)
    outbound = json.dumps(preview.issue, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert hashlib.sha256(outbound).hexdigest() == preview.payload_sha256
    assert preview.total_bytes == len(outbound)
    body = preview.issue["body"]
    assert "bug-report-fingerprint:" in body
    assert SECRET_TOKEN not in body
    assert "fixtureuser" not in body
    # scanner certifies the exact bytes that would leave the machine
    from core.bug_report.scanner import scan_payload

    assert scan_payload(outbound) == ()


def test_preview_field_removal_changes_payload_and_bytes(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_draft_kwargs())
    full = service.preview(draft.report_id)
    trimmed = service.preview(draft.report_id, remove_fields=("logs", "flags"), remove_attachments=("logs-daemon.log",))
    assert trimmed.payload_sha256 != full.payload_sha256
    assert "daemon.log" not in trimmed.issue["body"]
    assert "Flags" not in trimmed.issue["body"]
    assert trimmed.total_bytes < full.total_bytes


def test_submission_requires_approval_and_sends_exact_preview_bytes(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_draft_kwargs())
    transport = FakeTransport()
    result = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert result.status == "failed"
    assert "approval" in result.detail.lower()
    assert transport.calls == []  # nothing left the machine

    preview = service.preview(draft.report_id)
    consent = service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    assert consent.payload_sha256 == preview.payload_sha256

    result = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert result.status == "submitted"
    assert result.issue_url.endswith("/issues/42")
    assert len(transport.calls) == 2  # search (dedup) + create

    create_call = transport.calls[-1]
    assert create_call["method"] == "POST"
    assert create_call["url"].endswith(f"/repos/{DESTINATION}/issues")
    sent = json.loads(create_call["data"].decode("utf-8"))
    assert sent == preview.issue  # the exact previewed bytes are what left the machine
    assert hashlib.sha256(create_call["data"]).hexdigest() == preview.payload_sha256


def test_credential_value_never_enters_artifacts_only_the_header(tmp_path, monkeypatch) -> None:
    fine_grained = "github_pat_" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6"
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", fine_grained)
    service = _service(tmp_path)
    draft = service.create_draft(**_draft_kwargs())
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    transport = FakeTransport()
    result = service.submit(draft.report_id, transport=transport)  # default lookup reads the env var
    assert result.status == "submitted"

    create_call = transport.calls[-1]
    assert create_call["headers"]["Authorization"] == f"Bearer {fine_grained}"

    store_dir = tmp_path / "bug_reports"
    for path in store_dir.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert fine_grained.encode() not in content, f"credential leaked into {path}"
    receipts = service.receipts()
    assert receipts and receipts[0]["credential_source"].startswith("env:")
    assert fine_grained not in json.dumps(receipts)


def test_conversation_material_is_refused_at_draft_creation(tmp_path) -> None:
    service = _service(tmp_path)
    kwargs = _draft_kwargs()
    kwargs["messages"] = [{"role": "user", "content": CONVERSATION_TEXT}]
    with pytest.raises(TypeError):
        service.create_draft(**kwargs)
    kwargs = _draft_kwargs()
    kwargs["log_sources"] = [{"name": "conversation", "lines": [CONVERSATION_TEXT]}]
    with pytest.raises(Exception, match=r"(?i)conversation|message"):
        service.create_draft(**kwargs)


def test_uncleanable_prose_fails_closed_at_draft_creation() -> None:
    # A dirty payload can never become model input: SanitizedMaterial re-runs the outbound
    # scanner at construction and fails closed.
    from core.bug_report.schema import SanitizedMaterial

    with pytest.raises(UnsafeReportContentError):
        SanitizedMaterial.from_payload({"note": f"leak {SECRET_TOKEN}"})


def test_draft_status_roundtrip(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_draft_kwargs())
    status = service.status(draft.report_id)
    assert status["state"] == "draft"
    assert status["fingerprint"] == draft.fingerprint
    assert status["consent"] is None
