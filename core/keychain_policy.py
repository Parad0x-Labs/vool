"""The Keychain interaction policy — the ONE authority deciding when runtime code may
initiate an interactive macOS Keychain operation (2026-09-02 operator addendum).

Scratch/test processes and unattended boots used to re-attempt Keychain writes on every
launch; each attempt from a binary the Keychain had never seen stacked one more dialog
("A keychain cannot be found to store node_signing_key") that nothing can cancel.

The law:

- Keychain WRITES (create/migrate) require an explicit operator grant: the
  ``VOOL_KEYCHAIN_ALLOWED=1`` environment variable, or the ``keychain.enabled`` flag file in
  the ACTIVE config home — the seam a Settings action writes with one explicit operator
  action. Nothing else may initiate a keychain write.
- Keychain READS of ALREADY-SAVED credentials stay available to a runtime that has used the
  Keychain before (migration marker / sidecar index present), so existing saved credentials
  remain readable through the normal signed-app path.
- Every denial is surfaced ONCE per process ("Keychain unavailable; using local protected
  fallback") — never as repeated dialogs.
- Isolation stays with VOOL_HOME only: the grant flag lives in the active config home, so
  two isolated homes never share Keychain grants.
"""
from __future__ import annotations

import os
import sys

from core.runtime_paths import active_config_home_dir

GRANT_ENV = "VOOL_KEYCHAIN_ALLOWED"
GRANT_FLAG_FILE = "keychain.enabled"

_NOTICE_SHOWN = False
FALLBACK_NOTICE = (
    "Keychain unavailable; using the account-file-permissions local fallback "
    "(protected by OS file permissions only, not against profile or disk theft)."
)


def keychain_write_granted() -> bool:
    """True only after ONE explicit operator action: the env grant or the Settings flag file.

    An explicit ``VOOL_KEYCHAIN_ALLOWED=0`` in the environment overrides a present flag file
    (the operator turned it off for this run).
    """
    raw = str(os.environ.get(GRANT_ENV) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        return (active_config_home_dir() / GRANT_FLAG_FILE).exists()
    except OSError:
        return False


def surface_fallback_notice() -> None:
    """Print the degradation notice exactly once per process."""
    global _NOTICE_SHOWN
    if _NOTICE_SHOWN:
        return
    _NOTICE_SHOWN = True
    print(FALLBACK_NOTICE, file=sys.stderr)
