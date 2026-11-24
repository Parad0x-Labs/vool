"""No page the docs site publishes names a word in tools/public_names.py.

The published pages are the ones docs/SUMMARY.md lists. Generated pages leave those names out at
the generator; this check also catches a hand-written page and any new generator.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools import public_names

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
_LINK = re.compile(r"\]\(([^)#\s]+)")


def _published() -> list[Path]:
    pages = []
    for raw in _LINK.findall((DOCS / "SUMMARY.md").read_text(encoding="utf-8")):
        if raw.startswith(("http://", "https://", "mailto:")):
            continue
        page = (DOCS / raw).resolve()
        if page.is_file():
            pages.append(page)
    return pages


def test_the_summary_lists_pages():
    assert len(_published()) > 20


@pytest.mark.parametrize("page", _published(), ids=lambda page: page.relative_to(DOCS).as_posix())
def test_a_published_page_names_none_of_the_kept_back_words(page):
    assert public_names.found_in(page.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("word", sorted(public_names.UNNAMED))
def test_the_matcher_finds_whole_words_in_any_case_only(word):
    assert public_names.found_in(f"the {word.upper()} tools and `{word}.publish`") == [word]
    assert public_names.found_in(f"{word}1 and my{word} are other words") == []
    assert public_names.unnamed(word.upper()) and not public_names.unnamed(word[:-1])


@pytest.mark.parametrize("word", sorted(public_names.UNNAMED))
def test_a_generator_refuses_a_page_that_still_names_one(word):
    with pytest.raises(SystemExit, match=word):
        public_names.refuse_if_named("page.md", f"a row for {word}.publish")
    assert public_names.refuse_if_named("page.md", "plain text") == "plain text"
