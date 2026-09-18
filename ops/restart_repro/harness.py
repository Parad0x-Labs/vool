"""Loopback model stub + cold-restart cycle runner for the restart-first-turn P0.

Shapes mirror the served-reality bench (ops/served_reality in the bench
worktree) so the reproduction is the SAME case: one stub speaking the
OpenAI and Ollama wire protocols, one daemon booted from this worktree
with an isolated home, deterministic scripted answers carrying markers
only the stub can produce.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

STUB_MODEL = "restart-repro-stub"
CLOUD_STUB_MODEL = "restart-repro/cloud-stub"

TURN_TIMEOUT_S = 60.0
BOOT_TIMEOUT_S = 60.0


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def stable_session_id(chat_id: str) -> str:
    """The product's deterministic session id for a chat_id (core/web/api/runtime.py)."""
    return f"openclaw:{hashlib.sha256(chat_id.encode('utf-8')).hexdigest()[:20]}"


class StubCall:
    def __init__(self, ts: float, path: str, status: int | None, body_last_user: str) -> None:
        self.ts = ts
        self.path = path
        self.status = status
        self.last_user = body_last_user[:200]

    def to_dict(self) -> dict[str, Any]:
        return {"ts": round(self.ts, 3), "path": self.path, "status": self.status, "last_user": self.last_user}


class _StubHandler(BaseHTTPRequestHandler):
    server_version = "restart-repro-stub/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _record(self, path: str, status: int | None, last_user: str = "") -> None:
        self.server.stub.calls.append(StubCall(time.time(), path, status, last_user))  # type: ignore[attr-defined]

    def do_CONNECT(self) -> None:  # noqa: N802
        payload = json.dumps({"error": "restart-repro: external network contained"}).encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self._record("CONNECT " + self.path, 502)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.endswith("/models"):
            payload = {"object": "list", "data": [{"id": STUB_MODEL, "object": "model"}]}
            status = 200
        elif path.endswith("/api/tags"):
            payload = {"models": [{"name": STUB_MODEL, "model": STUB_MODEL, "size": 1}]}
            status = 200
        elif path.endswith("/api/ps"):
            payload = {
                "models": [
                    {"name": STUB_MODEL, "model": STUB_MODEL, "size": 1, "size_vram": 1,
                     "digest": "sha256:" + "0" * 64}
                ]
            }
            status = 200
        elif path.endswith("/api/version"):
            payload = {"version": "restart-repro-stub"}
            status = 200
        else:
            payload = {"error": f"no GET route {path}"}
            status = 404
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._record(path, status)

    def do_POST(self) -> None:  # noqa: N802
        stub = self.server.stub  # type: ignore[attr-defined]
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        last_user = ""
        for message in reversed(list(body.get("messages") or [])):
            if isinstance(message, dict) and str(message.get("role") or "") == "user":
                last_user = str(message.get("content") or "")
                break
        if not last_user and isinstance(body.get("prompt"), str):
            last_user = body["prompt"]

        content = ""
        with stub.lock:
            for pattern, answer in list(stub.rules):
                if pattern is None or re.search(pattern, last_user or ""):
                    content = answer
                    break

        if "/chat/completions" in path:
            payload = {
                "id": f"stub-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": str(body.get("model") or STUB_MODEL),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        else:
            payload = {
                "model": str(body.get("model") or STUB_MODEL),
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
            }
        out = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        self._record(path, 200, last_user)


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # noqa: ARG002, D102
        return


class ProviderStub:
    """Scripted model provider: OpenAI + Ollama protocols.

    ``bind_host`` defaults to a NON-loopback LAN address when one exists: the
    authorship law (core/final_answer_authorship.py) treats OpenAI-compatible
    LOOPBACK endpoints as locally-certifiable, so a loopback stub makes every
    lane uncertifiable and no turn can ever author — a policy refusal, not the
    restart defect under test. On the LAN address the lanes keep the shape of
    a real remote provider (operator-attested), while traffic still never
    leaves this machine.
    """

    def __init__(self, bind_host: str | None = None) -> None:
        if bind_host is None:
            bind_host = _lan_bind_host()
        self.bind_host = bind_host
        self.rules: list[tuple[str | None, str]] = []
        self.lock = threading.Lock()
        self.calls: list[StubCall] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    def start(self) -> int:
        self._server = _QuietServer((self.bind_host, 0), _StubHandler)
        self._server.stub = self  # type: ignore[attr-defined]
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="repro-stub", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def set_rules(self, rules: list[tuple[str | None, str]]) -> None:
        with self.lock:
            self.rules = list(rules)

    def chat_calls(self) -> list[StubCall]:
        return [c for c in self.calls if "chat" in c.path and "CONNECT" not in c.path]

    def clear_model_calls(self) -> None:
        self.calls = [c for c in self.calls if not ("chat" in c.path and "CONNECT" not in c.path)]

    def dump(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.calls]


def _lan_bind_host() -> str:
    """A non-loopback address of THIS machine, so stub lanes stay contained.

    The daemon (also on this machine) reaches it directly; nothing external
    can. Falls back to loopback when no LAN address exists (and then the
    authorship law will refuse the lanes -- recorded honestly by the harness).
    """
    import subprocess

    for interface in ("en0", "en1"):
        try:
            out = subprocess.run(
                ["ipconfig", "getifaddr", interface], capture_output=True, text=True, timeout=2.0
            )
        except Exception:
            continue
        host = (out.stdout or "").strip()
        if host and not host.startswith("127."):
            return host
    return "127.0.0.1"


def http_json(method: str, url: str, body: Any = None, timeout_s: float = 10.0) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
            try:
                return int(response.status), json.loads(raw) if raw else None
            except ValueError:
                return int(response.status), {"_raw": raw.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return int(exc.code), json.loads(raw) if raw else None
        except ValueError:
            return int(exc.code), {"_raw": raw.decode("utf-8", errors="replace")}


class Daemon:
    def __init__(
        self,
        *,
        app_dir: Path,
        home: Path,
        workspace: Path,
        stub_port: int,
        run_dir: Path,
        trace: bool = False,
    ) -> None:
        self.app_dir = app_dir
        self.home = home
        self.workspace = workspace
        self.stub_port = stub_port
        self.stub_host = "127.0.0.1"
        self.run_dir = run_dir
        self.port = free_port()
        self.trace = trace
        self.process: subprocess.Popen | None = None
        self.stderr_path = run_dir / "daemon.stderr.log"
        self.stdout_path = run_dir / "daemon.stdout.log"

    def _env(self) -> dict[str, str]:
        stub_base = f"http://{self.stub_host}:{self.stub_port}"
        env = dict(os.environ)
        for key in ("OPENROUTER_API_KEY", "TETHER_API_KEY", "OPENAI_API_KEY", "VOOL_REMOTE_API_KEY"):
            env.pop(key, None)
        env.update(
            {
                "VOOL_HOME": str(self.home),
                "VOOL_WORKSPACE_ROOT": str(self.workspace),
                "VOOL_SKIP_PROVIDER_PREWARM": "1",
                "VOOL_CREDENTIAL_STORE": "vault",
                "VOOL_DISABLE_MESH_DAEMON": "1",
                "VOOL_PUBLIC_HIVE_ENABLED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "VLLM_BASE_URL": f"{stub_base}/v1",
                "VLLM_MODEL": STUB_MODEL,
                "VOOL_OLLAMA_CHAT_URL": f"{stub_base}/api/chat",
                "VOOL_OLLAMA_URL": stub_base,
                "VOOL_OLLAMA_PS_URL": f"{stub_base}/api/ps",
                "VOOL_RAW_OLLAMA_API_URL": stub_base,
                "VOOL_LOADED_OLLAMA_MODELS": STUB_MODEL,
                "OLLAMA_HOST": stub_base,
                "TETHER_API_KEY": "restart-repro-stub-key-not-a-real-credential",
                "TETHER_BASE_URL": f"{stub_base}/v1",
                "TETHER_MODEL": CLOUD_STUB_MODEL,
                "HTTP_PROXY": stub_base,
                "HTTPS_PROXY": stub_base,
                "ALL_PROXY": stub_base,
                "NO_PROXY": f"127.0.0.1,localhost,::1,{self.stub_host}",
                "no_proxy": f"127.0.0.1,localhost,::1,{self.stub_host}",
            }
        )
        return env

    def start(self, *, python_bin: str, boot_timeout_s: float = BOOT_TIMEOUT_S) -> dict[str, Any]:
        """Boot and gate on /healthz — the product's own readiness publication."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        entry = ["-m", "ops.restart_repro.trace_main"] if self.trace else ["-m", "apps.vool_api_server"]
        env = self._env()
        if self.trace:
            env["VOOL_RESTART_TRACE"] = "1"
        with self.stdout_path.open("ab") as out, self.stderr_path.open("ab") as err:
            self.process = subprocess.Popen(
                [python_bin, *entry, "--port", str(self.port), "--bind", "127.0.0.1"],
                cwd=str(self.app_dir),
                env=env,
                stdout=out,
                stderr=err,
                start_new_session=False,
            )
        deadline = time.monotonic() + boot_timeout_s
        runtime: dict[str, Any] = {}
        last_error = ""
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon died during boot (exit {self.process.returncode})")
            try:
                status, payload = http_json("GET", f"http://127.0.0.1:{self.port}/healthz", timeout_s=3.0)
                if status == 200 and isinstance(payload, dict):
                    runtime = dict(payload.get("runtime") or {})
                    return runtime
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.25)
        self.terminate()
        raise RuntimeError(f"daemon did not answer /healthz within {boot_timeout_s}s ({last_error})")

    def terminate(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                os.kill(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.kill(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None

    def chat(
        self, text: str, *, chat_id: str, turn_id: str, timeout_s: float = TURN_TIMEOUT_S
    ) -> dict[str, Any]:
        body = {
            "chat_id": chat_id,
            "messages": [{"role": "user", "content": text}],
            "turn_id": turn_id,
            "workspace": str(self.workspace),
        }
        status, payload = http_json("POST", f"http://127.0.0.1:{self.port}/api/chat", body, timeout_s=timeout_s)
        message = dict((payload or {}).get("message") or {})
        result = {
            "status": status,
            "content": str(message.get("content") or ""),
        }
        if status != 200:
            result["raw_payload"] = str(payload)[:500]
        return result

    def events(self, session_id: str) -> list[dict[str, Any]]:
        status, payload = http_json(
            "GET", f"http://127.0.0.1:{self.port}/api/runtime/events?session={session_id}", timeout_s=10.0
        )
        if status != 200 or not isinstance(payload, dict):
            return []
        return list(payload.get("events") or [])


ROUTING_EVENT_TYPES = {
    "model_routing_started",
    "model_routing_failed",
    "model_lane_selected",
    "model_lane_proof",
    "model.call_failed",
    "model.call_completed",
    "task_completed",
    "task_received",
}


def routing_timeline(events: list[dict[str, Any]], *, after_ts: float = 0.0) -> list[dict[str, Any]]:
    timeline = []
    for event in events:
        if str(event.get("event_type") or "") not in ROUTING_EVENT_TYPES:
            continue
        if float(event.get("created_ts") or event.get("ts") or 0.0) < after_ts:
            continue
        row = {
            "event_type": str(event.get("event_type") or ""),
            "message": str(event.get("message") or "")[:160],
            "ts": str(event.get("created_at") or ""),
        }
        for key in ("rejection_reason", "fallback_reason", "lane", "task_kind", "ranked_candidates"):
            if event.get(key) is not None:
                row[key] = event.get(key)
        timeline.append(row)
    return timeline
