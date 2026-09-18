"""An isolated served daemon with a SCRIPTED provider behind it.

Why a stub endpoint and not a real model: model execution on this machine is cloud-only (three
OOM freezes, 2026-08-28), and a served proof does not need real weights -- it needs a real socket,
a real daemon process, the real HTTP door and a real provider call over the wire. The stub speaks
the Ollama dialect the runtime already targets, so everything between the request and the response
is production code.

Ports are chosen from the ephemeral range and never 11435/11436/11440, which are the operator's.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ScriptedProvider:
    """An Ollama-dialect endpoint that answers from a table and COUNTS what it was asked.

    The count is the load-bearing part: "the prohibited call was never made" is only a claim
    until something on the other end of the socket can say it never arrived.
    """

    def __init__(
        self,
        table: dict[str, Any],
        default: Any = "",
        *,
        after_tool_result: dict[str, Any] | None = None,
        ambiguity_verdict: dict[str, Any] | None = None,
    ) -> None:
        #: `table` maps a model id to what it replies FIRST. A dict value is emitted as a native
        #: tool call; a string is prose. `after_tool_result` is what a model replies once the
        #: conversation already carries a tool result -- which is how a two-round tool turn is
        #: scripted without the stub needing to know anything about the runtime.
        self.table = dict(table)
        self.default = default
        self.after_tool_result = dict(after_tool_result or {})
        self.ambiguity_verdict = ambiguity_verdict
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/api/version"):
                    return self._send({"version": "0.0.0-stub"})
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": name} for name in rig.table]})
                if self.path.startswith("/api/ps"):
                    return self._send({"models": []})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                model = str(body.get("model") or "")
                messages = body.get("messages") or []
                prompt = " ".join(
                    str(m.get("content") or "") for m in messages if isinstance(m, dict)
                )
                tools = body.get("tools") or []
                has_tool_result = any(
                    str(m.get("role") or "") == "tool" for m in messages if isinstance(m, dict)
                )
                with rig._lock:
                    rig.calls.append(
                        {
                            "model": model,
                            "prompt": prompt[:400],
                            "path": self.path,
                            "tools": [
                                str(((t or {}).get("function") or {}).get("name") or "")
                                for t in tools
                                if isinstance(t, dict)
                            ],
                            "has_tool_result": has_tool_result,
                        }
                    )
                reply = rig.reply_for(model, has_tool_result=has_tool_result)
                if rig.ambiguity_verdict is not None:
                    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
                    if AMBIGUITY_SYSTEM_PROMPT in prompt:
                        reply = json.dumps(rig.ambiguity_verdict)
                if isinstance(reply, dict):
                    # A scripted TOOL CALL, in the dialect the runtime parses.
                    message = {"role": "assistant", "content": "", "tool_calls": [reply]}
                    if self.path.startswith("/v1/"):
                        return self._send(
                            {
                                "model": model,
                                "choices": [
                                    {"index": 0, "finish_reason": "tool_calls", "message": message}
                                ],
                            }
                        )
                    return self._send(
                        {"model": model, "done": True, "done_reason": "stop", "message": message}
                    )
                text = str(reply)
                if self.path.startswith("/v1/"):
                    return self._send(
                        {
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": text},
                                }
                            ],
                            "usage": {"prompt_tokens": 40, "completion_tokens": 30},
                        }
                    )
                return self._send(
                    {
                        "model": model,
                        "done": True,
                        "done_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                        "prompt_eval_count": 40,
                        "eval_count": 30,
                    }
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def reply_for(self, model: str, *, has_tool_result: bool) -> Any:
        if has_tool_result and model in self.after_tool_result:
            return self.after_tool_result[model]
        return self.table.get(model, self.default)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def calls_for(self, model: str) -> int:
        with self._lock:
            return sum(1 for call in self.calls if call["model"] == model)

    def generations_for(self, model: str) -> int:
        """Calls that carried a prompt. A health probe posts an empty conversation and is not a
        generation -- counting it would make "the prohibited call was never made" unfalsifiable."""

        with self._lock:
            return sum(
                1
                for call in self.calls
                if call["model"] == model and str(call["prompt"] or "").strip()
            )

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def __enter__(self) -> ScriptedProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


class ServedDaemon:
    """`apps.vool_api_server` in its own process, its own VOOL_HOME and its own port."""

    def __init__(self, home: Path, *, env_extra: dict[str, str] | None = None) -> None:
        self.home = home
        self.port = free_port()
        self.env_extra = dict(env_extra or {})
        self.process: subprocess.Popen | None = None
        self.log_path = home / "daemon.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "VOOL_HOME": str(self.home),
                "PYTHONPATH": str(REPO_ROOT),
                "VOOL_DISABLE_MESH_DAEMON": "1",
                "VOOL_DISABLE_COMPUTE_MODE": "1",
                "VOOL_DISABLE_STUN": "1",
                "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "authorship-served-rig",
                "VOOL_SKIP_PROVIDER_PREWARM": "1",
                "VOOL_INSTALL_PROFILE": "local-only",
                "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
                "VOOL_LOCAL_MODELS_ENABLED": "1",
                "VOOL_DAEMON_BIND_PORT": str(free_port()),
            }
        )
        env.update(self.env_extra)
        env.pop("PYTEST_CURRENT_TEST", None)
        return env

    def start(self, *, timeout: float = 180.0) -> ServedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=self._env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"daemon exited {self.process.returncode}\n{self.log_tail()}"
                )
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except (URLError, HTTPError, OSError):
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text("utf-8", "replace").splitlines()[-lines:])
        except Exception:
            return "<no daemon log>"

    def chat(self, message: str, *, session_id: str, model: str = "", timeout: float = 180.0) -> dict:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "session_id": session_id,
        }
        if model:
            payload["model"] = model
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=20)

    def __enter__(self) -> ServedDaemon:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


def seed_in_home(home: Path, script: str) -> str:
    """Run a snippet inside the daemon's own VOOL_HOME, so it writes the daemon's database."""

    env = dict(os.environ)
    env.update({"VOOL_HOME": str(home), "PYTHONPATH": str(REPO_ROOT)})
    env.pop("PYTEST_CURRENT_TEST", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"seed failed:\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout
