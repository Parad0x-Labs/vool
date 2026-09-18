"""C01 — the model-sufficiency writer on REAL /api/chat turns, through a restart.

One isolated served daemon, one scripted Ollama-dialect provider behind the real HTTP door,
both registered models CERTIFIED through the daemon's own operator route (the sealed probe runs
against this file's body-aware scripted provider), and then the eight served scenarios driven
one after another:

    successful validated answer, objectively wrong (validator-rejected) answer, refusal,
    failed provider, cancelled turn, model switch, two overlapping sessions, same-turn retry —

each asserting the DURABLE ROW IDENTITY the sufficiency writer recorded under the daemon home:
exact task_kind / provider / model / outcome / stage / turn_key / runtime session, one row per
(turn, provider), nothing for cancelled or transport-failed turns, nothing borrowed across
turns or sessions — and that model selection consumes only ELIGIBLE observations.

Scripted-provider boundary evidence: this file proves the PLUMBING (identity, durability,
eligibility joins). Model-authored usefulness is a separate evidence class and is NOT claimed
here.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests._blackbox_served_rig import free_port

MODEL_A = "qwen3-stub:2b"
MODEL_B = "stub-second:3b"
PROVIDER_NAME = "ollama-local"

SEED = '''
import sys
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()

def manifest(name, params):
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external", runtime_dependency="ollama",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30, **params}},
        metadata={{
            "runtime_family": "ollama", "cost_class": "free_local",
            "model_digest": "sha256:stub-" + name, "chat_template_hash": "tmpl-" + name,
            "quantization": "q4_K_M", "parameter_billions": 2.0,
        }},
    )

conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
for name, params in {models!r}:
    upsert_provider_manifest(manifest(name, params))
print(sorted(m.provider_id for m in list_provider_manifests()))
'''


class BodyAwareScriptedProvider:
    """The Ollama-dialect stub, extended so replies see the full request body.

    The extra body visibility exists for exactly two things: the sealed tool-certification
    probe (its continuation must echo a nonce it delivered in a tool result, and its recovery
    case must repair a rejected call) and per-turn slow/empty behaviours used by the cancel and
    retry scenarios. Every reply is computed from what the runtime actually sent.
    """

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = dict(table)
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
                    return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in rig.table]})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                model = str(body.get("model") or "")
                if model in rig.fail_models:
                    return self._send({"error": "scripted provider transport failure"}, status=500)
                messages = body.get("messages") or []
                prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
                tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or []) if isinstance(t, dict)]
                with rig._lock:
                    rig.calls.append(
                        {
                            "model": model,
                            "prompt": prompt[:400],
                            # Full provider-wire capture (same discipline as the Blackbox rig):
                            # the complete turn prompt AND the ollama-dialect top-level system
                            # field, so provider-boundary consumption proofs can assert on the
                            # exact bytes the runtime sent.
                            "full_prompt": prompt[:100000],
                            "system": str(body.get("system") or "")[:100000],
                            "path": self.path,
                            "tools": tools,
                        }
                    )
                reply = rig.reply(model, body)
                if isinstance(reply, dict):
                    message = {"role": "assistant", "content": reply.get("content", ""), "tool_calls": reply["tool_calls"]}
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": message})
                return self._send(
                    {"model": model, "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": str(reply)},
                     "prompt_eval_count": 40, "eval_count": 30}
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.slow_seconds = 0.0
        self.empty_once: set[str] = set()
        self.fail_models: set[str] = set()
        self._emptied: set[str] = set()

    # ---------------------------------------------------------------- reply law

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        tool_names = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or []) if isinstance(t, dict)]
        has_tool_result = any(str(m.get("role") or "") == "tool" for m in messages)

        # ---- the sealed tool-certification probe choreography ----
        if "vool_probe_add" in tool_names:
            return {
                "content": "",
                "tool_calls": [
                    {"id": "probe-add-1", "type": "function", "function": {"name": "vool_probe_add", "arguments": {"left": 19, "right": 23}}},
                    {"id": "probe-nonce-1", "type": "function", "function": {"name": "vool_probe_lookup_nonce", "arguments": {"key": "alpha"}}},
                ],
            }
        if "vool_probe_echo" in tool_names:
            repair = "synthetic_validation_error" in prompt or "recovered" in prompt
            token = "recovered" if repair else "repair-me"
            return {
                "content": "",
                "tool_calls": [{"id": f"probe-echo-{token}", "type": "function", "function": {"name": "vool_probe_echo", "arguments": {"token": token}}}],
            }
        if not tool_names and has_tool_result:
            # Result continuation: echo back the sealed nonce the tool result carried.
            nonces = set(re.findall(r"\b[0-9a-f]{12}\b", prompt))
            if nonces:
                nonce = sorted(nonces)[0]
                return f"The sum is 42 and the sealed nonce is {nonce}."
        if not tool_names and not has_tool_result:
            if "SLOW-TURN" in prompt and self.slow_seconds:
                time.sleep(self.slow_seconds)
            if model in self.empty_once and model not in self._emptied:
                self._emptied.add(model)
                return ""  # structurally empty: the runtime's bounded same-provider retry
        return self.table.get(model, "")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def __enter__(self) -> BodyAwareScriptedProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
