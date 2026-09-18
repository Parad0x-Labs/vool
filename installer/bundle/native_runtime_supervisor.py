"""Owned runtime lifecycle for the native VOOL/VOOL window.

The macOS app launcher ``exec``s the native host; this object then owns any API process it starts.
It will attach only to a runtime whose stamped Git identity matches the exact candidate, rejects an
unknown occupant on the canonical port, gates the window on real ``/healthz`` readiness, and tears
its child process group down when the native event loop exits.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


class NativeRuntimeError(RuntimeError):
    """The exact native runtime could not be established safely."""


def _sha_matches(expected: str, actual: str) -> bool:
    expected = str(expected or "").strip().lower()
    actual = str(actual or "").strip().lower()
    if not expected or not actual:
        return False
    if not all(char in "0123456789abcdef" for char in expected + actual):
        return False
    return expected.startswith(actual) or actual.startswith(expected)


class NativeRuntimeSupervisor:
    """Start, identify, monitor, and stop the API used by one native-window lifetime."""

    def __init__(
        self,
        project_root: Path,
        command: list[str],
        *,
        expected_sha: str,
        health_url: str = "http://127.0.0.1:11435/healthz",
        env: dict[str, str] | None = None,
        require_owned: bool = True,
        startup_timeout: float = 90.0,
        poll_interval: float = 0.25,
        popen: Callable[..., Any] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.command = [str(part) for part in command]
        self.expected_sha = str(expected_sha or "").strip().lower()
        self.health_url = str(health_url)
        self.env = dict(os.environ if env is None else env)
        self.require_owned = bool(require_owned)
        self.startup_timeout = float(startup_timeout)
        self.poll_interval = float(poll_interval)
        self._popen = popen
        self._sleep = sleep
        self.process: Any | None = None
        self.log_handle: Any | None = None
        self.attached_existing = False
        self.ready_identity: dict[str, Any] = {}

    @classmethod
    def from_environment(cls, *, module_path: str | Path) -> NativeRuntimeSupervisor:
        module_root = Path(module_path).resolve().parents[2]
        root = Path(os.environ.get("VOOL_PROJECT_ROOT") or module_root).expanduser().resolve()
        mode = str(os.environ.get("VOOL_RUNTIME_MODE") or "wrapper").strip().lower()
        if mode == "self-contained":
            command = [sys.executable, "-m", "apps.vool_api_server"]
        else:
            starter = root / "Start_VOOL.sh"
            command = ["bash", str(starter)]
        expected = str(os.environ.get("VOOL_EXPECTED_COMMIT") or "").strip()
        if not expected:
            with contextlib.suppress(Exception):
                expected = subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=5,
                ).strip()
        origin = str(os.environ.get("VOOL_NATIVE_API_URL") or "http://127.0.0.1:11435").rstrip("/")
        if mode == "self-contained":
            # The owned daemon listens where the window will look. Without this the self-contained
            # command took the server's default port whatever the origin said, so a second bundle
            # instance (an acceptance run beside an operator's live window) attached to -- or
            # stopped -- the runtime on 11435 instead of owning its own.
            port = urllib.parse.urlsplit(origin).port
            if port:
                command = [*command, "--port", str(port)]
        timeout = float(os.environ.get("VOOL_NATIVE_STARTUP_TIMEOUT") or 300)
        require_owned = str(os.environ.get("VOOL_NATIVE_REQUIRE_OWNED_RUNTIME") or "1").strip().lower() not in {
            "0", "false", "no", "off",
        }
        return cls(
            root,
            command,
            expected_sha=expected,
            health_url=f"{origin}/healthz",
            require_owned=require_owned,
            startup_timeout=timeout,
        )

    @property
    def owns_runtime(self) -> bool:
        return self.process is not None

    def _health(self) -> dict[str, Any] | None:
        try:
            request = urllib.request.Request(self.health_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=1.5) as response:
                if int(getattr(response, "status", 200)) != 200:
                    return None
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            return None
        return dict(payload) if isinstance(payload, dict) else None

    @staticmethod
    def _identity(payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        runtime = payload.get("runtime")
        if not isinstance(runtime, dict):
            # /api/runtime/version may itself be the stamp; accept that form for diagnostics.
            runtime = payload if "commit" in payload or "commit_full" in payload else {}
        return dict(runtime)

    def _identity_matches(self, payload: dict[str, Any] | None) -> bool:
        identity = self._identity(payload)
        actual = str(identity.get("commit_full") or identity.get("commit") or "").strip()
        return _sha_matches(self.expected_sha, actual)

    @staticmethod
    def _recognized_vool(payload: dict[str, Any] | None) -> bool:
        """Recognize the stable VOOL health schema, never the user-chosen display name."""
        if not isinstance(payload, dict):
            return False
        runtime = payload.get("runtime")
        return (
            payload.get("ok") is True
            and isinstance(runtime, dict)
            and isinstance(runtime.get("pid"), int)
            and int(runtime["pid"]) > 1
            and isinstance(runtime.get("protocol_version"), int)
            and int(runtime["protocol_version"]) >= 1
            and str(runtime.get("workstation_version") or "").startswith("vool-workstation-")
            and bool(str(runtime.get("release_version") or "").strip())
        )

    def _stop_stale_runtime(self, payload: dict[str, Any]) -> None:
        """Stop only a positively identified VOOL/VOOL API occupant; unknown ports fail closed."""
        if not self._recognized_vool(payload):
            raise NativeRuntimeError("canonical API port is occupied by an unidentifiable process")
        runtime = dict(payload["runtime"])
        pid = int(runtime["pid"])
        if pid == os.getpid():
            raise NativeRuntimeError("runtime health endpoint reported the native host PID")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as exc:
            raise NativeRuntimeError(f"could not stop stale VOOL runtime pid={pid}: {type(exc).__name__}") from exc
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._health() is None:
                return
            self._sleep(0.2)
        raise NativeRuntimeError(f"stale VOOL runtime pid={pid} survived SIGTERM")

    def ensure_ready(self) -> dict[str, Any]:
        """Return the exact runtime stamp, or raise before any native window is created."""
        if not self.expected_sha:
            raise NativeRuntimeError("expected source SHA is unavailable; refusing an unidentifiable native build")
        existing = self._health()
        if existing is not None:
            if self._identity_matches(existing) and not self.require_owned:
                self.attached_existing = True
                self.ready_identity = self._identity(existing)
                return dict(self.ready_identity)
            # The native bundle's default is an owned process tree: even an exact pre-existing API
            # is replaced so Quit has a mechanically provable child to tear down. An integration
            # host may opt into exact-match attachment with VOOL_NATIVE_REQUIRE_OWNED_RUNTIME=0.
            self._stop_stale_runtime(existing)
        if not self.command:
            raise NativeRuntimeError("native runtime start command is empty")
        if self.command[0] == "bash" and len(self.command) > 1 and not Path(self.command[1]).is_file():
            raise NativeRuntimeError(f"runtime starter is missing: {self.command[1]}")

        log_dir = Path(self.env.get("VOOL_HOME") or self.project_root / "dist" / "vool-home") / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_handle = (log_dir / "native-runtime-child.log").open("ab")
        deadline = time.monotonic() + self.startup_timeout
        # The child can wait for human authorization without racing a shorter
        # independent timeout. Leave time for binding the API after authorization.
        child_env = dict(self.env)
        child_env["VOOL_NATIVE_AUTHORIZATION_DEADLINE"] = str(deadline - min(5.0, self.startup_timeout / 10))
        kwargs: dict[str, Any] = {
            "cwd": str(self.project_root),
            "env": child_env,
            "stdin": subprocess.DEVNULL,
            "stdout": self.log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        try:
            self.process = self._popen(self.command, **kwargs)
        except Exception as exc:
            self._close_log()
            raise NativeRuntimeError(f"runtime spawn failed: {type(exc).__name__}: {exc}") from exc

        while time.monotonic() < deadline:
            returncode = self.process.poll()
            if returncode is not None:
                self.shutdown()
                raise NativeRuntimeError(f"runtime child exited before readiness (returncode={returncode})")
            payload = self._health()
            if payload is not None:
                if not self._identity_matches(payload):
                    actual = self._identity(payload).get("commit_full") or self._identity(payload).get("commit") or "unknown"
                    self.shutdown()
                    raise NativeRuntimeError(
                        f"spawned runtime identity mismatch: expected {self.expected_sha}, served {actual}"
                    )
                self.ready_identity = self._identity(payload)
                return dict(self.ready_identity)
            self._sleep(self.poll_interval)
        self.shutdown()
        raise NativeRuntimeError(f"runtime readiness timed out after {self.startup_timeout:.1f}s")

    def assert_alive(self) -> None:
        """Raise when an owned child has died or the serving identity changed."""
        if self.process is not None and self.process.poll() is not None:
            raise NativeRuntimeError(f"owned runtime child exited (returncode={self.process.returncode})")
        payload = self._health()
        if payload is None:
            raise NativeRuntimeError("runtime health endpoint is unavailable")
        if not self._identity_matches(payload):
            raise NativeRuntimeError("runtime identity changed while the native app was open")

    def _close_log(self) -> None:
        handle, self.log_handle = self.log_handle, None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()

    def shutdown(self) -> None:
        """Tear down only the process group this native host actually created."""
        child, self.process = self.process, None
        if child is not None and child.poll() is None:
            with contextlib.suppress(Exception):
                if os.name == "posix":
                    os.killpg(os.getpgid(child.pid), signal.SIGTERM)
                else:
                    child.terminate()
            try:
                child.wait(timeout=8)
            except Exception:
                with contextlib.suppress(Exception):
                    if os.name == "posix":
                        os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                    else:
                        child.kill()
                with contextlib.suppress(Exception):
                    child.wait(timeout=3)
        self._close_log()

    close = shutdown
