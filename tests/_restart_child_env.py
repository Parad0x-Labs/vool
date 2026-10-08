"""Child-process environment for the native restart proofs. Contributor: sls_0x.

Secret-bearing variables are dropped by name. The Keychain isolation pins are kept by exact name:
their names contain CREDENTIAL/KEY but their values are storage-mode selectors ('vault', 'file',
'0', the fail backend), and a child started without them runs with native Keychain access.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

SECRET_NAME = re.compile(r"API.?KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL", re.I)
ISOLATION_PINS = frozenset({
    "VOOL_KEY_STORAGE_MODE",
    "VOOL_CREDENTIAL_STORE",
    "VOOL_KEYCHAIN_ALLOWED",
    "PYTHON_KEYRING_BACKEND",
})


def scrub_child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Copy ``environ`` without secret-named variables, keeping the isolation pins."""
    return {k: v for k, v in environ.items() if k in ISOLATION_PINS or not SECRET_NAME.search(k)}


SEATBELT = "/usr/bin/sandbox-exec"


def isolated_child_command(policy: object, argv: list[str], *, platform: str | None = None) -> tuple[list[str], str]:
    """``(command, isolation)`` for one restart-proof child.

    On macOS the child runs under the Seatbelt policy (no network, no writes outside the profile) and a missing
    ``sandbox-exec`` is a failure, never a silent downgrade. Elsewhere there is no Seatbelt: the child runs directly,
    still with the scrubbed environment, its own profile and its own in-process network denial, so the restart
    contract is proven on the Linux path too. ``isolation`` names which of the two ran, for the process receipt.
    """
    import os
    import sys

    if (platform or sys.platform) == "darwin":
        if not os.path.exists(SEATBELT):
            raise FileNotFoundError(f"{SEATBELT} is required for the macOS restart proof")
        return [SEATBELT, "-f", str(policy), *argv], "seatbelt"
    return list(argv), "in-process-network-deny"
