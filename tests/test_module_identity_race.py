"""R2 (AUD-20260829-003) regression + sabotage-proof harness.

Root cause (council verdict, corroborated independently by two investigator reports, R01 and
R03): the four A9 alias shims under `core/agent_runtime/` (`live_data_plan`, `attempt_approval`,
`attempt_followup`, `attempt_retry`) used to run `sys.modules[__name__] = _canonical`, replacing
their own sys.modules registry entry with a different object outright. Under concurrent first
import of one of these names -- exactly what happens when the conductor's ThreadPoolExecutor runs
two weather nodes that both do `from core.agent_runtime.live_data_plan import _weather_subtask`
for the first time in a fresh daemon process (core/conductor/operations.py,
core/conductor/scheduler.py:260, max_concurrent=2) -- CPython's import fast path can hand the
LOSING thread the module object it looked up BEFORE the winning thread's swap happened. That
object was abandoned at the swap and never carried a single canonical name, so the loser's
`IMPORT_FROM` bytecode raises

    ImportError: cannot import name '_weather_subtask' from 'core.agent_runtime.live_data_plan'
    (<path>)

byte-exact to what reached the operator's screen (AUD-20260829-003 evidence E001, E009). R03
reproduced this at 7,954/30,000 (26.5%) with a barrier-synchronized two-thread first-import
harness; this file ports that method against the real shim/canonical pairs in this tree.

Why this races the WHOLE per-shim loop inside its own subprocess (see `_run_worker` and the
`--worker` CLI at the bottom of this file) rather than purging `sys.modules` directly inside the
shared pytest process: what matters for the race is that `sys.modules` has no entry for the
target name yet (the "cold" precondition a fresh process has for a name it has never imported) --
not a literal new OS process per round, which is why an in-process purge-and-reimport loop
faithfully reproduces the hazard (this is also R03's own method: "30,000 rounds in the repo
venv", not 30,000 process launches). But `core/agent_runtime/live_data_render.py` and
`live_data_runner.py` import `LiveDataPlan`, `SubtaskLifecycle`, `SubtaskOutcome` etc. at THEIR
OWN module-load time -- if this test purged and re-imported `core.live_data_plan` inside the
shared test session, any code that later constructs instances via the freshly-reimported module
and compares them (`isinstance`) against a class object some other module cached earlier in the
same session would silently diverge. Running each shim's full round-loop in a fresh subprocess
keeps that churn out of the shared session entirely: the subprocess's `sys.modules` is discarded
when it exits, and the parent test process never touches these modules itself.

Sabotage proof performed by hand for AUD-20260829-003/BLUE2_REPORT.md (I6): this file's
`--worker` mode, run against a shim manually reverted to the old
`sys.modules[__name__] = _canonical` idiom, reproduces the ImportError above at high frequency
(measured: ~88-92% of 30,000 rounds per shim on this machine, all four shims); against the
re-export shims below it, 0/30,000 for all four across repeated runs.

**Pass 3 addendum.** The re-export shims above closed the race but broke a DIFFERENT invariant --
identity (`core.agent_runtime.X is core.X`), which silently vacuated ~18 `mock.patch` sites tree-
wide (BLUE-3). The pass-3 fix moves the resolution to `core/agent_runtime/__init__.py`: a package's
own `__init__` always finishes, for every thread, before any thread can look up one of its child
names, which makes registering the alias name there (`sys.modules[f"{__name__}.<child>"] =
<canonical>`) a genuinely single-threaded boot seam -- no thread ever observes the alias name in a
half-assigned state, because nothing is registered under it until this single-threaded step has
already completed for every possible caller. `test_cold_start_concurrent_first_import_of_the_package_itself`
below is the race harness for THIS specific mechanism: the harness above purges only the SUBMODULE
sys.modules entries (`core.agent_runtime` itself stays loaded across rounds, since a long-lived
pytest session cannot cleanly re-run a package's `__init__` without corrupting other tests' cached
references), so it actually exercises the alias files' pass-1/2 fallback body, not the pass-3
`__init__.py` mechanism specifically -- confirmed by checking `alias is canonical` after each of
its rounds returns False. Testing genuine concurrent COLD START of the package itself needs a
fresh interpreter per round (an already-imported package cannot be "un-imported" safely), so that
test spawns real subprocesses -- fewer rounds than the harness above for wall-clock reasons, but
the mechanism it is testing has no timing-dependent branch inside it (see the `__init__.py`
docstring for why), so a much smaller sample is still dispositive: measured 0/300 by hand for
BLUE2_REPORT_PASS3.md, both import failures and identity failures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Matches R03's reproduction exactly. Override for a fast local smoke pass; CI and any run whose
# result is cited as evidence should use the default.
DEFAULT_ROUNDS = 30_000

# (shim dotted path, canonical dotted path, one name only the canonical module defines that
# downstream production code imports directly through the shim -- three of these are
# leading-underscore "private" names, which is exactly why a wildcard `import *`-based re-export
# would NOT have been a sufficient fix; see the shim files themselves).
TARGETS: list[tuple[str, str, str]] = [
    ("core.agent_runtime.live_data_plan", "core.live_data_plan", "_weather_subtask"),
    ("core.agent_runtime.attempt_approval", "core.attempt_approval", "is_approval_valid"),
    ("core.agent_runtime.attempt_followup", "core.attempt_followup", "classify_followup_intent"),
    ("core.agent_runtime.attempt_retry", "core.attempt_retry", "plan_retry_generation"),
]


def _race_once(shim_module: str, symbol: str) -> list[str | None]:
    """One barrier-synced two-thread concurrent-first-import round.

    Uses a real `from X import Y` statement (via exec of compiled code) so CPython's actual
    IMPORT_NAME/IMPORT_FROM bytecodes run -- this is what produces the exact
    "ImportError: cannot import name ... from '...' (path)" shape, not a synthetic AttributeError
    from a manual importlib.import_module + getattr.
    """
    results: list[str | None] = [None, None]
    barrier = threading.Barrier(2)
    code = compile(f"from {shim_module} import {symbol}\n", "<module_identity_race>", "exec")

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            exec(code, {})
        except ImportError as exc:
            results[idx] = str(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _run_worker(shim_module: str, canonical_module: str, symbol: str, rounds: int) -> dict:
    failures = 0
    both_failed = 0
    sample_failure = None
    for _ in range(rounds):
        sys.modules.pop(shim_module, None)
        sys.modules.pop(canonical_module, None)
        round_failures = [r for r in _race_once(shim_module, symbol) if r is not None]
        if round_failures:
            failures += 1
            if len(round_failures) == 2:
                both_failed += 1
            if sample_failure is None:
                sample_failure = round_failures[0]
    return {
        "shim_module": shim_module,
        "rounds": rounds,
        "failures": failures,
        "both_threads_failed": both_failed,
        "sample_failure": sample_failure,
    }


def _configured_rounds() -> int:
    raw = os.environ.get("VOOL_MODULE_IDENTITY_RACE_ROUNDS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_ROUNDS


@pytest.mark.parametrize("shim_module,canonical_module,symbol", TARGETS, ids=[t[0] for t in TARGETS])
def test_concurrent_first_import_never_raises_importerror(shim_module, canonical_module, symbol):
    """Sabotage-proof: reverting any one of the four shims to the sys.modules-swap idiom must
    turn this red, naming the exact ImportError that reached the operator (E001)."""
    rounds = _configured_rounds()
    env = dict(os.environ)
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing_path if existing_path else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            shim_module,
            canonical_module,
            symbol,
            str(rounds),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"race worker subprocess for {shim_module} crashed (exit {proc.returncode}): "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-4000:]!r}"
    )
    stdout_lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    assert stdout_lines, f"race worker for {shim_module} produced no output; stderr={proc.stderr[-4000:]!r}"
    result = json.loads(stdout_lines[-1])

    assert result["failures"] == 0, (
        f"{shim_module}: {result['failures']}/{result['rounds']} concurrent-first-import rounds "
        f"raised ImportError -- the sys.modules-swap hazard (AUD-20260829-003 R2) is back. "
        f"Sample failure: {result['sample_failure']}"
    )


# ---------------------------------------------------------------------------------------------
# Pass 3: concurrent COLD START of core.agent_runtime itself -- the exact scenario the
# `__init__.py` alias registration must be race-free for. One round = one fresh subprocess (an
# already-imported package cannot be safely "un-imported" in a live process; see the module
# docstring), so this needs real process spawns rather than the purge-and-reimport trick above.
DEFAULT_COLD_START_ROUNDS = 60


def _cold_start_worker_round(shim_module: str, canonical_module: str, symbol: str) -> dict:
    """Runs INSIDE the fresh subprocess (see `--cold-start-worker`): two threads race the literal
    first-ever touch of `core.agent_runtime` in this interpreter via `from <shim_module> import
    <symbol>`, then confirms `alias is canonical` from this same fresh process."""
    import importlib

    results: list[str | None] = [None, None]
    barrier = threading.Barrier(2)
    code = compile(f"from {shim_module} import {symbol}\n", "<cold_start_race>", "exec")

    def worker(idx: int) -> None:
        barrier.wait()
        try:
            exec(code, {})
        except ImportError as exc:
            results[idx] = str(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    alias_mod = sys.modules.get(shim_module) or importlib.import_module(shim_module)
    canon_mod = sys.modules.get(canonical_module) or importlib.import_module(canonical_module)
    return {
        "failures": [r for r in results if r is not None],
        "identity_holds": alias_mod is canon_mod,
    }


def _cold_start_rounds() -> int:
    raw = os.environ.get("VOOL_MODULE_IDENTITY_COLD_START_ROUNDS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_COLD_START_ROUNDS


@pytest.mark.parametrize("shim_module,canonical_module,symbol", TARGETS, ids=[t[0] for t in TARGETS])
def test_cold_start_concurrent_first_import_of_the_package_itself(shim_module, canonical_module, symbol):
    """Sabotage-proof for the PASS-3 mechanism specifically: reverting
    `core/agent_runtime/__init__.py`'s alias registration must turn this red (and would also turn
    `test_alias_module_object_is_identically_the_canonical_module_object`, in
    tests/test_module_identity_mock_patch.py, red)."""
    rounds = _cold_start_rounds()
    env = dict(os.environ)
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing_path if existing_path else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    total_failures = 0
    identity_failures = 0
    sample_failure = None
    for _ in range(rounds):
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--cold-start-worker",
                shim_module,
                canonical_module,
                symbol,
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"cold-start worker for {shim_module} crashed (exit {proc.returncode}): "
            f"stdout={proc.stdout!r} stderr={proc.stderr[-4000:]!r}"
        )
        stdout_lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
        result = json.loads(stdout_lines[-1])
        if result["failures"]:
            total_failures += 1
            sample_failure = sample_failure or result["failures"][0]
        if not result["identity_holds"]:
            identity_failures += 1

    assert total_failures == 0, (
        f"{shim_module}: {total_failures}/{rounds} cold-start concurrent-import rounds raised "
        f"ImportError -- sample: {sample_failure}"
    )
    assert identity_failures == 0, (
        f"{shim_module}: {identity_failures}/{rounds} cold-start rounds did NOT have "
        f"alias is canonical -- the pass-3 identity fix regressed"
    )


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--cold-start-worker":
        _, _flag, arg_shim, arg_canonical, arg_symbol = sys.argv[:5]
        print(json.dumps(_cold_start_worker_round(arg_shim, arg_canonical, arg_symbol)))
        raise SystemExit(0)

    if len(sys.argv) >= 6 and sys.argv[1] == "--worker":
        _, _flag, arg_shim, arg_canonical, arg_symbol, arg_rounds = sys.argv[:6]
        print(json.dumps(_run_worker(arg_shim, arg_canonical, arg_symbol, int(arg_rounds))))
        raise SystemExit(0)

    # Manual standalone run across all four targets (used for the before/after sabotage-proof
    # numbers reported in BLUE2_REPORT.md). Usage: python tests/test_module_identity_race.py [N]
    _n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROUNDS
    for _shim, _canonical, _symbol in TARGETS:
        print(json.dumps(_run_worker(_shim, _canonical, _symbol, _n)))
