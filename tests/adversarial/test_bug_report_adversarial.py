"""Adversarial input family for the safe bug reporter.

Malformed, hostile, and edge payloads against the whole pipeline: control characters and
null bytes, ANSI escapes, homoglyphs, megabyte-scale inputs, split-token smuggling (where
the compensating control is exact-preview + approval, asserted here), duplicate
submission, path-traversal ids, corrupt draft files, and injected extra fields on disk.
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
SECRET = "sk-or-v1-" + "n" * 48


def _service(tmp_path):
    return BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)


def _kwargs(**overrides):
    base = dict(
        expected="fine",
        actual="broken",
        repro_steps=["one step"],
        error_text="",
        category="crash",
        lanes=[],
        tools=[],
        models=[],
        log_sources=[],
        destination_repo=DESTINATION,
        title="title",
    )
    base.update(overrides)
    return base


def test_control_characters_and_null_bytes_are_normalized(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs(
        actual=f"crash\x00 after \x1b[31mred\x1b[0m output with \x07 bell and key {SECRET}",
    ))
    blob = json.dumps(draft.to_dict())
    assert "\x00" not in blob
    assert "\x1b" not in blob
    assert "\x07" not in blob
    assert SECRET not in blob


def test_homoglyph_input_does_not_crash_or_bypass_redaction(tmp_path) -> None:
    service = _service(tmp_path)
    # Cyrillic 'о' inside the vendor prefix defeats prefix matching, but the VALUE is a
    # high-entropy run: the entropy rule must still mask it. Prefix-shape redaction is not
    # the only layer.
    smuggled = "Np3kQ7mZ9xL2vR5tW8yB4cD6fG1hJ0sA"
    homoglyph = "sk-оr-v1-" + smuggled
    draft = service.create_draft(**_kwargs(actual=f"failed near {homoglyph}"))
    blob = json.dumps(draft.to_dict())
    assert smuggled not in blob  # the entropy rule masked the smuggled value


def test_megabyte_scale_error_text_is_bounded_not_fatal(tmp_path) -> None:
    service = _service(tmp_path)
    huge = "RuntimeError: " + ("z" * 3_000_000)
    draft = service.create_draft(**_kwargs(error_text=huge, actual="x" * 2_000_000))
    blob = json.dumps(draft.to_dict())
    assert len(blob) < 200_000  # bounded by capture caps, not by input size


def test_split_token_across_lines_is_visible_in_the_exact_preview(tmp_path) -> None:
    """A token split across two lines can defeat regex redaction. The compensating control
    is the exact-bytes preview: whatever WILL leave the machine is exactly what the user
    approved. Assert the preview never silently transforms content after approval."""
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs(
        actual=f"leak half A {'ghp_' + 'H' * 20} then line break\nand half B {'H' * 20 + 'END'}",
    ))
    preview = service.preview(draft.report_id)
    import hashlib

    outbound = json.dumps(preview.issue, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert hashlib.sha256(outbound).hexdigest() == preview.payload_sha256
    # and the scanner still catches the joined shape when it appears on one line:
    from core.bug_report.scanner import scan_text

    assert scan_text("joined ghp_" + "H" * 20 + "END tail")


def test_sequential_double_submit_is_duplicate_not_two_issues(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs())
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)
    creates: list[dict] = []

    def transport(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        creates.append({"url": url})
        return 201, json.dumps({"number": 8, "html_url": f"https://github.com/{DESTINATION}/issues/8"}).encode()

    first = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    second = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert first.status == "submitted"
    assert second.status == "duplicate"
    assert second.duplicate_of == first.issue_url
    assert len(creates) == 1


def test_path_traversal_report_id_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    from core.bug_report.schema import DraftNotFoundError

    with pytest.raises(DraftNotFoundError):
        service.status("../../etc/passwd")
    with pytest.raises(DraftNotFoundError):
        service.preview("..%2f..%2fetc%2fpasswd")


def test_corrupt_draft_file_yields_typed_error_not_crash(tmp_path) -> None:
    from core.bug_report.schema import DraftNotFoundError

    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs())
    path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DraftNotFoundError):
        service.status(draft.report_id)
    with pytest.raises(DraftNotFoundError):
        service.preview(draft.report_id)


def test_injected_extra_field_in_draft_file_is_refused(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs())
    path = tmp_path / "bug_reports" / "drafts" / f"{draft.report_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["conversation"] = ["user: private text", "assistant: private reply"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match=r"(?i)unknown|unexpected|conversation"):
        service.preview(draft.report_id)


def test_attachment_payloads_carry_no_secrets_and_are_bounded(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs(
        error_text='  File "core/x.py", line 2, in f\nRuntimeError: boom with ' + SECRET,
        log_sources=[{"name": "daemon.log", "lines": [f"2026-09-01T00:00:00 ERROR {SECRET}"] * 300}],
    ))
    preview = service.preview(draft.report_id)
    for name, payload in preview.attachment_payloads.items():
        assert SECRET.encode() not in payload, f"secret leaked into attachment {name}"
        assert len(payload) <= 70_000, f"attachment {name} exceeds the bounded-log budget"
    assert preview.total_bytes <= 300_000


def test_unicode_direction_overrides_are_stripped(tmp_path) -> None:
    service = _service(tmp_path)
    bidi = "crash after \u202ecritical\u202e error and key " + SECRET
    draft = service.create_draft(**_kwargs(actual=bidi))
    blob = json.dumps(draft.to_dict())
    assert "\u202e" not in blob
    assert "\u202c" not in blob
