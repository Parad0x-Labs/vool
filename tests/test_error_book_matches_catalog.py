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
