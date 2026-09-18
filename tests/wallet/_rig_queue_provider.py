"""A queue-driven scripted model for served proofs.

The test decides, generation by generation, exactly what the stand-in "model" emits: a native tool call
or plain text. Nothing here reads the user's words, so a served proof cannot pass because a regex happened
to match the phrasing under test. What the runtime OFFERED is recorded for every generation (tool names,
untruncated prompt), so a proof can assert that the production demand gate exposed the wallet tools for
that phrasing, and a discriminator control can make the model emit different terms than the user typed.

This is plumbing evidence: it proves the runtime's tool contract, approval pause and effects, never a real
model's understanding.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.wallet._rig_provider import MODEL


class QueueProvider:
    def __init__(self, *, default_text: str = "Nothing was scripted for this turn.", after_tool_result: str = "Done.") -> None:
        self.default_text = default_text
        self.after_tool_result = after_tool_result
        self.queue: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.consumed: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        rig = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path.startswith("/api/tags") or self.path.startswith("/api/ps"):
                    return self._send({"models": [{"name": MODEL, "model": MODEL, "size": 1, "size_vram": 1, "details": {"parameter_size": "2B", "quantization_level": "Q4_K_M"}}]})
                return self._send({"version": "0.0.0-queue-stub"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
                except Exception:
                    body = {}
                if self.path.startswith("/api/show"):
                    return self._send({"model_info": {}, "details": {}})
                model = str(body.get("model") or "")
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or []) if isinstance(t, dict)]
                has_tool_result = any(str(m.get("role") or "") == "tool" for m in messages)
                reply = rig._reply(tools=tools, has_tool_result=has_tool_result)
                with rig._lock:
                    rig.calls.append({"model": model, "path": self.path, "tools": tools, "has_tool_result": has_tool_result,
                                      "prompt": " ".join(str(m.get("content") or "") for m in messages), "reply": reply})
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

    # -- scripting ---------------------------------------------------------------------------------------
    def enqueue_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            self.queue.append({"tool": name, "arguments": dict(arguments)})

    def enqueue_text(self, text: str) -> None:
        with self._lock:
            self.queue.append({"text": str(text)})

    def _reply(self, *, tools: list[str], has_tool_result: bool) -> Any:
        if has_tool_result:
            return self.after_tool_result
        with self._lock:
            if not tools or not self.queue:
                # certification probes and tool-less lanes never consume a scripted turn
                return self.default_text
            item = self.queue.pop(0)
            offered = item.get("tool") in tools if "tool" in item else True
            self.consumed.append({**item, "offered_tools": list(tools), "tool_was_offered": offered})
        if "text" in item:
            return item["text"]
        if not offered:
            return f"(scripted tool {item['tool']} was not offered on this turn)"
        return {"id": f"call-{len(self.consumed)}", "type": "function", "function": {"name": item["tool"], "arguments": item["arguments"]}}

    # -- reading -----------------------------------------------------------------------------------------
    def generations(self) -> int:
        with self._lock:
            return sum(1 for call in self.calls if call["tools"])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> QueueProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


__all__ = ["MODEL", "QueueProvider"]
