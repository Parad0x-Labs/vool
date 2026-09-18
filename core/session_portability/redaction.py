"""Export-side redaction: the last line of defense before conversation data leaves the home.

Write-time guards (`append_conversation_event`'s `redact_secrets`, receipt-side
`redact_tool_arguments`) protect rows written after they existed. Rows written before those
guards shipped — and any writer that missed them — still exist on disk. EVERY string the
collector lifts from the home passes through here, so a legacy planted secret or an absolute
private path cannot ride a bundle out of the machine.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.privacy_guard import scrub_secret_material, secret_material_present
from core.secret_redaction import contains_secret, redact_secrets

#: Placeholders substituted for private filesystem spans. Deliberately constant strings: a
#: redacted value must never be able to echo any part of what it replaced.
_HOME_PLACEHOLDER = "[redacted-home]"
_VOOL_HOME_PLACEHOLDER = "[redacted-vool-home]"

_POSIX_HOME_RE = re.compile(r"(?<![\w/])/Users/[A-Za-z0-9._-]+|(?<![\w/])/home/[A-Za-z0-9._-]+")
_WINDOWS_HOME_RE = re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+")
_TMP_VOLATILE_RE = re.compile(r"/private/var/folders/[\w/+_-]+|/var/folders/[\w/+_-]+")

_REDACTION_MARKER = "[export-redacted]"


class RedactionReport:
    """Counts what the pass touched, so a preview can state the redaction honestly."""

    def __init__(self) -> None:
        self.secrets = 0
        self.paths = 0

    @property
    def total(self) -> int:
        return self.secrets + self.paths


def _scrub_text(value: str, report: RedactionReport, private_roots: list[Path]) -> str:
    scrubbed = redact_secrets(value)
    scrubbed = scrub_secret_material(scrubbed)
    if scrubbed != value or contains_secret(value) or secret_material_present(value):
        if value != scrubbed or contains_secret(value) or secret_material_present(value):
            report.secrets += 1
            scrubbed = redact_secrets(scrubbed)
            scrubbed = scrub_secret_material(scrubbed)
    for pattern in (_POSIX_HOME_RE, _WINDOWS_HOME_RE, _TMP_VOLATILE_RE):
        if pattern.search(scrubbed):
            report.paths += 1
            scrubbed = pattern.sub(_HOME_PLACEHOLDER, scrubbed)
    for root in private_roots:
        root_str = str(root)
        if root_str and root_str in scrubbed:
            report.paths += 1
            scrubbed = scrubbed.replace(root_str, _VOOL_HOME_PLACEHOLDER)
    if contains_secret(scrubbed) or secret_material_present(scrubbed):
        # A residue that still reads as credential material after both scrubbers: drop the
        # span entirely rather than let a half-masked secret travel.
        report.secrets += 1
        scrubbed = _REDACTION_MARKER
    return scrubbed


def redact_value(value: Any, report: RedactionReport, private_roots: list[Path]) -> Any:
    """Recursively scrub every string in a JSON-shaped value (dicts, lists, scalars)."""
    if isinstance(value, str):
        return _scrub_text(value, report, private_roots)
    if isinstance(value, dict):
        return {
            str(key): redact_value(item, report, private_roots) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item, report, private_roots) for item in value]
    return value


#: Field names whose value is credential-shaped BY NAME. The value is replaced wholesale: a key
#: slot's name survives, its content never does.
_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "passphrase",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "private_key",
        "seed",
        "credential",
    }
)


def redact_record(value: Any, report: RedactionReport, private_roots: list[Path]) -> Any:
    """`redact_value` plus wholesale redaction of credential-named fields (value never scanned)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key).strip().lower()
            if name in _SECRET_FIELD_NAMES and isinstance(item, (str, bytes)) and item:
                report.secrets += 1
                out[str(key)] = _REDACTION_MARKER
            else:
                out[str(key)] = redact_record(item, report, private_roots)
        return out
    return redact_value(value, report, private_roots)
