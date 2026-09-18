"""A served drive for the artifact readers: real daemon, real upload door, real ``/api/chat``.

The rig is the authorship/Blackbox lanes' proven shape (2026-09-02) with one deliberate change:
this ScriptedProvider keeps the **whole** model-bound request, not a 400-character sample of it.
That is the entire point of the drive. The claim under test is "the words on page 3 of the PDF
reached the payload the model was given, alongside the user's own question" -- and a truncated
capture cannot distinguish that from "page 1 reached it and the rest was dropped."

Everything between the HTTP request and the extracted bytes is production code from this checkout:
the raw upload door, `core.chat_attachments`, `core.artifact_readers`, the router, the provider
adapter. The only substitution is the model itself, which answers from a table and records what it
was asked. That substitution is what makes this a TRANSPORT-and-provenance proof: it establishes
that the evidence arrived, and deliberately establishes nothing about whether a model reasons over
it well. Model execution on this machine is cloud-only, and a stub is not a model launch.
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
#: The lane-local optional decoders (`pypdf`), installed with `pip install --target`. Absent, the
#: PDF text layer comes from PDFKit; present, it is also portable off Apple hardware.
READER_DEPS = REPO_ROOT / ".reader-deps"


def canonical_session(seed: str) -> str:
    """A session id BOTH doors agree on, in the shape the sidebar actually mints.

    The chat door passes a canonical ``openclaw:<20 hex>`` id through verbatim and re-hashes
    anything else; the upload door stores under the id it was handed, unchanged. A drive that
    uploads under ``"drive1"`` and then chats under ``"drive1"`` therefore stages into one chat and
    asks from another, and the attachment is correctly refused as not-owned. Using the canonical
    shape is what the composer does, so it is what the drive does.
    """
    import hashlib as _hashlib

    return "openclaw:" + _hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class CapturingProvider:
    """An Ollama-dialect endpoint that answers from a table and keeps every request in full."""

    def __init__(self, table: dict[str, Any] | None = None, default: str = "Acknowledged.", reply_fn: Any = None) -> None:
        self.table = dict(table or {})
        self.default = default
        #: Optional `(request_body) -> str`: a scripted reply chosen from the prompt itself (a drive
        #: that must answer "as a table" with a table). Consulted before the model-name table.
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
                    # Deliberately EMPTY. The stub is registered as its own provider (see
                    # `ServedDaemon.register_provider`); advertising a model here as well made the
                    # Ollama registration mint a SECOND manifest for the same name pointed at
                    # 127.0.0.1:11434, and resolution then picked the unreachable one and reported
                    # the model unavailable. One identity, one manifest, one base URL.
                    return self._send({"models": []})
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
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                with rig._lock:
                    rig.calls.append(
                        {
                            "model": model,
                            "path": self.path,
                            "messages": messages,
                            "text": _all_text(messages),
                            "image_count": _count_images(messages),
                            "images": _image_labels(messages),
                        }
                    )
                probe = _probe_reply(body)
                if probe is not None:
                    if self.path.startswith("/v1/"):
                        return self._send(
                            {"model": model, "choices": [{"index": 0, "finish_reason": "tool_calls" if probe.get("tool_calls") else "stop", "message": probe}]}
                        )
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": probe})
                scripted = None
                if rig.reply_fn is not None:
                    try:
                        scripted = rig.reply_fn(body)
                    except Exception:
                        scripted = None
                text = str(scripted) if scripted else str(rig.table.get(model, rig.default))
                if self.path.startswith("/v1/"):
                    return self._send(
                        {
                            "model": model,
                            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
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

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def payloads(self) -> list[str]:
        """Every model-bound request as one string, in order."""
        with self._lock:
            return [str(call["text"]) for call in self.calls]

    def last_payload(self) -> str:
        payloads = self.payloads()
        return payloads[-1] if payloads else ""

    def any_payload_has(self, needle: str) -> bool:
        return any(needle in payload for payload in self.payloads())

    def total_images(self) -> int:
        with self._lock:
            return sum(int(call["image_count"]) for call in self.calls)

    def __enter__(self) -> CapturingProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()




# --- the tool-calling half of the scripted provider -----------------------------------------------
#
# The runtime will not let an UNCERTIFIED local model author a final answer, and certification is a
# real probe: it offers synthetic tools and requires the provider to emit parallel calls with
# correct arguments, to carry a sealed tool result into its next answer, and to repair a call the
# runtime rejected. That gate is production behaviour and is not bypassed for this drive.
#
# So the scripted provider actually does those things. It is a compatibility dialect that emits
# structured tool calls -- exactly what the project contract asks of a provider without native tool
# support -- not a switch that makes the gate pass. Nothing here touches the reader assertions: the
# probe's tools are synthetic, its nonce comes from the runtime, and the reader turns that follow
# take the plain-text branch above.

_PROBE_ADD = "vool_probe_add"
_PROBE_NONCE = "vool_probe_lookup_nonce"
_PROBE_ECHO = "vool_probe_echo"


def _tool_names(body: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            names.add(str(((tool.get("function") or {}) if isinstance(tool.get("function"), dict) else {}).get("name") or tool.get("name") or ""))
    return {name for name in names if name}


def _tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if str(message.get("role") or "") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        elif isinstance(content, dict):
            out.append(content)
    return out


def _call(name: str, arguments: dict[str, Any], index: int) -> dict[str, Any]:
    return {"id": f"call_{name}_{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _probe_reply(body: dict[str, Any]) -> dict[str, Any] | None:
    """Answer the certification probe, or return None so the plain-text branch handles the turn."""
    messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    offered = _tool_names(body)
    results = _tool_results(messages)
    if _PROBE_ECHO in offered and len(offered) == 1:
        # The repair case: the runtime rejected `repair-me` and named the token it wants instead.
        wanted = next((str(r.get("required_token") or "") for r in results if r.get("required_token")), "")
        return {"role": "assistant", "content": "", "tool_calls": [_call(_PROBE_ECHO, {"token": wanted or "repair-me"}, 1)]}
    if {_PROBE_ADD, _PROBE_NONCE} <= offered:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [_call(_PROBE_ADD, {"left": 19, "right": 23}, 1), _call(_PROBE_NONCE, {"key": "alpha"}, 2)],
        }
    if results and any("sum" in result or "nonce" in result for result in results):
        # The continuation: carry the sealed results the runtime just handed back, unaltered.
        total = next((result.get("sum") for result in results if "sum" in result), "")
        nonce = next((result.get("nonce") for result in results if "nonce" in result), "")
        return {"role": "assistant", "content": f"The sum is {total} and the nonce is {nonce}."}
    return None


def _all_text(messages: list[dict[str, Any]]) -> str:
    """Flatten every text part of every message, both dialects.

    Native Ollama carries a string ``content`` with a sibling ``images`` list; the OpenAI dialect
    carries a list of typed parts. Both are read, because which one the runtime chose is the
    runtime's business and this rig asserts about the content either way.
    """
    out: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    out.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    out.append(part)
    return "\n".join(out)


def _count_images(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        count += len([i for i in (message.get("images") or []) if i])
        content = message.get("content")
        if isinstance(content, list):
            count += len([p for p in content if isinstance(p, dict) and p.get("type") == "image_url"])
    return count


def _image_labels(messages: list[dict[str, Any]]) -> list[str]:
    """The text label emitted immediately before each image part -- how a frame is addressed."""
    labels: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        previous = ""
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                previous = str(part.get("text") or "")
            elif part.get("type") == "image_url":
                labels.append(previous)
    return labels


class ServedDaemon:
    """``apps.vool_api_server`` in its own process, its own VOOL_HOME and its own port."""

    def __init__(
        self,
        home: Path,
        *,
        provider: CapturingProvider,
        model: str = "reader-drive:stub",
        provider_name: str = "reader-stub",
        vision: bool = False,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        self.home = Path(home)
        self.provider_name = provider_name
        #: Whether the registered manifest declares image input. `manifest_supports_images` reads
        #: `metadata.input_modalities`, and the door sends pixels only when it says True -- so this
        #: flag is what separates "frames travelled" from "frames were withheld, and said so".
        self.vision = bool(vision)
        self.port = free_port()
        self.provider = provider
        self.model = model
        self.env_extra = dict(env_extra or {})
        self.process: subprocess.Popen | None = None
        self.log_path = self.home / "daemon.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        stub = self.provider.base_url
        env.update(
            {
                "VOOL_HOME": str(self.home),
                # Both the repo and the lane-local decoders. Without the second entry the daemon
                # child cannot import `pypdf`, and the PDF lane would silently fall back.
                "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), str(READER_DEPS)]),
                "VOOL_DISABLE_MESH_DAEMON": "1",
                "VOOL_DISABLE_COMPUTE_MODE": "1",
                "VOOL_DISABLE_STUN": "1",
                "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "artifact-readers-drive",
                "VOOL_SKIP_PROVIDER_PREWARM": "1",
                "VOOL_INSTALL_PROFILE": "local-only",
                "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
                "VOOL_LOCAL_MODELS_ENABLED": "1",
                "VOOL_DAEMON_BIND_PORT": str(free_port()),
                # EVERY local endpoint points at the stub. A single one left pointing at a real
                # Ollama sends the drive to a live model on this machine, which is barred here.
                "OLLAMA_HOST": stub,
                "VOOL_OLLAMA_URL": stub,
                "VOOL_OLLAMA_CHAT_URL": f"{stub}/api/chat",
                "VOOL_OLLAMA_PS_URL": f"{stub}/api/ps",
                "VOOL_OLLAMA_TAGS_URL": f"{stub}/api/tags",
                # The compiled OCR helper is cached per run, inside the isolated home: the drive
                # never writes to operator state, and never reaches the Keychain to build it.
                "VOOL_READER_TOOLS_DIR": str(self.home / "reader_tools"),
            }
        )
        env.update(self.env_extra)
        env.pop("PYTEST_CURRENT_TEST", None)
        return env

    def register_provider(self) -> str:
        """Register the scripted provider as its OWN local model, in this home's manifest store.

        Necessary, not cosmetic: the shipped Ollama registration pins ``base_url`` to
        ``127.0.0.1:11434`` with no environment override, so the certification probe -- which
        reads the MANIFEST, not the chat-lane environment -- would otherwise open a connection to
        the operator's real Ollama. Measured 2026-09-03: it did exactly that and came back
        ``HTTP 404`` for an unknown model. Local model execution on this machine is barred, so the
        drive registers its own loopback provider through the production registry API and points
        the probe there instead.
        """
        script = (
            "from core.model_registry import ModelRegistry\n"
            "from storage.model_provider_manifest import ModelProviderManifest\n"
            "ModelRegistry().register_manifest(ModelProviderManifest(\n"
            f"    provider_name={self.provider_name!r},\n"
            f"    model_name={self.model!r},\n"
            "    source_type='http',\n"
            "    adapter_type='openai_compatible',\n"
            "    license_name='scripted-test-provider',\n"
            "    license_reference='local drive rig',\n"
            "    license_url_or_reference='local drive rig',\n"
            "    weight_location='external',\n"
            "    weights_bundled=False,\n"
            "    redistribution_allowed=False,\n"
            "    runtime_dependency='loopback http',\n"
            f"    capabilities={['summarize', 'extract', 'format', 'tool_intent', 'long_context'] + (['multimodal'] if self.vision else [])!r},\n"
            f"    runtime_config={{'base_url': {self.provider.base_url!r}, 'health_path': '/api/tags', "
            "'timeout_seconds': 120, 'health_timeout_seconds': 5, 'temperature': 0.0, "
            "'supports_json_mode': False, 'supports_json_schema': False, 'context_window': 8192, "
            "'native_ollama_chat': True},\n"
            f"    metadata={{'input_modalities': {(['text', 'image'] if self.vision else ['text'])!r}}},\n"
            "    enabled=True,\n"
            "))\n"
            "print('registered')\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=str(REPO_ROOT), env=self.env(), capture_output=True, text=True, timeout=180
        )
        if completed.returncode != 0:
            raise RuntimeError(f"provider registration failed:\n{completed.stdout}\n{completed.stderr}")
        return completed.stdout.strip()

    def start(self, *, timeout: float = 240.0) -> ServedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        self.register_provider()
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "127.0.0.1"],
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

    def log_tail(self, lines: int = 60) -> str:
        try:
            return "\n".join(self.log_path.read_text("utf-8", "replace").splitlines()[-lines:])
        except Exception:
            return "<no daemon log>"

    # --- the doors under test -----------------------------------------------------------------

    def upload(self, *, session_id: str, name: str, data: bytes, declared_type: str = "application/octet-stream", source: str = "") -> dict[str, Any]:
        """The REAL raw upload door, byte-for-byte, exactly as the composer posts to it."""
        headers = {
            "Content-Type": "application/octet-stream",
            "x-vool-session-id": session_id,
            "x-vool-attachment-name": name,
            "x-vool-attachment-type": declared_type,
        }
        if source:
            headers["x-vool-attachment-source"] = source
        request = Request(f"{self.base_url}/api/chat/attachments/upload", data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=180) as response:
                return {"status": response.status, **json.loads(response.read().decode("utf-8"))}
        except HTTPError as exc:
            return {"status": exc.code, **json.loads(exc.read().decode("utf-8") or "{}")}

    def limits(self) -> dict[str, Any]:
        with urlopen(f"{self.base_url}/api/chat/attachments/limits", timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def remove(self, *, session_id: str, attachment_id: str) -> dict[str, Any]:
        """The composer's cancel: a staged attachment withdrawn before the turn is sent."""
        body = json.dumps({"session_id": session_id, "attachment_id": attachment_id}).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat/attachments/remove",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                return {"status": response.status, **json.loads(response.read().decode("utf-8"))}
        except HTTPError as exc:
            return {"status": exc.code, **json.loads(exc.read().decode("utf-8") or "{}")}

    def dictation_availability(self) -> dict[str, Any]:
        """GET the dictation door: whether THIS daemon's machine can transcribe."""
        with urlopen(f"{self.base_url}/api/chat/dictation", timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def dictate(self, *, session_id: str, data: bytes, audio_type: str = "audio/x-wav") -> dict[str, Any]:
        """POST one recording to the REAL dictation door, exactly as the composer would."""
        headers = {
            "Content-Type": "application/octet-stream",
            "x-vool-session-id": session_id,
            "x-vool-audio-type": audio_type,
        }
        request = Request(f"{self.base_url}/api/chat/dictation", data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                return {"status": response.status, **json.loads(response.read().decode("utf-8"))}
        except HTTPError as exc:
            return {"status": exc.code, **json.loads(exc.read().decode("utf-8") or "{}")}

    def chat(self, message: str, *, session_id: str, attachments: list[str] | None = None, timeout: float = 300.0, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "session_id": session_id,
            "model": self.model,
            "mode": "auto",
        }
        if attachments:
            payload["attachments"] = list(attachments)
        payload.update(extra)
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def certify(self, *, provider_name: str = "", model_name: str = "", timeout: float = 300.0) -> dict[str, Any]:
        """Run the PRODUCTION local-model tool certification against the scripted provider.

        Through the real ``/api/model-tool-certification/run`` door, against the real prober. The
        provider passes because it genuinely emits parallel tool calls with the right arguments,
        carries a sealed tool result into its next answer and repairs a rejected call -- which is
        what the probe measures. No certification row is written by hand.
        """
        payload = {"provider_name": provider_name or self.provider_name, "model_name": model_name or self.model, "timeout_seconds": timeout}
        request = Request(
            f"{self.base_url}/api/model-tool-certification/run",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout + 60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return {"state": "error", "http_status": exc.code, "body": exc.read().decode("utf-8", "replace")[:600]}

    def models(self) -> list[dict[str, Any]]:
        with urlopen(f"{self.base_url}/api/tags", timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("models") if isinstance(payload, dict) else payload
        return [row for row in (rows or []) if isinstance(row, dict)]

    def certification_status(self, *, provider_name: str = "", model_name: str = "") -> dict[str, Any]:
        from urllib.parse import urlencode

        query = urlencode({"provider_name": provider_name or self.provider_name, "model_name": model_name or self.model})
        with urlopen(f"{self.base_url}/api/model-tool-certification?{query}", timeout=60) as response:
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
