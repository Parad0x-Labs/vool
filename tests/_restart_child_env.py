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
    "VOOL_KEY_STORAGE_MODE",
    "VOOL_CREDENTIAL_STORE",
    "VOOL_CREDENTIAL_STORE",
    "VOOL_KEYCHAIN_ALLOWED",
    "VOOL_KEYCHAIN_ALLOWED",
    "PYTHON_KEYRING_BACKEND",
})


def scrub_child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Copy ``environ`` without secret-named variables, keeping the isolation pins."""
    return {k: v for k, v in environ.items() if k in ISOLATION_PINS or not SECRET_NAME.search(k)}
