"""An isolated served daemon with a SCRIPTED Ollama-dialect provider behind it -- the Blackbox
served-drive rig.

Same shape as the authorship lane's proven rig (2026-09-02): a real daemon process
(``apps.vool_api_server``) in its own ``VOOL_HOME`` on an ephemeral port, a real socket, the
real HTTP door, and a stub endpoint that COUNTS what it was asked and answers from a table. Model
execution on this machine is cloud-only; a stub is not a model launch. Everything between the
request and the tool's bytes on disk is production code from THIS checkout.
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
    """An Ollama-dialect endpoint that answers from a table and records every call."""

    def __init__(self, table: dict[str, Any], default: Any = "", *, after_tool_result: dict[str, Any] | None = None, reply_fn: Any = None) -> None:
        self.table = dict(table)
        self.default = default
        self.after_tool_result = dict(after_tool_result or {})
        #: Optional hook ``reply_fn(body: dict) -> reply | None`` consulted BEFORE the table, so a
        #: journey can answer from the PROMPT's shape (a file-content request vs a summary ask)
        #: rather than per model. None falls through to the table unchanged.
        self.reply_fn = reply_fn
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
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
                prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
                tools = body.get("tools") or []
                has_tool_result = any(str(m.get("role") or "") == "tool" for m in messages if isinstance(m, dict))
                with rig._lock:
                    rig.calls.append(
                        {
                            "model": model,
                            "prompt": prompt[:400],
                            # PB01: the full turn prompt (system + messages), for provider-boundary
                            # consumption proofs (learned guidance riding the system prompt).
                            "full_prompt": prompt[:20000],
                            # The TAIL of the same prompt: newest observations ride at the end and a
                            # long turn can push them past the head slice above. Additive recording
                            # only -- no consumer of the existing fields changes behavior.
                            "prompt_tail": prompt[-20000:],
                            # The ollama dialect carries the system prompt as a TOP-LEVEL field,
                            # not inside messages — record it or system-riding delivery is invisible.
                            "system": str(body.get("system") or "")[:20000],
                            "path": self.path,
                            "tools": [str(((t or {}).get("function") or {}).get("name") or "") for t in tools if isinstance(t, dict)],
                            "has_tool_result": has_tool_result,
                        }
                    )
                reply = rig.reply_for(model, has_tool_result=has_tool_result, body=body)
                if isinstance(reply, list):
                    # A scripted native batch: several tool calls in one reply.
                    message = {"role": "assistant", "content": "", "tool_calls": reply}
                    if self.path.startswith("/v1/"):
                        return self._send({"model": model, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}]})
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": message})
                if isinstance(reply, dict):
                    message = {"role": "assistant", "content": "", "tool_calls": [reply]}
                    if self.path.startswith("/v1/"):
                        return self._send({"model": model, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}]})
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": message})
                text = str(reply)
                if self.path.startswith("/v1/"):
                    return self._send(
                        {
                            "model": model,
                            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                            "usage": {"prompt_tokens": 40, "completion_tokens": 30},
                        }
                    )
                return self._send(
                    {"model": model, "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": text},
                     "prompt_eval_count": 40, "eval_count": 30}
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def reply_for(self, model: str, *, has_tool_result: bool, body: dict[str, Any] | None = None) -> Any:
        if self.reply_fn is not None and body is not None:
            custom = self.reply_fn(body)
            if custom is not None:
                return custom
        if has_tool_result and model in self.after_tool_result:
            return self.after_tool_result[model]
        return self.table.get(model, self.default)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def generations_for(self, model: str) -> int:
        with self._lock:
            return sum(1 for call in self.calls if call["model"] == model and str(call["prompt"] or "").strip())

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def __enter__(self) -> ScriptedProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


class ServedDaemon:
    """``apps.vool_api_server`` in its own process, its own VOOL_HOME and its own port."""

    def __init__(self, home: Path, *, env_extra: dict[str, str] | None = None) -> None:
        self.home = home
        self.port = free_port()
        self.env_extra = dict(env_extra or {})
        self.process: subprocess.Popen | None = None
        self.log_path = home / "daemon.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "VOOL_HOME": str(self.home),
                "PYTHONPATH": str(REPO_ROOT),
                "VOOL_DISABLE_MESH_DAEMON": "1",
                "VOOL_DISABLE_COMPUTE_MODE": "1",
                "VOOL_DISABLE_STUN": "1",
                "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "blackbox-served-rig",
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

    def start(self, *, timeout: float = 240.0) -> ServedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT), env=self.env(), stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
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

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text("utf-8", "replace").splitlines()[-lines:])
        except Exception:
            return "<no daemon log>"

    def chat(self, message: str, *, session_id: str, model: str = "", timeout: float = 180.0, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": [{"role": "user", "content": message}], "stream": False, "session_id": session_id}
        if model:
            payload["model"] = model
        payload.update(extra)
        request = Request(f"{self.base_url}/api/chat", data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise self._annotate_http_error(exc) from exc

    def chat_stream(self, message: str, *, session_id: str, model: str = "", timeout: float = 180.0, **extra: Any) -> dict[str, Any]:
        """The streamed chat lane the served UI uses: NDJSON frames, the last `done` frame is the
        turn's final payload. A turn started through this lane is cancellable via
        `/api/chat/cancel` under (session_id, turn_id)."""
        payload: dict[str, Any] = {"messages": [{"role": "user", "content": message}], "stream": True, "session_id": session_id}
        if model:
            payload["model"] = model
        payload.update(extra)
        request = Request(f"{self.base_url}/api/chat", data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        final: dict[str, Any] = {}
        try:
            with urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(frame, dict):
                        final = frame
        except HTTPError as exc:
            raise self._annotate_http_error(exc) from exc
        return final

    def _annotate_http_error(self, exc: HTTPError) -> HTTPError:
        """A served 5xx is the daemon's own exception surfacing, and its log names it. The bare
        urllib error carries none of that, and once the runner's tmpdir is gone the failure is
        undiagnosable -- measured, run 35777904719 shard 6: "HTTP Error 500" on a turn that
        passes everywhere else, its traceback lost with the ephemeral home. The type stays
        HTTPError so callers that catch it keep their behaviour; only the message grows."""
        return HTTPError(
            exc.url, exc.code, f"{exc.reason}\ndaemon log tail:\n{self.log_tail()}", exc.hdrs, None
        )

    def cancel_turn(self, *, session_id: str, turn_id: str, timeout: float = 30.0) -> dict[str, Any]:
        """The operator's stop button: POST /api/chat/cancel for one in-flight turn."""
        request = Request(f"{self.base_url}/api/chat/cancel",
                          data=json.dumps({"session_id": session_id, "turn_id": turn_id}).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
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

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


def pending_session_approvals(home: Path, session: str) -> list[dict[str, Any]]:
    """The approvals THIS chat is waiting on. The daemon keys every approval by the canonical chat
    identity its production authority derives from the client's handle, so concurrent sessions
    sharing one daemon never resolve -- or resume with -- each other's approvals."""
    from core.chat_session_identity import canonical_chat_session_id

    approvals_path = home / "data" / "pending_approvals.json"
    if not approvals_path.is_file():
        return []
    data = json.loads(approvals_path.read_text(encoding="utf-8"))
    raw = list(data.values()) if isinstance(data, dict) else list(data)
    owner = canonical_chat_session_id(session)
    return [
        entry for entry in raw
        if isinstance(entry, dict) and entry.get("status") == "pending"
        and canonical_chat_session_id(str(entry.get("session_id") or "")) == owner
    ]


def _chat_with_operator(daemon: ServedDaemon, home: Path, session: str, text: str, *,
                        model: str = "", **extra: Any) -> dict:
    """One operator turn through the real chat door.

    Auto mode prompts for overwrite-class task-plane mutations (the permission matrix's own
    row), so the runtime pauses those turns with a pending approval. The operator journey --
    the same authority the native runs exercise -- is to Allow through the production
    /api/mode door and resume with the token. Bounded at two approval rounds; a turn the
    runtime did NOT pause returns unchanged, so negative-permission assertions (a refusal
    that raises no approval) are untouched.
    """
    def _chat(text_arg: str, **extra_arg: Any) -> dict:
        return daemon.chat(text_arg, session_id=session, model=model or None, mode="auto",
                           timeout=900.0, **extra_arg)

    reply = _chat(text, **extra)
    for _round in range(2):
        entries = pending_session_approvals(home, session)
        if not entries:
            break
        token = ""
        for entry in entries:
            token = str(entry["approval_id"])
            request = Request(
                f"{daemon.base_url}/api/mode",
                data=json.dumps({"op": "resolve_approval", "session_id": session,
                                 "approval_id": str(entry["approval_id"]), "decision": "allow"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=30) as response:
                resolved = json.loads(response.read().decode())
            assert resolved.get("ok") is True, resolved
        reply = _chat(text, approval_token=token, **{k: v for k, v in extra.items() if k != "approval_token"})
    return reply


def run_in_home(home: Path, script: str, *, env_extra: dict[str, str] | None = None) -> str:
    """Run a snippet inside the daemon's own VOOL_HOME (and the same Blackbox store)."""
    env = dict(os.environ)
    env.update({"VOOL_HOME": str(home), "PYTHONPATH": str(REPO_ROOT), "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "blackbox-served-rig", "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0"})
    env.update(env_extra or {})
    env.pop("PYTEST_CURRENT_TEST", None)
    completed = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(f"in-home script failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout


SEED_MANIFEST = '''
import sys
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()

def manifest(name):
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external", runtime_dependency="ollama",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30}},
        metadata={{
            "runtime_family": "ollama", "cost_class": "free_local",
            "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
            "quantization": "q4_K_M", "parameter_billions": 2.0,
        }},
    )

conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
for name in {registered!r}:
    upsert_provider_manifest(manifest(name))
print(sorted(m.provider_id for m in list_provider_manifests()))
'''
