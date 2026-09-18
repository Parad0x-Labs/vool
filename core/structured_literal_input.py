from __future__ import annotations

import re

_STRUCTURED_LITERAL_HINT_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?:^|[\s(])(?:/|~/|\./)[^\s]+"
    r"|"
    r"\b[A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml|sh)\b"
    r")"
)

_STRUCTURED_LITERAL_MARKERS = (
    "with exactly this content",
    "with exactly this code",
    "with this content",
    "with this code",
    "that says:",
    "create a file",
    "create file",
    "file named",
    "append a second line",
    "append line",
    "append ",
    "overwrite ",
    "read the whole file",
    "read the file",
    "inside ",
    "under ",
    " in /",
    "update ",
    "edit ",
    "change ",
    "patch ",
)


def looks_like_structured_literal_input(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw or not _STRUCTURED_LITERAL_HINT_RE.search(raw):
        return False
    lowered = raw.lower()
    if any(marker in lowered for marker in _STRUCTURED_LITERAL_MARKERS):
        return True
    return "\n" in raw and ("def " in lowered or "class " in lowered or "->" in raw)
