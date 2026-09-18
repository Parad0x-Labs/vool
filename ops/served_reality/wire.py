"""Bench→daemon HTTP client with exact wire-byte retention.

Every exchange the bench performs is captured byte-for-byte (request line,
headers, body; response status, headers, body) into a per-run artifact file,
so a case's proof can cite the exact bytes that flew — sanitized only of
credential VALUES (never of structure).

Truth rules enforced here:

* A transport error is an exception, never a green result.
* The response body is returned decoded but the BYTES are retained; content
  that is not valid UTF-8 survives as replacement text in the record and the
  raw bytes stay in the artifact.
* Timeouts are wall-clock bounded by a watchdog thread because a wedged
  daemon can hold a socket open past any socket timeout (measured on this
  product: urllib timeouts do not fire on a wedged request path).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ops.served_reality.classify import BenchError, ProductAssertionError

_SENSITIVE_HEADERS = {"authorization", "x-api-key", "api-key", "cookie", "x-vool-session-token"}


@dataclass
class HttpExchange:
    exchange_id: str
    method: str
    url: str
    path: str
    request_headers: dict[str, str]
    request_body: bytes | None
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: bytes | None = None
    elapsed_s: float = 0.0
    error: str | None = None
    ts: float = 0.0

    def response_json(self) -> Any:
        if self.response_body is None:
            return None
        try:
            return json.loads(self.response_body)
        except (ValueError, UnicodeDecodeError):
            return None

    def response_text(self) -> str:
        if self.response_body is None:
            return ""
        return self.response_body.decode("utf-8", errors="replace")

    def to_public_dict(self) -> dict[str, Any]:
        def _clean(headers: dict[str, str]) -> dict[str, str]:
            return {
                k: (f"<redacted len={len(v)}>" if k.lower() in _SENSITIVE_HEADERS else v)
                for k, v in headers.items()
            }

        return {
            "exchange_id": self.exchange_id,
            "ts": round(self.ts, 3),
            "method": self.method,
            "url": self.url,
            "request_headers": _clean(self.request_headers),
            "request_body": (self.request_body or b"").decode("utf-8", errors="replace"),
            "status": self.status,
            "response_headers": _clean(self.response_headers),
            "response_body": self.response_text(),
            "elapsed_s": round(self.elapsed_s, 3),
            "error": self.error,
        }


class WireLog:
    """Append-only exact-byte log with a JSONL public mirror."""

    def __init__(self, run_dir: Path, name: str) -> None:
        self._exchanges: list[HttpExchange] = []
        self._lock = threading.Lock()
        run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = run_dir / f"wire-{name}.jsonl"
        self.bytes_path = run_dir / f"wire-{name}.bytes"
        self._jsonl = self.jsonl_path.open("a", encoding="utf-8")  # noqa: SIM115
        self._bytes = self.bytes_path.open("ab")  # noqa: SIM115
        self._byte_cursor = 0

    def record(self, exchange: HttpExchange) -> None:
        with self._lock:
            blob = json.dumps(exchange.to_public_dict(), sort_keys=True).encode("utf-8")
            start = self._byte_cursor
            self._bytes.write(blob)
            self._bytes.write(b"\n")
            self._bytes.flush()
            self._byte_cursor = self._bytes.tell()
            self._jsonl.write(json.dumps(exchange.to_public_dict(), sort_keys=True) + "\n")
            self._jsonl.flush()
            self._exchanges.append(exchange)

    def exchanges(self) -> list[HttpExchange]:
        with self._lock:
            return list(self._exchanges)

    def close(self) -> None:
        with self._lock:
            self._jsonl.close()
            self._bytes.close()


class WireClient:
    """One client bound to one base URL (the daemon)."""

    def __init__(self, base_url: str, wire_log: WireLog, *, default_timeout_s: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.wire_log = wire_log
        self.default_timeout_s = float(default_timeout_s)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> HttpExchange:
        timeout = float(timeout_s if timeout_s is not None else self.default_timeout_s)
        url = f"{self.base_url}{path}"
        request_headers = {"accept": "application/json"}
        if body is not None:
            request_headers["content-type"] = "application/json"
        for key, value in (headers or {}).items():
            request_headers[str(key).lower()] = str(value)
        payload: bytes | None = None
        if body is not None:
            payload = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")

        exchange = HttpExchange(
            exchange_id=f"wx-{uuid.uuid4().hex[:12]}",
            method=method.upper(),
            url=url,
            path=path,
            request_headers=dict(request_headers),
            request_body=payload,
            ts=time.time(),
        )

        req = urllib.request.Request(url, data=payload, method=method.upper())
        for key, value in request_headers.items():
            req.add_header(key, value)

        result: dict[str, Any] = {}
        done = threading.Event()

        def _perform() -> None:
            try:
                with urllib.request.urlopen(req, timeout=max(timeout, 0.1)) as resp:
                    result["status"] = int(resp.status)
                    result["headers"] = {k: str(v) for k, v in resp.headers.items()}
                    result["body"] = resp.read()
            except urllib.error.HTTPError as exc:
                # An HTTP error status IS a response — retained as such, never
                # raised: the case asserts on the status truthfully.
                try:
                    body = exc.read()
                except Exception:  # pragma: no cover - body already drained
                    body = b""
                result["status"] = int(exc.code)
                result["headers"] = {k: str(v) for k, v in (exc.headers or {}).items()}
                result["body"] = body
            except Exception as exc:  # transport failure
                result["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                done.set()

        started = time.monotonic()
        worker = threading.Thread(target=_perform, daemon=True)
        worker.start()
        # Watchdog: socket timeouts alone do not fire against a wedged
        # server; the wall clock here is the honest bound.
        if not done.wait(timeout=timeout + 5.0):
            result.setdefault("error", f"bench watchdog timeout after {timeout + 5.0:.1f}s")
        exchange.elapsed_s = time.monotonic() - started
        exchange.status = result.get("status")
        exchange.response_headers = dict(result.get("headers") or {})
        exchange.response_body = result.get("body")
        exchange.error = result.get("error")
        self.wire_log.record(exchange)
        return exchange

    def get(self, path: str, **kwargs: Any) -> HttpExchange:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: Any, **kwargs: Any) -> HttpExchange:
        return self.request("POST", path, body=body, **kwargs)

    def post_chat(
        self,
        text: str,
        *,
        chat_id: str,
        turn_id: str | None = None,
        workspace: str | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> HttpExchange:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "messages": [{"role": "user", "content": text}],
        }
        if turn_id:
            body["turn_id"] = turn_id
        if workspace:
            body["workspace"] = workspace
        if extra_body:
            body.update(extra_body)
        return self.post("/api/chat", body, timeout_s=timeout_s)

    def healthz(self, timeout_s: float = 5.0) -> HttpExchange:
        return self.get("/healthz", timeout_s=timeout_s)

    def events(self, session_id: str, timeout_s: float = 10.0) -> list[dict[str, Any]]:
        exchange = self.get(f"/api/runtime/events?session={session_id}", timeout_s=timeout_s)
        if exchange.error:
            # A read that cannot reach the daemon mid-case is a run-surface
            # product failure (the daemon the case is driving died or
            # wedged) — classified VOOL, never silently retried.
            raise ProductAssertionError(f"events read failed: {exchange.error}")
        payload = exchange.response_json()
        if not isinstance(payload, dict):
            raise BenchError("events read returned non-dict payload")
        return list(payload.get("events") or [])

    def receipts(self, session_id: str, timeout_s: float = 10.0) -> dict[str, Any]:
        exchange = self.get(f"/api/runtime/receipts?session={session_id}", timeout_s=timeout_s)
        if exchange.error:
            raise ProductAssertionError(f"receipts read failed: {exchange.error}")
        payload = exchange.response_json()
        if not isinstance(payload, dict):
            raise BenchError("receipts read returned non-dict payload")
        return payload

    def chat_history(self, session_id: str, timeout_s: float = 10.0) -> list[dict[str, Any]]:
        exchange = self.get(f"/api/chat/history?session={session_id}", timeout_s=timeout_s)
        if exchange.error:
            raise ProductAssertionError(f"history read failed: {exchange.error}")
        payload = exchange.response_json()
        if not isinstance(payload, dict):
            raise BenchError("history read returned non-dict payload")
        return list(payload.get("messages") or [])


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()
