from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

MAX_BODY_BYTES = 256 * 1024  # 256 KB


@dataclass
class WebhookIngressConfig:
    host: str = "127.0.0.1"
    port: int = 8989
    mirror_base_url: str = "http://127.0.0.1:8787"


class MirrorForwarder:
    def __init__(self, mirror_base_url: str):
        self.base = mirror_base_url.rstrip("/")

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[bool, int]:
        url = f"{self.base}/{path.lstrip('/')}"
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                return 200 <= resp.status < 300, resp.status
        except urllib.error.HTTPError as e:
            return False, int(e.code)
        except urllib.error.URLError:
            return False, 0

    def publish_snapshot(self, topic: str, payload: dict[str, Any]) -> tuple[bool, int]:
        return self._post(f"/publish/{urllib.parse.quote(topic)}", payload)

    def publish_offer(self, channel: str, payload: dict[str, Any]) -> tuple[bool, int]:
        return self._post(f"/docs/offers/{urllib.parse.quote(channel)}", payload)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_ok(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def build_handler(forwarder: MirrorForwarder):
    class WebhookIngressHandler(BaseHTTPRequestHandler):
        server_version = "VoolWebhookIngress/0.1"

        def _write_json(self, code: int, payload: dict[str, Any]) -> None:
            raw = _json_ok(payload)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json_body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None

            if length <= 0 or length > MAX_BODY_BYTES:
                return None

            raw = self.rfile.read(length)
            try:
                obj = json.loads(raw.decode("utf-8"))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None

        def do_GET(self) -> None:
            path = urlparse(self.path).path.rstrip("/")

            if path == "/healthz":
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "time": _utcnow(),
                        "service": "vool-webhook-ingress",
                    },
                )
                return

            self._write_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path.rstrip("/")
            payload = self._read_json_body()

            if not payload:
                self._write_json(400, {"ok": False, "error": "invalid_json_or_body"})
                return

            # Generic endpoints
            if path.startswith("/snapshot/"):
                topic = unquote(path[len("/snapshot/"):]).strip()
                if not topic:
                    self._write_json(400, {"ok": False, "error": "missing_topic"})
                    return
                ok, status = forwarder.publish_snapshot(topic, payload)
                self._write_json(200 if ok else 502, {
                    "ok": ok,
                    "kind": "snapshot",
                    "topic": topic,
                    "mirror_status": status,
                    "received_at": _utcnow(),
                })
                return

            if path.startswith("/offer/"):
                channel = unquote(path[len("/offer/"):]).strip()
                if not channel:
                    self._write_json(400, {"ok": False, "error": "missing_channel"})
                    return
                ok, status = forwarder.publish_offer(channel, payload)
                self._write_json(200 if ok else 502, {
                    "ok": ok,
                    "kind": "offer",
                    "channel": channel,
                    "mirror_status": status,
                    "received_at": _utcnow(),
                })
                return

            # Platform-specific convenience aliases
            if path.startswith("/telegram/snapshot/"):
                topic = unquote(path[len("/telegram/snapshot/"):]).strip()
                ok, status = forwarder.publish_snapshot(topic, payload)
                self._write_json(200 if ok else 502, {
                    "ok": ok,
                    "platform": "telegram",
                    "kind": "snapshot",
                    "topic": topic,
                    "mirror_status": status,
                    "received_at": _utcnow(),
                })
                return

            if path.startswith("/telegram/offer/"):
                channel = unquote(path[len("/telegram/offer/"):]).strip()
                ok, status = forwarder.publish_offer(channel, payload)
                self._write_json(200 if ok else 502, {
                    "ok": ok,
                    "platform": "telegram",
                    "kind": "offer",
                    "channel": channel,
                    "mirror_status": status,
                    "received_at": _utcnow(),
                })
                return

            if path.startswith("/discord/snapshot/"):
                topic = unquote(path[len("/discord/snapshot/"):]).strip()
                ok, status = forwarder.publish_snapshot(topic, payload)
                self._write_json(200 if ok else 502, {
                    "ok": ok,
                    "platform": "discord",
                    "kind": "snapshot",
                    "topic": topic,
                    "mirror_status": status,
                    "received_at": _utcnow(),
                })
                return

            if path.startswith("/discord/offer/"):
                channel = unquote(path[len("/discord/offer/"):]).strip()
                ok, status = forwarder.publish_offer(channel, payload)
                self._write_json(200 if ok else 502, {
                    "ok": ok,
                    "platform": "discord",
                    "kind": "offer",
                    "channel": channel,
                    "mirror_status": status,
                    "received_at": _utcnow(),
                })
                return

            self._write_json(404, {"ok": False, "error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return WebhookIngressHandler


class WebhookIngressServer:
    def __init__(self, config: WebhookIngressConfig | None = None):
        self.config = config or WebhookIngressConfig()
        self.forwarder = MirrorForwarder(self.config.mirror_base_url)
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        handler = build_handler(self.forwarder)
        self.httpd = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        self._thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="vool-webhook-ingress",
            daemon=False,
        )
        self._thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"


def main() -> None:
    server = WebhookIngressServer()
    server.start()
    print(f"Vool webhook ingress running at {server.url()}")
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
