"""The configured default destination for bug reports.

The reporting flow is consent-bound to an EXACT destination per draft, but a user
opening "Report a problem" should not have to know the project's feedback repository
by heart. This module is the single owner of that default:

* ``VOOL_BUG_REPORT_DESTINATION`` env var when set (highest precedence, so an owner
  can point every install at their own fork/enterprise);
* else the persisted choice in ``<home>/bug_report_destination.txt`` (written when the
  owner changes the default in the UI);
* else the built-in project feedback repository ``Parad0x-Labs/vool-feedback``.

The resolved value is validated by the SAME shape rule the draft pipeline enforces,
so an unusable configured value can never silently become a draft's destination:
an invalid configured value is reported as invalid, not swallowed to a fallback.
"""
from __future__ import annotations

import os
import re

from core.runtime_paths import data_path

#: The built-in default: the project's private feedback repository. Reports are
#: submitted only after explicit per-draft consent; nothing about this default sends
#: anything by itself.
BUILTIN_DESTINATION = "Parad0x-Labs/vool-feedback"

DESTINATION_ENV = "VOOL_BUG_REPORT_DESTINATION"
_CONFIG_NAME = "bug_report_destination.txt"

DESTINATION_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def is_valid_destination(value: str) -> bool:
    return bool(DESTINATION_RE.match(str(value or "").strip()))


def _config_file():
    return data_path(_CONFIG_NAME)


def default_destination() -> str:
    """The configured default destination, validated. Raises ``ValueError`` when a
    configured value exists but is not a usable ``owner/name`` -- misconfiguration is
    surfaced, never silently replaced."""
    env_value = os.environ.get(DESTINATION_ENV, "").strip()
    if env_value:
        if not is_valid_destination(env_value):
            raise ValueError(f"{DESTINATION_ENV} is not a valid owner/name repository: {env_value!r}")
        return env_value
    try:
        stored = _config_file().read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        stored = ""
    if stored:
        if not is_valid_destination(stored):
            raise ValueError(f"stored default destination is not a valid owner/name repository: {stored!r}")
        return stored
    return BUILTIN_DESTINATION


def set_default_destination(value: str) -> str:
    """Persist a new default (owner-local choice). Returns the stored value."""
    clean = str(value or "").strip()
    if not is_valid_destination(clean):
        raise ValueError(f"not a valid owner/name repository: {clean!r}")
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean + "\n", encoding="utf-8")
    return clean


def reset_default_destination() -> None:
    """Forget the stored choice; the built-in default applies again."""
    try:
        _config_file().unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "BUILTIN_DESTINATION",
    "DESTINATION_ENV",
    "default_destination",
    "is_valid_destination",
    "reset_default_destination",
    "set_default_destination",
]
