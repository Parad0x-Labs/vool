"""The session runtime home must be pinned before the first runtime import.

`network.signer` resolves `_KEY_DIR = data_path("keys")` once, AT IMPORT. pytest loads the root
conftest and the args' directory conftests (tests/conftest.py -> apps.vool_agent ->
network.signer) as INITIAL conftests — before pytest_configure — so a home pinned only in
pytest_configure never reached import-frozen paths: `_KEY_DIR` stayed at the repository
checkout's own `.vool_local`, one key record shared by every pytest session in a job. A record
written there under another protection (an unattended no-passphrase persist uses the random
account-file secret) then failed every later session's passphrase-pinned load with
cryptography.exceptions.InvalidTag — CI runs 35546610740 / 35570948370, shard tests (3): 24/51
test_public_hive_bridge.py cases red inside the hermetic gauntlet's child sessions while the
parent session, holding the cached keypair, stayed green.

The root conftest now pins the home at import time. These cases fail if that pin ever moves
back to a hook that runs after the imports it must precede.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Set by the spawned-session case below: the in-session case reports its resolved paths here so
# the parent process can compare the child's home against its own.
_REPORT_ENV = "VOOL_SESSION_HOME_PROBE_REPORT"

# Captured at MODULE IMPORT (collection time): before any autouse fixture or earlier test in
# this process has legitimately repointed the signer's per-test key directories. The live
# globals at test time may move by design (per-test signer isolation repoints _KEY_DIR into a
# per-test directory), so the import-time pin is asserted against this snapshot, and only the
# repo-checkout exclusion is asserted against the live value.
import network.signer as _signer_at_import  # noqa: E402

from core.runtime_paths import active_vool_home as _active_home_at_import  # noqa: E402

_IMPORT_TIME_HOME = _active_home_at_import()
_IMPORT_TIME_KEY_DIR = _signer_at_import._KEY_DIR


def test_import_frozen_runtime_paths_stay_inside_the_session_home() -> None:
    import network.signer as signer

    assert _IMPORT_TIME_HOME in _IMPORT_TIME_KEY_DIR.parents, (
        f"network.signer._KEY_DIR at import ({_IMPORT_TIME_KEY_DIR}) resolved outside the session "
        f"runtime home ({_IMPORT_TIME_HOME}): an import-time path escaped the session pin again"
    )

    repo_root = Path(__file__).resolve().parents[1]
    assert repo_root not in _IMPORT_TIME_KEY_DIR.parents, (
        f"network.signer._KEY_DIR at import ({_IMPORT_TIME_KEY_DIR}) points into the repository "
        "checkout -- every pytest session in the job would share one key record again"
    )
    assert repo_root not in signer._KEY_DIR.parents, (
        f"network.signer._KEY_DIR ({signer._KEY_DIR}) points into the repository checkout -- "
        "every pytest session in the job would share one key record again"
    )

    report = os.environ.get(_REPORT_ENV)
    if report:
        Path(report).write_text(
            json.dumps({"key_dir": str(_IMPORT_TIME_KEY_DIR), "home": str(_IMPORT_TIME_HOME)}),
            encoding="utf-8",
        )


def test_a_spawned_pytest_session_pins_its_own_home_not_its_parents(tmp_path: Path) -> None:
    """The CI failure lived in SPAWNED pytest sessions (the hermetic gauntlet's pollution
    matrix), which inherit the parent's environment at process start. A child session's
    import-frozen key dir must land under the CHILD's own fresh session home — not under the
    parent's home (state shared across processes) and not under the repository checkout."""
    import network.signer as parent_signer

    report = tmp_path / "child-home.json"
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", __file__,
            "-k", "test_import_frozen_runtime_paths_stay_inside_the_session_home",
            "-q", "-p", "no:cacheprovider",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, _REPORT_ENV: str(report)},
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]

    payload = json.loads(report.read_text(encoding="utf-8"))
    child_home = Path(payload["home"]).resolve()
    child_key_dir = Path(payload["key_dir"]).resolve()
    parent_home = Path(os.environ["VOOL_HOME"]).resolve()
    assert child_home != parent_home, (
        "the spawned session reused the parent's runtime home -- cross-process shared key "
        "state is back"
    )
    assert child_home in child_key_dir.parents, (
        f"the spawned session's import-frozen key dir ({child_key_dir}) resolved outside its "
        f"own session home ({child_home})"
    )
    assert child_key_dir != parent_signer._KEY_DIR.resolve(), (
        "the spawned session shares the parent's frozen key dir"
    )
