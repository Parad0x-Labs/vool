"""A real TLS external-service fixture. Configuration arrives on stdin, never argv."""
from __future__ import annotations

import hashlib
import json
import ssl
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    config = json.loads(sys.stdin.readline())
    expected = config["access"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.serve()

        def do_POST(self):
            self.serve()

        def serve(self):
            path = self.path
            path_auth = path.startswith("/proxy/" + expected + "/")
            route = path[len("/proxy/" + expected):] if path_auth else path
            authorized = path_auth or self.headers.get("Authorization") == "Bearer " + expected
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            record = {"path": ("/proxy/{credential}/" + path.split("/", 3)[-1]) if path.startswith("/proxy/") else path, "authorized": authorized,
                      "path_auth": path_auth, "authorization_header": bool(self.headers.get("Authorization")),
                      "method": self.command, "stream": body.get("stream", False)}
            with Path(config["log"]).open("a") as handle:
                handle.write(json.dumps(record) + "\n")
            if route == "/v1/marketplace/models":
                return self.send(200, {"models": [{"id": "fixture-model", "pricing": {"prompt": "0", "completion": "0"}}]})
            if not authorized:
                return self.send(401, {"error": {"message": "Access refused"}})
            mode = body.get("fixture_mode")
            if mode == "timeout":
                time.sleep(5)
            if mode == "error":
                return self.send(503, {"error": "Unavailable"})
            if mode == "payment":
                return self.send(402, {"payment": "must be refused"})
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "https://other.example.org/")
                return self.end_headers()
            if mode == "oversized":
                value = b"x" * (5 * 1024 * 1024)
                self.send_response(200)
                self.send_header("Content-Length", str(len(value)))
                self.end_headers()
                return self.wfile.write(value)
            if route == "/v1/models":
                return self.send(200, {"data": [{"id": "fixture-model", "name": "Fixture model",
                                                "pricing": {"prompt": "0", "completion": "0"}, "context_length": 16384}]})
            if route == "/v1/version":
                return self.send(200, {"engine": {"version": "fixture-1"}, "service": "external-fixture"})
            if route == "/v1/scan":
                source = body["source"]
                return self.send(200, {"kind": "scan", "engine": "fixture-1", "service": "external-fixture",
                                       "verdict": "pass", "lockfile": {"version": 1},
                                       "artifacts": [{"name": source["name"], "digest": hashlib.sha256(source["content"].encode()).hexdigest()}],
                                       "findings": [], "policy": {"violations": []}})
            if route == "/v1/chat/completions":
                user = next((str(message["content"]) for message in reversed(body.get("messages", [])) if message["role"] == "user"), "")
                answer = user[::-1]
                if not body.get("stream"):
                    return self.send(200, {"id": "fixture-response", "model": body["model"],
                                           "choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                                           "usage": {"prompt_tokens": len(user.split()), "completion_tokens": len(answer.split())}})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for start in range(0, len(answer), 7):
                    value = {"model": body["model"], "choices": [{"delta": {"content": answer[start:start + 7]}, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            return self.send(404, {"error": "Unknown fixture route"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config["certificate"], config["private_key"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(json.dumps({"port": server.server_port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
