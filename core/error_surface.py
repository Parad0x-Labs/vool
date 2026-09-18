"""User-facing error text: say what failed without shipping internals to the chat bubble.

An unhandled exception's ``str()`` carries whatever the raising code interpolated — absolute
filesystem paths (which expose the operator's username and layout), URLs with query strings, and,
in the worst case, a credential that was in scope at the raise site. Both the streaming transport
and the JSON 500 responses rendered that verbatim as the assistant's answer.

The exception CLASS is the genuinely useful part for a user ("TimeoutError" tells them to retry,
"PermissionError" tells them to check access), and it is safe by construction. The message body is
redacted, path-stripped, and truncated — never dropped silently, because an operator debugging their
own machine still needs a hint.
"""
from __future__ import annotations

import re

from core.secret_redaction import redact_secrets

# Absolute POSIX and Windows paths -> the basename only, so "/Users/alice/Desktop/x/y.py" leaks
# neither the username nor the layout while still naming the file that failed.
_POSIX_PATH_RE = re.compile(r"(?:/[^/\s\"']+){2,}/?")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"']+\\)*[^\\\s\"']*")
_MAX_DETAIL = 160

_GENERIC = "Something went wrong on this machine while handling that turn."


def _strip_paths(text: str) -> str:
    def _posix(match: re.Match[str]) -> str:
        tail = match.group(0).rstrip("/").rsplit("/", 1)[-1]
        return tail or "<path>"

    def _windows(match: re.Match[str]) -> str:
        tail = match.group(0).rstrip("\\").rsplit("\\", 1)[-1]
        return tail or "<path>"

    return _WINDOWS_PATH_RE.sub(_windows, _POSIX_PATH_RE.sub(_posix, text))


def safe_error_text(exc: object, *, prefix: str = "") -> str:
    """A chat-safe one-liner for ``exc``: the exception class plus a redacted, path-stripped detail.

    Never raises, and never returns an empty string.
    """
    try:
        kind = type(exc).__name__ if isinstance(exc, BaseException) else ""
        raw = str(exc or "")
    except Exception:
        kind, raw = "", ""
    detail = ""
    try:
        detail = " ".join(_strip_paths(redact_secrets(raw)).split())
        if len(detail) > _MAX_DETAIL:
            detail = detail[:_MAX_DETAIL].rstrip() + "…"
    except Exception:
        detail = ""
    head = f"{prefix}{_GENERIC}" if prefix else _GENERIC
    if kind and detail:
        return f"{head} ({kind}: {detail})"
    if kind:
        return f"{head} ({kind})"
    if detail:
        return f"{head} ({detail})"
    return head


__all__ = ["safe_error_text"]
