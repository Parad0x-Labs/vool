"""An answer about what a document on this computer says needs that document read on this turn.

Answer-quality Mac check on the v0.8 head f4f048f7 (real qwen3:8b, loop off, 2026-11-13): "what does
the lease pdf in my downloads say about notice" ran machine.find_file, never read a file, and the reply
described three lease PDFs: a "30-day notice" (the lease says three months for the tenant and six for
the landlord), "1-month" for a second file (it says 60 days), and a Desktop file placed in Downloads.
Every claim guard reads claims against what the turn read, so a turn that read nothing had nothing to
contradict it.

The rule here: when the person asks what a document of theirs says, and no read of a document ran and
succeeded on this turn, the reply cannot state the contents. It says what was found and that nothing
was read. Read only; never raises.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The tools that put a document's own words in front of the answer.
READ_TOOLS = frozenset({"machine.read_file", "workspace.read_file", "pdf.extract_text", "pdf.ocr", "email.read",
                        "operator.apple_note_read", "operator.show_note"})
_FIND_TOOLS = frozenset({"machine.find_file", "machine.list_directory", "workspace.search_text", "workspace.list_files"})

_DOC_NOUN = (
    r"(?:pdfs?|documents?|docs?|files?|contracts?|leases?|letters?|invoices?|receipts?|reports?|statements?|"
    r"agreements?|policy|policies|forms?|spreadsheets?|csvs?|presentations?|slides?|essays?|cv|resume|manual)"
)
_FILE_NAME = r"[\w\-. ]{1,80}\.(?:pdf|docx?|txt|md|rtf|csv|xlsx?|pptx?|pages|odt)\b"
# The person's own document: a possessive or a place on this computer, or a filename.
_OWN_DOC_RE = re.compile(
    r"\b(?:my|our|the|this|that)\s+(?:[\w'’-]+\s+){0,3}?" + _DOC_NOUN + r"\b[^.?!]{0,60}?"
    r"\b(?:in|on|from|under)\s+(?:my|the|this)\s+(?:downloads|desktop|documents|home|computer|mac|laptop|folder|drive)\b"
    r"|\b(?:my|our)\s+(?:[\w'’-]+\s+){0,3}?" + _DOC_NOUN + r"\b"
    r"|" + _FILE_NAME,
    re.IGNORECASE,
)
# Asking what it SAYS, not where it is or how big it is.
_ASKS_WHAT_IT_SAYS_RE = re.compile(
    r"\b(?:say|says|said|mention|mentions|state|states|cover|covers|contain|contains|include|includes|"
    r"specify|specifies|stipulate|stipulates|tell\s+me\s+about)\b"
    r"|\b(?:summari[sz]e|summary\s+of|explain|go\s+(?:over|through)|read\s+(?:me|out)|what(?:'s|\s+is)\s+in)\b",
    re.IGNORECASE,
)
_PASTED_MATERIAL_MIN_CHARS = 600


def asks_what_a_document_says(text: str) -> bool:
    """Whether the ask is about the contents of a document of the person's on this computer."""
    value = " ".join(str(text or "").split())
    if not value or len(str(text or "")) >= _PASTED_MATERIAL_MIN_CHARS:
        # A long message carries its own material: what it says is in front of the answer already.
        return False
    return bool(_OWN_DOC_RE.search(value) and _ASKS_WHAT_IT_SAYS_RE.search(value))


def _step_ok(step: dict[str, Any]) -> bool:
    if step.get("correction"):
        return False
    status = str(step.get("status") or "").strip().lower()
    if "ok" in step:
        return bool(step.get("ok")) and status not in {"failed", "error", "refused", "denied"}
    return status in {"ok", "executed", "completed", "success", "reported"}


def _found_names(steps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for step in steps:
        if str(step.get("tool_name") or "") not in _FIND_TOOLS:
            continue
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        for key in ("matches", "entries", "paths", "files"):
            for item in details.get(key) or ():
                raw = item.get("path") or item.get("name") if isinstance(item, dict) else item
                name = Path(str(raw or "")).name
                if name and name not in names:
                    names.append(name)
    return names[:6]


def unread_document_reply(request: str, executed_steps: list[dict[str, Any]] | None, answer: str) -> str | None:
    """The reply to send instead of `answer` when it would state a document's contents that no tool read on
    this turn; None when the answer may stand (not a document ask, or a read ran and succeeded)."""
    try:
        if not asks_what_a_document_says(request) or not str(answer or "").strip():
            return None
        steps = [s for s in (executed_steps or []) if isinstance(s, dict)]
        if any(str(s.get("tool_name") or "") in READ_TOOLS and _step_ok(s) for s in steps):
            return None
        found = _found_names(steps)
        if len(found) == 1:
            return (f"I found {found[0]}, but I didn't manage to read it on this turn, so I can't tell you what it "
                    f"says. Ask me again to read {found[0]}.")
        if found:
            listed = ", ".join(found)
            return (f"I found these files: {listed}. I didn't read any of them on this turn, so I can't tell you what "
                    f"they say. Tell me which one to read.")
        return ("I didn't read that document on this turn, so I can't tell you what it says. Tell me its name or the "
                "folder it is in, and I'll read it.")
    except Exception:
        return None


__all__ = ["READ_TOOLS", "asks_what_a_document_says", "unread_document_reply"]
