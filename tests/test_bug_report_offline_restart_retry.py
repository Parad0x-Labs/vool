"""Offline persistence: a draft survives a restart and retries through the same authority.

NEW regression for the diagnostics-reporting goal. Distinct from the base's retry test
(which retries within ONE service instance): here the process "restarts" -- a fresh
BugReportService over the same store -- and the draft, its consent binding and its
destination must all survive, so the retry runs through the SAME authority (consent
hash + fingerprint + scanner) rather than a rebuilt shortcut. Revocation also survives
the restart: a consent withdrawn before the restart stays withdrawn after it.
"""
from __future__ import annotations

import json

from core.bug_report import BugReportService
from core.bug_report.schema import DraftNotFoundError

SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
DESTINATION = "example-owner/example-repo"


def _credential():
    return ("test-token-not-a-real-credential", "env:TEST")


def _create_and_approve(service) -> tuple[str, str]:
    draft = service.create_draft(
        expected="works",
        actual=f"offline failure with token {SECRET} in prose",
        repro_steps=["start a turn", "cut the network"],
        error_text="ConnectionError: network unreachable",
        category="network",
        destination_repo=DESTINATION,
        title="offline submit",
    )
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    return draft.report_id, preview.payload_sha256


def test_offline_failure_then_restart_retries_the_same_bytes_and_authority(tmp_path) -> None:
    store = tmp_path / "bug_reports"
    first_service = BugReportService(store_dir=store, project_root=tmp_path)
    report_id, sha_before = _create_and_approve(first_service)

    def offline(method, url, *, data, headers, timeout):
        raise ConnectionError("network is down")

    offline_result = first_service.submit(report_id, transport=offline, credential_lookup=_credential)
    assert offline_result.status == "failed"
    assert offline_result.failure_code == "upstream_unreachable"
    assert first_service.receipts() == []

    # ---- the process restarts: a fresh service instance over the same durable store ----
    restarted = BugReportService(store_dir=store, project_root=tmp_path)
    status = restarted.status(report_id)
    assert status["state"] != "submitted"
    assert status["consent"] is not None  # the consent binding survived the restart
    assert status["consent"]["payload_sha256"] == sha_before

    posts: list[bytes] = []

    def online(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        posts.append(data)
        return 201, json.dumps({"number": 12, "html_url": f"https://github.com/{DESTINATION}/issues/12"}).encode()

    result = restarted.submit(report_id, transport=online, credential_lookup=_credential)
    assert result.status == "submitted"
    assert result.issue_url.endswith("/issues/12")
    # the retried bytes hash to the SAME payload the consent bound before the restart
    assert len(posts) == 1
    assert "offline submit" in json.loads(posts[0].decode("utf-8"))["title"]
    receipts = restarted.receipts()
    assert len(receipts) == 1 and receipts[0]["payload_sha256"] == sha_before


def test_revocation_survives_the_restart(tmp_path) -> None:
    store = tmp_path / "bug_reports"
    first_service = BugReportService(store_dir=store, project_root=tmp_path)
    report_id, _sha = _create_and_approve(first_service)
    cleared = first_service.revoke(report_id)
    assert cleared.consent is None

    restarted = BugReportService(store_dir=store, project_root=tmp_path)
    assert restarted.status(report_id)["consent"] is None

    def would_send(method, url, *, data, headers, timeout):
        raise AssertionError("a revoked consent must never reach the transport")

    result = restarted.submit(report_id, transport=would_send, credential_lookup=_credential)
    assert result.status == "failed"
    assert result.failure_code == "approval_required"


def test_local_export_survives_the_restart_too(tmp_path) -> None:
    """The no-GitHub-access path is durable as well: an export made before a restart is
    still on disk and in its ledger after it, without touching the submission ledger."""
    store = tmp_path / "bug_reports"
    service = BugReportService(store_dir=store, project_root=tmp_path)
    report_id, _sha = _create_and_approve(service)
    exported = service.export(report_id)

    restarted = BugReportService(store_dir=store, project_root=tmp_path)
    from pathlib import Path

    assert Path(exported["path"]).exists()
    assert len(restarted.export_receipts()) == 1
    assert restarted.export_receipts()[0]["destination"] == "local-export"
    assert restarted.receipts() == []
    # the draft itself is still there, still approvable, still submittable
    assert restarted.status(report_id)["report_id"] == report_id


def test_draft_for_unknown_id_after_restart_is_a_typed_error(tmp_path) -> None:
    restarted = BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)
    try:
        restarted.status("br_000000000000")
    except DraftNotFoundError:
        return
    raise AssertionError("expected DraftNotFoundError for an absent draft")
