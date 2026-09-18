"""Approval-bypass family for the safe bug reporter.

Every way a submission could slip out without a valid, exact, human-granted consent:
no approval, stale hash, wrong hash, post-approval field removal, post-approval draft
tampering on disk, forged consent object, missing confirm flag, and approval for a
different report. Each test must leave the transport un-called.
"""
from __future__ import annotations

import json

import pytest

from core.bug_report import BugReportService


def _fake_credential():
    """Every transport-driven submit in this file carries a fake credential so the
    tests exercise the submission path, not the no-credential fail-closed path."""
    return ("test-token-not-a-real-credential", "env:TEST")



DESTINATION = "example-owner/example-repo"
SECRET = "sk-or-v1-" + "k" * 48


class NoCallTransport:
    def __call__(self, *args, **kwargs):
        raise AssertionError("transport must not be called when approval is invalid")


def _service(tmp_path):
    return BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)


def _make_draft(service, **overrides):
    kwargs = dict(
        expected="it works",
        actual=f"it failed after key {SECRET} was rotated",
        repro_steps=["start the daemon", "send one turn"],
        error_text='  File "core/x.py", line 3, in f\nValueError: boom',
        category="regression",
        lanes=["turn_frontdoor_deterministic"],
        tools=[],
        models=[],
        log_sources=[],
        destination_repo=DESTINATION,
        title="boom",
    )
    kwargs.update(overrides)
    return service.create_draft(**kwargs)


def test_submit_without_any_approval_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _make_draft(service)
    result = service.submit(draft.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"
    assert "approval" in result.detail.lower()


def test_submit_with_wrong_payload_hash_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _make_draft(service)
    from core.bug_report.schema import ApprovalMismatchError

    with pytest.raises(ApprovalMismatchError):
        service.approve(draft.report_id, payload_sha256="0" * 64, confirm=True)
    result = service.submit(draft.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"


def test_approve_requires_confirm_flag(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _make_draft(service)
    preview = service.preview(draft.report_id)
    with pytest.raises(Exception, match=r"(?i)confirm"):
        service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=False)
    result = service.submit(draft.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"


def test_submit_sends_the_consented_bytes_not_a_newer_preview(tmp_path) -> None:
    """A trimmed preview taken AFTER approval cannot swap the payload: submission is
    bound to the consent manifest's exact bytes, and re-approving new removals against
    the old hash is refused."""
    service = _service(tmp_path)
    draft = _make_draft(service, log_sources=[{"name": "daemon.log", "lines": ["2026-09-01T00:00:00 INFO up"]}])
    full = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=full.payload_sha256, confirm=True)
    trimmed = service.preview(draft.report_id, remove_fields=("logs",))
    assert trimmed.payload_sha256 != full.payload_sha256

    from core.bug_report.schema import ApprovalMismatchError

    with pytest.raises(ApprovalMismatchError):
        # approving the trimmed shape while presenting the OLD (full) hash must not work
        service.approve(draft.report_id, payload_sha256=full.payload_sha256, remove_fields=("logs",), confirm=True)

    sent: dict = {}

    def transport(method, url, *, data, headers, timeout):
        if url.endswith("/issues") and method == "POST":
            sent["data"] = data
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 6, "html_url": "https://github.com/x/y/issues/6"}).encode()

    result = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert result.status == "submitted"
    import hashlib

    # exactly the APPROVED bytes left the machine -- not the newer trimmed preview
    assert hashlib.sha256(sent["data"]).hexdigest() == full.payload_sha256
    assert b"daemon.log" in sent["data"]


def test_submit_applies_the_consented_removals_not_newer_ones(tmp_path) -> None:
    """If consent was granted for a REMOVED-fields preview, submit must send exactly that."""
    service = _service(tmp_path)
    draft = _make_draft(service, log_sources=[{"name": "daemon.log", "lines": ["2026-09-01T00:00:00 INFO up"]}])
    trimmed = service.preview(draft.report_id, remove_fields=("logs",), remove_attachments=("logs-daemon.log",))
    service.approve(
        draft.report_id,
        payload_sha256=trimmed.payload_sha256,
        remove_fields=("logs",),
        remove_attachments=("logs-daemon.log",),
        confirm=True,
    )

    sent: dict = {}

    def transport(method, url, *, data, headers, timeout):
        if url.endswith("/issues") and method == "POST":
            sent["data"] = data
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 7, "html_url": "https://github.com/x/y/issues/7"}).encode()

    result = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert result.status == "submitted"
    assert b"daemon.log" not in sent["data"]
    import hashlib

    assert hashlib.sha256(sent["data"]).hexdigest() == trimmed.payload_sha256


def test_draft_tampered_on_disk_after_approval_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _make_draft(service)
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)

    draft_path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    raw = json.loads(draft_path.read_text(encoding="utf-8"))
    raw["actual"] = raw["actual"] + " and something else changed"
    draft_path.write_text(json.dumps(raw), encoding="utf-8")

    result = service.submit(draft.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"
    assert "mismatch" in result.detail.lower() or "consent" in result.detail.lower()


def test_forged_consent_in_draft_file_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _make_draft(service)

    import hashlib

    forged_sha = hashlib.sha256(b"attacker-chosen-bytes").hexdigest()
    draft_path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    raw = json.loads(draft_path.read_text(encoding="utf-8"))
    raw["consent"] = {
        "report_id": draft.report_id,
        "payload_sha256": forged_sha,
        "fields_included": [],
        "attachments_included": [],
        "destination_repo": DESTINATION,
        "approved_at": "2026-09-01T00:00:00+00:00",
        "approver": "local",
        "removed_fields": [],
        "removed_attachments": [],
    }
    draft_path.write_text(json.dumps(raw), encoding="utf-8")

    result = service.submit(draft.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"


def test_approval_of_one_report_does_not_authorize_another(tmp_path) -> None:
    service = _service(tmp_path)
    first = _make_draft(service, title="one")
    second = _make_draft(service, title="two")
    preview = service.preview(first.report_id)
    service.approve(first.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    result = service.submit(second.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"
    assert "approval" in result.detail.lower()


def test_approval_for_wrong_destination_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _make_draft(service)
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    # rewrite destination on disk after approval
    draft_path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    raw = json.loads(draft_path.read_text(encoding="utf-8"))
    raw["destination_repo"] = "attacker/payload-drop"
    draft_path.write_text(json.dumps(raw), encoding="utf-8")
    result = service.submit(draft.report_id, transport=NoCallTransport(), credential_lookup=_fake_credential)
    assert result.status == "failed"
