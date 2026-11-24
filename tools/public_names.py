"""Names that the published docs (the pages docs/SUMMARY.md lists) never show.

One list for every page generator and for the docs-site check. A generator leaves out whatever
these words name, such as a tool surface, and refuses to write a page that still contains one. A
hand-written page that names one fails tests/test_published_docs_keep_names_back.py. Adding a name
here is the one change that keeps it off every published page.
"""
from __future__ import annotations

import re

#: Lower-case words, matched whole and in any letter case.
UNNAMED: frozenset[str] = frozenset({"web0"})

_PATTERN = re.compile(r"(?<![a-z0-9])(?:" + "|".join(sorted(map(re.escape, UNNAMED))) + r")(?![a-z0-9])", re.IGNORECASE)


def unnamed(word: str) -> bool:
    """True when ``word`` (a tool surface, a tool-name prefix) is one the published docs leave out."""
    return str(word or "").strip().lower() in UNNAMED


def found_in(text: str) -> list[str]:
    """The kept-back words that ``text`` contains, lower-case and in order of first appearance."""
    return list(dict.fromkeys(match.group(0).lower() for match in _PATTERN.finditer(str(text or ""))))


def refuse_if_named(where: str, text: str) -> str:
    """``text`` unchanged, or SystemExit naming the words a published page may not show."""
    words = found_in(text)
    if words:
        raise SystemExit(f"{where}: a published page may not name {', '.join(words)} (tools/public_names.py)")
    return text
