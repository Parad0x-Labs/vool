"""The file-name rule for chat exports: a chat title made safe to be part of a file name.

Standard library only, on purpose. The native window's save dialog checks the server's export
name with the same rule (installer/bundle/vool_window.py), and on Linux that window runs on the
system python3, where the API server's own dependencies are not installed. The chat export API
(core/web/api/chat_export_api.py) imports these names from here, so the rule has one home.
"""
from __future__ import annotations

import re
import unicodedata

# These characters cannot appear in a file name on at least one desktop system (or would be read
# as a path), so they become spaces.
_FILENAME_FORBIDDEN = frozenset('/\\:*?"<>|')
# A title's share of the file name, in UTF-8 bytes: the name as a whole must stay well under
# the 255-byte limit common file systems set, with room for " - VOOL chat - 2026-10-19.pdf".
MAX_FILENAME_TITLE_BYTES = 120
_WHITESPACE = re.compile(r"\s+")
_ZWJ = "‍"     # joins emoji into one picture; kept
_ZWNJ = "‌"    # part of how Persian and Indic words are spelled; kept


def _extends_previous(ch: str) -> bool:
    """True for a character that belongs to the one before it: a joiner, a combining mark,
    an emoji variation selector, a skin tone or an emoji tag."""
    code = ord(ch)
    return (ch == _ZWJ or unicodedata.category(ch) in ("Mn", "Me", "Mc")
            or code in (0xFE0E, 0xFE0F) or 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F)


def _grapheme_safe_cut(text: str, index: int) -> int:
    """Move a cut point back until it falls between two whole characters as a person sees
    them, so a cut never leaves half an emoji, a lone mark or half of a flag."""
    while 0 < index < len(text) and (_extends_previous(text[index]) or text[index - 1] == _ZWJ):
        index -= 1
    if 0 < index < len(text) and "\U0001f1e6" <= text[index] <= "\U0001f1ff":
        flags = 0
        while flags < index and "\U0001f1e6" <= text[index - 1 - flags] <= "\U0001f1ff":
            flags += 1
        if flags % 2:
            index -= 1
    return index


def filename_title(title: str, *, max_bytes: int = MAX_FILENAME_TITLE_BYTES) -> str:
    """A chat title made safe to be part of a file name on Windows, macOS and Linux.

    Letters of every script and emoji are kept. Path characters, characters a desktop system
    forbids in file names, control characters and invisible direction overrides (which could
    make ``report<RLO>fdp.exe`` look like a PDF) become spaces. Leading dots (a hidden file) and
    dashes (read as an option by command-line tools) go, and so do trailing dots and spaces
    (dropped by Windows). A long title is cut at a word where
    possible, within ``max_bytes`` of UTF-8, and marked with an ellipsis.
    """
    text = unicodedata.normalize("NFC", str(title or ""))
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch in (_ZWJ, _ZWNJ):
            out.append(ch)
        elif ch in _FILENAME_FORBIDDEN or category[0] in ("C", "Z"):
            out.append(" ")
        else:
            out.append(ch)
    cleaned = _WHITESPACE.sub(" ", "".join(out)).strip()
    cleaned = cleaned.lstrip(". -").rstrip(". ")
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return cleaned
    budget = max_bytes - len("…".encode())
    index = len(cleaned)
    while index and len(cleaned[:index].encode("utf-8")) > budget:
        index -= 1
    index = _grapheme_safe_cut(cleaned, index)
    cut = cleaned[:index]
    space = cut.rfind(" ")
    if space >= len(cut) // 2:
        cut = cut[:space]
    cut = cut.rstrip(" .-")
    return (cut + "…") if cut else ""
