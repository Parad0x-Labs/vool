"""Parent side. Never imports the runtime -- that is the guarantee, not an implementation detail.

If this module imported `core.web.api.runtime`, the parent interpreter would acquire exactly the
state the boundary exists to keep out. So the parent only knows about temp directories, environment
dicts, `subprocess`, and the protocol document.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from tests.gauntlet.hermetic.protocol import KIND_INFRA, ChildOutcome, parse

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_TIMEOUT_S = 120.0

# Group-local budgets, JUSTIFIED BY MEASUREMENT, not by convenience
# (validation-logs/gauntlet-g1-g12-p0-20260905/latency/: 5 independent bootstrapped children,
# zero concurrent pytest processes sampled before and after every run, spread under 1.2s):
# boot p50 1.31s; per-turn p50 3.98s (one lookup) / 4.91s (rebind) / 8.08s (two-lookup
# aggregate) / 12.04s max 12.22s (the G1 typo clarification -- two lookups plus obligation
# recovery); the full six-turn group plus boot p50 41.59s, max 41.85s. Production latency is
# therefore bounded and small -- no interactive turn is minutes -- and the earlier 164.5s group
# figure was shared-machine contention (a ~4x inflation), not production. The global default
# stays at the disciplined 120s; this one group gets a wider TEST-LOCAL budget so a load spike
# on a shared box does not turn a measured ~55s child into a false infra failure, while a
# genuine hang still dies at 240s and `_hang_forever` continues to pin the mechanism at 15s.
#
# `harness_control` joins it by the same rule: measured 149.9 s (rc 0, full evidence) once the
# ambiguity probe's standing scripted verdict let every plain knowledge control turn run its
# FULL answering path -- generation, composition, publication gates -- instead of the probe's
# fast clarification short-circuit the original 120 s budget was calibrated for. Measured again
# on a slower reference box at ~290 s with full evidence and identical per-turn shapes: the
# spread is machine latency, not work, so the budget sits at 2x that measurement.
GROUP_TIMEOUTS: dict[str, float] = {"g1_continuation": 240.0, "harness_control": 600.0}

# Environment the child must NOT inherit from whoever invoked pytest, or the run stops being
# deterministic and starts depending on the developer's shell.
_STRIPPED = (
    "VOOL_HOME", "VOOL_WORKSPACE_ROOT", "VOOL_PROJECT_ROOT", "VOOL_DAEMON_BIND_PORT",
    "VOOL_DISABLE_MESH_DAEMON", "VOOL_PUBLIC_HIVE_ENABLED", "VOOL_PUBLIC_HIVE_REMOTE_CONFIG",
)


def child_env(home: Path, *, port: int) -> dict[str, str]:
    """A clean environment pinned to this child's own home.

    `VOOL_HOME` is the seam -- verified, not assumed: `core.runtime_paths.active_vool_home()`
    reads it, and `active_config_home_dir()` is `active_vool_home()/config`, which is where
    `core/web/api/runtime.py:590` writes `agent-bootstrap.json`. Redirecting it is what keeps the
    developer's real config directory untouched.
    """
    env = {key: value for key, value in os.environ.items() if key not in _STRIPPED}
    env["VOOL_HOME"] = str(home)
    env["VOOL_WORKSPACE_ROOT"] = str(home / "workspace")
    env["VOOL_DISABLE_MESH_DAEMON"] = "1"
    env["VOOL_DISABLE_COMPUTE_DAEMON"] = "1"
    env["VOOL_DAEMON_BIND_PORT"] = str(port)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


# THE REMAINING CANONICAL RED IS NOT OURS -- measured, and the earlier hypothesis was wrong.
#
# The previous pass assumed the two `test_toolsmith_run_tests_fs_confinement` failures were caused
# by this harness (children as extra load, or filesystem residue) and added a cross-process lock on
# that assumption. Attribution disproves it, so the lock was reverted rather than left in place
# wearing a false justification.
#
# Attribution, over the EXACT failing shard-2 composition (222 files):
#     A  exact shard 2, gauntlet included ............. victim RED
#     B  shard 2 with tests/gauntlet DESELECTED ....... victim RED   <- gauntlet not required
#     C  gauntlet present, child launches deselected .. victim RED
#     E  non-gauntlet + only the child launchers ...... victim RED
#
# Then the same non-gauntlet composition (215 files) at the RELEASED BASE 5d24af39, where no
# gauntlet file exists at all: victim RED. Bisected from 97 preceding files down to ONE.
#
# MINIMAL REPRODUCTION, at the released base, no gauntlet involved:
#     pytest tests/test_toolsmith_run_tests_fs_confinement.py                    -> 5 passed
#     pytest tests/test_t3_reliability_hardening.py  <victim>                    -> 2 FAILED
#     pytest <victim>  tests/test_t3_reliability_hardening.py                    -> 14 passed
#
# So it is a pre-existing, order-dependent defect between `test_t3_reliability_hardening` and the
# Toolsmith filesystem-confinement tests, latent on the released base. The base's own canonical run
# is 4/4 green only because its weight-based shard split happens to separate the two; adding ANY
# files -- the gauntlet is merely what we added -- reshuffles the split and surfaces it.
#
# The real discriminator is SHARD COMPOSITION, not gauntlet execution. Fixing it means fixing
# whatever `test_t3_reliability_hardening` leaves behind, which is production/test territory owned
# by another lane and explicitly out of scope here. Do NOT weaken the Toolsmith tests: they are
# confinement guarantees and they are the ones telling the truth.


def run_group(
    group: str,
    *,
    timeout_s: float | None = None,
    home: Path | None = None,
    keep_home: bool = False,
    port: int = 49300,
) -> ChildOutcome:
    """One scenario group, one fresh interpreter, one disposable home."""
    if timeout_s is None:
        timeout_s = GROUP_TIMEOUTS.get(group, DEFAULT_TIMEOUT_S)
    owned = home is None
    root = Path(home) if home is not None else Path(tempfile.mkdtemp(prefix="gauntlet-child-"))
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    stdout = stderr = ""
    returncode = -1
    timed_out = False
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "tests.gauntlet.hermetic.child", group],
            cwd=str(PROJECT_ROOT),
            env=child_env(root, port=port),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = time.monotonic() - started

    if timed_out:
        outcome = ChildOutcome(
            kind=KIND_INFRA, group=group, returncode=returncode, stdout=stdout, stderr=stderr,
            duration_s=duration, infra_reason=f"child exceeded the {timeout_s:.0f}s timeout",
        )
    else:
        document = parse(stdout)
        if document is None:
            outcome = ChildOutcome(
                kind=KIND_INFRA, group=group, returncode=returncode, stdout=stdout, stderr=stderr,
                duration_s=duration,
                infra_reason=f"child emitted no protocol document (rc={returncode})",
            )
        else:
            outcome = ChildOutcome(
                kind=str(document.get("kind") or KIND_INFRA),
                group=str(document.get("group") or group),
                payload=dict(document.get("payload") or {}),
                returncode=returncode, stdout=stdout, stderr=stderr, duration_s=duration,
            )
            # A zero `kind=result` with a non-zero exit is a contradiction; distrust it.
            if outcome.is_result and returncode != 0:
                outcome = ChildOutcome(
                    kind=KIND_INFRA, group=group, returncode=returncode, stdout=stdout,
                    stderr=stderr, duration_s=duration,
                    infra_reason=f"child claimed a result but exited {returncode}",
                )
    if owned and not keep_home:
        shutil.rmtree(root, ignore_errors=True)
    else:
        outcome.payload.setdefault("_home", str(root))
    return outcome


_GROUP_CACHE: dict[str, ChildOutcome] = {}
CHILD_LAUNCHES: list[str] = []


def group_evidence(group: str, *, timeout_s: float | None = None) -> dict[str, Any]:
    """The evidence for one group, computed by ONE child and shared by every assertion about it.

    Without the cache each assertion would launch its own interpreter, which is both slow and a
    silent drift back to one-child-per-test -- the shape the group registry exists to prevent.
    `CHILD_LAUNCHES` records every real launch so that invariant is assertable rather than assumed.
    """
    outcome = _GROUP_CACHE.get(group)
    if outcome is None:
        CHILD_LAUNCHES.append(group)
        outcome = run_group(group, timeout_s=timeout_s)
        _GROUP_CACHE[group] = outcome
    return outcome.require_result()["evidence"]


def group_self_test(group: str) -> dict[str, Any]:
    group_evidence(group)
    return _GROUP_CACHE[group].require_result()["self_test"]


__all__ = [
    "CHILD_LAUNCHES",
    "DEFAULT_TIMEOUT_S",
    "ChildOutcome",
    "child_env",
    "group_evidence",
    "group_self_test",
    "run_group",
]
