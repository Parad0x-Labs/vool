"""A bounded, memory-only opaque mailbox. No pairing, grant or desktop API here."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import queue
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROTOCOL = "vool.mobile.sealed.v2"
MAX_BODY = 6 * 1024 * 1024


def envelope(value, *, request=False):
    keys = {"protocol", "nonce", "ciphertext"} | ({"device_id"} if request else set())
    if not isinstance(value, dict) or set(value) != keys or value.get("protocol") != PROTOCOL:
        raise ValueError("sealed packet required")
    if not re.fullmatch(r"[0-9a-f]{48}", str(value.get("nonce", ""))):
        raise ValueError("invalid nonce")
    if not isinstance(value.get("ciphertext"), str) or len(value["ciphertext"]) > MAX_BODY:
        raise ValueError("invalid ciphertext")
    if request and not re.fullmatch(r"phone:[a-f0-9]{20}", str(value.get("device_id", ""))):
        raise ValueError("invalid device")
    return value


@dataclass
class Job:
    packet: dict
    event: threading.Event = field(default_factory=threading.Event)
    response: dict | None = None


class Mailbox:
    def __init__(self, token):
        self.token_hash = hashlib.sha256(token.encode()).digest()
        self.queue = queue.Queue(maxsize=12)
        self.jobs = {}
        self.lock = threading.Lock()

    def authorized(self, value):
        return hmac.compare_digest(self.token_hash, hashlib.sha256(value.encode()).digest())


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, rooms, *, response_timeout=20):
        self.rooms = {room: Mailbox(token) for room, token in rooms.items()}
        self.response_timeout = response_timeout
        self.slots = threading.BoundedSemaphore(20)
        for room, token in rooms.items():
            if not re.fullmatch(r"[a-f0-9]{48,64}", room) or len(token) < 32:
                raise ValueError("Use an unpredictable room and a separate PC token.")
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        for room in self.rooms.values():
            with room.lock:
                for job in room.jobs.values():
                    job.event.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(25)

    def log_message(self, *_args):
        pass

    def reply(self, status, value):
        data = json.dumps(value, separators=(",", ":")).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (OSError, TimeoutError):
            pass

    def do_POST(self):
        try:
            if self.headers.get("Origin") or self.headers.get("Transfer-Encoding"):
                raise ValueError()
            sizes = self.headers.get_all("Content-Length") or []
            if len(sizes) != 1 or not 0 < int(sizes[0]) <= MAX_BODY:
                raise ValueError()
            value = json.loads(self.rfile.read(int(sizes[0])))
            parts = self.path.split("/")
            if len(parts) < 4 or parts[1] != "rooms":
                return self.reply(404, {"error": "not_found"})
            room = self.server.rooms.get(parts[2])
            if not room:
                return self.reply(404, {"error": "not_found"})
            tail = "/".join(parts[3:])
            if tail == "api/mobile/companion":
                packet = envelope(value, request=True)
                key = secrets.token_hex(16)
                job = Job(packet)
                with room.lock:
                    if len(room.jobs) >= 12:
                        return self.reply(429, {"error": "busy"})
                    room.jobs[key] = job
                try:
                    room.queue.put_nowait((key, job))
                    job.event.wait(self.server.response_timeout)
                    if job.response is None:
                        return self.reply(503, {"error": "computer_unreachable"})
                    return self.reply(job.response["status"], job.response["body"])
                finally:
                    with room.lock:
                        room.jobs.pop(key, None)
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not room.authorized(token):
                return self.reply(403, {"error": "refused"})
            if tail == "pc/poll":
                while True:
                    try:
                        key, job = room.queue.get(timeout=1)
                    except queue.Empty:
                        return self.reply(200, {"job": None})
                    with room.lock:
                        if key in room.jobs:
                            return self.reply(200, {"job": {"id": key, "body": job.packet}})
            if tail == "pc/answer":
                if type(value.get("status")) is not int or not 200 <= value["status"] < 600:
                    raise ValueError()
                # Even the PC is prevented from turning this into a plaintext tunnel.
                packet = envelope(value["body"])
                with room.lock:
                    job = room.jobs.get(value.get("id"))
                    if not job or job.response is not None:
                        return self.reply(409, {"error": "stale"})
                    job.response = {"status": value["status"], "body": packet}
                    job.event.set()
                return self.reply(200, {"ok": True})
            return self.reply(404, {"error": "not_found"})
        except (ValueError, KeyError, TypeError, OSError, RecursionError, queue.Full):
            return self.reply(400, {"error": "invalid_packet"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        raise SystemExit("Restrict the config to its owner.")
    server = RelayServer(("127.0.0.1", args.port), json.loads(args.config.read_text()))
    print(json.dumps({"address": server.server_address}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
