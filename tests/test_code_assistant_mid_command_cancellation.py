"""Mid-command cancellation: a cancel press while a command RUNS must reach the sandbox job,
kill it, and come back as the typed ``cancelled`` outcome — not as success, not as a hang, not
as a generic failure.

The served layer already places the turn's ``cancel_event`` on the source context
(``core/web/api/runtime.py`` registers the live turn and stamps the context); the job runner
already honors ``cancel_event`` mid-process (``JobRunner._wait``, CANCELLED_RETURNCODE=125,
status ``cancelled``). The missing hop, measured here RED: ``_run_command`` never passed the
token into the runner, so a 30-second command slept the full turn away.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core.runtime_execution_tools import execute_runtime_tool

SLEEP_SCRIPT = "import time\ntime.sleep(30)\n"


def _ctx(workspace: Path, cancel: threading.Event) -> dict:
    from core.mode_permission_policy import reset_mode_permission_state, set_active_mode

    reset_mode_permission_state()
    set_active_mode("cancel-sess", "auto")
    return {
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "session_id": "cancel-sess",
        "operating_mode": "auto",
        "cancel_event": cancel,
    }


@pytest.mark.parametrize(
    ("intent", "arguments"),
    [
        ("sandbox.run_command", {"command": "python3 sleep.py"}),
        ("workspace.run_tests", {"command": "python3 sleep.py"}),
    ],
)
def test_cancel_pressed_mid_command_kills_the_job(tmp_path: Path, intent: str, arguments: dict) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "sleep.py").write_text(SLEEP_SCRIPT, encoding="utf-8")
    cancel = threading.Event()
    ctx = _ctx(workspace, cancel)
    result_box: dict = {}

    def run() -> None:
        result_box["result"] = execute_runtime_tool(intent, arguments, source_context=ctx)

    worker = threading.Thread(target=run)
    started = time.monotonic()
    worker.start()
    time.sleep(1.0)  # the command is now RUNNING (the sandbox exec itself takes a moment)
    cancel.set()
    worker.join(timeout=20.0)
    elapsed = time.monotonic() - started

    assert not worker.is_alive(), "the turn must not wait out a 30s command after cancel"
    assert elapsed < 15.0, f"cancel must interrupt the job promptly, took {elapsed:.1f}s"
    result = result_box["result"]
    assert result is not None
    assert result.ok is False, "a cancelled command is never a success"
    status = str(result.status or "")
    details_status = str((result.details or {}).get("status") or "")
    assert "cancel" in status or "cancel" in details_status or int(result.details.get("returncode", 0) or 0) == 125, (
        f"the outcome must be the typed cancelled state, got status={status!r} details={details_status!r} "
        f"rc={result.details.get('returncode')}"
    )
    # The cancelled command is never replayed as executed output: stdout stays empty.
    assert not str(result.details.get("stdout") or "").strip()


# --------------------------------------------------------------------- token SHAPES
#
# The same context key carries three different shapes across this runtime: a
# ``threading.Event`` from the served door (``core/live_turns``), the composite event a code
# task builds (``core/code_assistant/task_runtime._CompositeCancelEvent``), and a plain
# callable/flag at the conductor and router seams (``core/conductor/scheduler``,
# ``core/memory_first_router``). The job runner polls ``is_set``; only the first two answer it.
# Handing it a bare callable raised AttributeError inside ``JobRunner._wait`` and took the
# command down as a generic failure instead of running it. These two name that boundary.


def _run(intent: str, arguments: dict, ctx: dict, *, timeout: float = 25.0):
    box: dict = {}

    def run() -> None:
        box["result"] = execute_runtime_tool(intent, arguments, source_context=ctx)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=timeout)
    assert not worker.is_alive(), "the command never returned"
    return box["result"]


def test_a_callable_cancel_token_cancels_the_running_job(tmp_path: Path) -> None:
    """The conductor/router shape: cancellation is a callable, not an Event. A callable that
    reports cancelled must kill the child exactly like the served Event does."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "sleep.py").write_text(SLEEP_SCRIPT, encoding="utf-8")
    flag = {"cancelled": False}
    ctx = _ctx(workspace, threading.Event())
    ctx["cancel_event"] = lambda: flag["cancelled"]

    box: dict = {}

    def run() -> None:
        box["result"] = execute_runtime_tool("sandbox.run_command", {"command": "python3 sleep.py"}, source_context=ctx)

    worker = threading.Thread(target=run)
    started = time.monotonic()
    worker.start()
    time.sleep(1.0)
    flag["cancelled"] = True
    worker.join(timeout=20.0)
    elapsed = time.monotonic() - started

    assert not worker.is_alive(), "a callable cancel token must interrupt the job, not be ignored"
    assert elapsed < 15.0, f"cancel must interrupt the job promptly, took {elapsed:.1f}s"
    result = box["result"]
    assert result.ok is False, "a cancelled command is never a success"
    assert "cancel" in str(result.status or "") or int(result.details.get("returncode", 0) or 0) == 125, result.details
    assert not str(result.details.get("stdout") or "").strip()


def test_an_unreadable_cancel_token_is_not_permission_to_run(tmp_path: Path) -> None:
    """A signal that cannot be read counts as SET. The alternative -- letting the exception
    escape, or treating an unreadable token as 'not cancelled' -- runs a child the operator may
    already have stopped."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "sleep.py").write_text(SLEEP_SCRIPT, encoding="utf-8")

    def _explode() -> bool:
        raise RuntimeError("cancel channel is gone")

    ctx = _ctx(workspace, threading.Event())
    ctx["cancel_event"] = _explode

    started = time.monotonic()
    result = _run("sandbox.run_command", {"command": "python3 sleep.py"}, ctx)
    elapsed = time.monotonic() - started

    assert elapsed < 15.0, f"an unreadable token must not sleep out the 30s command, took {elapsed:.1f}s"
    assert result.ok is False
    assert "cancel" in str(result.status or "") or int(result.details.get("returncode", 0) or 0) == 125, result.details
