"""Secret redaction at ingest — BEFORE hot persistence and BEFORE compression.

Law (dossier §23.3): secrets stay out of ordinary logs. Redaction happens at
admission, not at pack time; what this module produces is an OPERATIONAL-class
record that must never be advertised byte-exact (redaction is not byte-exact;
that is the two-classes law). The receipt returned alongside records only the
pattern CLASS and count — never the matched text.
"""
from __future__ import annotations

import re
from typing import Any

# High-confidence token formats only. A redaction that fires on ordinary prose
# would corrupt real logs, so each pattern is pinned to its ecosystem's prefix
# or to explicit key names — never to generic "long hex string" shapes.
_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # .env-style assignment whose VARIABLE NAME names a credential class. The
    # dossier measured exactly this leak class (verbatim .env key material in
    # captured payloads), and plain token patterns cannot see it.
    ("env_assignment", re.compile(r"\b[A-Z][A-Z0-9_]{0,40}(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY)[A-Z0-9_]{0,40}\s*=\s*[^\s\"';]{4,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*", re.IGNORECASE)),
)

# Field-name classes: dict keys whose string values are redacted wholesale.
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "secret_key",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "private_key",
        "client_secret",
        "aws_secret_access_key",
        "authorization",
    }
)
_REDACTED = "[REDACTED:{klass}]"
_MAX_FIELD_VALUE = 4096


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for klass, pattern in _TOKEN_PATTERNS:
        def _sub(match: re.Match[str], _k: str = klass) -> str:
            counts[_k] = counts.get(_k, 0) + 1
            return _REDACTED.format(klass=_k)

        text = pattern.sub(_sub, text)
    return text, counts


def redact_value(value: Any, *, _depth: int = 0) -> tuple[Any, dict[str, int]]:
    """Recursively redact a payload. Returns (redacted_copy, class_counts)."""
    if _depth > 12:
        return value, {}
    if isinstance(value, str):
        if len(value) > _MAX_FIELD_VALUE:
            value = value[:_MAX_FIELD_VALUE]
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        counts: dict[str, int] = {}
        for key, item in value.items():
            key_str = str(key)
            normalized = key_str.strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_FIELD_NAMES and isinstance(item, str) and item:
                out[key_str] = _REDACTED.format(klass=normalized)
                counts[normalized] = counts.get(normalized, 0) + 1
                continue
            redacted_item, item_counts = redact_value(item, _depth=_depth + 1)
            out[key_str] = redacted_item
            for name, n in item_counts.items():
                counts[name] = counts.get(name, 0) + n
        return out, counts
    if isinstance(value, (list, tuple)):
        out_list = []
        counts_list: dict[str, int] = {}
        for item in value:
            redacted_item, item_counts = redact_value(item, _depth=_depth + 1)
            out_list.append(redacted_item)
            for name, n in item_counts.items():
                counts_list[name] = counts_list.get(name, 0) + n
        if isinstance(value, tuple):
            out_list = tuple(out_list)
        return out_list, counts_list
    return value, {}


def redact_payload(payload: Any) -> tuple[Any, dict[str, int]]:
    """Public ingest gate: redact one event payload before it is stored anywhere."""
    return redact_value(payload)


__all__ = ["redact_payload", "redact_text", "redact_value"]
