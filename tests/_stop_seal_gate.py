"""Test-only gate: hold a served daemon's seal at one chosen point, so a test presses Stop exactly there.

Loaded only by a daemon a test starts through :func:`start_gated` (never by the product). It wraps
two functions of ``core.finalization`` and calls through to them; while a hold file is absent it
changes nothing. Each hold is used once (its file is removed when it catches a turn):

* ``hold_after_learning``: in the turn's worker, right after it decided what it learns from the
  exchange, before it returns (a served stream seals its answer after that);
* ``hold_at_check``: after ``finalize_answer`` read the turn's cancel marker, before the seal;
* ``hold_at_bind``: inside the seal, before the answer is bound (``fail_bind`` makes the bind fail).

The gate writes ``after_learning`` / ``at_check`` / ``at_bind`` when it holds, ``stop_waiting`` when a stop waits for the
held seal, and goes on when the test writes ``go``. Files, not timing, decide every ordering.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

GATE_ENV = "VOOL_TEST_STOP_SEAL_GATE"
_BOOT = (
    "import runpy, tests._stop_seal_gate as gate; gate.install(); "
    "runpy.run_module('apps.vool_api_server', run_name='__main__', alter_sys=True)"
)


def wait_for(path: Path, timeout: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def _take(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _caller_chain() -> str:
    """Where the held seal was called from (the repo's own frames), for the test's evidence."""
    import traceback

    frames = [f for f in traceback.extract_stack()[:-2] if "/tests/" not in f.filename and "site-packages" not in f.filename]
    return "\n".join(f"{Path(f.filename).name}:{f.name}:{f.lineno}" for f in frames[-12:])


def install() -> None:
    gate = Path(os.environ[GATE_ENV])
    import core.finalization as fin
    import core.persistent_memory as pm

    real_marker, real_bind = fin._turn_cancel_marker_fired, fin._bind_durably

    def marker(context):
        fired = real_marker(context)
        if sys._getframe(1).f_code.co_name == "finalize_answer" and _take(gate / "hold_at_check"):
            (gate / "at_check").write_text(_caller_chain(), encoding="utf-8")
            wait_for(gate / "go")
        return fired

    def bind(commit, **kwargs):
        if _take(gate / "hold_at_bind"):
            from core.semantic.semantic_admissions import current_request_id

            request_id = str(current_request_id() or "")
            seals = getattr(pm, "_SEALS_IN_FLIGHT", None)
            if seals is not None:
                import threading

                class _Watched(threading.Event):
                    def wait(self, timeout=None):
                        (gate / "stop_waiting").touch()
                        return super().wait(timeout)

                with pm._TRANSCRIPT_STAGE_LOCK:
                    if request_id in seals:
                        seals[request_id] = _Watched()
            (gate / "at_bind").write_text(_caller_chain(), encoding="utf-8")
            wait_for(gate / "go")
            if (gate / "fail_bind").exists():
                from core.final_response_store import FinalizationRejected

                raise FinalizationRejected(str(commit.get("turn_id") or ""), "sha256:held", "sha256:refused")
        return real_bind(commit, **kwargs)

    import core.context_retrieval as retrieval

    real_telemetry = retrieval.update_retrieval_telemetry

    def telemetry(*args, **kwargs):
        # The turn's worker calls this right after it decided what it learns from the exchange
        # (memory extraction, the turn-context page), before it returns and the stream seals.
        if sys._getframe(1).f_code.co_name == "_run_agent_locked" and _take(gate / "hold_after_learning"):
            (gate / "after_learning").write_text(_caller_chain(), encoding="utf-8")
            wait_for(gate / "go")
        return real_telemetry(*args, **kwargs)

    fin._turn_cancel_marker_fired = marker
    fin._bind_durably = bind
    retrieval.update_retrieval_telemetry = telemetry


def start_gated(daemon, gate: Path, *, timeout: float = 240.0):
    """Start ``daemon`` (a ``tests._reader_served_rig.ServedDaemon``) with this gate installed."""
    from tests._reader_served_rig import REPO_ROOT

    gate.mkdir(parents=True, exist_ok=True)
    daemon.env_extra[GATE_ENV] = str(gate)
    daemon.home.mkdir(parents=True, exist_ok=True)
    daemon.register_provider()
    handle = daemon.log_path.open("wb")
    daemon.process = subprocess.Popen(
        [sys.executable, "-c", _BOOT, "--port", str(daemon.port), "--bind", "127.0.0.1"],
        cwd=str(REPO_ROOT), env=daemon.env(), stdout=handle, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon.process.poll() is not None:
            raise RuntimeError(f"daemon exited {daemon.process.returncode}\n{daemon.log_tail()}")
        try:
            with urlopen(f"{daemon.base_url}/healthz", timeout=3) as response:
                if response.status == 200:
                    return daemon
        except (URLError, HTTPError, OSError):
            time.sleep(1.0)
    raise TimeoutError(f"daemon never became healthy\n{daemon.log_tail()}")
