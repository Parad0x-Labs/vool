"""Deterministic fingerprint + deduplication for the safe bug reporter.

Cause families: determinism (same root cause -> same fingerprint across report ids and
timestamps), noise tolerance (timestamps, request ids, turn ids, counts), discrimination
(different stack/category/components -> different fingerprint), local receipt dedup, and
destination-side dedup via the search call.
"""
from __future__ import annotations

import json

from core.bug_report import BugReportService
from core.bug_report.fingerprint import compute_fingerprint
from core.bug_report.schema import InvolvedComponents, SanitizedError, StackFrameSanitized


def _fake_credential():
    """Every transport-driven submit in this file carries a fake credential so the
    tests exercise the submission path, not the no-credential fail-closed path."""
    return ("test-token-not-a-real-credential", "env:TEST")



DESTINATION = "example-owner/example-repo"


def _service(tmp_path):
    return BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)


def _kwargs(**overrides):
    base = dict(
        expected="answer served",
        actual="daemon crashed",
        repro_steps=["start daemon", "send a live-data turn"],
        error_text=(
            "Traceback (most recent call last):\n"
            '  File "/Users/fixtureuser/vool/core/conductor.py", line 88, in plan\n'
            "ValueError: cannot merge span 17 for session sess_deadbeef"
        ),
        category="crash",
        lanes=["live_data_typed_plan"],
        tools=["web.fetch"],
        models=["llama-3.1-8b"],
        log_sources=[],
        destination_repo=DESTINATION,
        title="crash on live data turn",
    )
    base.update(overrides)
    return base


def _err() -> SanitizedError:
    return SanitizedError(
        exc_type="ValueError",
        message="cannot merge span for session",
        frames=(
            StackFrameSanitized(file="conductor.py", line=88, function="plan"),
            StackFrameSanitized(file="agent_node.py", line=12, function="_resolve"),
        ),
    )


def test_fingerprint_is_deterministic_and_stable_across_ids_and_times() -> None:
    a = compute_fingerprint(
        category="crash",
        error=_err(),
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=("web.fetch",), models=("llama-3.1-8b",)),
        repro_steps=["start daemon", "send a live-data turn"],
        title="crash on live data turn",
    )
    b = compute_fingerprint(
        category="crash",
        error=_err(),
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=("web.fetch",), models=("llama-3.1-8b",)),
        repro_steps=["start daemon", "send a live-data turn"],
        title="crash on live data turn",
    )
    assert a == b and len(a) >= 16


def test_fingerprint_ignores_timestamps_request_ids_and_counts() -> None:
    noisy_a = compute_fingerprint(
        category="crash", error=_err(),
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=(), models=()),
        repro_steps=["at 2026-09-01T12:00:00 request req_12345 send turn 17"],
        title="crash 2026-09-01 req_12345",
    )
    noisy_b = compute_fingerprint(
        category="crash", error=_err(),
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=(), models=()),
        repro_steps=["at 2027-01-02T03:04:05 request req_98765 send turn 99"],
        title="crash 2027-01-02 req_98765",
    )
    assert noisy_a == noisy_b


def test_fingerprint_ignores_line_numbers_but_honors_functions() -> None:
    shifted = SanitizedError(
        exc_type="ValueError",
        message="cannot merge span for session",
        frames=(
            StackFrameSanitized(file="conductor.py", line=104, function="plan"),
            StackFrameSanitized(file="agent_node.py", line=30, function="_resolve"),
        ),
    )
    same = compute_fingerprint(
        category="crash", error=shifted,
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=(), models=()),
        repro_steps=["start daemon", "send a live-data turn"],
        title="crash on live data turn",
    )
    original = compute_fingerprint(
        category="crash", error=_err(),
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=(), models=()),
        repro_steps=["start daemon", "send a live-data turn"],
        title="crash on live data turn",
    )
    assert same == original

    renamed = SanitizedError(
        exc_type="ValueError",
        message="cannot merge span for session",
        frames=(StackFrameSanitized(file="conductor.py", line=88, function="plan_v2"),),
    )
    different = compute_fingerprint(
        category="crash", error=renamed,
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=(), models=()),
        repro_steps=["start daemon", "send a live-data turn"],
        title="crash on live data turn",
    )
    assert different != original


def test_fingerprint_discriminates_category_and_components() -> None:
    base = dict(
        components=InvolvedComponents(lanes=("live_data_typed_plan",), tools=(), models=()),
        repro_steps=["x"], title="t",
    )
    a = compute_fingerprint(category="crash", error=_err(), **base)
    b = compute_fingerprint(category="hang", error=_err(), **base)
    c = compute_fingerprint(
        category="crash", error=_err(),
        components=InvolvedComponents(lanes=("currency_frontdoor",), tools=(), models=()),
        repro_steps=["x"], title="t",
    )
    assert len({a, b, c}) == 3


def test_drafts_of_the_same_root_cause_share_fingerprint(tmp_path) -> None:
    service = _service(tmp_path)
    first = service.create_draft(**_kwargs(title="crash on live data turn"))
    second = service.create_draft(**_kwargs(title="crash on live data turn again"))
    assert first.fingerprint == second.fingerprint
    assert first.report_id != second.report_id


def test_second_submission_of_same_fingerprint_is_a_duplicate(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs())
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)

    calls: list[dict] = []

    def transport(method, url, *, data, headers, timeout):
        calls.append({"method": method, "url": url})
        if "/search/issues" in url:
            return 200, b'{"total_count": 0, "items": []}'
        return 201, json.dumps({"number": 5, "html_url": f"https://github.com/{DESTINATION}/issues/5"}).encode()

    first_result = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert first_result.status == "submitted"

    second = service.create_draft(**_kwargs())
    second_preview = service.preview(second.report_id)
    service.approve(second.report_id, payload_sha256=second_preview.payload_sha256, confirm=True)
    second_result = service.submit(second.report_id, transport=transport, credential_lookup=_fake_credential)
    assert second_result.status == "duplicate"
    assert second_result.duplicate_of == first_result.issue_url
    creates = [c for c in calls if c["method"] == "POST" and c["url"].endswith("/issues")]
    assert len(creates) == 1  # no second issue was created


def test_destination_side_dedup_returns_existing_issue(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(**_kwargs())
    preview = service.preview(draft.report_id)
    service.approve(draft.report_id, payload_sha256=preview.payload_sha256, confirm=True)

    existing_url = f"https://github.com/{DESTINATION}/issues/3"

    def transport(method, url, *, data, headers, timeout):
        if "/search/issues" in url:
            return 200, json.dumps({
                "total_count": 1,
                "items": [{"html_url": existing_url, "number": 3, "state": "open"}],
            }).encode()
        raise AssertionError("create must not be called when the destination already has this fingerprint")

    result = service.submit(draft.report_id, transport=transport, credential_lookup=_fake_credential)
    assert result.status == "duplicate"
    assert result.duplicate_of == existing_url
