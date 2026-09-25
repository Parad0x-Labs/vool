"""Windows bundle supervisor for the Ollama and VOOL API child processes."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_POLL_SECONDS = 2.0
_STARTUP_TIMEOUT_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 30.0


def _is_windows_platform() -> bool:
    """Indirection over ``os.name == "nt"`` so tests can simulate "running on Windows" for one
    call without mutating the real ``os.name`` -- ``pathlib.Path`` itself branches on ``os.name``
    to choose ``WindowsPath``/``PosixPath``, so patching the global attribute directly breaks
    every OTHER ``Path(...)`` construction for the duration of the patch, including ones made
    deep inside pytest's own failure reporting (measured: full run 36063857499 shard 5 died with
    ``cannot instantiate 'WindowsPath'`` inside ``_pytest``'s traceback formatter while rendering
    an assertion failure, because a test had flipped the global)."""
    return os.name == "nt"


class BundleSupervisor:
    def __init__(self, root: Path, *, env: dict[str, str] | None = None) -> None:
        self.root = root.resolve()
        self.env = dict(os.environ if env is None else env)
        local_app_data = Path(self.env.get("LOCALAPPDATA") or Path.home())
        self.home = Path(self.env.get("VOOL_HOME") or local_app_data / "VOOL").expanduser()
        self.run_dir = self.home / "run"
        self.log_dir = self.home / "logs"
        self.state_path = self.run_dir / "bundle-supervisor.json"
        self.lock_path = self.run_dir / "bundle-supervisor.lock"
        self.stop_path = self.run_dir / "stop"
        self.model_status_path = self.run_dir / "model-pull.json"
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        self.handles: dict[str, Any] = {}
        self.started_at: dict[str, float] = {}
        self.failures: dict[str, int] = {"ollama": 0, "api": 0, "window": 0}
        self.restart_counts: dict[str, int] = {"ollama": 0, "api": 0, "window": 0}
        self.next_attempt: dict[str, float] = {"ollama": 0.0, "api": 0.0, "window": 0.0}
        self.last_exit: dict[str, dict[str, Any]] = {}
        self.health: dict[str, dict[str, Any]] = {}
        self.supervisor_started_at = time.time()
        self._lock_handle: Any | None = None
        self.window_started = False

    def _manifest(self) -> dict[str, Any]:
        path = self.root / "bundle_manifest.json"
        try:
            # Windows PowerShell 5 emits UTF-8 JSON with a BOM by default.
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _model(self) -> str:
        payload = self._manifest()
        return str(payload.get("selected_model") or payload.get("model") or "").strip()

    def _model_store(self) -> Path:
        payload = self._manifest()
        raw = str(payload.get("model_store") or "").strip().replace("\\", "/")
        while "//" in raw:
            raw = raw.replace("//", "/")
        runtime = self.home
        if raw:
            local_prefix = "%LOCALAPPDATA%/VOOL"
            home_prefix = "%VOOL_HOME%"
            lowered = raw.lower()
            if lowered == local_prefix.lower() or lowered.startswith(local_prefix.lower() + "/"):
                suffix = raw[len(local_prefix) :].lstrip("/")
                return runtime / suffix if suffix else runtime
            if lowered == home_prefix.lower() or lowered.startswith(home_prefix.lower() + "/"):
                suffix = raw[len(home_prefix) :].lstrip("/")
                return runtime / suffix if suffix else runtime
        return runtime / "models"

    def _prepare(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.env["VOOL_BUNDLE_ROOT"] = str(self.root)
        self.env["VOOL_BUNDLE_MANIFEST"] = str(self.root / "bundle_manifest.json")
        self.env["VOOL_HOME"] = str(self.home)
        model_store = self._model_store()
        self.env["OLLAMA_MODELS"] = str(model_store)
        model = self._model()
        if model:
            self.env["VOOL_BUNDLE_MODEL"] = model
            self.env["VOOL_OLLAMA_MODEL"] = model
        model_store.mkdir(parents=True, exist_ok=True)
        self._write_model_status("delegated_to_api", "The API owns the non-blocking model pull.")

    def _write_model_status(self, status: str, detail: str = "") -> None:
        payload = {
            "schema": "vool.bundle_model_pull.v1",
            "model": self._model(),
            "status": str(status),
            "detail": str(detail)[:240],
            "updated_at_epoch": time.time(),
        }
        with contextlib.suppress(OSError):
            self.model_status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _log(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n"
        try:
            with (self.log_dir / "bundle-supervisor.log").open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    def _acquire_lease(self) -> bool:
        """Allow one supervisor per bundle home, including after an abrupt exit."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if _is_windows_platform():
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n".encode("ascii", errors="replace"))
            handle.flush()
        except (ImportError, OSError):
            handle.close()
            return False
        self._lock_handle = handle
        return True

    def _release_lease(self) -> None:
        handle = self._lock_handle
        self._lock_handle = None
        if handle is None:
            return
        with contextlib.suppress(ImportError, OSError):
            handle.seek(0)
            if _is_windows_platform():
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            handle.close()

    def _healthy(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                return 200 <= int(response.status) < 400
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def _health_probe(self, name: str, url: str) -> bool:
        healthy = self._healthy(url)
        previous = self.health.get(name, {}).get("healthy")
        self.health[name] = {
            "url": url,
            "healthy": healthy,
            "checked_at": time.time(),
        }
        if previous is None or previous != healthy:
            self._log(f"health {name}={'healthy' if healthy else 'unhealthy'} url={url}")
        return healthy

    def _child_env(self) -> dict[str, str]:
        env = dict(self.env)
        app = str(self.root / "app")
        env["PYTHONPATH"] = app + os.pathsep + str(env.get("PYTHONPATH") or "")
        return env

    def _start(self, name: str, argv: list[str], *, cwd: Path) -> None:
        now = time.time()
        if now < self.next_attempt[name]:
            return
        log_path = self.log_dir / f"{name}.log"
        handle = log_path.open("ab")
        creationflags = 0
        if _is_windows_platform():
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        try:
            child = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=self._child_env(),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception as exc:
            handle.close()
            self._schedule_retry(name, f"failed to start: {type(exc).__name__}: {exc}")
            return
        self.processes[name] = child
        self.handles[name] = handle
        self.started_at[name] = now
        self.next_attempt[name] = now
        self.restart_counts[name] += 1
        self._write_state()
        self._log(f"started {name} pid={child.pid}")

    def _schedule_retry(self, name: str, reason: str) -> None:
        self.failures[name] += 1
        delay = min(_MAX_BACKOFF_SECONDS, 2 ** min(self.failures[name], 5))
        self.next_attempt[name] = time.time() + delay
        self.last_exit[name] = {
            "reason": str(reason)[:240],
            "at": time.time(),
            "retry_in": delay,
        }
        self._log(f"{name} retry scheduled reason={reason}; retry_in={delay}s")
        self._write_state()

    def _stop_child(self, name: str) -> None:
        child = self.processes.pop(name, None)
        handle = self.handles.pop(name, None)
        if child is not None and child.poll() is None:
            try:
                if _is_windows_platform():
                    completed = subprocess.run(
                        ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=8,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if completed.returncode != 0:
                        self._log(
                            f"{name} process-tree stop returned {completed.returncode}; using direct fallback"
                        )
                else:
                    child.terminate()
                child.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    child.kill()
                    child.wait(timeout=5)
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        self.started_at.pop(name, None)

    def _reap_dead(self, name: str) -> None:
        child = self.processes.get(name)
        if child is None or child.poll() is None:
            return
        returncode = child.poll()
        self._stop_child(name)
        self._schedule_retry(name, f"exited returncode={returncode}")

    def _ensure_ollama(self) -> None:
        if self._health_probe("ollama", "http://127.0.0.1:11434/api/tags"):
            return
        child = self.processes.get("ollama")
        if child is not None and child.poll() is None:
            if time.time() - self.started_at.get("ollama", time.time()) <= _STARTUP_TIMEOUT_SECONDS:
                return
            self._stop_child("ollama")
            self._schedule_retry("ollama", "startup timeout")
            return
        self._start("ollama", [str(self.root / "ollama" / "ollama.exe"), "serve"], cwd=self.root)

    def _ensure_api(self) -> None:
        if self._health_probe("api", "http://127.0.0.1:11435/healthz"):
            return
        child = self.processes.get("api")
        if child is not None and child.poll() is None:
            if time.time() - self.started_at.get("api", time.time()) <= _STARTUP_TIMEOUT_SECONDS:
                return
            self._stop_child("api")
            self._schedule_retry("api", "startup timeout")
            return
        self._clear_stale_api_processes()
        self._start(
            "api",
            [str(self.root / "python" / "python.exe"), str(self.root / "app" / "apps" / "vool_api_server.py"), "--port", "11435"],
            cwd=self.root / "app",
        )

    def _clear_stale_api_processes(self) -> None:
        """Stop only stale VOOL API Python processes before a bundle API start on Windows."""
        if not _is_windows_platform():
            return
        command = (
            "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') "
            "-and (($_.CommandLine -like '*apps.vool_api_server*') "
            "-or ($_.CommandLine -like '*vool_api_server.py*')) } | "
            "ForEach-Object { "
            "try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} "
            "}"
        )
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            self._log(f"stale api cleanup unavailable: {type(exc).__name__}: {exc}")

    def _ensure_window(self) -> None:
        """Open the native UI once the API is reachable; closing the UI is user intent."""
        if self.window_started or not self._healthy("http://127.0.0.1:11435/healthz"):
            return
        self._start(
            "window",
            [str(self.root / "python" / "pythonw.exe"), str(self.root / "vool_window.py")],
            cwd=self.root,
        )
        self.window_started = "window" in self.processes

    def _write_state(self) -> None:
        payload = {
            "schema": "vool.bundle_supervisor.v1",
            "root": str(self.root),
            "supervisor_pid": os.getpid(),
            "started_at": self.supervisor_started_at,
            "updated_at": time.time(),
            "pids": {name: child.pid for name, child in self.processes.items() if child.poll() is None},
            "model": self._model(),
            "health": self.health,
            "failures": self.failures,
            "restart_counts": self.restart_counts,
            "next_attempt": self.next_attempt,
            "last_exit": self.last_exit,
        }
        with contextlib.suppress(OSError):
            self.state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def run(self, *, once: bool = False) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if not self._acquire_lease():
            self._log("supervisor already running; duplicate launch ignored")
            return 0
        self._prepare()
        self.stop_path.unlink(missing_ok=True)
        self._log("supervisor started")
        try:
            while not self.stop_path.exists():
                self._reap_dead("ollama")
                self._reap_dead("api")
                self._ensure_ollama()
                self._ensure_api()
                self._ensure_window()
                self._write_state()
                if once:
                    break
                time.sleep(_POLL_SECONDS)
        finally:
            self._stop_child("window")
            self._stop_child("api")
            self._stop_child("ollama")
            self._write_model_status("stopped", "Bundle supervisor stopped.")
            self.state_path.unlink(missing_ok=True)
            self._log("supervisor stopped")
            self._release_lease()
        return 0


def main() -> int:
    # C15: the ONE bounded unattended preflight — the .app supervisor is an unattended boot.
    from core.unattended_preflight import preflight

    preflight("installer.bundle.bundle_supervisor")
    parser = argparse.ArgumentParser(prog="vool-bundle-supervisor")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.stop:
        local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        home = Path(os.environ.get("VOOL_HOME") or local_app_data / "VOOL")
        run_dir = home / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "stop").write_text("stop\n", encoding="utf-8")
        return 0
    return BundleSupervisor(root).run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
