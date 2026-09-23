from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The session runtime home is pinned at ROOT-CONFTEST IMPORT TIME — before any other conftest or
# runtime module can load — because pytest loads the root conftest AND the args' directory
# conftests (tests/conftest.py -> apps.vool_agent -> network.signer) as INITIAL conftests, BEFORE
# pytest_configure runs. Runtime modules freeze path state at import: network.signer resolves
# `_KEY_DIR = data_path("keys")` once, at import, so a home pinned only in pytest_configure never
# reached it — _KEY_DIR stayed at the repository checkout's own .vool_local, one key record SHARED
# by every pytest session in a job. A record written there under another protection (measured
# locally: an unattended no-passphrase persist creates it under the random account-file secret)
# then fails every later session's passphrase-pinned load with cryptography.exceptions.InvalidTag:
# CI runs 35546610740 / 35570948370, shard tests (3), the hermetic gauntlet's child pytest
# sessions failed 24/51 test_public_hive_bridge.py cases exactly this way while the parent
# session, holding the cached keypair, stayed green. Pinning here gives every session — top-level
# or spawned child, whose own root-conftest import re-pins to its own fresh home — private state
# for import-frozen AND runtime-resolved paths alike.
_TEST_RUNTIME_HOME: Path | None = Path(
    tempfile.mkdtemp(prefix="vool_pytest_home_", dir=tempfile.gettempdir())
).resolve()
os.environ["VOOL_HOME"] = str(_TEST_RUNTIME_HOME)
# Zero-Keychain-dialogs law (2026-09-02 operator addendum): the test session is UNATTENDED.
# Set BEFORE any runtime import, so signer/credential storage select their explicit
# non-interactive backends and no test ever initiates a macOS Keychain operation. A test that
# deliberately exercises the keychain policy sets the grant itself.
os.environ["VOOL_KEY_STORAGE_MODE"] = "file"
os.environ["VOOL_CREDENTIAL_STORE"] = "vault"
os.environ.pop("VOOL_KEYCHAIN_ALLOWED", None)

from core.env_compat import apply_legacy_nulla_env
from core.runtime_paths import configure_runtime_home
from storage.db import configure_default_db_path

configure_runtime_home(_TEST_RUNTIME_HOME)
configure_default_db_path(_TEST_RUNTIME_HOME / "data" / "vool_web0_v2.db")


def pytest_configure(config) -> None:
    apply_legacy_nulla_env()
    del config
    global _TEST_RUNTIME_HOME
    if _TEST_RUNTIME_HOME is None:  # pragma: no cover - the module-level pin above already ran
        _TEST_RUNTIME_HOME = Path(tempfile.mkdtemp(prefix="vool_pytest_home_", dir=tempfile.gettempdir())).resolve()
    os.environ["VOOL_HOME"] = str(_TEST_RUNTIME_HOME)
    configure_runtime_home(_TEST_RUNTIME_HOME)
    configure_default_db_path(_TEST_RUNTIME_HOME / "data" / "vool_web0_v2.db")
    os.environ["VOOL_KEY_STORAGE_MODE"] = "file"
    os.environ["VOOL_CREDENTIAL_STORE"] = "vault"
    os.environ.pop("VOOL_KEYCHAIN_ALLOWED", None)


def pytest_unconfigure(config) -> None:
    del config
    global _TEST_RUNTIME_HOME
    configure_runtime_home(None)
    configure_default_db_path(None)
    if _TEST_RUNTIME_HOME is None:
        return
    shutil.rmtree(_TEST_RUNTIME_HOME, ignore_errors=True)
    _TEST_RUNTIME_HOME = None
