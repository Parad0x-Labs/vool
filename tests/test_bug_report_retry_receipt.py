"""Failed-submission retry + durable receipt truth for the safe bug reporter.

Cause families: failure retains the local draft; safe retry reuses the same report and the
still-valid consent; receipts record WHAT left the machine (hashes, sizes, names,
destination, credential source label) without recording any sensitive content; the receipt
ledger is a verifiable hash chain that detects tampering.
"""
from __future__ import annotations

import json

from core.bug_report import BugReportService


def _fake_credential():
    """Every transport-driven submit in this file carries a fake credential so the
    tests exercise the submission path, not the no-credential fail-closed path."""
    return ("test-token-not-a-real-credential", "env:TEST")



DESTINATION = "example-owner/example-repo"
SECRET = "ghp_" + "R9r8R7r6R5r4R3r2R1r0R9r8R7r6"


def _service(tmp_path):
    return BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)


def _approved_draft(service, **overrides):
    kwargs = dict(
        expected="works",
        actual=f"fails after token {SECRET} expiry",
        repro_steps=["run one turn"],
        error_text='  File "core/x.py", line 2, in g\nRuntimeError: provider 500',
        category="crash",
        lanes=["turn_frontdoor_deterministic"],
        tools=[],
        models=["llama-3.1-8b"],
        log_sources=[{"name": "daemon.log", "lines": ["2026-09-01T00:00:00 ERROR provider 500"]}],
        destination_repo=DESTINATION,
        title="provider 500",
    )
    kwargs.update(overrides)
    draft = service.create_draft(**kwargs)
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    return draft, preview


def _ok_transport(calls):
    def transport(method, url, *, data, headers, timeout):
        calls.append({"method": method, "url": url, "data": data})
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 11, "html_url": f"https://github.com/{DESTINATION}/issues/11"}).encode()

    return transport


def test_failed_submission_retains_the_local_draft(tmp_path) -> None:
    service = _service(tmp_path)
    draft, _preview = _approved_draft(service)

    def failing(method, url, *, data, headers, timeout):
        raise TimeoutError("connection timed out")

    result = service.submit(draft.report_id, transport=failing, credential_lookup=_fake_credential)
    assert result.status == "failed"
    assert "TimeoutError" in result.detail
    draft_path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    assert draft_path.exists()
    stored = json.loads(draft_path.read_text(encoding="utf-8"))
    assert stored["report_id"] == draft.report_id
    assert service.status(draft.report_id)["state"] != "submitted"
    assert service.receipts() == []  # nothing left the machine, so no receipt claims otherwise


def test_retry_after_failure_submits_the_same_bytes_without_reapproval(tmp_path) -> None:
    service = _service(tmp_path)
    draft, preview = _approved_draft(service)

    def failing(method, url, *, data, headers, timeout):
        raise ConnectionError("reset")

    first = service.submit(draft.report_id, transport=failing, credential_lookup=_fake_credential)
    assert first.status == "failed"

    calls: list[dict] = []
    second = service.submit(draft.report_id, transport=_ok_transport(calls), credential_lookup=_fake_credential)
    assert second.status == "submitted"
    create = next(c for c in calls if c["method"] == "POST" and c["url"].endswith("/issues"))
    import hashlib

    assert hashlib.sha256(create["data"]).hexdigest() == preview.payload_sha256


def test_receipt_records_what_left_but_not_content(tmp_path) -> None:
    service = _service(tmp_path)
    draft, preview = _approved_draft(service)
    calls: list[dict] = []
    result = service.submit(draft.report_id, transport=_ok_transport(calls), credential_lookup=_fake_credential)
    assert result.status == "submitted"

    rows = service.receipts()
    assert len(rows) == 1
    row = rows[0]
    assert row["report_id"] == draft.report_id
    assert row["fingerprint"] == draft.fingerprint
    assert row["payload_sha256"] == preview.payload_sha256
    assert row["destination"] == DESTINATION
    assert row["issue_url"].endswith("/issues/11")
    assert row["issue_number"] == 11
    assert row["total_bytes"] == preview.total_bytes
    assert isinstance(row["field_names"], list) and "expected" in row["field_names"]
    assert any(name.startswith("logs-") for name in row["attachment_names"])
    assert row["credential_source"]  # label present
    assert set(row.keys()) == {
        "seq", "prev_hash", "event_hash", "receipt_id", "report_id", "fingerprint",
        "payload_sha256", "destination", "issue_url", "issue_number", "submitted_at",
        "total_bytes", "field_names", "attachment_names", "credential_source",
    }

    receipts_path = tmp_path / "bug_reports" / "receipts.jsonl"
    raw = receipts_path.read_bytes()
    assert SECRET.encode() not in raw
    assert b"provider 500" not in raw          # no error-message content
    assert b"daemon.log\n" not in raw           # no log content (name-only references are fine)
    body_text = preview.issue["body"]
    assert body_text.encode() not in raw        # the report body itself is never in the receipt


def test_receipt_chain_verifies_and_detects_tampering(tmp_path) -> None:
    service = _service(tmp_path)
    draft, _preview = _approved_draft(service)
    calls: list[dict] = []
    assert service.submit(draft.report_id, transport=_ok_transport(calls), credential_lookup=_fake_credential).status == "submitted"
    assert service.verify_receipts() is True

    receipts_path = tmp_path / "bug_reports" / "receipts.jsonl"
    lines = receipts_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["destination"] = "attacker/drop"
    lines[0] = json.dumps(row)
    receipts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert service.verify_receipts() is False


def test_receipt_chain_links_across_multiple_submissions(tmp_path) -> None:
    service = _service(tmp_path)
    first, _ = _approved_draft(service, title="one")
    second, _ = _approved_draft(service, title="two", category="hang",
                                 error_text='  File "core/y.py", line 9, in h\nTimeoutError: slow')
    calls: list[dict] = []
    assert service.submit(first.report_id, transport=_ok_transport(calls), credential_lookup=_fake_credential).status == "submitted"
    assert service.submit(second.report_id, transport=_ok_transport(calls), credential_lookup=_fake_credential).status == "submitted"
    rows = service.receipts()
    assert len(rows) == 2
    assert rows[0]["event_hash"] == rows[1]["prev_hash"]
    assert service.verify_receipts() is True


def test_missing_credential_fails_closed_without_network(tmp_path) -> None:
    import os

    service = _service(tmp_path)
    draft, _preview = _approved_draft(service)
    for name in ("VOOL_BUG_REPORT_GITHUB_TOKEN", "GITHUB_TOKEN"):
        os.environ.pop(name, None)

    def no_network(*args, **kwargs):
        raise AssertionError("no network call may happen without a credential")

    result = service.submit(draft.report_id, transport=no_network, credential_lookup=lambda: (None, "env:ABSENT"))
    assert result.status == "failed"
    assert "credential" in result.detail.lower()
    assert service.receipts() == []
