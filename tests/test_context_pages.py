"""Context pages — the residency law, erasure, and verbatim re-injection.

Every invariant here is written so that sabotaging the law turns a named test
red (CLAUDE.md §6b.4): if a page pinned by open work can be archived, if
erasure can touch a PERSISTENT page, or if page_in serves unverified bytes,
the specific test named for that cause must fail.

Pages are content-addressed: kind + source + content IS the identity, so two
admissions of identical evidence share one page (dedup is the point). Tests
therefore use DISTINCT content per scenario — sharing a hash would share pins
across scenarios, which is the law working, not a bug.
"""

from __future__ import annotations

import pytest

from core.context_pages import (
    ContextPage,
    ErasureNotAllowedError,
    PageNotFoundError,
    PinActiveError,
    admit_page,
    archive_closed_pages,
    erase_page,
    new_pin_id,
    page_in,
    pin_page,
    release_pin,
    restorable_pages,
    session_audit_root,
)

_TEST_SESSION = "sess-audit-1"


def _page(content: str, **overrides) -> ContextPage:
    defaults = dict(
        kind="tool_result",
        content=content,
        source="currency.reference",
        session_id=_TEST_SESSION,
        task_id="",
    )
    defaults.update(overrides)
    return ContextPage(**defaults)


def test_page_in_returns_admitted_content_verbatim():
    page = _page('{"asset": "alpha", "rate": 0.91}')
    admit_page(page)
    assert page_in(page.page_hash) == page.content


def test_identical_content_deduplicates_to_one_page():
    first = admit_page(_page('{"asset": "dup", "rate": 1.0}', task_id="task-dup-1"))
    second = admit_page(_page('{"asset": "dup", "rate": 1.0}', task_id="task-dup-2"))
    assert first["page_hash"] == second["page_hash"]
    assert second["deduplicated"] is True
    # releasing one task's pin leaves the other task's pin in force:
    release_pin(first["page_hash"], pin_id="task:task-dup-1")
    records = archive_closed_pages(session_id=_TEST_SESSION)
    assert all(r["page_hash"] != first["page_hash"] for r in records)


def test_erasable_pages_never_reach_the_persistent_store():
    # Placement is the only erasure enforcement point that works: provider
    # caches cannot be deleted, so ERASABLE bytes must never be admitted.
    page = _page('{"asset": "volatile", "note": "never persist"}',
                 retention_class="ERASABLE")
    admitted = admit_page(page)
    assert admitted["volatile"] is True
    with pytest.raises(PageNotFoundError, match="volatile-only"):
        page_in(page.page_hash)
    assert all(
        p["page_hash"] != page.page_hash
        for p in restorable_pages(session_id=_TEST_SESSION)
    )


def test_pinned_page_cannot_be_archived_while_its_obligation_is_open():
    # THE RESIDENCY LAW. Sabotage check: if archive_closed_pages ever
    # archives a pinned page, this test must fail with the law's name.
    page = _page('{"asset": "pinned", "obligation": "open"}', task_id="task-open")
    admit_page(page)
    records = archive_closed_pages(session_id=_TEST_SESSION)
    assert all(r["page_hash"] != page.page_hash for r in records), (
        "residency law violated: a page pinned by an open task was archived"
    )


def test_released_pin_allows_archival_with_receipted_digest_record():
    page = _page('{"asset": "closing", "obligation": "done"}', task_id="task-closing")
    admit_page(page)
    release_pin(page.page_hash, pin_id="task:task-closing")
    records = archive_closed_pages(session_id=_TEST_SESSION)
    assert page.page_hash in [r["page_hash"] for r in records]
    record = next(r for r in records if r["page_hash"] == page.page_hash)
    assert record["kind"] == "tool_result"
    assert len(record["page_hash"]) == 64


def test_archived_page_can_be_recalled_verbatim():
    page = _page('{"asset": "recalled", "hop": 2}', task_id="task-recall")
    admit_page(page)
    release_pin(page.page_hash, pin_id="task:task-recall")
    archive_closed_pages(session_id=_TEST_SESSION)
    # Archive changes residency, not existence: recall is byte-identical.
    assert page_in(page.page_hash) == page.content


def test_erasing_a_persistent_page_is_refused():
    content = '{"asset": "persistent", "keep": true}'
    page = _page(content)
    admitted = admit_page(page)
    with pytest.raises(ErasureNotAllowedError):
        erase_page(admitted["page_hash"])
    # refused means refused: the bytes are still servable
    assert page_in(admitted["page_hash"]) == content


def test_erasing_a_pinned_erasable_page_is_refused():
    page = _page('{"asset": "pinned-erasable", "note": "wait for release"}',
                 retention_class="ERASABLE", task_id="task-still-open")
    admitted = admit_page(page)
    with pytest.raises(PinActiveError):
        erase_page(admitted["page_hash"])


def test_erasure_removes_bytes_but_keeps_a_provable_hash_receipt():
    page = _page('{"asset": "erasable", "note": "right to be forgotten"}',
                 retention_class="ERASABLE")
    admitted = admit_page(page)
    result = erase_page(admitted["page_hash"])
    assert len(result["erasure_receipt"]) == 64
    with pytest.raises(PageNotFoundError):
        page_in(page.page_hash)


def test_page_in_of_unknown_hash_names_the_hash():
    with pytest.raises(PageNotFoundError, match="deadbeef"):
        page_in("deadbeef" * 8)


def test_unknown_trust_and_retention_classes_are_rejected_at_admission():
    with pytest.raises(ValueError, match="trust_class"):
        _page('{"x": 1}', trust_class="SORT_OF_TRUSTED")
    with pytest.raises(ValueError, match="retention_class"):
        _page('{"x": 1}', retention_class="KEEP_FOREVER")


def test_audit_root_is_order_stable():
    admit_page(_page('{"asset": "root-a"}'))
    admit_page(_page('{"asset": "root-b"}'))
    root_one = session_audit_root(_TEST_SESSION)
    root_two = session_audit_root(_TEST_SESSION)
    assert root_one == root_two
    assert root_one["page_count"] >= 2
    assert len(root_one["audit_root"]) == 64


def test_manual_pins_are_honored_by_the_residency_law():
    page = _page('{"asset": "review-held", "note": "held by an open review"}')
    admit_page(page)
    pin = new_pin_id()
    pin_page(page.page_hash, pin_id=pin, reason="held by an open review")
    assert all(
        r["page_hash"] != page.page_hash
        for r in archive_closed_pages(session_id=_TEST_SESSION)
    )
    release_pin(page.page_hash, pin_id=pin)
    assert any(
        r["page_hash"] == page.page_hash
        for r in archive_closed_pages(session_id=_TEST_SESSION)
    )


def test_unverified_bytes_are_never_served_by_page_in():
    # Tamper with the CAS behind a page's back: the served address must go
    # loud instead of handing back confidently wrong evidence.
    import hashlib

    from storage.chunk_store import chunk_root

    content = '{"asset": "tampered-target", "integrity": true}'
    page = _page(content)
    admit_page(page)
    canonical = page.content.encode("utf-8")
    chunk_hash = hashlib.sha256(canonical).hexdigest()
    chunk_path = chunk_root() / chunk_hash[:2] / chunk_hash[2:4] / chunk_hash
    original = chunk_path.read_bytes()
    try:
        chunk_path.write_bytes(original + b' "corruption": true}')
        with pytest.raises(PageNotFoundError, match="hash verification"):
            page_in(page.page_hash)
    finally:
        chunk_path.write_bytes(original)
