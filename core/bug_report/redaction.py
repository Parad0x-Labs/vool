"""Deterministic, model-free redaction for bug-report material.

This is the pipeline's FIRST sanitization stage and it is deliberately not a model: the
same input must produce the same output every time, on every machine, auditable by regex.
Layering:

1. bug-report-specific families the objective names: cookies, auth headers, home paths /
   usernames, emails, IPs, high-entropy runs, labelled secrets (including the ``X is Y``
   prose form), hex blobs.
2. ``core.secret_redaction.redact_secrets`` -- the repo's existing high-confidence secret
   masker (vendor token shapes, JWTs, PEM blocks, Bearer values) -- run as a counted
   backstop so nothing that library catches can survive either.

Redaction is privacy-first: it would rather over-mask than leak. Structure that
reproduction needs (file names, function names, line numbers, timestamps, log levels,
lane/tool/model ids) is preserved because the rules only bind to value shapes, not to
code identifiers.
"""
from __future__ import annotations

import math
import re
import unicodedata

from core.bug_report.schema import RedactionSummary
from core.secret_redaction import redact_secrets as _backstop_redact

# --- header families (whole-value masking; label kept so the report stays readable).
# The (?![ \t]*\[redacted) guard after the colon keeps matching idempotent: an already-
# masked "Cookie: [redacted]" must not match again (the scanner shares these patterns),
# and placing it directly after the colon closes the zero-width backtracking path.

_COOKIE_HEADER_RE = re.compile(
    r"(?i)(?<![a-z0-9-])(set-cookie|cookie)\s*:(?![ \t]*\[redacted)\s*[^\r\n]+"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)(?<![a-z0-9-])(authorization|proxy-authorization|x-api-key|api-key|x-auth-token|x-session-token)"
    r"\s*:(?![ \t]*\[redacted)\s*[^\r\n]+"
)

# --- home paths / usernames -------------------------------------------------------------
# /Users/<name>, /home/<name>, C:\Users\<name>, C:/Users/<name>: mask just the username
# segment; the project-relative remainder is what reproduction needs.
_HOME_SEGMENT_RE = re.compile(
    r"(?i)([a-z]:)?[\\/](users|home)[\\/][A-Za-z0-9._-]+"
)
_TILDE_HOME_RE = re.compile(r"(?<![A-Za-z0-9])~(?=/|\\|\s|$)")
_HOME_ENV_RE = re.compile(r"(?<![A-Za-z0-9])\$HOME\b", re.IGNORECASE)

# --- usernames in labelled positions -------------------------------------------------------
_USERNAME_LABEL_RE = re.compile(
    r"(?i)(?<![a-z0-9_])((?:[a-z0-9]+[_-])*(?:user|owner|principal|username|login|runas|account)(?:[_-][a-z0-9]+)*)"
    r"\s*[=:]\s*[\"']?([A-Za-z0-9._@+-]{2,64})[\"']?"
)

# --- emails ----------------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])")

# --- IPs ---------------------------------------------------------------------------------------
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# IPv6: require `::`, or a hextet chain with at least one non-decimal hex group. A clock
# time like 12:34:56 is all-decimal with no `::` and must NOT be masked.
_IPV6_CANDIDATE_RE = re.compile(r"(?<![\w:.])([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?:%[A-Za-z0-9]+)?(?![\w:.])")

# --- labelled secrets (bug-report layer: adds session/cookie/auth labels and "X is Y") ----------
_BR_SECRET_LABEL = (
    r"password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|bearer"
    r"|client[_-]?secret|private[_-]?key|passphrase|credential|session|cookie|csrf|basic[_-]?auth"
)
_BR_LABELED_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:" + _BR_SECRET_LABEL + r")(?:[_-][A-Za-z0-9]+)*)"
    r"\s*[:=]\s*[\"']?(?!\[redacted)([^\s\"']{4,})[\"']?"
)
_BR_LABELED_IS_RE = re.compile(
    r"(?i)\b(password|secret|passphrase|token|api[_-]?key)\s+is\s+[\"']?([A-Za-z0-9+=/_-]{4,})[\"']?(?![\w-])"
)
# AWS access-key ids longer than the canonical 16: core.secret_redaction pins
# AKIA[0-9A-Z]{16}\b exactly, so a 17+ char run (real keys get pasted with padding or
# transcription drift) falls through. Privacy-first: catch the longer runs here too.
_AKIA_LONG_RE = re.compile(r"\bAKIA[0-9A-Z]{16,}\b")

# --- entropy / hex blobs --------------------------------------------------------------------------
_ENTROPY_RUN_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{20,}(?![A-Za-z0-9+/=_-])")
_HEX_BLOB_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")

_ENTROPY_THRESHOLD = 3.8  # bits/char; random hex ~= 4.0, random base64 ~= 6.0, English words < 3.5

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _looks_like_secret_run(run: str) -> bool:
    """A long run is masked when it mixes letters and digits and looks random, or is a hex blob."""
    if _HEX_BLOB_RE.fullmatch(run):
        return True
    has_alpha = any(c.isalpha() for c in run)
    has_digit = any(c.isdigit() for c in run)
    if not (has_alpha and has_digit):
        return False
    return _shannon_entropy(run) >= _ENTROPY_THRESHOLD


def _is_v6_candidate(match_text: str) -> bool:
    text = match_text
    if "::" in text:
        return True
    groups = [g for g in text.split(":") if g]
    if len(groups) < 3:
        return False
    return any(any(ch in "abcdefABCDEF" for ch in g) for g in groups)


def normalize_text(text: str) -> str:
    """NFKC-normalize and strip control characters, ANSI escapes, and bidi overrides."""
    value = unicodedata.normalize("NFKC", str(text))
    value = _BIDI_RE.sub("", value)
    value = _ANSI_ESCAPE_RE.sub("", value)
    value = _CONTROL_CHARS_RE.sub(" ", value)
    return value


def redact_text(text: str) -> tuple[str, RedactionSummary]:
    """Return (sanitized text, per-rule replacement counts). Deterministic; never raises."""
    counts: dict[str, int] = {}
    total = 0

    def bump(rule: str, by: int) -> None:
        nonlocal total
        if by:
            counts[rule] = counts.get(rule, 0) + by
            total += by

    def apply(rule: str, pattern: re.Pattern, replacement) -> None:
        nonlocal value
        bump(rule, len(pattern.findall(value)))
        value = pattern.sub(replacement, value)

    value = normalize_text(text)
    if not value:
        return value, RedactionSummary()

    # Layer 0 -- the repo-wide high-confidence vendor secret masker, FIRST, so precise
    # token shapes (ghp_/sk-/AKIA/JWT/PEM/Bearer) are attributed to secret_vendor and
    # never fall through to the coarser entropy rule.
    pre_markers = value.count("[redacted")
    backstopped = _backstop_redact(value)
    if backstopped != value:
        bump("secret_vendor", backstopped.count("[redacted") - pre_markers)
        value = backstopped

    def _header_sub(m: re.Match) -> str:
        return f"{m.group(1)}: [redacted]"

    apply("cookie_header", _COOKIE_HEADER_RE, _header_sub)
    apply("auth_header", _AUTH_HEADER_RE, _header_sub)

    def _home_sub(m: re.Match) -> str:
        drive = m.group(1) or ""
        return f"{drive}/{m.group(2)}/[redacted-user]"

    apply("home_path", _HOME_SEGMENT_RE, _home_sub)
    apply("home_path", _TILDE_HOME_RE, "[redacted-home]")
    apply("home_path", _HOME_ENV_RE, "[redacted-home]")

    def _username_sub(m: re.Match) -> str:
        return f"{m.group(1)}: [redacted-user]"

    apply("username_label", _USERNAME_LABEL_RE, _username_sub)
    apply("email", _EMAIL_RE, "[redacted-email]")

    def _ipv6_sub(m: re.Match) -> str:
        return "[redacted-ip]" if _is_v6_candidate(m.group(0)) else m.group(0)

    apply("ip_v4", _IPV4_RE, "[redacted-ip]")
    v6_hits = sum(1 for m in _IPV6_CANDIDATE_RE.finditer(value) if _is_v6_candidate(m.group(0)))
    value = _IPV6_CANDIDATE_RE.sub(_ipv6_sub, value)
    bump("ip_v6", v6_hits)

    def _labeled_sub(m: re.Match) -> str:
        return f"{m.group(1)}: [redacted]"

    def _labeled_is_sub(m: re.Match) -> str:
        return f"{m.group(1)} is [redacted]"

    apply("labeled_secret", _BR_LABELED_RE, _labeled_sub)
    apply("labeled_secret", _BR_LABELED_IS_RE, _labeled_is_sub)
    apply("secret_vendor", _AKIA_LONG_RE, "[redacted-api-key]")

    def _entropy_sub(m: re.Match) -> str:
        run = m.group(0)
        return "[redacted-secret]" if _looks_like_secret_run(run) else run

    entropy_hits = sum(1 for m in _ENTROPY_RUN_RE.finditer(value) if _looks_like_secret_run(m.group(0)))
    value = _ENTROPY_RUN_RE.sub(_entropy_sub, value)
    bump("high_entropy", entropy_hits)

    return value, RedactionSummary(rule_counts=counts, total_replacements=total)


__all__ = ["normalize_text", "redact_text"]
