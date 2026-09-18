"""Client for the isolated nebula-media worker subprocess.

Heavy ffmpeg/transcode work NEVER runs inside the VOOL UI/server event loop:
this client speaks line-delimited JSON to ``nebula-media-worker`` (from the
nebula-media repo) as a child process. A malformed 8K file or a codec failure
comes back as a structured error — it cannot take VOOL down. A dead worker is
restarted on demand; an unconfigured/missing nebula-media is reported
truthfully as unavailable rather than faked.

Configuration (env):
    NEBULA_MEDIA_HOME   path to a nebula-media checkout/install (added to the
                        child's PYTHONPATH). Optional if already importable.
    NEBULA_WORKER_TIMEOUT  per-request timeout seconds (default 900).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path


class MediaWorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class MediaWorkerUnavailable(MediaWorkerError):
    def __init__(self, reason: str):
        super().__init__("worker_unavailable", reason)


class NebulaWorkerClient:
    """One long-lived worker process, guarded by a lock, restart-on-crash."""

    protocol_version = "media-worker/1"

    def __init__(self, *, python: str | None = None,
                 nebula_home: str | None = None, timeout: float | None = None):
        self._python = python or sys.executable
        self._nebula_home = nebula_home or os.environ.get("NEBULA_MEDIA_HOME", "")
        self._timeout = float(timeout or os.environ.get("NEBULA_WORKER_TIMEOUT", "900"))
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- state
    def availability(self) -> dict:
        """Truthful availability probe: never claims ready without a live round-trip."""
        try:
            result = self.request("probe", {"src": __file__}, timeout=10)
        except MediaWorkerUnavailable:
            return {"available": False, "reason": "nebula-media worker could not be started"}
        except MediaWorkerError as exc:
            # probe of a non-media file must fail INSIDE the worker with a typed error;
            # that proves the worker is alive.
            if exc.code in {"invalid_media", "io_error", "op_failed"}:
                return {"available": True, "protocol": self.protocol_version}
            return {"available": False, "reason": f"worker unhealthy: {exc.code}"}
        _ = result
        return {"available": True, "protocol": self.protocol_version}

    # ---------------------------------------------------------------- calls
    def request(self, op: str, params: dict, *, timeout: float | None = None) -> dict | list[dict]:
        with self._lock:
            proc = self._ensure_proc()
            assert proc is not None and proc.stdin and proc.stdout
            rid = uuid.uuid4().hex
            line = json.dumps({"id": rid, "op": op, "params": params})
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError) as exc:
                self._kill()
                raise MediaWorkerUnavailable(f"worker pipe broke: {exc}") from exc

            timeout_s = float(timeout or self._timeout)

            def _readline() -> str:
                reader = proc.stdout  # type: ignore[union-attr]
                result: list[str] = []
                t = threading.Thread(target=lambda: result.append(reader.readline()), daemon=True)
                t.start()
                t.join(timeout_s)
                if not result or not result[0]:
                    if proc.poll() is not None:
                        self._kill()
                        raise MediaWorkerUnavailable(
                            f"worker exited during '{op}' (code {proc.returncode}); "
                            "is nebula-media importable? set NEBULA_MEDIA_HOME"
                        )
                    raise MediaWorkerError("timeout",
                                           f"worker '{op}' timed out after {timeout_s:.0f}s")
                return result[0]

            try:
                raw = _readline().strip()
                resp = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                self._kill()
                raise MediaWorkerError("internal", f"unparseable worker response: {exc}") from exc
            if not isinstance(resp, dict) or not resp.get("ok"):
                err = (resp or {}).get("error") or {}
                raise MediaWorkerError(str(err.get("code", "internal")),
                                       str(err.get("message", "worker error")))
            return resp.get("result")

    def close(self) -> None:
        with self._lock:
            self._kill()

    # ---------------------------------------------------------------- internals
    def _spawn(self) -> subprocess.Popen:
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        if self._nebula_home:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (self._nebula_home + os.pathsep + existing).rstrip(os.pathsep)
        try:
            return subprocess.Popen(
                [self._python, "-m", "nebula.worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, env=env,
                start_new_session=True,   # own process group so a crash-kill
                                          # also reaps any running ffmpeg child
            )
        except OSError as exc:
            raise MediaWorkerUnavailable(f"could not spawn media worker: {exc}") from exc

    def _ensure_proc(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._kill()
        self._proc = self._spawn()
        return self._proc

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.poll() is None:
                    # kill the whole group: any in-flight ffmpeg child dies with
                    # the worker, so a crashed operation cannot keep writing.
                    import signal

                    try:
                        os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass
        self._proc = None


_default_client: NebulaWorkerClient | None = None


def get_media_worker() -> NebulaWorkerClient:
    global _default_client
    if _default_client is None:
        home = os.environ.get("NEBULA_MEDIA_HOME", "")
        resolved = str(Path(home).expanduser()) if home else ""
        _default_client = NebulaWorkerClient(nebula_home=resolved)
    return _default_client


def reset_media_worker() -> None:
    """Test hook."""
    global _default_client
    if _default_client is not None:
        _default_client.close()
    _default_client = None
