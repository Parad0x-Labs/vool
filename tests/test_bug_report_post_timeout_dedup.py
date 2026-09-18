"""A timeout AFTER the POST reached GitHub must not create a duplicate issue.

NEW regression for the diagnostics-reporting goal. The scenario is not the base's
sequential double-submit (already covered by the adversarial suite): the FIRST submit's
POST genuinely reaches the destination and creates the issue, but the response never
comes back, so the caller sees a timeout with an UNKNOWN outcome. The retry must find
the already-created issue through the fingerprint-keyed dedup search and return
"duplicate" -- a second create is refused, and the unknown outcome is never guessed
into either "submitted" or "failed-for-good".
"""
from __future__ import annotations

import json

from core.bug_report import BugReportService

SECRET = "ghp_" + "R9r8R7r6R5r4R3r2R1r0R9r8R7r6"
DESTINATION = "example-owner/example-repo"


def _credential():
    return ("test-token-not-a-real-credential", "env:TEST")


def _approved(service) -> str:
    draft = service.create_draft(
        expected="works",
        actual=f"fails after token {SECRET} expiry",
        repro_steps=["run one turn"],
        error_text='RuntimeError: provider 500',
        category="crash",
        destination_repo=DESTINATION,
        title="provider 500",
    )
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    return draft.report_id


def test_retry_after_post_timeout_dedups_against_the_created_issue(tmp_path) -> None:
    service = BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)
    report_id = _approved(service)

    created: list[bytes] = []
    searches: list[str] = []

    def transport(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            searches.append(url)
            if not created:
                # before the first POST the issue does not exist yet
                return 200, b'{"total_count": 0, "items": []}'
            body = json.dumps({
                "total_count": 1,
                "items": [{"html_url": f"https://github.com/{DESTINATION}/issues/41"}],
            }).encode()
            return 200, body
        # the POST reaches GitHub and CREATES the issue -- then the response is lost
        created.append(data)
        raise TimeoutError("read timed out after the request was sent")

    first = service.submit(report_id, transport=transport, credential_lookup=_credential)
    assert first.status == "failed"
    assert first.failure_code == "upstream_timeout"
    assert len(created) == 1  # the bytes DID leave the machine once
    assert service.receipts() == []  # nothing may claim an outcome it does not know

    second = service.submit(report_id, transport=transport, credential_lookup=_credential)
    assert second.status == "duplicate"
    assert second.duplicate_of == f"https://github.com/{DESTINATION}/issues/41"
    # exactly one create ever happened: the retry deduped BEFORE creating again
    assert len(created) == 1
    assert len(searches) == 2  # both attempts searched; only the first searched empty


def test_retry_sends_the_identical_bytes_no_silent_changes(tmp_path) -> None:
    """During the unknown-outcome window nothing about the report may silently change:
    the retry sends byte-identical payload under the same still-valid consent."""
    service = BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)
    report_id = _approved(service)

    posts: list[bytes] = []
    attempts: list[int] = []

    def transport(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        posts.append(data)
        attempts.append(len(posts))
        if len(posts) == 1:
            raise TimeoutError("connection dropped after send")
        return 201, json.dumps({"number": 9, "html_url": f"https://github.com/{DESTINATION}/issues/9"}).encode()

    first = service.submit(report_id, transport=transport, credential_lookup=_credential)
    assert first.status == "failed"
    second = service.submit(report_id, transport=transport, credential_lookup=_credential)
    assert second.status == "submitted"
    assert len(posts) == 2
    assert posts[0] == posts[1]  # byte-identical: no silent report changes during retries
