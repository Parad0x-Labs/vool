"""``docs/ERROR_BOOK.md`` is a generated view of ``vool.fault.v1`` — it must never fork it.

These fail closed if the committed doc drifts from the catalog: a new/changed/removed fault, or a
hand-edit of the doc, reds here until ``python -m tools.generate_error_book`` is re-run. This is the
CI law "ERROR_BOOK stays in sync with the canonical fault taxonomy."
"""
from __future__ import annotations

import pathlib

from core.faults.catalog import all_codes, catalog_digest
from tools.generate_error_book import build_error_book_markdown

DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "ERROR_BOOK.md"


def test_error_book_exists() -> None:
    assert DOC.is_file(), "docs/ERROR_BOOK.md must exist (run: python -m tools.generate_error_book)"


def test_error_book_is_byte_identical_to_the_generator() -> None:
    """The committed doc must equal what the generator emits from the live catalog right now."""
    expected = build_error_book_markdown()
    actual = DOC.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/ERROR_BOOK.md is out of sync with core/faults/catalog.py — "
        "regenerate with `python -m tools.generate_error_book`."
    )


def test_error_book_stamps_the_current_catalog_digest() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"`{catalog_digest()}`" in text, "the doc must stamp the current catalog digest"


def test_every_catalog_code_appears_in_the_book() -> None:
    text = DOC.read_text(encoding="utf-8")
    missing = [code for code in all_codes() if f"`{code}`" not in text]
    assert not missing, f"ERROR_BOOK is missing catalog codes: {missing}"


DEV_DOC = DOC.parent / "FAULT_BOUNDARIES.md"


def test_the_developer_view_is_byte_identical_to_the_generator() -> None:
    """Code owners and the boundary matrix live in their own generated file, off the docs site."""
    from tools.generate_error_book import build_fault_boundaries_markdown

    assert DEV_DOC.is_file(), "docs/FAULT_BOUNDARIES.md must exist (run: python -m tools.generate_error_book)"
    assert DEV_DOC.read_text(encoding="utf-8") == build_fault_boundaries_markdown(), (
        "docs/FAULT_BOUNDARIES.md is out of sync with core/faults — "
        "regenerate with `python -m tools.generate_error_book`."
    )


def test_every_code_has_exactly_one_section_headed_by_the_code() -> None:
    """The ``### <code>`` heading is the code's stable anchor: one per code, never two."""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    for code in all_codes():
        assert lines.count(f"### {code}") == 1, f"{code}: expected exactly one section heading"


def test_the_published_book_names_no_internal_module() -> None:
    """The book is published on the docs site; module paths belong in the developer view."""
    import re

    text = DOC.read_text(encoding="utf-8")
    assert not re.search(r"`core[./]|\bcore\.[a-z_]+\.|\.py\b|apps/", text), (
        "the published Error Book must not name internal modules or source files"
    )
    summary = (DOC.parent / "SUMMARY.md").read_text(encoding="utf-8")
    assert "(ERROR_BOOK.md)" in summary, "the Error Book is published from SUMMARY.md"
    assert "FAULT_BOUNDARIES.md" not in summary, "the developer view stays off the docs site"
