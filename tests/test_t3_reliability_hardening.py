from __future__ import annotations

import contextlib
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from core.maintenance import MaintenanceConfig, MaintenanceLoop
from sandbox.container_adapter import ExecutionResult
from sandbox.job_runner import JobRunner
from sandbox.resource_limits import ExecutionPolicy

# The three JobRunner-timeout tests below globally mock `sandbox.job_runner.subprocess.run`,
# which also poisons `sandbox.job_runner._BACKEND_USABLE` (see the `isolation_backend_probe_
# cache_reset` autouse fixture in tests/conftest.py for the mechanism and why the fix lives
# there, as a suite-wide invariant, rather than as a fixture these tests opt into individually).

# --- (1) maintenance loop survives a raising run_tick -----------------------


def test_maintenance_loop_survives_raising_run_tick() -> None:
    # A transient failure in run_tick must be logged + retried, not silently
    # kill the (daemon=False) maintenance thread.
    loop = MaintenanceLoop(
        config=MaintenanceConfig(
            tick_seconds=0,
            max_failure_backoff_seconds=0,
        )
    )

    call_count = {"n": 0}
    third_tick_reached = threading.Event()

    def _flaky_tick() -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient sqlite error")
        if call_count["n"] >= 3:
            third_tick_reached.set()

    logged: list[str] = []

    def _capture_log(event_type: str, *args, **kwargs) -> None:
        logged.append(event_type)

    with mock.patch.object(loop, "run_tick", side_effect=_flaky_tick), mock.patch(
        "core.maintenance.audit_logger.log", side_effect=_capture_log
    ):
        loop.start()
        try:
            # The loop must keep ticking past the failure; if the raise had
            # killed the thread, run_tick would never reach call #3.
            assert third_tick_reached.wait(timeout=5.0), "maintenance loop died after a raising tick"
        finally:
            loop.stop()

    assert call_count["n"] >= 3
    assert "maintenance_tick_failed" in logged
    assert not (loop._thread and loop._thread.is_alive())


def test_maintenance_loop_logs_failure_then_resets_counter_on_recovery() -> None:
    # The loop should record consecutive_failures and clear it once a tick
    # succeeds again.
    loop = MaintenanceLoop(
        config=MaintenanceConfig(
            tick_seconds=0,
            max_failure_backoff_seconds=0,
        )
    )

    outcomes = iter([RuntimeError("boom"), RuntimeError("boom"), None])
    recovered = threading.Event()

    def _tick() -> None:
        try:
            result = next(outcomes)
        except StopIteration:
            recovered.set()
            return
        if isinstance(result, BaseException):
            raise result
        recovered.set()

    captured: list[dict] = []

    def _capture_log(event_type: str, *args, **kwargs) -> None:
        if event_type == "maintenance_tick_failed":
            captured.append(dict(kwargs.get("details") or {}))

    with mock.patch.object(loop, "run_tick", side_effect=_tick), mock.patch(
        "core.maintenance.audit_logger.log", side_effect=_capture_log
    ):
        loop.start()
        try:
            assert recovered.wait(timeout=5.0)
        finally:
            loop.stop()

    failure_counts = [int(d.get("consecutive_failures", 0)) for d in captured]
    assert failure_counts[:2] == [1, 2]


def test_backoff_sleep_returns_immediately_when_stop_requested() -> None:
    loop = MaintenanceLoop(config=MaintenanceConfig(tick_seconds=30, max_failure_backoff_seconds=300))
    loop._stop.set()
    started = time.time()
    stopped = loop._backoff_sleep(consecutive_failures=5)
    assert stopped is True
    assert time.time() - started < 1.0



class _FakeTimedOutProcess:
    """A process whose wait times out, for the three tests below.

    They used to mock `sandbox.job_runner.subprocess.run`. The runner no longer calls it: it
    starts the child with `subprocess.Popen(..., start_new_session=True)` so the whole process
    group can be torn down on timeout and cancellation (a `subprocess.run` timeout kills the
    direct child only and orphans everything it spawned). The sabotage is the same — the wait
    raises `TimeoutExpired` — moved to the call the runner actually makes.

    `pid` is deliberately a number no process holds, so the group teardown's `os.getpgid`
    raises `ProcessLookupError` and returns without signalling anything real.
    """

    def __init__(self, timeout_exc, drained=("", "")):
        self._exc = timeout_exc
        self._drained = drained
        self._raised = False
        self.pid = 2**30
        self.args = ["fake"]
        self.returncode = None

    def communicate(self, timeout=None):
        if not self._raised:
            self._raised = True
            raise self._exc
        return self._drained

    def wait(self, timeout=None):
        self.returncode = -9
        return self.returncode

    def kill(self):
        return None


def _prime_backend_probe_cache() -> None:
    """Run the backend probe BEFORE `subprocess.Popen` is mocked.

    `sandbox.job_runner.subprocess` IS the `subprocess` module, so patching `.Popen` on it
    patches it globally — including underneath `subprocess.run`, which `_backend_usable` uses
    for its own probe. Letting the probe run first (and cache its verdict) keeps the mock
    aimed at the job's own launch and nothing else.
    """
    from sandbox.job_runner import JobRunner as ProbeRunner

    with tempfile.TemporaryDirectory() as probe_dir:
        with contextlib.suppress(Exception):
            ProbeRunner(
                ExecutionPolicy(workspace_root=Path(probe_dir), network_isolation_mode="heuristic_only")
            )._with_network_isolation(["true"], (Path(probe_dir).resolve(),))


# --- (2) JobRunner returns graceful result on timeout -----------------------


def test_job_runner_returns_graceful_result_on_timeout() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = JobRunner(
            ExecutionPolicy(
                workspace_root=Path(tmpdir),
                network_isolation_mode="heuristic_only",
            )
        )
        timeout_exc = subprocess.TimeoutExpired(cmd=["sleep", "1000"], timeout=1)
        _prime_backend_probe_cache()
        with mock.patch(
            "sandbox.job_runner.subprocess.Popen",
            return_value=_FakeTimedOutProcess(timeout_exc),
        ):
            result = runner.run(["sleep", "1000"])

    assert isinstance(result, ExecutionResult)
    assert result.returncode != 0
    assert result.returncode == 124
    assert "timed out" in result.stderr.lower()


def test_job_runner_timeout_preserves_partial_output() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = JobRunner(
            ExecutionPolicy(
                workspace_root=Path(tmpdir),
                network_isolation_mode="heuristic_only",
            )
        )
        timeout_exc = subprocess.TimeoutExpired(
            cmd=["python3"],
            timeout=1,
            output=b"partial-stdout",
            stderr=b"partial-stderr",
        )
        _prime_backend_probe_cache()
        with mock.patch(
            "sandbox.job_runner.subprocess.Popen",
            return_value=_FakeTimedOutProcess(timeout_exc),
        ):
            result = runner.run(["python3", "-c", "while True: pass"])

    assert result.returncode == 124
    assert "partial-stdout" in result.stdout
    assert "partial-stderr" in result.stderr
    assert "timed out" in result.stderr.lower()


def test_job_runner_timeout_handles_none_partial_output() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = JobRunner(
            ExecutionPolicy(
                workspace_root=Path(tmpdir),
                network_isolation_mode="heuristic_only",
            )
        )
        timeout_exc = subprocess.TimeoutExpired(cmd=["python3"], timeout=1)  # no output captured
        _prime_backend_probe_cache()
        with mock.patch(
            "sandbox.job_runner.subprocess.Popen",
            return_value=_FakeTimedOutProcess(timeout_exc),
        ):
            result = runner.run(["python3", "-c", "pass"])

    assert result.returncode == 124
    assert result.stdout == ""
    assert "timed out" in result.stderr.lower()


# --- (4) liquefy_bridge background export guard -----------------------------


def test_export_task_bundle_sync_does_not_raise_when_get_connection_fails() -> None:
    import core.liquefy_bridge as lb

    logged: list[str] = []
    with mock.patch.object(lb, "get_connection", side_effect=RuntimeError("db unavailable")), mock.patch.object(
        lb.audit_logger, "log", side_effect=lambda event_type, **kw: logged.append(event_type)
    ):
        # Must not raise even though get_connection fails inside the try, and
        # the finally must not crash on the unbound connection.
        lb._export_task_bundle_sync("task-xyz")

    assert "liquefy_vault_error" in logged


def test_async_run_swallows_worker_exception() -> None:
    import core.liquefy_bridge as lb

    done = threading.Event()
    logged: list[str] = []

    @lb._async_run
    def _boom() -> None:
        try:
            raise ValueError("kaboom")
        finally:
            done.set()

    with mock.patch.object(lb.audit_logger, "log", side_effect=lambda event_type, **kw: logged.append(event_type)):
        # Calling the guarded async wrapper must never propagate to the caller.
        _boom()
        assert done.wait(timeout=5.0)
        # Give the guard a moment to record the swallowed error.
        for _ in range(50):
            if "liquefy_async_task_error" in logged:
                break
            time.sleep(0.02)

    assert "liquefy_async_task_error" in logged


def test_async_run_guards_thread_spawn_failure() -> None:
    import core.liquefy_bridge as lb

    logged: list[str] = []

    @lb._async_run
    def _noop() -> None:
        return None

    with mock.patch("core.liquefy_bridge.threading.Thread", side_effect=RuntimeError("cannot spawn")), mock.patch.object(
        lb.audit_logger, "log", side_effect=lambda event_type, **kw: logged.append(event_type)
    ):
        # A failure to even start the background thread must not crash the caller.
        _noop()

    assert "liquefy_async_spawn_error" in logged


# --- (3) daemon genesis-PoW move was dropped this round (adversarial review
#         flagged an empty-nonce broadcast window that earns sybil_invalid_pow
#         strikes); revisit with a broadcast-gated-on-nonce fix.
