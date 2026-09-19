"""A labelled SYNTHETIC free tool-selection lane for the served tool-flow tests. Tests only.

The runtime's own policy (core/paid_call_reservation.py: "internal_tool_intent_call") keeps an
explicit PAID pin off the turn's internal tool-selection rounds; those rounds need a free lane.
This service is that lane: a loopback OpenAI-compatible endpoint whose reply is a deterministic
function of the request that arrived -- a native tool call naming an OFFERED tool on the first
round, a plain completion once the tool result is in the context, or a scripted fault (an
unregistered tool name, malformed arguments JSON) for the refusal cases. It records every request
so the tests assert on the wire, not on the runtime's account of itself.

It is NOT UsePod and proves nothing about UsePod's live service.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: The harmless disposable local tool these tests exercise, as the runtime names the intent.
TOOL_INTENT = "workspace.list_files"
#: The same name after core.cloud_tool_call_contract's native encoding (dots -> __).
TOOL_NATIVE_NAME = "workspace__list_files"


class FreeToolLaneService:
    """Deterministic replies, keyed by what the arriving request actually contains."""

    def __init__(self, *, mode: str = "tool_round_trip", tool_name: str | None = None) -> None:
        self.mode = mode
        self.tool_name = tool_name
        self.requests: list[dict] = []
        self.lock = threading.RLock()
        service = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                return

            def do_GET(self) -> None:
                # The OpenAI-compatible adapter validates its model against the lane's own
                # catalogue before dispatch; answer it with the one model this lane serves.
                payload = json.dumps({"object": "list", "data": [{"id": "freelane-tool-picker", "object": "model"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()

            def do_POST(self) -> None:
                service._handle(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FreeToolLaneService:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FreeToolLaneService:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # --- reply logic ---------------------------------------------------------------------------------

    def _seen_observation(self, body: dict) -> bool:
        """Whether the executed tool's result is already in the context (the observation message
        core/prompt_normalizer renders for the next round)."""
        for message in body.get("messages") or []:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and ("Real tool observations from" in content or "Real tool result from" in content):
                return True
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and ("Real tool observations from" in str(part.get("text") or "") or "Real tool result from" in str(part.get("text") or "")):
                        return True
        return False

    def _reply(self, body: dict) -> dict:
        with self.lock:
            self.requests.append({"body": body, "at": time.time()})
        if self.mode == "tool_round_trip" and self._seen_observation(body):
            # The tool result is in context: answer plainly so the loop breaks to grounded
            # synthesis on the pinned answering lane.
            return {
                "id": f"chatcmpl-freelane-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "selection complete"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20},
            }
        if self.mode == "tool_round_trip":
            name = self.tool_name or TOOL_NATIVE_NAME
            return {
                "id": f"chatcmpl-freelane-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_freelane_1", "type": "function",
                         "function": {"name": name, "arguments": json.dumps({"path": "."})}}
                    ]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
            }
        if self.mode == "forbidden_tool":
            return {
                "id": f"chatcmpl-freelane-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_freelane_x", "type": "function",
                         "function": {"name": self.tool_name or "sandbox__run_command",
                                      "arguments": json.dumps({"command": "echo forbidden"})}}
                    ]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
            }
        if self.mode == "malformed_arguments":
            return {
                "id": f"chatcmpl-freelane-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_freelane_m", "type": "function",
                         "function": {"name": TOOL_NATIVE_NAME, "arguments": '{"path": not json'}}
                    ]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22},
            }
        return {
            "id": f"chatcmpl-freelane-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "selection complete"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 3, "total_tokens": 20},
        }

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        payload = json.dumps(self._reply(body)).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
        handler.wfile.flush()
