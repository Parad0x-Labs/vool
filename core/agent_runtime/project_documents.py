"""Document asks in a chat bound to a project are answered from the project's files first.

0.7.8 sweep block 3 (2026-11-12): every document turn in a project chat failed -- "read my packing
list file", "which of my files mentions the boiler service?", "where did I write down the boiler
service date", "what's on my packing list?" went to cross-chat recall, the code investigation, the
machine-wide finder, a wrong read_file path or a plain model reply, and none read the project. This
lane searches the bound project's own text files (names and contents, bounded) and answers with the
file it read. Contributor: sls_0x.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

LANE_ID = "project_documents"

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".org", ".rst", ".log", ".ini"}
_MAX_FILES = 400
_MAX_BYTES = 256 * 1024
_EXCERPT_CHARS = 1800

_DOC_ASK_RE = re.compile(
    r"\b(?:(?:read|open|show|display)\s+(?:me\s+)?(?:my|the|our)\s+[\w\s-]{1,40}?(?:file|\w*list|notes?|doc(?:ument)?|plan|budget|recipe)s?\b"
    r"|which\s+(?:of\s+)?(?:my|the|our)\s+(?:files?|notes?|docs?|documents?)\s+(?:mentions?|talks?\s+about|has|have|contains?|says?)\b"
    r"|where\s+(?:did|have)\s+i\s+(?:write|wrote|note|noted|put|save|saved|jot|jotted)\b"
    r"|what(?:'s|\s+is|\s+are)\s+(?:on|in)\s+(?:my|the|our)\s+[\w\s-]{1,40}?\b(?:list|notes?|file|plan|budget|doc(?:ument)?)\b"
    r"|(?:find|search)\s+(?:my|the|our)\s+(?:files?|notes?|docs?|documents?)\s+(?:for|about)\b)",
    re.IGNORECASE,
)
_STOP = {
    "read", "open", "show", "display", "me", "my", "the", "our", "file", "files", "list", "note", "notes", "doc", "docs",
    "document", "documents", "which", "of", "mentions", "mention", "talks", "about", "has", "have", "contains", "contain",
    "says", "say", "where", "did", "i", "write", "wrote", "down", "note", "noted", "put", "save", "saved", "jot", "jotted",
    "what", "what's", "whats", "is", "are", "on", "in", "find", "search", "for", "a", "an", "and", "to", "it", "please",
    "pls", "can", "you", "u", "that", "this", "there", "plan", "budget",
}
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")


def _project_root(session_id: str, source_context: dict[str, Any] | None) -> Path | None:
    """The folder of the project this chat is bound to, or None for an unbound chat."""
    try:
        from core.context_namespace import authoritative_chat_workspace

        root, reason = authoritative_chat_workspace(str(session_id or ""))
    except Exception:
        return None
    if reason != "project" or not root:
        return None
    path = Path(str(root)).expanduser()
    return path if path.is_dir() else None


def _terms(text: str) -> list[str]:
    seen: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        word = word.strip("'-")
        if len(word) >= 3 and word not in _STOP and word not in seen:
            seen.append(word)
    return seen


def _text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= _MAX_FILES:
            break
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES:
            try:
                if path.stat().st_size <= _MAX_BYTES:
                    files.append(path)
            except OSError:
                continue
    return files


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def project_document_answer(question: str, *, session_id: str, source_context: dict[str, Any] | None = None) -> str | None:
    """The answer from the project's files, or None when this is not a project document ask."""
    text = " ".join(str(question or "").split())
    if not text or not _DOC_ASK_RE.search(text):
        return None
    root = _project_root(session_id, source_context)
    if root is None:
        return None
    terms = _terms(text)
    if not terms:
        return None
    scored: list[tuple[int, int, Path, str]] = []
    for path in _text_files(root):
        name = path.stem.lower().replace("_", " ").replace("-", " ")
        body = _read(path)
        lowered = body.lower()
        name_hits = sum(1 for term in terms if term in name)
        body_hits = sum(1 for term in terms if term in lowered)
        if name_hits or body_hits:
            scored.append((name_hits, body_hits, path, body))
    rel = lambda p: str(p.relative_to(root))  # noqa: E731
    if not scored:
        return (f"I looked through the {len(_text_files(root))} text files in this project and none of them "
                f"mentions {', '.join(terms[:3])}.")
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    asks_which = re.search(r"\bwhich\s+(?:of\s+)?(?:my|the|our)\s+(?:files?|notes?|docs?|documents?)\b", text, re.IGNORECASE)
    if asks_which:
        names = [rel(path) for _, hits, path, _ in scored if hits]
        if not names:
            names = [rel(scored[0][2])]
        return "These files in this project mention it:\n" + "\n".join(f"- {name}" for name in names[:10])
    best_name_hits, _, best, body = scored[0]
    if re.search(r"\bwhere\s+(?:did|have)\s+i\b", text, re.IGNORECASE) or not best_name_hits:
        lines = [line.strip() for line in body.splitlines() if any(term in line.lower() for term in terms)]
        shown = "\n".join(f"> {line}" for line in lines[:5]) or "> " + body.strip()[:300]
        return f"In **{rel(best)}** in this project:\n{shown}"
    excerpt = body.strip()
    more = ""
    if len(excerpt) > _EXCERPT_CHARS:
        excerpt, more = excerpt[:_EXCERPT_CHARS].rstrip(), "\n…(the file continues)"
    return f"From **{rel(best)}** in this project:\n\n{excerpt}{more}"


__all__ = ["LANE_ID", "project_document_answer"]
