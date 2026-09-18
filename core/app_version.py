"""Single source of the installed VOOL app version.

The self-update check compares the latest GitHub release tag against this. It is the
authoritative *runtime* version; `tests/test_app_version.py` asserts it stays in sync
with `pyproject.toml [project].version`, so the two never drift. After a successful
update swaps in new code, the new code carries the new VOOL_VERSION, so the next check
sees the install as up to date.
"""
from __future__ import annotations

VOOL_VERSION = "0.6.0"


def installed_version() -> str:
    return VOOL_VERSION


__all__ = ["VOOL_VERSION", "installed_version"]
