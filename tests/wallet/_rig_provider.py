"""A prompt-routed scripted model for multi-turn served proofs: the stand-in model picks a native
tool call from the LAST USER MESSAGE, so one daemon can drive propose -> approve -> status. It speaks
both the Ollama and the OpenAI dialects the runtime uses and records every call untruncated."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "qwen3-stub:2b"
_PAY_RE = re.compile(r"pay\s+(?P<amount>\d+)\s+lamports?\s+to\s+(?P<dest>[1-9A-HJ-NP-Za-km-z]{32,44})(?:\s+for\s+(?P<memo>[^\n.?!]+))?", re.I)
_SEND_RE = re.compile(r"send\s+(?P<amount>\d+(?:\.\d+)?)\s+(?P<asset>SOL|ETH|BNB)\s+(?:on\s+(?P<chain>solana|base|ethereum|bnb|robinhood)\s+)?to\s+(?P<dest>0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})(?:\s+on\s+(?P<chain2>solana|base|ethereum|bnb|robinhood))?(?:\s+for\s+(?P<memo>[^\n.?!]+))?", re.I)
_PROPOSAL_RE = re.compile(r"\b(pay-[0-9a-f]{20})\b")


def route_tool_call(last_user: str) -> dict[str, Any] | None:
    text = str(last_user or "")
    lowered = text.lower()
    ids = _PROPOSAL_RE.findall(text)
    if "status" in lowered and ids:
        return {"id": "call-status", "type": "function", "function": {"name": "wallet__payment_status", "arguments": {"proposal_id": ids[-1]}}}
    if "simulate" in lowered and ids:
        return {"id": "call-sim", "type": "function", "function": {"name": "wallet__simulate", "arguments": {"proposal_id": ids[-1]}}}
    match = _PAY_RE.search(text)
    if match:
        return {"id": "call-propose", "type": "function", "function": {"name": "wallet__propose", "arguments": {"destination": match.group("dest"), "amount_minor": int(match.group("amount")), "asset": "SOL", "memo": (match.group("memo") or "").strip()}}}
    sent = _SEND_RE.search(text)
    if sent:
        arguments = {"destination": sent.group("dest"), "amount": sent.group("amount"), "asset": sent.group("asset").upper(), "memo": (sent.group("memo") or "").strip()}
        chain = sent.group("chain") or sent.group("chain2")
        if chain:
            arguments["chain"] = chain.lower()
        return {"id": "call-propose", "type": "function", "function": {"name": "wallet__propose", "arguments": arguments}}
    if "x402" in lowered:
        # fetch a paid resource and park its capped proposal: the model surface stays
        # proposal-only (this route never approves, signs or retries)
        import re as _re

        url_match = _re.search(r"(https?://\S+)", text)
        if url_match:
            return {"id": "call-x402", "type": "function", "function": {"name": "x402__propose", "arguments": {"resource_url": url_match.group(1).rstrip(".,;:)")}}}
    if "wallet" in lowered:
        return {"id": "call-wstatus", "type": "function", "function": {"name": "wallet__status", "arguments": {}}}
    return None


class PromptRoutedProvider:
    def __init__(self, *, default_text: str = "I could not decide what to do.") -> None:
        self.default_text = default_text
        self.after_tool_result = "unused: the runtime renders the tool result itself"
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        rig = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                # The stub model is always RESIDENT (it costs no RAM): /api/ps lists it exactly as the shared
                # rig does, so the resource governor never plans a load for it and the memory gate keeps
                # protecting the machine from real models only.
                if self.path.startswith("/api/tags") or self.path.startswith("/api/ps"):
                    return self._send({"models": [{"name": MODEL, "model": MODEL, "size": 1, "size_vram": 1, "details": {"parameter_size": "2B", "quantization_level": "Q4_K_M"}}]})
                return self._send({"version": "0.0.0-stub"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                model = str(body.get("model") or "")
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                prompt = " ".join(str(m.get("content") or "") for m in messages)
                users = [str(m.get("content") or "") for m in messages if str(m.get("role") or "") == "user"]
                last_user = users[-1] if users else ""
                tools = body.get("tools") or []
                has_tool_result = any(str(m.get("role") or "") == "tool" for m in messages)
                with rig._lock:
                    rig.calls.append({"model": model, "prompt": prompt, "last_user": last_user, "path": self.path, "tools": [str(((t or {}).get("function") or {}).get("name") or "") for t in tools if isinstance(t, dict)], "has_tool_result": has_tool_result})
                if self.path.startswith("/api/show"):
                    return self._send({"model_info": {}, "details": {}})
                reply = rig.reply_for(model, has_tool_result=has_tool_result, last_user=last_user, tools=[str(((t or {}).get("function") or {}).get("name") or "") for t in tools if isinstance(t, dict)], prompt=prompt)
                if isinstance(reply, dict):
                    message = {"role": "assistant", "content": "", "tool_calls": [reply]}
                    if self.path.startswith("/v1/"):
                        return self._send({"model": model, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}]})
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": message})
                text = str(reply)
                if self.path.startswith("/v1/"):
                    return self._send({"model": model, "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}], "usage": {"prompt_tokens": 40, "completion_tokens": 30}})
                return self._send({"model": model, "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": text}, "prompt_eval_count": 40, "eval_count": 30})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def reply_for(self, model: str, *, has_tool_result: bool, last_user: str = "", tools: list[str] | None = None, prompt: str = "") -> Any:
        if has_tool_result:
            return self.after_tool_result
        if '"ambiguous": true or false' in str(prompt or "") and "well-known referents" in str(prompt or ""):
            # the runtime's entity-ambiguity probe (core.entity_ambiguity): a wallet request names one exact thing,
            # so the stand-in judges it the way a model would, and never answers the question itself
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        call = route_tool_call(last_user)
        if call is not None and (not tools or call["function"]["name"] in tools):
            return call
        return self.default_text

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def generations_for(self, model: str) -> int:
        with self._lock:
            return sum(1 for call in self.calls if call["model"] == model and str(call["prompt"] or "").strip())

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def __enter__(self) -> PromptRoutedProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


#: Run in the daemon's own home AFTER SEED_MANIFEST: certifies the stub model for the final-answer
#: author role the way an operator's probe run would, so the runtime is willing to call it with tools.
CERTIFY_SEED = '''
import sys
sys.path.insert(0, "{root}")
from storage.model_provider_manifest import list_provider_manifests
from tests._authorship_certification import certify_for_authorship

for manifest in list_provider_manifests():
    if manifest.model_name in {registered!r}:
        print("certified", manifest.model_name, certify_for_authorship(manifest))
'''


def seed_daemon(home, provider_base_url: str) -> None:
    """Register the stub model in the served home and certify it for authorship."""
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, run_in_home

    run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider_base_url, registered=[MODEL]))
    run_in_home(home, CERTIFY_SEED.format(root=REPO_ROOT, registered=[MODEL]))
