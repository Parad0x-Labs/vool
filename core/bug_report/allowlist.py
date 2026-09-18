"""Allowlist reconstruction: build typed structure from redacted material.

Redaction rewrites values; this module decides what SHAPE may survive at all:

- prose fields (expected/actual/step text) are normalized + redacted + capped, but stay
  prose -- they are the user's own bug description, and the outbound scan gates them;
- tracebacks are parsed into typed frames (basename file, line, function) so no path or
  message beyond the sanitized exception line can ride along;
- component identifiers (lanes/tools/models) must match a strict id pattern or are
  refused outright.
"""
from __future__ import annotations

import re

from core.bug_report.redaction import redact_text
from core.bug_report.schema import RedactionSummary, SanitizedError, StackFrameSanitized


def _absorb(into: RedactionSummary, other: RedactionSummary) -> None:
    for rule, count in other.rule_counts.items():
        into.rule_counts[rule] = into.rule_counts.get(rule, 0) + count
    into.total_replacements += other.total_replacements


_TRUNCATION_MARKER = "…[truncated]"

_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_EXC_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt|Warning|Interrupted))\b[:\s]?(.*)$")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

MAX_ERROR_MESSAGE_CHARS = 1000


def sanitize_prose(text: str, *, max_chars: int) -> str:
    """Normalize + redact free text, then hard-cap its length."""
    if text is None:
        return ""
    sanitized, _summary = redact_text(str(text))
    if len(sanitized) <= max_chars:
        return sanitized
    return sanitized[:max_chars].rstrip() + _TRUNCATION_MARKER


def sanitize_identifier(value: str, *, field: str) -> str:
    """Return the identifier when it matches the safe id pattern, else empty string."""
    if not value:
        return ""
    candidate = str(value)
    return candidate if _ID_RE.match(candidate) else ""


def require_identifier(value: str, *, field: str) -> str:
    candidate = str(value or "")
    if not _ID_RE.match(candidate):
        raise ValueError(f"unsafe {field} identifier refused: {candidate!r}")
    return candidate


def _basename(path: str) -> str:
    for sep in ("/", "\\"):
        path = path.rsplit(sep, 1)[-1]
    return path


def parse_stack(error_text: str, *, summary: RedactionSummary | None = None) -> SanitizedError | None:
    """Reconstruct a typed error from raw traceback text.

    Everything passes redaction first; frames keep only basename/line/function; the
    exception line keeps its type plus a redacted, capped message.
    """
    if not error_text or not str(error_text).strip():
        return None
    sanitized, text_summary = redact_text(str(error_text))
    if summary is not None:
        _absorb(summary, text_summary)
    frames: list[StackFrameSanitized] = []
    for match in _FRAME_RE.finditer(sanitized):
        frames.append(StackFrameSanitized(file=_basename(match.group(1)), line=int(match.group(2)), function=match.group(3)))
    exc_type = "Error"
    message = sanitized.strip().splitlines()[-1].strip() if sanitized.strip() else ""
    for line in reversed(sanitized.strip().splitlines()):
        stripped = line.strip()
        exc_match = _EXC_LINE_RE.match(stripped)
        if exc_match:
            exc_type = exc_match.group(1).rsplit(".", 1)[-1]
            message = exc_match.group(2).strip()
            break
    message, msg_summary = redact_text(message)
    if summary is not None:
        _absorb(summary, msg_summary)
    if len(message) > MAX_ERROR_MESSAGE_CHARS:
        message = message[:MAX_ERROR_MESSAGE_CHARS].rstrip() + _TRUNCATION_MARKER
    return SanitizedError(exc_type=exc_type, message=message, frames=tuple(frames))


def sanitize_log_line(line: str, *, summary: RedactionSummary | None = None) -> tuple[str, bool]:
    """Redact one log line. Returns (sanitized_line, has_safe_shape)."""
    sanitized, line_summary = redact_text(str(line))
    if summary is not None:
        _absorb(summary, line_summary)
    return sanitized, has_safe_log_shape(sanitized)


# Log lines are only kept when they match a structured logging shape. Anything else --
# conversation turns ("user: ..."), arbitrary prose -- is dropped at reconstruction time.
# There is deliberately NO bare "component: message" shape: it is exactly the shape of a
# chat turn, and a conversation line slipping through is a worse failure than an
# unparsed log line.
_SAFE_LOG_SHAPES = (
    re.compile(r"^\d{4}-\d{2}-\d{2}[T ][0-9:.+,Z-]+"),
    re.compile(r"^\[?\d{4}-\d{2}-\d{2}\]?"),
    re.compile(r"^(?:TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|CRITICAL|FATAL)\b"),
    re.compile(r"^\[?(?:TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|CRITICAL|FATAL)\]?\s*[:|-]"),
    re.compile(r"^\s*File \""),
    re.compile(r"^Traceback \(most recent call last\)"),
)


def has_safe_log_shape(line: str) -> bool:
    return any(shape.match(line) for shape in _SAFE_LOG_SHAPES)


__all__ = [
    "MAX_ERROR_MESSAGE_CHARS",
    "has_safe_log_shape",
    "parse_stack",
    "require_identifier",
    "sanitize_identifier",
    "sanitize_log_line",
    "sanitize_prose",
]
