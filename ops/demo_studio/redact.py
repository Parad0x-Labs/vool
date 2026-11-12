"""The redaction engine: nothing private reaches a demo artifact.

Demo videos are outward-facing by construction. Every text the studio emits —
captions, the public event mirror, the manifest's free-text fields — passes
through this engine, and an independent leak scan re-checks the finished
artifacts. Canaries (operator-designated strings that must never appear) are
redacted like any other secret AND reported by the scan if they survive.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RedactionRule:
    rule_id: str
    pattern: re.Pattern[str]
    replacement: str


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


GENERIC_USER_PATH = _compile(r"(?<![\w./-])/(?:Users|home)/[A-Za-z0-9._-]+(?:/[\w.\-{} ]+)*")
EMAIL = _compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
API_KEY = _compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
BEARER = _compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}\b")
KEYVAL_SECRET = _compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|authorization)\b(\s*[=:]\s*)"
    r"(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;\"']{6,})"
)
LONG_HEX = _compile(r"\b[0-9a-f]{48,}\b")


def _word_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])")


@dataclass
class RedactionEngine:
    """Applies typed redaction rules to free text; scans finished text."""

    workspace_root: str = ""
    runtime_home: str = ""
    scan_secrets: tuple[str, ...] = ()

    rules: list[RedactionRule] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        rules: list[RedactionRule] = []
        # Order matters: the most specific literal rules win first so a
        # run's own paths become their typed placeholders, not a generic one.
        if self.runtime_home:
            rules.append(RedactionRule("runtime_home", _word_prefix_pattern(self.runtime_home), "<home>"))
        if self.workspace_root:
            rules.append(RedactionRule("workspace_root", _word_prefix_pattern(self.workspace_root), "<workspace>"))
        rules.append(RedactionRule("user_path", GENERIC_USER_PATH, "<home>"))
        for name in (os.environ.get("USER"), os.environ.get("LOGNAME"), _fallback_username()):
            if name and len(name) >= 3:
                rules.append(RedactionRule("username", _word_pattern(name), "<user>"))
        hostname = socket.gethostname()
        if hostname:
            rules.append(RedactionRule("hostname", _word_pattern(hostname), "<host>"))
        rules.append(RedactionRule("email", EMAIL, "<redacted-email>"))
        rules.append(RedactionRule("api_key", API_KEY, "<redacted-key>"))
        rules.append(RedactionRule("bearer", BEARER, "<redacted-token>"))
        rules.append(RedactionRule("keyval_secret", KEYVAL_SECRET, r"\1\2<redacted>"))
        rules.append(RedactionRule("long_hex", LONG_HEX, "<redacted-hex>"))
        for index, secret in enumerate(self.scan_secrets):
            rules.append(
                RedactionRule(f"scan_secret_{index}", _compile(re.escape(secret)), "<redacted>")
            )
        self.rules = rules

    # -- redaction -------------------------------------------------------
    def redact(self, text: str) -> str:
        out = text or ""
        for rule in self.rules:
            out = rule.pattern.sub(rule.replacement, out)
        return out

    # -- independent scan ------------------------------------------------
    def scan(self, artifact: str, text: str) -> list[dict[str, str]]:
        violations: list[dict[str, str]] = []
        for secret in self.scan_secrets:
            if secret in text:
                violations.append(
                    {"artifact": artifact, "rule": "scan_secret", "detail": "canary secret survived"}
                )
        home = self.runtime_home
        if home and home in text:
            violations.append(
                {"artifact": artifact, "rule": "runtime_home", "detail": "runtime home path survived"}
            )
        if self.workspace_root and self.workspace_root in text:
            violations.append(
                {"artifact": artifact, "rule": "workspace_root", "detail": "workspace path survived"}
            )
        if GENERIC_USER_PATH.search(text):
            violations.append(
                {"artifact": artifact, "rule": "user_path", "detail": "absolute user path survived"}
            )
        if API_KEY.search(text) or BEARER.search(text):
            violations.append(
                {"artifact": artifact, "rule": "credential", "detail": "credential-shaped token survived"}
            )
        name = _fallback_username() or os.environ.get("USER") or ""
        if name and len(name) >= 3 and _word_pattern(name).search(text):
            violations.append(
                {"artifact": artifact, "rule": "username", "detail": "operator username survived"}
            )
        return violations


def _word_prefix_pattern(path: str) -> re.Pattern[str]:
    """Match the literal path as a prefix of longer paths too."""
    return _compile(re.escape(path.rstrip("/")) + r"(?![\w.-])")


def _fallback_username() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no username in exotic environments
        return ""


def redact_text(text: str, *, engine: RedactionEngine) -> str:
    """Free-text redaction with an explicit engine (no ambient magic)."""
    return engine.redact(text)


def leak_scan(
    artifact: str,
    text: str,
    *,
    workspace_root: str = "",
    runtime_home: str = "",
    scan_secrets: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Standalone leak scan (no engine needed) for finished artifacts."""
    engine = RedactionEngine(
        workspace_root=workspace_root, runtime_home=runtime_home, scan_secrets=scan_secrets
    )
    return engine.scan(artifact, text)
