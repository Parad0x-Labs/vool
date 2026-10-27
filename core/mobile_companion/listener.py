"""A separate, closed HTTP surface. It never delegates to the desktop router."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.runtime_paths import active_data_dir

_LOCK = threading.RLock()
_GET = frozenset({"/api/mobile/info", "/api/mobile/pairing/challenge"})
_POST = frozenset({"/api/mobile/pairing/claim", "/api/mobile/companion"})
_MAX_BODY = 6 * 1024 * 1024
_HEADER_DEADLINE = 5.0
_MIN_BODY_RATE = 16 * 1024  # bytes per second a real phone easily beats
_RATE_WINDOW = 10.0  # seconds: rides out a phone's network stall; a burst then a drip ages out


def _recent_rate(progress, now):
    """Body bytes per second over the last few seconds, never a lifetime average."""
    recent = [item for item in progress.get("recent") or () if now - item[0] <= _RATE_WINDOW]
    if not recent:
        return 0.0
    first_at, first_bytes = recent[0]
    return (progress["bytes"] - first_bytes) / max(now - first_at, 0.5)


def _settings_path() -> Path:
    return active_data_dir() / "mobile_companion" / "listener.json"


def load_settings() -> dict:
    try:
        value = json.loads(_settings_path().read_text())
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("invalid settings")
        return {
            "enabled": value.get("enabled") is True,
            "address": str(value.get("address") or ""),
            "port": int(value.get("port") or 0),
            "mode": str(value.get("mode") or "lan"),
            "relay_endpoint": str(value.get("relay_endpoint") or ""),
            "relay_room": str(value.get("relay_room") or ""),
        }
    except (OSError, ValueError, TypeError):
        return {"enabled": False, "address": "", "port": 0}


def _remembered_port() -> int:
    """The port phone access last used. A paired phone keeps dialing it, so switching access off
    and on again (or a restart on a new address) must not move it."""
    try:
        value = json.loads(_settings_path().read_text())
        port = int(value.get("last_port") or value.get("port") or 0)
        return port if 1024 <= port <= 65535 else 0
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def _save_settings(value: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, **value}))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def private_address(address: str) -> str:
    ip = ipaddress.ip_address(address)
    # An explicit interface address, never a wildcard or public-facing bind.
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    if ip.version != 4 or ip.is_unspecified or ip.is_multicast or not (ip.is_private or ip in cgnat):
        raise ValueError("Choose this computer's home or private-network address.")
    return str(ip)


def network_addresses() -> list[str]:
    """Read local interfaces only. No DNS lookup, probe or internet request."""
    import socket

    addresses = []
    if os.name == "posix" and Path("/sbin/ifconfig").exists():
        result = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, timeout=3)
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) > 1 and parts[0] == "inet":
                try:
                    address = private_address(parts[1])
                    if not ipaddress.ip_address(address).is_loopback:
                        addresses.append(address)
                except ValueError:
                    pass
    elif os.name == "posix":
        import fcntl
        import struct

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _index, interface in socket.if_nameindex():
                try:
                    data = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", interface.encode()[:15]))
                    address = private_address(socket.inet_ntoa(data[20:24]))
                    if not ipaddress.ip_address(address).is_loopback:
                        addresses.append(address)
                except (OSError, ValueError):
                    pass
    return list(dict.fromkeys(addresses))


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    SLOTS = 12
    PER_SOURCE = 4

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(self.SLOTS)
        self.source_lock = threading.Lock()
        self.sources = {}
        # Progress belongs to the transport, before JSON parsing or dispatch.
        # Header drippers cannot displace a phone whose body is making progress.
        self.reading = {}
        super().__init__(address, handler)

    def _evict_slow_sender(self, source):
        with self.source_lock:
            now = time.monotonic()
            others = []
            for request, progress in self.reading.items():
                if progress["source"] == source:
                    continue
                elapsed = max(now - progress["started"], 0.001)
                rate = _recent_rate(progress, now)
                if progress["body"] and (rate >= _MIN_BODY_RATE or (
                        not progress["bytes"] and elapsed < 1)):
                    continue
                others.append(((progress["body"], rate, progress["started"]), request))
            if not others:
                return False
            _rank, request = min(others, key=lambda item: item[0])
            self.reading.pop(request, None)
        with suppress(OSError):
            request.shutdown(socket.SHUT_RDWR)
        return True

    def process_request(self, request, client_address):
        source = client_address[0]
        with self.source_lock:
            within_source = self.sources.get(source, 0) < self.PER_SOURCE
        admitted = within_source and self.slots.acquire(blocking=False)
        if within_source and not admitted and self._evict_slow_sender(source):
            # Slow senders from other addresses cannot hold every slot against a phone.
            admitted = self.slots.acquire(timeout=1)
        if admitted:
            with self.source_lock:
                if self.sources.get(source, 0) < self.PER_SOURCE:
                    self.sources[source] = self.sources.get(source, 0) + 1
                    now = time.monotonic()
                    self.reading[request] = {"source": source, "started": now,
                                             "bytes": 0, "body": False}
                else:
                    self.slots.release()
                    admitted = False
        if not admitted:
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_source(source, request)
            raise

    def body_progress(self, request, received):
        with self.source_lock:
            progress = self.reading.get(request)
            if progress is not None:
                now = time.monotonic()
                if not progress["body"]:
                    progress.update(body=True, started=now, recent=[(now, 0)])
                progress.update(bytes=received)
                recent = progress["recent"]
                recent.append((now, received))
                while len(recent) > 1 and now - recent[0][0] > _RATE_WINDOW:
                    recent.pop(0)

    def finished_reading(self, request):
        with self.source_lock:
            self.reading.pop(request, None)

    def _release_source(self, source, request):
        with self.source_lock:
            self.reading.pop(request, None)
            remaining = self.sources[source] - 1
            if remaining:
                self.sources[source] = remaining
            else:
                del self.sources[source]
            self.slots.release()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_source(client_address[0], request)


def _handler(manager):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)
            # A per-read timeout restarts on every byte; this deadline does not.
            self._deadline = threading.Timer(_HEADER_DEADLINE, self._cut)
            self._deadline.daemon = True
            self._deadline.start()

        def _cut(self):
            with suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)

        def _extend(self, seconds):
            self._deadline.cancel()
            if seconds:
                self._deadline = threading.Timer(seconds, self._cut)
                self._deadline.daemon = True
                self._deadline.start()

        def finish(self):
            self._deadline.cancel()
            super().finish()

        def log_message(self, *_args):
            # No URLs, pairing ids, grants or bodies in access logs.
            pass

        def _reply(self, response):
            body = response.body or b""
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _route(self):
            from core.web.api import mobile_companion_api as api

            parsed = urlsplit(self.path)
            allowed = _GET if self.command == "GET" else _POST
            if parsed.path not in allowed:
                return api._err(404, "not_found")
            # Native clients send no Origin. A website cannot operate this listener.
            if self.headers.get("Origin") or self.headers.get("Transfer-Encoding"):
                return api._err(403, "native_client_required")
            if not manager.running:
                return api._err(503, "phone_access_off")
            if self.command == "GET":
                self._extend(0)
                self.server.finished_reading(self.request)
                from core.mobile_companion.sealing import challenge_response

                return challenge_response(
                    parsed.path,
                    parse_qs(parsed.query),
                    runtime=manager.runtime,
                    model_name=manager.model_name,
                    client_host=self.client_address[0],
                )
            lengths = self.headers.get_all("Content-Length") or []
            try:
                if len(lengths) != 1:
                    raise ValueError
                size = int(lengths[0])
                if size <= 0 or size > _MAX_BODY:
                    return api._err(413, "body_too_large")
                self.connection.settimeout(15)
                self._extend(_HEADER_DEADLINE + size / _MIN_BODY_RATE)
                chunks = []
                received = 0
                self.server.body_progress(self.request, received)
                while received < size:
                    chunk = self.rfile.read1(min(64 * 1024, size - received))
                    if not chunk:
                        raise ValueError("Incomplete request body.")
                    chunks.append(chunk)
                    received += len(chunk)
                    self.server.body_progress(self.request, received)
                raw = b"".join(chunks)
                self._extend(0)
                self.server.finished_reading(self.request)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, OSError, RecursionError):
                return api._err(400, "invalid_request")
            from core.mobile_companion.sealing import dispatch

            return dispatch(
                parsed.path,
                body,
                dict(self.headers),
                runtime=manager.runtime,
                model_name=manager.model_name,
                client_host=self.client_address[0],
                active=lambda: manager.running,
            )

        def do_GET(self):
            # A disconnected peer cannot amplify server error logs.
            with suppress(BrokenPipeError, ConnectionResetError, TimeoutError):
                self._reply(self._route())

        def do_POST(self):
            self.do_GET()

    return Handler


class CompanionListener:
    def __init__(self, runtime, model_name="vool"):
        self.runtime = runtime
        self.model_name = model_name
        self.server = None
        self.thread = None
        self.relay = None
        self._lock = threading.RLock()

    @property
    def running(self):
        return self.server is not None

    def snapshot(self):
        value = load_settings()
        value["running"] = self.running
        if self.server is not None:
            value.update(address=self.server.server_address[0], port=self.server.server_address[1])
        value["addresses"] = network_addresses()
        value["relay_connected"] = bool(self.relay and self.relay.connected)
        return value

    def configure(self, *, enabled, address="", port=0, mode="lan", relay_endpoint="", relay_room=""):
        if type(enabled) is not bool or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid phone connection settings.")
        with self._lock:
            if not enabled:
                # Persist OFF first. A crash during close cannot reopen the listener.
                last = self.server.server_address[1] if self.server is not None else _remembered_port()
                _save_settings({"enabled": False, "address": "", "port": 0, "last_port": last})
                self.close()
                return self.snapshot()
            if mode == "relay":
                from core.credential_store import get_credential, strict_reads
                from core.mobile_companion.relay import OutboundRelay, validate

                validate(relay_endpoint, relay_room)
                with strict_reads():
                    token = get_credential("mobile.relay.token")
                if not token:
                    raise ValueError("Save your relay connection on your computer first.")
                self.close()
                _save_settings(
                    {
                        "enabled": True,
                        "address": "",
                        "port": 0,
                        "mode": "relay",
                        "relay_endpoint": relay_endpoint,
                        "relay_room": relay_room,
                    }
                )
                self.relay = OutboundRelay(
                    self.runtime,
                    relay_endpoint,
                    relay_room,
                    token,
                    active=lambda: load_settings()["enabled"],
                    model_name=self.model_name,
                )
                self.relay.start()
                from core.mobile_companion.bot_runtime import manager_for

                manager_for(self.runtime).restore()
                return self.snapshot()
            if mode != "lan":
                raise ValueError("Choose home network or your own relay.")
            if self.relay is not None:
                raise ValueError("Turn phone access off before changing its network.")
            address = private_address(address or next(iter(network_addresses()), ""))
            if self.server is not None:
                if (address, port) not in {self.server.server_address, (self.server.server_address[0], 0)}:
                    raise ValueError("Turn phone access off before changing its network.")
                return self.snapshot()
            remembered = _remembered_port() if port == 0 else 0
            try:
                server = _Server((address, remembered or port), _handler(self))
            except OSError:
                if not remembered:
                    raise
                server = _Server((address, port), _handler(self))  # the old port is taken; take a new one
            try:
                self.server = server
                _save_settings({"enabled": True, "address": address, "port": server.server_address[1],
                                "last_port": server.server_address[1]})
                self.thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                self.thread.start()
                from core.mobile_companion.bot_runtime import manager_for

                manager_for(self.runtime).restore()
            except BaseException:
                self.server = None
                server.server_close()
                raise
            return self.snapshot()

    def restore(self):
        value = load_settings()
        if value["enabled"]:
            try:
                self.configure(**value)
            except (OSError, ValueError):
                # An unavailable saved interface fails closed, without widening the bind.
                self.configure(enabled=False)

    def close(self):
        with self._lock:
            bots = getattr(self.runtime, "phone_bot_manager", None)
            if bots is not None:
                bots.close()
            relay, self.relay = self.relay, None
            if relay is not None:
                relay.close()
            server, thread = self.server, self.thread
            self.server = None
            self.thread = None
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=2)
            from core.web.api.mobile_companion_api import mobile_companion_service

            mobile_companion_service().ingress.flush()


def listener_for(runtime) -> CompanionListener:
    with _LOCK:
        listener = getattr(runtime, "phone_listener", None)
        if listener is None:
            listener = CompanionListener(runtime)
            runtime.phone_listener = listener
        return listener
