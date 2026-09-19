"""Deterministic model-provider stub for the served-reality bench.

A single loopback HTTP server that speaks BOTH wire protocols the assembled
product uses for model lanes:

* OpenAI-compatible ``/v1/chat/completions`` + ``/v1/models`` (+ bare
  ``/models``) — used by the ``vllm-local`` and ``tether-remote`` manifest
  lanes registered through ``VLLM_BASE_URL`` / ``TETHER_BASE_URL``.
* Ollama-style ``/api/chat`` + ``/api/tags`` — used by the intent arbiter,
  resource governor and any hardcoded-Ollama seam routed via
  ``VOOL_OLLAMA_CHAT_URL`` / ``OLLAMA_HOST``.

Every request and response is captured as exact wire bytes to an append-only
in-memory log (and optionally a JSONL file) so a case can PROVE which lane the
product actually consulted instead of trusting the product's own story.

Responses are scripted by the bench, never generated: a script is an ordered
list of rules; the first rule whose ``match`` regex hits the last user message
(or that has no ``match``) fires. Rules can deliver content, HTTP failure
statuses, delays (for cancellation/timeouts) and malformed bodies (for
provider-failure classification). No randomness anywhere.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

STUB_MODEL_ID = "served-reality-stub"
CLOUD_STUB_MODEL_ID = "served-reality-cloud-stub"


@dataclass
class StubRule:
    """One scripted provider behavior.

    Exactly one of ``content`` / ``fail`` / ``malformed`` may be set.
    ``match`` is a regex tested against the last user message of the incoming
    request (empty/None matches anything). ``delay_s`` stalls the response to
    exercise cancellation and bounded-timeout paths.
    """

    content: str | None = None
    match: str | None = None
    fail: dict[str, Any] | None = None
    malformed: str | None = None
    delay_s: float = 0.0
    ollama_think: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "content": self.content,
            "fail": self.fail,
            "malformed": self.malformed is not None,
            "delay_s": self.delay_s,
        }


@dataclass
class WireExchange:
    """One captured provider exchange: the exact bytes both directions."""

    ts: float
    proto: str  # "openai" | "ollama"
    method: str
    path: str
    request_headers: dict[str, str]
    request_body: bytes
    status: int | None
    response_body: bytes | None
    error: str | None = None
    rule_index: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        def _clean(headers: dict[str, str]) -> dict[str, str]:
            # Sanitize authorization-style headers to a presence flag: wire
            # bytes are retained, secrets never are.
            out = {}
            for key, value in headers.items():
                lk = str(key).lower()
                if lk in {"authorization", "x-api-key", "api-key"} or "key" in lk or "token" in lk:
                    out[key] = f"<redacted len={len(str(value))}>"
                else:
                    out[key] = value
            return out

        body_text = None
        if self.request_body is not None:
            body_text = self.request_body.decode("utf-8", errors="replace")
        resp_text = None
        if self.response_body is not None:
            resp_text = self.response_body.decode("utf-8", errors="replace")
        return {
            "ts": round(self.ts, 3),
            "proto": self.proto,
            "method": self.method,
            "path": self.path,
            "request_headers": _clean(self.request_headers),
            "request_body": body_text,
            "status": self.status,
            "response_body": resp_text,
            "error": self.error,
            "rule_index": self.rule_index,
        }


def _extract_last_user_message(body: dict[str, Any]) -> str:
    for message in reversed(list(body.get("messages") or [])):
        if isinstance(message, dict) and str(message.get("role") or "") == "user":
            return str(message.get("content") or "")
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        return prompt
    return ""


class _Handler(BaseHTTPRequestHandler):
    server_version = "served-reality-stub/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_CONNECT(self) -> None:
        """The bench's network-containment gate.

        With HTTP(S)_PROXY pointed here (and NO_PROXY covering loopback),
        every external HTTPS fetch the product attempts becomes a
        deterministic, recorded 502 — the honest "offline machine"
        deployment shape. Loopback traffic (the stub lanes themselves) never
        transits the proxy.
        """
        stub: ProviderStub = self.server.stub  # type: ignore[attr-defined]
        exchange = WireExchange(
            ts=time.time(),
            proto="proxy",
            method="CONNECT",
            path=self.path,
            request_headers={k: str(v) for k, v in self.headers.items()},
            request_body=b"",
            status=None,
            response_body=None,
            error="blocked_by_bench_default_no_external_network",
        )
        payload = json.dumps(
            {"error": "served-reality bench: external network is contained by default"}
        ).encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        exchange.status = 502
        exchange.response_body = payload
        stub.record(exchange)

    # -- helpers ---------------------------------------------------------
    def _capture_request(self, proto: str) -> tuple[str, dict[str, Any] | None, bytes]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        parsed: dict[str, Any] | None = None
        if raw:
            try:
                parsed = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                parsed = None
        return proto, parsed, raw

    def _send(self, exchange: WireExchange, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        exchange.status = status
        exchange.response_body = payload

    def _send_json(self, exchange: WireExchange, status: int, obj: Any) -> None:
        self._send(exchange, status, json.dumps(obj).encode("utf-8"), "application/json")

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:
        stub: ProviderStub = self.server.stub  # type: ignore[attr-defined]
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        if path.startswith("/bench/evidence/"):
            # Deterministic grounded-fetch page: the product's URL-grounding
            # lane can fetch this real (loopback) URL and ground its answer
            # on content that never changes.
            exchange = WireExchange(
                ts=time.time(),
                proto="page",
                method="GET",
                path=self.path,
                request_headers={k: str(v) for k, v in self.headers.items()},
                request_body=b"",
                status=None,
                response_body=None,
            )
            marker = path.rsplit("/", 1)[-1]
            body = (
                f"BUILD CODE {marker}: this is the served-reality bench evidence page. "
                "It states one verifiable fact: the marker on this page is "
                f"URLPAGE-4408 and the build code is {marker}."
            )
            self._send(exchange, 200, body.encode("utf-8"), "text/plain; charset=utf-8")
            stub.record(exchange)
            return
        proto = "openai" if path.endswith("/models") else "ollama"
        exchange = WireExchange(
            ts=time.time(),
            proto=proto,
            method="GET",
            path=self.path,
            request_headers={k: str(v) for k, v in self.headers.items()},
            request_body=b"",
            status=None,
            response_body=None,
        )
        model_id = stub.model_id
        if path.endswith("/models"):
            self._send_json(
                exchange,
                200,
                {"object": "list", "data": [{"id": model_id, "object": "model", "owned_by": "served-reality-bench"}]},
            )
        elif path.endswith("/api/tags"):
            self._send_json(
                exchange,
                200,
                {"models": [{"name": model_id, "model": model_id, "size": 1}]},
            )
        elif path.endswith("/api/ps"):
            # The product checks loaded models before deciding to LOAD one
            # (a load attempt on a stub model trips the real low-memory
            # gate). Declaring the stub model resident keeps the lane honest:
            # the "runtime" answers directly, no load required.
            self._send_json(
                exchange,
                200,
                {
                    "models": [
                        {
                            "name": model_id,
                            "model": model_id,
                            "size": 1,
                            "size_vram": 1,
                            "digest": "sha256:" + "0" * 64,
                        }
                    ]
                },
            )
        elif path.endswith("/api/version"):
            self._send_json(exchange, 200, {"version": "served-reality-stub"})
        else:
            self._send_json(exchange, 404, {"error": f"stub: no GET route {path}"})
        stub.record(exchange)

    def do_POST(self) -> None:
        stub: ProviderStub = self.server.stub  # type: ignore[attr-defined]
        path = urllib.parse.urlparse(self.path).path
        proto = "openai" if "/chat/completions" in path else "ollama"
        _, parsed, raw = self._capture_request(proto)
        exchange = WireExchange(
            ts=time.time(),
            proto=proto,
            method="POST",
            path=self.path,
            request_headers={k: str(v) for k, v in self.headers.items()},
            request_body=raw,
            status=None,
            response_body=None,
        )
        body = parsed if isinstance(parsed, dict) else {}
        user_message = _extract_last_user_message(body)
        rule, index = stub.pick_rule(user_message)
        exchange.rule_index = index

        if rule is not None and rule.delay_s > 0:
            # Bounded by the stub itself so a wedged case cannot pin a worker
            # forever even if the client already went away.
            time.sleep(min(rule.delay_s, 300.0))

        if rule is not None and rule.malformed is not None:
            self._send(exchange, 200, rule.malformed.encode("utf-8"), "application/json")
            stub.record(exchange)
            return
        if rule is not None and rule.fail is not None:
            status = int(rule.fail.get("status") or 500)
            payload = json.dumps({"error": rule.fail.get("error") or "stub-injected provider failure"}).encode("utf-8")
            self._send(exchange, status, payload, "application/json")
            stub.record(exchange)
            return

        content = rule.content if rule is not None and rule.content is not None else ""
        if proto == "openai":
            self._send_json(
                exchange,
                200,
                {
                    "id": f"stub-{int(time.time() * 1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": str(body.get("model") or stub.model_id),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        else:
            payload: dict[str, Any] = {
                "model": str(body.get("model") or stub.model_id),
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
            }
            if rule is not None and rule.ollama_think:
                payload["message"]["thinking"] = ""
            self._send_json(exchange, 200, payload)
        stub.record(exchange)


class _QuietHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that swallows per-connection transport noise.

    A client that drops a connection mid-response (watchdog timeouts,
    cancelled turns) is expected bench traffic, not an error to print.
    """

    def handle_error(self, request, client_address) -> None:
        return


class ProviderStub:
    """Owns the stub HTTP server, its scripted rules and the wire log."""

    def __init__(self, *, model_id: str = STUB_MODEL_ID, wire_log_path: str | None = None) -> None:
        self.model_id = model_id
        self._rules: list[StubRule] = []
        self._rules_lock = threading.Lock()
        self._log: list[WireExchange] = []
        self._log_lock = threading.Lock()
        self._wire_log_path = wire_log_path
        self._wire_log_file = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> int:
        if self._server is not None:
            return int(self.port or 0)
        self._server = _QuietHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.stub = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        if self._wire_log_path:
            self._wire_log_file = open(self._wire_log_path, "a", encoding="utf-8")  # noqa: SIM115
        self._thread = threading.Thread(target=self._server.serve_forever, name="provider-stub", daemon=True)
        self._thread.start()
        return int(self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        if self._wire_log_file is not None:
            self._wire_log_file.close()
            self._wire_log_file = None

    # -- scripting -------------------------------------------------------
    def set_rules(self, rules: list[StubRule]) -> None:
        with self._rules_lock:
            self._rules = list(rules)

    def pick_rule(self, user_message: str) -> tuple[StubRule | None, int | None]:
        with self._rules_lock:
            for index, rule in enumerate(self._rules):
                if rule.match is None or re.search(rule.match, user_message or ""):
                    return rule, index
        return None, None

    # -- evidence --------------------------------------------------------
    def record(self, exchange: WireExchange) -> None:
        with self._log_lock:
            self._log.append(exchange)
        if self._wire_log_file is not None:
            self._wire_log_file.write(json.dumps(exchange.to_public_dict()) + "\n")
            self._wire_log_file.flush()

    def exchanges(self) -> list[WireExchange]:
        with self._log_lock:
            return list(self._log)

    def chat_calls(self) -> list[WireExchange]:
        return [e for e in self.exchanges() if "chat" in e.path and e.method == "POST"]

    def clear_log(self) -> None:
        with self._log_lock:
            self._log.clear()

    def public_wire_log(self) -> list[dict[str, Any]]:
        return [e.to_public_dict() for e in self.exchanges()]

    # -- endpoint urls ---------------------------------------------------
    def openai_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def ollama_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass
class StubPlan:
    """Convenience builder for common case scripts."""

    rules: list[StubRule] = field(default_factory=list)

    def answer(self, content: str, match: str | None = None) -> StubPlan:
        self.rules.append(StubRule(content=content, match=match))
        return self

    def fail(self, status: int, error: str, match: str | None = None) -> StubPlan:
        self.rules.append(StubRule(fail={"status": status, "error": error}, match=match))
        return self

    def stall(self, delay_s: float, content: str = "", match: str | None = None) -> StubPlan:
        self.rules.append(StubRule(content=content, match=match, delay_s=delay_s))
        return self

    def malformed(self, body: str, match: str | None = None) -> StubPlan:
        self.rules.append(StubRule(malformed=body, match=match))
        return self

    def apply(self, stub: ProviderStub) -> None:
        stub.set_rules(self.rules)
