"""A strict local stand-in for UsePod's documented HTTP surfaces. SYNTHETIC -- tests only.

This is NOT UsePod, and nothing that passes against it proves anything about UsePod's live service.
It implements the behaviour the public docs describe (read 2026-09-14) and refuses the rest, so a
client that passes against it has at least spoken the DOCUMENTED protocol:

* the token in the path, an unknown token answered 401, the ``Authorization`` header ignored;
* ``X-Pod-Routing-Mode`` (auto / marketplace-only / centralized-only), ``X-Pod-Providers`` (a pin is
  centralized-only, a pin with marketplace-only is 400, an unknown name is 400, an unsatisfiable pin
  is 503) and the ``X-Pod-Max-Price-*`` ceilings in USDC microunits per million tokens;
* marketplace and key-relay listings capped at the cheapest centralized price;
* ``X-Pod-Route``, ``X-Pod-Provider-Id`` and ``X-Balance-Remaining`` on every proxied response;
* accountless x402: an unpaid request gets 402 with a base64 JSON ``PAYMENT-REQUIRED`` quote; the paid
  retry must repeat method, path and body byte for byte and carry ``PAYMENT-SIGNATURE``; a signature
  settles one quote only.

Where the docs are silent the service makes a choice and lists it in :data:`SYNTHETIC_CHOICES`, so no
test result is read as a documented fact. It records exactly what arrived (method, path with the token
replaced by ``{token}``, headers, body bytes and their hash) so tests assert on the wire, not on the
client's own account of what it sent.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SYNTHETIC_CHOICES: dict[str, str] = {
    "no_balance_status": "402 (docs: rejected before upstream; status not stated)",
    "no_provider_at_price_status": "503 with error.type no_provider_at_price (docs: a dedicated result; status not stated)",
    "balance_header_format": "decimal USDC string with six places (docs: unit not stated)",
    "models_list_schema": "OpenAI list object (docs: schema not published)",
    "payment_response_schema": "base64 JSON {quote_id, charged_microunits, surplus_credited_microunits} (docs: schema not published)",
    "x402_cap_rule": "ceil((request bytes * input rate + max_tokens * output rate) / 1e6) at the selected listing",
    "sol_rail_rate": "fixed synthetic lamports-per-USDC-microunit factor",
    "prepaid_debit": "reported usage priced at the selected listing, rounded up",
    "reply_text": "a deterministic function of the last user message, never an answer keyed to a prompt",
}

DOCUMENTED_PROVIDER_NAMES = (
    "anthropic", "openai", "bedrock", "venice", "together", "groq",
    "openrouter", "nousresearch", "google", "surplus", "c0mpute", "uomi",
)
#: The network identifier the x402 docs show for Solana mainnet.
DOCUMENTED_MAINNET_NETWORK = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SYNTHETIC_PAY_TO = "9SynthPayTo" + "1" * 33


@dataclass(frozen=True)
class Listing:
    route: str  # "marketplace" | "key relay" | "centralized"
    provider_id: str  # a UUID for marketplace/key relay, a provider name for centralized
    input_microunits: int
    output_microunits: int


@dataclass
class ScriptedReply:
    text: str = ""
    tool_name: str = ""
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int | None = 21
    completion_tokens: int | None = 13
    stream_pieces: int = 3


def _last_user_text(body: dict[str, Any]) -> str:
    for message in reversed(list(body.get("messages") or [])):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def default_reply(body: dict[str, Any], protocol: str) -> ScriptedReply:
    digest = hashlib.sha256(_last_user_text(body).encode("utf-8")).hexdigest()[:12]
    return ScriptedReply(text=f"synthetic reply {digest} via {protocol}")


class StrictUsePodService:
    def __init__(
        self,
        *,
        tokens: dict[str, int] | None = None,
        models: dict[str, list[Listing]] | None = None,
        chain: Any = None,
        pay_to: str = SYNTHETIC_PAY_TO,
        usdc_mint: str = "",
    ) -> None:
        self.lock = threading.RLock()
        #: When set (a SIMULATION chain with ``payment_to``), a paid retry is verified by reading what that chain
        #: executed -- the claimed asset, to this service's pay-to owner, from the claimed payer -- instead of the
        #: manually recorded ``chain_payments``.
        self.chain = chain
        self.pay_to = pay_to
        self.usdc_mint = usdc_mint
        #: token -> balance in USDC microunits
        self.tokens: dict[str, int] = dict(tokens or {})
        self.models: dict[str, list[Listing]] = {key: list(value) for key, value in (models or {}).items()}
        #: When set, served verbatim at /v1/marketplace/models instead of the derived feed.
        self.feed_payload: Any = None
        self.feed_raw: bytes | None = None
        self.feed_status = 200
        self.reply: Callable[[dict[str, Any], str], ScriptedReply] = default_reply
        #: Fault knobs; see the handlers for the names each one honours.
        self.faults: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.quotes: dict[str, dict[str, Any]] = {}
        #: signature -> {"asset", "amount_atomic", "pay_to"}: the synthetic chain's confirmed payments.
        self.chain_payments: dict[str, dict[str, Any]] = {}
        self.used_signatures: set[str] = set()
        self.sol_lamports_per_usdc_microunit = 7
        service = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                service._handle(self, "GET")

            def do_POST(self) -> None:
                service._handle(self, "POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # --- lifecycle -------------------------------------------------------------------------------------

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> StrictUsePodService:
        self._thread.start()
        return self

    def stop(self) -> None:
        # socketserver.shutdown() waits on the serve loop's shutdown event with NO timeout
        # (measured: full run 36063857499 shard 1 -- a 6.25s-measured file's teardown sat 26
        # minutes in that Event.wait). Bounding the waits was NOT enough either: under the
        # thread contention of a full CI shard (hundreds of residual test threads), even a
        # 5s-bounded join was measured stretched past 600s because every GIL reacquisition
        # after a blocking call is starved (run 36079948280 shard 8: main parked in
        # thread.join at the watchdog kill while the serve thread sat healthy in select).
        # So stop() performs NO blocking synchronization at all: the shutdown request runs
        # on its own daemon thread, and the LISTENING SOCKET -- the resource any later
        # server needs -- is closed synchronously here. A serve loop still in select sees
        # the shutdown flag on its next 0.5s poll and exits (shutdown() sets the flag
        # before its wait; the loop never touches the closed socket). Termination and port
        # release are PROVEN by tests/test_strict_service_lifecycle.py, which polls
        # bounded for the thread's death and the port's refusal in a healthy process.
        stopper = threading.Thread(target=self._server.shutdown, daemon=True)
        stopper.start()
        self._server.server_close()

    def __enter__(self) -> StrictUsePodService:
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # --- test controls ---------------------------------------------------------------------------------

    def record_chain_payment(self, signature: str, *, asset: str, amount_atomic: int, pay_to: str = SYNTHETIC_PAY_TO) -> None:
        with self.lock:
            self.chain_payments[signature] = {"asset": asset, "amount_atomic": int(amount_atomic), "pay_to": pay_to}

    def requests_to(self, path_template: str) -> list[dict[str, Any]]:
        with self.lock:
            return [item for item in self.requests if item["path"] == path_template]

    def derived_feed(self) -> dict[str, Any]:
        """A feed in the live schema, computed from the listings (synthetic values)."""
        rows = []
        with self.lock:
            for model_id, listings in sorted(self.models.items()):
                centralized = [item for item in listings if item.route == "centralized"]
                market = self._capped_market(listings)
                c_in = min((item.input_microunits for item in centralized), default=None)
                c_out = min((item.output_microunits for item in centralized), default=None)
                everything = market + centralized
                best = min(everything, key=lambda item: item.input_microunits + item.output_microunits) if everything else None
                rows.append(
                    {
                        "centralized_input_per_1m": c_in,
                        "centralized_output_per_1m": c_out,
                        "centralized_providers": [
                            {"input_per_1m": item.input_microunits, "output_per_1m": item.output_microunits, "provider": item.provider_id}
                            for item in centralized
                        ],
                        "cheapest_input_per_1m": best.input_microunits if best else None,
                        "cheapest_output_per_1m": best.output_microunits if best else None,
                        "marketplace_provider_count": len(market),
                        "marketplace_total_tps": 0.0,
                        "model_id": model_id,
                        "pricing_mode": "per_token",
                        "uncensored": False,
                    }
                )
        return {"models": rows}

    # --- request handling ------------------------------------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        path = handler.path.split("?", 1)[0]
        headers = {key.lower(): value for key, value in handler.headers.items()}
        segments = [segment for segment in path.split("/") if segment]
        template = path
        token = ""
        if len(segments) >= 2 and segments[0] == "proxy" and segments[1] != "x402":
            token = segments[1]
            template = "/" + "/".join(["proxy", "{token}", *segments[2:]])
        with self.lock:
            self.requests.append(
                {
                    "method": method,
                    "path": template,
                    "headers": headers,
                    "body": body,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "at": time.time(),
                }
            )
        delay = self.faults.get("delay_seconds")
        if isinstance(delay, (int, float)) and delay > 0:
            time.sleep(float(delay))
        redirect = self.faults.get("redirect_location")
        if redirect and path != "/v1/marketplace/models":
            return self._send_json(handler, 302, {"error": {"type": "moved"}}, extra={"Location": str(redirect)})

        if method == "GET" and path == "/v1/marketplace/models":
            return self._feed(handler)
        if method == "POST" and segments[:3] == ["proxy", "x402", "v1"] and len(segments) == 4 and segments[3] == "chat":
            # An incomplete OpenAI path; /proxy/x402/v1/messages is a real surface and is handled below.
            return self._send_json(handler, 404, {"error": {"type": "not_found"}})
        if method == "POST" and path in {"/proxy/x402/v1/chat/completions", "/proxy/x402/v1/messages"}:
            protocol = "openai" if path.endswith("/chat/completions") else "anthropic"
            return self._x402(handler, path, headers, body, protocol)
        if not token:
            return self._send_json(handler, 404, {"error": {"type": "not_found"}})
        with self.lock:
            balance = self.tokens.get(token)
        if balance is None:
            return self._send_json(handler, 401, {"error": {"type": "unauthorized", "message": "unknown token"}})
        rest = "/" + "/".join(segments[2:])
        if method == "GET" and rest == "/balance":
            override = self.faults.get("balance_payload")
            return self._send_json(handler, 200, override if override is not None else {"usdc_balance": balance})
        if method == "GET" and rest == "/v1/models":
            with self.lock:
                ids = sorted(self.models)
            return self._send_json(handler, 200, {"object": "list", "data": [{"id": model_id, "object": "model"} for model_id in ids]})
        if method == "POST" and rest in {"/v1/chat/completions", "/v1/messages"}:
            if balance <= 0:
                return self._send_json(handler, 402, {"error": {"type": "insufficient_balance"}})
            protocol = "openai" if rest == "/v1/chat/completions" else "anthropic"
            return self._inference(handler, headers, body, protocol, token=token)
        return self._send_json(handler, 404, {"error": {"type": "not_found"}})

    def _feed(self, handler: BaseHTTPRequestHandler) -> None:
        if self.feed_raw is not None:
            return self._send_bytes(handler, self.feed_status, self.feed_raw, "application/json")
        payload = self.feed_payload if self.feed_payload is not None else self.derived_feed()
        return self._send_json(handler, self.feed_status, payload)

    @staticmethod
    def _capped_market(listings: list[Listing]) -> list[Listing]:
        centralized = [item for item in listings if item.route == "centralized"]
        market = [item for item in listings if item.route in {"marketplace", "key relay"}]
        if not centralized:
            return market
        cap_in = min(item.input_microunits for item in centralized)
        cap_out = min(item.output_microunits for item in centralized)
        return [replace(item, input_microunits=min(item.input_microunits, cap_in), output_microunits=min(item.output_microunits, cap_out)) for item in market]

    def _select(self, model_id: str, headers: dict[str, str]) -> tuple[Listing | None, int, dict[str, Any]]:
        mode = headers.get("x-pod-routing-mode", "auto")
        if mode not in {"auto", "marketplace-only", "centralized-only"}:
            return None, 400, {"error": {"type": "invalid_routing_mode"}}
        pins = [part.strip() for part in headers.get("x-pod-providers", "").split(",") if part.strip()]
        if pins and any(pin not in DOCUMENTED_PROVIDER_NAMES for pin in pins):
            return None, 400, {"error": {"type": "unknown_provider", "valid": list(DOCUMENTED_PROVIDER_NAMES)}}
        if pins and mode == "marketplace-only":
            return None, 400, {"error": {"type": "pin_conflicts_with_marketplace_only"}}
        ceilings: dict[str, int | None] = {}
        for name in ("x-pod-max-price-input", "x-pod-max-price-output"):
            raw = headers.get(name)
            if raw is None:
                ceilings[name] = None
            elif raw.isdigit():
                ceilings[name] = int(raw)
            else:
                return None, 400, {"error": {"type": "invalid_price_ceiling", "header": name}}
        with self.lock:
            listings = list(self.models.get(model_id) or [])
        if not listings:
            return None, 404, {"error": {"type": "model_not_found"}}

        def eligible(item: Listing) -> bool:
            max_in, max_out = ceilings["x-pod-max-price-input"], ceilings["x-pod-max-price-output"]
            return (max_in is None or item.input_microunits <= max_in) and (max_out is None or item.output_microunits <= max_out)

        def cheapest(items: list[Listing]) -> list[Listing]:
            return sorted((item for item in items if eligible(item)), key=lambda item: item.input_microunits + item.output_microunits)

        centralized = [item for item in listings if item.route == "centralized"]
        market = self._capped_market(listings)
        if pins:
            ordered = [item for pin in pins for item in centralized if item.provider_id == pin]
            chosen = next((item for item in ordered if eligible(item)), None)
            if chosen is None:
                return None, 503, {"error": {"type": "pin_unsatisfiable", "pin": pins}}
        elif mode == "marketplace-only":
            options = cheapest(market)
            if not options:
                return None, 503, {"error": {"type": "no_provider_at_price"}}
            chosen = options[0]
        elif mode == "centralized-only":
            options = cheapest(centralized)
            if not options:
                return None, 503, {"error": {"type": "no_provider_at_price"}}
            chosen = options[0]
        else:
            options = cheapest(market) or cheapest(centralized)
            if not options:
                return None, 503, {"error": {"type": "no_provider_at_price"}}
            chosen = options[0]
        forced = self.faults.get("force_listing")
        if isinstance(forced, Listing):
            # Provider behaviour that ignores the constraints -- what a client must detect, not trust.
            chosen = forced
        return chosen, 200, {}

    def _metadata_headers(self, listing: Listing, *, balance_after: int | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if not self.faults.get("omit_route_headers"):
            headers["X-Pod-Route"] = str(self.faults.get("route_header_value") or listing.route)
            headers["X-Pod-Provider-Id"] = str(self.faults.get("provider_id_value") or listing.provider_id)
        if balance_after is not None and not self.faults.get("omit_balance_header"):
            raw = self.faults.get("balance_header_value")
            headers["X-Balance-Remaining"] = str(raw) if raw is not None else f"{balance_after // 1_000_000}.{balance_after % 1_000_000:06d}"
        return headers

    def _inference(self, handler: BaseHTTPRequestHandler, headers: dict[str, str], raw: bytes, protocol: str, *, token: str) -> None:
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send_json(handler, 400, {"error": {"type": "invalid_json"}})
        if not isinstance(body, dict) or not isinstance(body.get("model"), str):
            return self._send_json(handler, 400, {"error": {"type": "invalid_request"}})
        if protocol == "anthropic" and not isinstance(body.get("max_tokens"), int):
            return self._send_json(handler, 400, {"error": {"type": "invalid_request", "message": "max_tokens required"}})
        listing, status, error = self._select(body["model"], headers)
        if listing is None:
            return self._send_json(handler, status, error)
        forced = self.faults.get("inference_response")
        if isinstance(forced, tuple) and len(forced) == 2:
            # A provider answer AFTER routing, verbatim. The live service was observed (2026-09-16,
            # candidate 035dea9b) answering HTTP 402 {"error": {"type": "no_provider_at_price"}}
            # after the request was sent; this knob replays exactly that shape.
            forced_status, forced_payload = forced
            return self._send_json(handler, int(forced_status), forced_payload)
        reply = self.reply(body, protocol)
        cost = self._cost(listing, reply)
        with self.lock:
            self.tokens[token] = self.tokens.get(token, 0) - cost
            balance_after = self.tokens[token]
        extra = self._metadata_headers(listing, balance_after=balance_after)
        return self._respond(handler, body, protocol, reply, extra)

    @staticmethod
    def _cost(listing: Listing, reply: ScriptedReply) -> int:
        prompt = int(reply.prompt_tokens or 0)
        completion = int(reply.completion_tokens or 0)
        return -((-(prompt * listing.input_microunits + completion * listing.output_microunits)) // 1_000_000)

    def _respond(self, handler: BaseHTTPRequestHandler, body: dict[str, Any], protocol: str, reply: ScriptedReply, extra: dict[str, str]) -> None:
        if self.faults.get("malformed_body"):
            return self._send_bytes(handler, 200, b"{not json", "application/json", extra=extra)
        stream = bool(body.get("stream"))
        if protocol == "openai":
            if stream:
                return self._openai_stream(handler, body, reply, extra)
            return self._send_json(handler, 200, self._openai_body(body, reply), extra=extra)
        if stream:
            return self._anthropic_stream(handler, body, reply, extra)
        return self._send_json(handler, 200, self._anthropic_body(body, reply), extra=extra)

    @staticmethod
    def _usage_openai(reply: ScriptedReply) -> dict[str, int]:
        usage: dict[str, int] = {}
        if reply.prompt_tokens is not None:
            usage["prompt_tokens"] = reply.prompt_tokens
        if reply.completion_tokens is not None:
            usage["completion_tokens"] = reply.completion_tokens
        if len(usage) == 2:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return usage

    def _openai_body(self, body: dict[str, Any], reply: ScriptedReply) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
        finish = str(self.faults.get("finish_reason") or "stop")
        if reply.tool_name:
            message["tool_calls"] = [
                {"id": "call_synth_1", "type": "function", "function": {"name": reply.tool_name, "arguments": json.dumps(reply.tool_arguments)}}
            ]
            finish = "tool_calls"
        payload: dict[str, Any] = {
            "id": f"chatcmpl-synth-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "model": body.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }
        usage = self._usage_openai(reply)
        if usage:
            payload["usage"] = usage
        return payload

    def _anthropic_body(self, body: dict[str, Any], reply: ScriptedReply) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if reply.text:
            content.append({"type": "text", "text": reply.text})
        stop = "end_turn"
        if reply.tool_name:
            content.append({"type": "tool_use", "id": "toolu_synth_1", "name": reply.tool_name, "input": dict(reply.tool_arguments)})
            stop = "tool_use"
        usage: dict[str, int] = {}
        if reply.prompt_tokens is not None:
            usage["input_tokens"] = reply.prompt_tokens
        if reply.completion_tokens is not None:
            usage["output_tokens"] = reply.completion_tokens
        return {
            "id": f"msg_synth_{uuid.uuid4().hex[:8]}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": content,
            "stop_reason": stop,
            "usage": usage,
        }

    @staticmethod
    def _pieces(text: str, count: int) -> list[str]:
        count = max(1, int(count))
        size = max(1, -(-len(text) // count))
        return [text[index : index + size] for index in range(0, len(text), size)] or [""]

    def _stream_start(self, handler: BaseHTTPRequestHandler, extra: dict[str, str]) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "close")
        for key, value in extra.items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.close_connection = True

    def _openai_stream(self, handler: BaseHTTPRequestHandler, body: dict[str, Any], reply: ScriptedReply, extra: dict[str, str]) -> None:
        self._stream_start(handler, extra)
        cut_after = self.faults.get("cut_stream_after_events")
        model = body.get("model")
        for events, piece in enumerate(self._pieces(reply.text, reply.stream_pieces), start=1):
            frame = {"id": "chatcmpl-synth", "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": {"content": piece}}]}
            handler.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
            handler.wfile.flush()
            if isinstance(cut_after, int) and events >= cut_after:
                return
        final = {"id": "chatcmpl-synth", "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        handler.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
        usage = self._usage_openai(reply)
        if usage:
            handler.wfile.write(f"data: {json.dumps({'choices': [], 'usage': usage, 'model': model})}\n\n".encode())
        if not self.faults.get("omit_stream_terminator"):
            handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()

    def _anthropic_stream(self, handler: BaseHTTPRequestHandler, body: dict[str, Any], reply: ScriptedReply, extra: dict[str, str]) -> None:
        self._stream_start(handler, extra)
        cut_after = self.faults.get("cut_stream_after_events")
        events = 0

        def emit(kind: str, data: dict[str, Any]) -> bool:
            nonlocal events
            handler.wfile.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode())
            handler.wfile.flush()
            events += 1
            return isinstance(cut_after, int) and events >= cut_after

        start_usage = {"input_tokens": reply.prompt_tokens} if reply.prompt_tokens is not None else {}
        if emit("message_start", {"message": {"id": "msg_synth_stream", "type": "message", "role": "assistant", "model": body.get("model"), "content": [], "usage": start_usage}}):
            return
        if emit("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}):
            return
        for piece in self._pieces(reply.text, reply.stream_pieces):
            if emit("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": piece}}):
                return
        if self.faults.get("anthropic_error_event"):
            emit("error", {"error": {"type": "overloaded_error", "message": "synthetic"}})
            return
        if emit("content_block_stop", {"index": 0}):
            return
        delta_usage = {"output_tokens": reply.completion_tokens} if reply.completion_tokens is not None else {}
        if emit("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": delta_usage}):
            return
        if not self.faults.get("omit_stream_terminator"):
            emit("message_stop", {})

    # --- x402 ------------------------------------------------------------------------------------------

    def _x402(self, handler: BaseHTTPRequestHandler, path: str, headers: dict[str, str], raw: bytes, protocol: str) -> None:
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send_json(handler, 400, {"error": {"type": "invalid_json"}})
        max_tokens = body.get("max_tokens", body.get("max_completion_tokens")) if isinstance(body, dict) else None
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            return self._send_json(handler, 400, {"error": {"type": "max_tokens_required"}})
        binding = hashlib.sha256(f"POST\n{path}\n{hashlib.sha256(raw).hexdigest()}".encode("ascii")).hexdigest()
        signature_header = headers.get("payment-signature")
        if not signature_header:
            listing, status, error = self._select(str(body.get("model") or ""), headers)
            if listing is None:
                return self._send_json(handler, status, error)
            cap = max(1, -((-(len(raw) * listing.input_microunits + max_tokens * listing.output_microunits)) // 1_000_000))
            quote_id = str(uuid.uuid4())
            quote = {
                "x402_version": 2,
                "quote_id": quote_id,
                "accepts": [
                    {"asset": "USDC", "scheme": "exact", "network": DOCUMENTED_MAINNET_NETWORK, "pay_to": self.pay_to, "amount_microunits": cap, "mode": "cap-with-surplus-credit"},
                    {"asset": "SOL", "scheme": "exact", "network": DOCUMENTED_MAINNET_NETWORK, "pay_to": self.pay_to, "amount_microunits": cap * self.sol_lamports_per_usdc_microunit, "mode": "cap-with-surplus-credit"},
                ],
            }
            mutate = self.faults.get("quote_mutator")
            if callable(mutate):
                quote = mutate(quote)
            with self.lock:
                self.quotes[quote_id] = {"binding": binding, "cap": cap, "listing": listing, "path": path}
            if self.faults.get("omit_payment_required_header"):
                return self._send_json(handler, 402, {"error": {"type": "payment_required"}})
            encoded = self.faults.get("raw_payment_required_header") or base64.b64encode(json.dumps(quote).encode("utf-8")).decode("ascii")
            return self._send_json(handler, 402, {"error": {"type": "payment_required"}}, extra={"PAYMENT-REQUIRED": encoded})
        held = self.faults.get("hold_then_drop_paid_retry_seconds")
        if isinstance(held, (int, float)) and held > 0:
            # SIMULATION of a request in flight when its sender dies: held open, then closed without being processed.
            # Nothing is verified or marked used, so the recorded operation can still be served on a resume.
            time.sleep(float(held))
            handler.close_connection = True
            return None
        if self.faults.get("drop_paid_retry"):
            # SIMULATION of a lost answer: the paid request reaches nobody who processes it and the connection closes
            # with no response, so the caller cannot know whether it was served. Nothing is verified or marked used.
            handler.close_connection = True
            return None
        try:
            claim = json.loads(base64.b64decode(signature_header, validate=True).decode("utf-8"))
        except Exception:
            return self._send_json(handler, 400, {"error": {"type": "malformed_payment_signature"}})
        with self.lock:
            quote = self.quotes.get(str(claim.get("quote_id") or ""))
        if quote is None:
            return self._send_json(handler, 402, {"error": {"type": "unknown_quote"}})
        if quote["binding"] != binding:
            return self._send_json(handler, 400, {"error": {"type": "request_does_not_match_quote"}})
        signature = str(claim.get("signature") or "")
        with self.lock:
            if signature in self.used_signatures:
                return self._send_json(handler, 409, {"error": {"type": "signature_already_settled"}})
        asset = str(claim.get("asset") or "")
        with self.lock:
            payment = self._chain_payment(signature, asset=asset, payer=str(claim.get("payer_wallet") or "")) if self.chain is not None else self.chain_payments.get(signature)
        needed = quote["cap"] if asset == "USDC" else quote["cap"] * self.sol_lamports_per_usdc_microunit
        if payment is None or payment["asset"] != asset or payment["pay_to"] != self.pay_to or payment["amount_atomic"] < needed:
            return self._send_json(handler, 402, {"error": {"type": "payment_not_verified"}})
        with self.lock:
            self.used_signatures.add(signature)
        paid_delay = self.faults.get("paid_retry_delay_seconds")
        if isinstance(paid_delay, (int, float)) and paid_delay > 0:
            time.sleep(float(paid_delay))
        listing = quote["listing"]
        reply = self.reply(body, protocol)
        charged = min(quote["cap"], self._cost(listing, reply))
        receipt = {"quote_id": claim.get("quote_id"), "charged_microunits": charged, "surplus_credited_microunits": quote["cap"] - charged}
        extra = self._metadata_headers(listing, balance_after=None)
        extra["PAYMENT-RESPONSE"] = base64.b64encode(json.dumps(receipt).encode("utf-8")).decode("ascii")
        return self._respond(handler, body, protocol, reply, extra)

    def _chain_payment(self, signature: str, *, asset: str, payer: str) -> dict[str, Any] | None:
        """What the SIMULATION chain executed under this signature, read the way a provider verifies a payment: the
        claimed asset (USDC by its mint, SOL as a System transfer) paid by the claimed payer to this service's pay-to."""
        executed = self.chain.payment_to(signature)
        if executed is None or str(executed.get("payer") or "") != payer:
            return None
        wanted = f"SPL:{self.usdc_mint}" if asset == "USDC" else "SOL"
        total = sum(int(item["amount_atomic"]) for item in executed["payments"] if item["asset"] == wanted and item["to_owner"] == self.pay_to)
        return {"asset": asset, "amount_atomic": total, "pay_to": self.pay_to} if total > 0 else None

    # --- wire helpers ----------------------------------------------------------------------------------

    def _send_json(self, handler: BaseHTTPRequestHandler, status: int, payload: Any, *, extra: dict[str, str] | None = None) -> None:
        self._send_bytes(handler, status, json.dumps(payload).encode("utf-8"), "application/json", extra=extra)

    @staticmethod
    def _send_bytes(handler: BaseHTTPRequestHandler, status: int, data: bytes, content_type: str, *, extra: dict[str, str] | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        for key, value in (extra or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(data)
        handler.wfile.flush()


__all__ = [
    "DOCUMENTED_MAINNET_NETWORK",
    "DOCUMENTED_PROVIDER_NAMES",
    "SYNTHETIC_CHOICES",
    "SYNTHETIC_PAY_TO",
    "Listing",
    "ScriptedReply",
    "StrictUsePodService",
    "default_reply",
]
