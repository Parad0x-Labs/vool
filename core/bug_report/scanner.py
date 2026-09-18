"""The outbound privacy scanner: the last gate before anything may leave the machine.

Unlike redaction (which rewrites), the scanner only JUDGES. It runs over the exact bytes
that would be sent and returns findings; callers fail closed on any hit. It re-uses every
detection family from redaction plus machine-local dynamic checks (the current user's
home path, username, hostname) that no static pattern can know.

Two spans are exempt BY CONSTRUCTION because the pipeline itself generates them from
already-safe material: the fingerprint marker line and JSON hash fields
(source_sha/fingerprint/payload_sha256/sha256) whose values are content-free digests of
sanitized content.
"""
from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass

from core.bug_report.redaction import (
    _AUTH_HEADER_RE,
    _BR_LABELED_IS_RE,
    _BR_LABELED_RE,
    _COOKIE_HEADER_RE,
    _EMAIL_RE,
    _ENTROPY_RUN_RE,
    _HEX_BLOB_RE,
    _HOME_ENV_RE,
    _HOME_SEGMENT_RE,
    _IPV4_RE,
    _IPV6_CANDIDATE_RE,
    _TILDE_HOME_RE,
    _USERNAME_LABEL_RE,
    _is_v6_candidate,
    _looks_like_secret_run,
)
from core.secret_redaction import contains_secret

# Spans the pipeline itself generated from sanitized material (content-free digests).
_DIGEST_FIELD_RE = re.compile(
    r'"(?:source_sha|fingerprint|payload_sha256|sha256|prev_hash|event_hash)"\s*:\s*"[0-9a-fA-F]{8,64}"'
)
_FINGERPRINT_MARKER_RE = re.compile(r"bug-report-fingerprint: [0-9a-f]{8,64}")
_MARKER_RE = re.compile(r"\[redacted[a-z0-9_-]*\]")


@dataclass(frozen=True)
class ScanFinding:
    rule: str
    start: int  # character offset into the scanned text
    excerpt: str = ""

    def to_dict(self) -> dict:
        return {"rule": self.rule, "start": self.start}


def _exempt_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (_DIGEST_FIELD_RE, _FINGERPRINT_MARKER_RE):
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))
    return spans


def _in_exempt_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= start and end <= e for s, e in spans)


def _dynamic_local_patterns() -> list[tuple[str, re.Pattern]]:
    """Machine-local identifiers no static rule can know: home dir, username, hostname."""
    patterns: list[tuple[str, re.Pattern]] = []
    home = os.path.expanduser("~")
    if home and home not in ("/", ""):
        patterns.append(("local_home", re.compile(re.escape(home))))
    for var in ("USER", "LOGNAME"):
        value = os.environ.get(var, "")
        if value and len(value) >= 3 and re.fullmatch(r"[A-Za-z0-9._-]+", value):
            patterns.append(("local_username", re.compile(rf"(?<![A-Za-z0-9._-]){re.escape(value)}(?![A-Za-z0-9._-])")))
    node = platform.node()
    if node and len(node) >= 3 and re.fullmatch(r"[A-Za-z0-9._-]+", node):
        patterns.append(("local_hostname", re.compile(rf"(?<![A-Za-z0-9._-]){re.escape(node)}(?![A-Za-z0-9._-])")))
    return patterns


_DYNAMIC_PATTERNS: list[tuple[str, re.Pattern]] | None = None


def _dynamic_patterns() -> list[tuple[str, re.Pattern]]:
    global _DYNAMIC_PATTERNS
    if _DYNAMIC_PATTERNS is None:
        _DYNAMIC_PATTERNS = _dynamic_local_patterns()
    return _DYNAMIC_PATTERNS


def scan_text(text: str) -> tuple[ScanFinding, ...]:
    """Scan already-composed outbound text. Returns findings; empty tuple means clean."""
    value = str(text)
    if not value:
        return ()
    spans = _exempt_spans(value)
    findings: list[ScanFinding] = []

    def check(rule: str, pattern: re.Pattern, *, conditional=None) -> None:
        for m in pattern.finditer(value):
            if conditional is not None and not conditional(m.group(0)):
                continue
            if _in_exempt_span(m.start(), m.end(), spans):
                continue
            findings.append(ScanFinding(rule=rule, start=m.start()))
            return  # one finding per rule is enough to fail closed

    # Boolean backstop from the repo-wide secret masker. Our own redaction markers must be
    # ignored first: "token: [redacted]" would otherwise look like a labelled secret.
    if contains_secret(_MARKER_RE.sub(" ", value)):
        findings.append(ScanFinding(rule="secret_vendor", start=0))

    check("cookie_header", _COOKIE_HEADER_RE)
    check("auth_header", _AUTH_HEADER_RE)
    check("home_path", _HOME_SEGMENT_RE)
    check("home_path", _TILDE_HOME_RE)
    check("home_path", _HOME_ENV_RE)
    check("username_label", _USERNAME_LABEL_RE)
    check("email", _EMAIL_RE)
    check("ip_v4", _IPV4_RE)
    check("ip_v6", _IPV6_CANDIDATE_RE, conditional=_is_v6_candidate)
    check("labeled_secret", _BR_LABELED_RE)
    check("labeled_secret", _BR_LABELED_IS_RE)
    check("high_entropy", _ENTROPY_RUN_RE, conditional=_looks_like_secret_run)
    check("hex_blob", _HEX_BLOB_RE)

    for rule, pattern in _dynamic_patterns():
        check(rule, pattern)

    return tuple(findings)


def scan_payload(payload: bytes) -> tuple[ScanFinding, ...]:
    """Scan the exact outbound bytes (decoded as the UTF-8 we always encode with)."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return (ScanFinding(rule="non_utf8_payload", start=0),)
    return scan_text(text)


def assert_clean(payload: bytes) -> None:
    """Fail closed: raise UnsafeReportContentError when the outbound bytes are not clean."""
    from core.bug_report.schema import UnsafeReportContentError

    findings = scan_payload(payload)
    if findings:
        raise UnsafeReportContentError(findings=[(f.rule, f.start) for f in findings])


def reset_dynamic_patterns_for_tests() -> None:
    global _DYNAMIC_PATTERNS
    _DYNAMIC_PATTERNS = None


__all__ = ["ScanFinding", "assert_clean", "scan_payload", "scan_text"]


# ---- document policy: NOT the egress policy --------------------------------------------------
#
# `scan_text` above judges OUTBOUND bug reports, where an email address, an IP or a labelled
# value is worth failing a report over. Local document staging must NOT inherit that policy:
# pasted source code, logs and configs routinely contain `os.environ.get("DB_PASSWORD")`,
# `YOUR_API_KEY` placeholders, 127.0.0.1 and contact addresses, and refusing them would break
# exactly the material users paste. A document is judged by credential FORMATS only — material
# that is itself a live-looking secret. Findings refuse the document typed; bytes are never
# redacted or mutated here.

_DOCUMENT_PLACEHOLDER_RE = re.compile(
    r"AKIAIOSFODNN7EXAMPLE|wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
)
_DOCUMENT_SECRET_RES: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("bearer_header", re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
]


def scan_document_text(text: str) -> tuple[ScanFinding, ...]:
    """Judge a LOCAL document by credential formats only. Empty tuple means clean.

    The documented example credentials (AWS's AKIAIOSFODNN7EXAMPLE and friends) are placeholders,
    not secrets, and never fail a document. Everything here refuses; nothing rewrites bytes.
    """
    value = str(text or "")
    if not value:
        return ()
    placeholders = [(m.start(), m.end()) for m in _DOCUMENT_PLACEHOLDER_RE.finditer(value)]
    findings: list[ScanFinding] = []
    for rule, pattern in _DOCUMENT_SECRET_RES:
        for m in pattern.finditer(value):
            if any(s <= m.start() and m.end() <= e for s, e in placeholders):
                continue
            findings.append(ScanFinding(rule=rule, start=m.start()))
            break  # one finding per family is enough to fail the document
    return tuple(findings)
