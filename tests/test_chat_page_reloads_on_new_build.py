"""A chat window must not keep running old JavaScript while claiming the new version.

Measured 2026-07-31: an operator's VOOL window showed `(a9ac0da)` in its header -- the header string
is FETCHED live from /api/runtime/version -- while the page itself had been loaded before the
renderer landed. Every table, heading and bold run in an answer reached them as literal `|`, `##`
and `**` characters, and the version string said they were up to date. The same answer, rendered by
the current page, produced 2 tables, 5 headings and zero leaked markdown characters.

The page therefore carries the commit of the runtime that SERVED it, compares against the live
commit at load and once a minute after, and reloads itself once per new commit. These tests hold the
substitution seam and the guards around it.
"""
from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html


def test_the_serving_commit_is_baked_into_the_page() -> None:
    html = render_vool_chat_html(build_commit="abc123def456")
    assert "const PAGE_BUILD_COMMIT = 'abc123def456';" in html
    assert "__PAGE_BUILD_COMMIT__" not in html


def test_an_empty_commit_disables_the_check_rather_than_looping_it() -> None:
    """No commit means no comparison. The client also refuses to act on an unsubstituted or empty
    value, so the reload can never fire on every poll and loop the page."""
    html = render_vool_chat_html()
    assert "const PAGE_BUILD_COMMIT = '';" in html
    assert "__PAGE_BUILD_COMMIT__" not in html


def test_the_client_guards_are_present() -> None:
    """The reload is once-per-commit and marked BEFORE reloading -- the two properties that make an
    automatic reload safe. Asserted as source text because the page is a static document; if the
    guard lines move, this names exactly what was lost."""
    html = render_vool_chat_html(build_commit="abc123")
    # Refuses to fire without a live commit, without a baked commit, or on an unsubstituted page.
    assert "if (!liveCommit || !PAGE_BUILD_COMMIT || PAGE_BUILD_COMMIT.indexOf('__') === 0) return;" in html
    # Same build: nothing happens.
    assert "if (liveCommit === PAGE_BUILD_COMMIT) return;" in html
    # Marked before location.reload(), so a broken substitution cannot loop the window.
    marked = html.index("sessionStorage.setItem(key, '1');")
    reloaded = html.index("location.reload();", marked)
    assert marked < reloaded
    # The upgrade is noticed within a minute, not only at load.
    assert "setInterval(" in html and "60000" in html
