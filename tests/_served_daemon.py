"""The served daemon rig: the real ``apps.vool_api_server`` in its own process, home and port.

Remote-only boot with every local model endpoint pointed at a dead port, so nothing local runs and
nothing reaches a real provider unless a test points a lane at its own SYNTHETIC service. Shared by
the served chat tests (tests/test_conversation_intent_served.py and the suites built on it).
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path(__file__).with_name("_served_daemon_launcher.py")
DEAD = "http://127.0.0.1:9"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _session(seed: str) -> str:
    """The canonical chat id shape the composer mints; the chat door passes it through verbatim."""
    return "openclaw:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


class ServedDaemon:
    #: The model the chat door is asked for; a subclass names its own lane.
    LANE_ID = ""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.port = _free_port()
        self.log_path = home.parent / "daemon.log"
        self.process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Remote-only boot: a local-only profile refuses to start without a local backend, and no
        # local model may run on this machine, so every local endpoint points at a dead port.
        for key in ("VOOL_INSTALL_PROFILE", "VOOL_LOCAL_MODELS_ENABLED", "PYTEST_CURRENT_TEST"):
            env.pop(key, None)
        env.update(
            {
                "VOOL_HOME": str(self.home),
                "PYTHONPATH": str(REPO_ROOT),
                "VOOL_PLUGIN_LIFECYCLE_PATH": str(self.home / "data/plugin-lifecycle.json"),
                "VOOL_PLUGINS_DIR": str(self.home / "plugins"),
                "VOOL_DISABLE_MESH_DAEMON": "1",
                "VOOL_DISABLE_COMPUTE_MODE": "1",
                "VOOL_DISABLE_STUN": "1",
                "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "served-drive-synthetic",
                "VOOL_SKIP_PROVIDER_PREWARM": "1",
                "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
                "VOOL_DAEMON_BIND_PORT": str(_free_port()),
                "OLLAMA_HOST": DEAD,
                "VOOL_OLLAMA_URL": DEAD,
                "VOOL_RAW_OLLAMA_API_URL": DEAD,
                "VOOL_OLLAMA_CHAT_URL": f"{DEAD}/api/chat",
                "VOOL_OLLAMA_PS_URL": f"{DEAD}/api/ps",
                "VOOL_OLLAMA_TAGS_URL": f"{DEAD}/api/tags",
            }
        )
        return env

    def start(self, timeout: float = 240.0) -> ServedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(LAUNCHER), "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except (URLError, HTTPError, OSError):
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")

    def log_tail(self, lines: int = 80) -> str:
        try:
            return "\n".join(self.log_path.read_text("utf-8", "replace").splitlines()[-lines:])
        except OSError:
            return "<no daemon log>"

    def stop(self) -> None:
        if self.process is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.process.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                self.process.wait(timeout=15)
                break
            except subprocess.TimeoutExpired:
                continue
        self.process = None

    def call(self, method: str, path: str, payload: dict | None = None, *, timeout: float = 120.0) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                status, raw, kind = response.status, response.read(), response.headers.get("Content-Type", "")
        except HTTPError as exc:
            status, raw, kind = exc.code, exc.read(), exc.headers.get("Content-Type", "")
        if "json" in kind and "ndjson" not in kind:
            return status, json.loads(raw.decode("utf-8"))
        return status, raw.decode("utf-8", "replace")

    def chat(self, text: str, *, session_id: str, stream: bool = False) -> tuple[int, object]:
        payload = {"messages": [{"role": "user", "content": text}], "stream": stream, "session_id": session_id, "model": self.LANE_ID, "mode": "auto"}
        return self.call("POST", "/api/chat", payload, timeout=300.0)

    def events(self, session_id: str) -> list[dict]:
        _status, payload = self.call("GET", f"/api/runtime/events?session={session_id}&limit=500")
        return list((payload or {}).get("events") or []) if isinstance(payload, dict) else []


def _completed_receipts(events: list[dict]) -> list[dict]:
    receipts = []
    for event in events:
        details = event.get("details") if isinstance(event.get("details"), dict) else event
        if str(event.get("event_type") or "") == "model.call_completed" and isinstance(details.get("provider_receipt"), dict):
            receipts.append(details["provider_receipt"])
    return receipts


def _answer_text(answer: object) -> str:
    """The assistant text of a buffered reply, or of a streamed one reassembled from its frames."""
    if isinstance(answer, dict):
        message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
        return str(message.get("content") or "")
    parts = []
    for line in str(answer or "").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        message = frame.get("message") if isinstance(frame.get("message"), dict) else {}
        if isinstance(message.get("content"), str) and not frame.get("done"):
            parts.append(message["content"])
    return "".join(parts)


def _rejections(events: list[dict]) -> list[str]:
    return [str(event.get("rejection_reason") or "") for event in events if event.get("event_type") == "model_routing_failed"]
