"""DRY RUN ONLY: a local stand-in for openrouter.ai, so every arm can run end to end for free.

It is an HTTPS proxy that answers only CONNECT to openrouter.ai (every other host is refused) and
serves that connection itself with a certificate from a throwaway CA written to the state folder.
Callers trust that CA through SSL_CERT_FILE / REQUESTS_CA_BUNDLE and reach it through
HTTPS_PROXY. Answers are canned, prices are made up, and the prompt cache is simulated from the
``cache_control`` marks the request carries. Nothing it produces is evidence.

    python fake_provider.py <state-dir> <port>
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import http.server
import json
import socketserver
import ssl
import sys
import threading
import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

STATE = Path(sys.argv[1])
PORT = int(sys.argv[2])
LOG = STATE / "requests.jsonl"
HOST = "openrouter.ai"
CATALOG = [
    {"id": "anthropic/claude-sonnet-4.5", "context_length": 200000,
     "pricing": {"prompt": "0.000003", "completion": "0.000015", "input_cache_read": "0.0000003", "input_cache_write": "0.00000375"}},
    {"id": "openai/gpt-4.1-mini", "context_length": 1000000, "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}},
    {"id": "moonshotai/kimi-k3", "context_length": 256000, "pricing": {"prompt": "0.0000006", "completion": "0.0000025"}},
]
for row in CATALOG:
    row["supported_parameters"] = ["tools", "tool_choice", "max_tokens", "temperature", "response_format"]
CACHE: dict[str, int] = {}
LOCK = threading.Lock()


def _cert(name: str, issuer_key=None, issuer_name=None, *, ca: bool = False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name or subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
    )
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        builder = builder.add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
    else:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
    return key, builder.sign(issuer_key or key, hashes.SHA256())


def _tls_context() -> ssl.SSLContext:
    STATE.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = _cert("vool-bench-dryrun-ca", ca=True)
    key, cert = _cert(HOST, ca_key, ca_cert.subject)
    pem = serialization.Encoding.PEM
    (STATE / "server.pem").write_bytes(cert.public_bytes(pem) + ca_cert.public_bytes(pem))
    (STATE / "server.key").write_bytes(
        key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(STATE / "server.pem", STATE / "server.key")
    (STATE / "ca.pem").write_bytes(ca_cert.public_bytes(pem))  # written last: its presence means ready
    return context


def _text(content) -> list[str]:
    if isinstance(content, list):
        return [json.dumps(part.get("text", "") if isinstance(part, dict) else part) for part in content]
    return [json.dumps(content)]


class Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send(self, payload, code: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/v1/key"):
            return self._send({"data": {"usage": 0.0, "label": "dry run", "limit_remaining": None, "is_free_tier": False}})
        if self.path.startswith("/api/v1/models"):
            return self._send({"data": CATALOG})
        return self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        messages = body.get("messages") or []
        flat: list[str] = []
        mark = 0
        for message in messages:
            content = message.get("content")
            flat += _text(content)
            if isinstance(content, list) and any(isinstance(p, dict) and p.get("cache_control") for p in content):
                mark = len(flat)
        total = sum(len(x) for x in flat) // 4 + 10
        cached = written = 0
        if mark:
            digest = hashlib.sha256("".join(flat[:mark]).encode()).hexdigest()
            size = sum(len(x) for x in flat[:mark]) // 4
            with LOCK:
                if digest in CACHE:
                    cached = size
                else:
                    CACHE[digest] = size
                    written = size
        cost = round((total - cached - written) * 3e-6 + cached * 3e-7 + written * 3.75e-6 + 40 * 1.5e-5, 8)
        with LOCK, LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "path": self.path, "model": body.get("model"), "messages": len(messages),
                                 "cache_control": bool(mark), "tools": len(body.get("tools") or [])}) + "\n")
        if body.get("tools") and body.get("tool_choice") == "required":
            name = body["tools"][0]["function"]["name"]
            message = {"role": "assistant", "content": None,
                       "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": "{}"}}]}
        elif "You grade one answer" in "".join(flat):
            message = {"role": "assistant", "content": '{"score": 1, "reason": "dry run judge"}'}
        else:
            message = {"role": "assistant", "content": "Dry-run answer from the stand-in provider (not a model)."}
        usage = {"prompt_tokens": total, "completion_tokens": 40, "total_tokens": total + 40, "cost": cost,
                 "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written}}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk = {"id": "gen-dry", "model": body.get("model"), "choices": [{"index": 0, "delta": message, "finish_reason": "stop"}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: " + json.dumps({"id": "gen-dry", "choices": [], "usage": usage}).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.close_connection = True
            return
        return self._send({"id": "gen-dry", "model": body.get("model"),
                           "choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": usage})


class Proxy(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main() -> None:
    context = _tls_context()

    class Tunnel(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_CONNECT(self) -> None:
            if self.path.split(":")[0] != HOST:
                self.send_response(502)  # the dry run reaches nothing else
                self.end_headers()
                return
            self.send_response(200, "Connection established")
            self.end_headers()
            with contextlib.suppress(OSError, ssl.SSLError):
                Provider(context.wrap_socket(self.connection, server_side=True), self.client_address, self.server)
            self.close_connection = True

    Proxy(("127.0.0.1", PORT), Tunnel).serve_forever()


if __name__ == "__main__":
    main()
