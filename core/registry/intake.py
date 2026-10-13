"""Shared local refusal checks. These heuristics never certify public admission."""
import re

_SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"(?i)(?:password|client_secret|api_key)\s*[:=]\s*[\"']?[^\s\"']{12,}"),
]


def secret_findings(files: dict[str, bytes]) -> list[dict]:
    return [{"check": "secrets-preflight", "path": name, "reason": "possible credential; remove it and resubmit"}
            for name, raw in files.items() if any(pattern.search(raw) for pattern in _SECRET_PATTERNS)]
