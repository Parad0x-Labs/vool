"""Source-level pins for the bug-report UI extensions (diagnostics-reporting goal).

NEW regressions pinning the user-facing wiring the goal added on top of the existing
flow (browser-level proof of the full journey is the served end-to-end evidence, not
these source assertions):

- the form prefills the SERVER-OWNED default destination and shows WHERE a report goes;
- the review screen offers the local-copy alternative (no GitHub access needed) and
  explicit approval withdrawal;
- a submission failure explains itself plain-words-first with the technical facts in an
  expandable disclosure, and offers the local copy;
- an Activity history card for a chat with failures gets a Report affordance wired to
  the sanitized report flow.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.vool_chat_page import render_vool_chat_html  # noqa: E402


def _chat_html() -> str:
    return render_vool_chat_html(build_commit="test")


def test_page_calls_the_destination_endpoint_and_prefills_it() -> None:
    html = _chat_html()
    assert "/api/bug-report/destination" in html
    assert "brLoadDefaultDestination" in html
    # the pick form seeds its destination field from the server-owned default
    assert "destination: brDefaultDestination" in html
    # the draft response's destination is authoritative for the review screen
    assert "data.draft.destination_repo" in html


def test_review_screen_offers_local_copy_and_withdrawal() -> None:
    html = _chat_html()
    assert "/api/bug-report/export" in html
    assert "Save a local copy instead" in html
    assert 'id="brExport"' in html
    assert 'id="brRevoke"' in html
    assert "/api/bug-report/revoke" in html
    assert "Withdraw approval" in html


def test_submission_failure_is_plain_first_with_expandable_technical_detail() -> None:
    html = _chat_html()
    assert "br-tech" in html           # the <details> disclosure element
    assert "Technical details" in html
    assert "diag.code" in html         # the stable condition code is shown, expandably
    assert "Save a local copy instead" in html


def test_activity_history_failed_chat_has_a_report_affordance() -> None:
    html = _chat_html()
    assert "data-history-report" in html          # the sibling Report button on failed cards
    assert "xp-history-row" in html               # the wrapper that keeps card + button valid HTML
    assert "bounded.failure_count" in html        # gated on the chat having actually failed
    # the button switches to that chat and opens the SAME sanitized flow
    assert "await openSession(sid)" in html


def test_failed_turn_and_home_entries_are_still_wired() -> None:
    html = _chat_html()
    assert "reportProblemForFailedRun" in html
    assert 'id="reportBtn"' in html
    assert "openBugReport" in html


def test_no_credential_leaves_the_page() -> None:
    """The distributed page never carries a token: submission is server-side only, and
    the page source must not embed any credential literal or storage of one."""
    html = _chat_html()
    for forbidden in ("VOOL_BUG_REPORT_GITHUB_TOKEN", "Authorization", "localStorage.setItem('gh", "ghp_"):
        assert forbidden not in html, forbidden
