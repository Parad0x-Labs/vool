"""Provider-bound served-turn rig for the native skill library.

NOT a test module. This builds the real thing end to end:

- a **scripted loopback provider** (an OpenAI-compatible HTTP server) that records every
  request body it is sent — the actual provider-bound payload — and answers deterministically:
  the runtime's native-tool-support probe, the sealed authorship-certification probe, and
  multi-step tool-call turns driven by a per-test script;
- a **real daemon** (``apps/vool_api_server.py``) booted as a subprocess against an isolated
  VOOL_HOME with ``VOOL_CUSTOM_BASE_URL`` pointed at the provider, ``VOOL_DEBUG_PROMPT=1``
  armed, and nothing else — the same server every served turn runs;

Everything here is loopback and scratch-rooted. No test residue reaches the operator's live
plugins tree, keychain, or user configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = sys.executable
PROVIDER_MODEL = "probe-model"
PROVIDER_MANIFEST_ID = f"custom-byok:{PROVIDER_MODEL}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# The scripted provider
# ---------------------------------------------------------------------------


class ProviderState:
    """Every request body the runtime bound for the wire, in order."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.script: list[dict[str, Any]] = []

    def record(self, body: dict[str, Any]) -> int:
        with self.lock:
            self.requests.append(body)
            return len(self.requests) - 1

    @property
    def turn_requests(self) -> list[dict[str, Any]]:
        """Requests that carried a system prompt and a tool catalog (real turns)."""
        return [
            body
            for body in self.requests
            if any(m.get("role") == "system" for m in (body.get("messages") or []))
            and body.get("max_tokens") != 1
        ]

    def system_prompts(self) -> list[str]:
        prompts = []
        for body in self.turn_requests:
            for message in body.get("messages") or []:
                if message.get("role") == "system":
                    prompts.append(str(message.get("content") or ""))
        return prompts


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _completion(*, content: str | None = None, tool_calls: list[dict[str, Any]] | None = None,
                finish: str = "stop", model: str = PROVIDER_MODEL) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-rig",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _tool_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in (body.get("messages") or []) if m.get("role") == "tool"]


def _certification_response(body: dict[str, Any]) -> dict[str, Any]:
    """Deterministic answers to the sealed authorship-certification probe stages."""
    user_text = " ".join(
        str(m.get("content") or "") for m in (body.get("messages") or []) if m.get("role") == "user"
    )
    tool_msgs = _tool_messages(body)
    assistant_calls = [
        m for m in (body.get("messages") or [])
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]

    if "Call both tools" in user_text and not tool_msgs:
        return _completion(tool_calls=[
            _tool_call("cert-1", "vool_probe_add", {"left": 19, "right": 23}),
            _tool_call("cert-2", "vool_probe_lookup_nonce", {"key": "alpha"}),
        ], finish="tool_calls")
    if tool_msgs and "Call both tools" in user_text:
        # Continuation: the sealed tool results came back; echo the sum and the nonce verbatim.
        results_text = " ".join(str(m.get("content") or "") for m in tool_msgs)
        nonce = ""
        for token in results_text.replace('"', " ").replace(",", " ").replace(":", " ").split():
            if len(token) == 12 and all(ch in "0123456789abcdef" for ch in token):
                nonce = token
        return _completion(content=f"The sum is 42 and the sealed nonce is {nonce}.")
    if "probe.echo" in user_text and not tool_msgs:
        return _completion(tool_calls=[
            _tool_call("cert-echo", "vool_probe_echo", {"token": "repair-me"}),
        ], finish="tool_calls")
    # Recovery retry: the tool result names the required token.
    results_text = " ".join(str(m.get("content") or "") for m in tool_msgs)
    if "recovered" in results_text:
        return _completion(tool_calls=[
            _tool_call("cert-echo-2", "vool_probe_echo", {"token": "recovered"}),
        ], finish="tool_calls")
    return _completion(tool_calls=[
        _tool_call("cert-echo", "vool_probe_echo", {"token": "repair-me"}),
    ], finish="tool_calls")


def _turn_response(body: dict[str, Any], state: ProviderState, model: str) -> dict[str, Any]:
    """Walk the per-test script: one step per tool result that has come back so far.

    Turn responses use the PROMPTED dialect — the intent JSON rides in the message content —
    because the served loop's action_plan path runs without native tool schemas. (The
    certification probe above is the only place native tool_calls are spoken, because that
    sealed exchange demands and parses them itself.)

    A step may carry ``prompt_contains``: a substring that must appear in the bound messages
    for that step to serve. A guarded script picks the LAST step whose marker matches (an
    unguarded step matches everything); scripts without any guard walk exactly as before.
    """
    done = len(_tool_messages(body))
    guarded = any("prompt_contains" in step for step in state.script)
    if guarded:
        blob = " ".join(
            str(m.get("content") or "") for m in (body.get("messages") or [])
        )
        step = None
        for candidate in state.script:
            marker = str(candidate.get("prompt_contains") or "")
            if not marker or marker in blob:
                step = candidate
        if step is None:
            step = {"final": "Done."}
        if "final" in step:
            return _completion(content=str(step["final"]))
    else:
        step_index = min(done, max(0, len(state.script) - 1))
        step = state.script[step_index] if state.script else {"final": "Done."}
        if "final" in step and done >= len(state.script) - 1:
            return _completion(content=str(step["final"]))
    if "tool" in step:
        payload = {"intent": str(step["tool"]), "arguments": dict(step.get("arguments") or {})}
        return _completion(content=json.dumps(payload))
    return _completion(content=str(step.get("final") or "Done."))


def make_provider_server(state: ProviderState) -> tuple[ThreadingHTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence
            return

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/models"):
                self._json({"object": "list", "data": [{"id": PROVIDER_MODEL, "object": "model"}]})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except Exception:
                self._json({"error": "bad json"}, 400)
                return
            index = state.record(body)

            # The native-tool-support probe: max_tokens==1 with a trivial tool.
            if body.get("max_tokens") == 1:
                self._json(_completion(content=""))
                return

            messages = body.get("messages") or []
            if any("sealed local diagnostic" in str(m.get("content") or "") for m in messages):
                self._json(_certification_response(body))
                return

            self._json(_turn_response(body, state, str(body.get("model") or PROVIDER_MODEL)))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, int(server.server_address[1])


# ---------------------------------------------------------------------------
# The real daemon
# ---------------------------------------------------------------------------


class ServedDaemon:
    """apps/vool_api_server.py as a subprocess, isolated home, provider pointed at the rig."""

    def __init__(self, home: Path, provider_port: int, *, extra_env: dict[str, str] | None = None) -> None:
        self.home = home
        self.port = _free_port()
        self.provider_port = provider_port
        self.proc: subprocess.Popen[bytes] | None = None
        self.extra_env = dict(extra_env or {})

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, *, wait: bool = True) -> "ServedDaemon":
        env = dict(os.environ)
        env.update(
            {
                "VOOL_HOME": str(self.home),
                "VOOL_CUSTOM_BASE_URL": f"http://127.0.0.1:{self.provider_port}",
                "VOOL_CUSTOM_API_KEY": "rig-local-key-never-a-real-credential",
                "VOOL_DEBUG_PROMPT": "1",
                "VOOL_LOCAL_MODELS_ENABLED": "1",
                "VOOL_PLUGINS_DIR": str(self.home / "empty-plugins"),
                # A second daemon must not fight the first for the mesh UDP port: two
                # rigs sharing 49152 killed the earlier daemon mid-pack (measured).
                "VOOL_DAEMON_BIND_PORT": str(_free_port()),
                "VOOL_DAEMON_HEALTH_PORT": "0",
            }
        )
        (self.home / "empty-plugins").mkdir(parents=True, exist_ok=True)
        env.update(self.extra_env)
        for key in ("VOOL_MCP_CONFIG", "VOOL_NATIVE_SKILLS_DIR"):
            env.pop(key, None)  # the daemon must load the REPO's shipped library
        log = open(self.home / "daemon.log", "ab")  # noqa: SIM115
        self.proc = subprocess.Popen(
            [VENV_PYTHON, str(REPO_ROOT / "apps" / "vool_api_server.py"),
             "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT), env=env, stdout=log, stderr=log,
            start_new_session=True,
        )
        if wait:
            self.wait_ready()
        return self

    def wait_ready(self, timeout: float = 90.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/api/skills", timeout=3) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError(f"daemon on :{self.port} never became ready; see {self.home}/daemon.log")

    def stop(self) -> None:
        if self.proc is None:
            return
        import time as _time

        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            for _ in range(50):
                if self.proc.poll() is not None:
                    break
                _time.sleep(0.2)
            if self.proc.poll() is not None:
                break
        self.proc = None
        # Zero-leak sweep: the boot pidfile and any stragglers on OUR port are this rig's mess.
        pidfile = self.home / ".vool_api_server.pid"
        for candidate in (self.home / ".vool_api_server.pid", self.home / "daemon.pid"):
            with open(os.devnull) as _:
                pass
            if candidate.is_file():
                candidate.unlink()

    # -- HTTP helpers -------------------------------------------------------

    def get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=30) as response:
            return json.loads(response.read())

    def post(self, path: str, payload: dict[str, Any], timeout: float = 420.0) -> dict[str, Any]:
        import urllib.error

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # The turn admission lock serializes concurrent served turns by design (409): a
            # queued caller retries instead of failing the proof.
            if path.endswith("/chat") or "chat" in path:
                for _ in range(30):
                    time.sleep(2.0)
                    try:
                        request = urllib.request.Request(
                            f"{self.base_url}{path}",
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"content-type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(request, timeout=timeout) as response:
                            return json.loads(response.read())
                    except urllib.error.HTTPError as retry_exc:
                        if retry_exc.code != 409:
                            raise
                        continue
            raise

    # -- rig-specific operations ---------------------------------------------

    def pin_provider_model(self) -> dict[str, Any]:
        return self.post(
            "/api/cloud/model",
            {"model": PROVIDER_MODEL, "provider": "custom", "confirm_paid": True},
        )

    def certify_provider_model(self) -> dict[str, Any]:
        return self.post(
            "/api/model-tool-certification/run",
            {"provider_name": "custom-byok", "model_name": PROVIDER_MODEL},
            timeout=420.0,
        )

    def chat(self, text: str, *, model: str = PROVIDER_MANIFEST_ID, mode: str = "manual",
             workspace: str | None = None, session: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "mode": mode,
        }
        if workspace:
            payload["workspace"] = workspace
        if session:
            payload["session_id"] = session
        return self.post("/v1/chat/completions", payload)

    def session_events(self, session_id: str) -> list[dict[str, Any]]:
        events = self.get(f"/api/runtime/events?session={session_id}")
        return list(events.get("events") or [])

    def skill_event(self, session_id: str) -> dict[str, Any] | None:
        for event in self.session_events(session_id):
            if event.get("event_type") == "tool_offer_skills":
                return event
        return None

    def executed_tools(self, session_id: str) -> list[str]:
        names = []
        for event in self.session_events(session_id):
            kind = str(event.get("event_type") or "")
            if kind in ("tool_executed", "tool_result", "tool_selected"):
                names.append(str(event.get("intent") or event.get("tool") or kind))
        return names

    def prompt_debug_payloads(self) -> list[dict[str, Any]]:
        path = self.home / "logs" / "prompt_debug.jsonl"
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows


def workspace_events_include(events: list[dict[str, Any]], *types: str) -> bool:
    wanted = set(types)
    return any(str(e.get("event_type") or "") in wanted for e in events)


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
