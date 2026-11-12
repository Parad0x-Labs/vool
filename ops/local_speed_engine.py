"""In-repo recording engine for the speed tracker. No model ever runs.

A minimal Ollama-API stand-in for `ops/speed_tracker.py`: it answers instantly with fixed
replies, logs every request byte-for-byte to a JSONL file, and keeps the same residency rules
Ollama keeps (one runner per model, keyed on num_ctx / num_gpu / num_thread; a different key
reloads; keep_alive absent means the server default). The frozen harness in the mission packet
stays the authority for the delivery measurements; this engine exists so the repo itself can
track per-release speed regressions model-free, in CI, with no secrets.

Usage: python ops/local_speed_engine.py <port> <log.jsonl>
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
LOG = sys.argv[2]
INSTALLED = ("qwen3:8b", "qwen3:4b", "nomic-embed-text:latest")
DEFAULT_KEEP_ALIVE_S = 300.0
REPLY = "Tracked reply number"
LOCK = threading.Lock()
COUNTER = [0]
RESIDENT: dict[str, dict] = {}


def _log(entry: dict) -> None:
    with LOCK, open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _keep_alive_seconds(value) -> float:
    if value is None or value == "":
        return DEFAULT_KEEP_ALIVE_S
    if isinstance(value, (int, float)):
        return float("inf") if value < 0 else float(value)
    text = str(value).strip().lower()
    if text.startswith("-"):
        return float("inf")
    if text.endswith("ms"):
        return float(text[:-2] or 0) * 0.001
    if text.endswith("m"):
        return float(text[:-1] or 0) * 60
    if text.endswith("h"):
        return float(text[:-1] or 0) * 3600
    if text.endswith("s"):
        return float(text[:-1] or 0)
    try:
        return float(text)
    except ValueError:
        return DEFAULT_KEEP_ALIVE_S


def _runner_key(body: dict) -> list:
    options = body.get("options") or {}
    return [body.get("model"), options.get("num_ctx"), options.get("num_gpu"), options.get("num_thread")]


def _book(body: dict, now: float) -> dict:
    model = str(body.get("model") or "")
    key = _runner_key(body)
    with LOCK:
        for name in [n for n, row in RESIDENT.items() if row["until"] <= now]:
            del RESIDENT[name]
        current = RESIDENT.get(model)
        if current is None:
            state = "cold"
        elif current["key"] != key:
            state = "reload"
        else:
            state = "warm"
        keep_alive = _keep_alive_seconds(body.get("keep_alive"))
        if keep_alive <= 0:
            RESIDENT.pop(model, None)
        else:
            RESIDENT[model] = {"key": key, "until": now + keep_alive}
        resident = sorted(RESIDENT)
    return {"residency": state, "runner_key": key, "resident_after": resident}


def _probe_reply(body: dict):
    """Answers the runtime's own sealed tool-certification probe so a lane can be certified."""
    names = {((tool.get("function") or {}).get("name") or "") for tool in body.get("tools") or []}
    messages = body.get("messages") or []
    first = str((messages[:1] or [{}])[0].get("content") or "")
    if not (any(name.startswith("vool_probe_") for name in names) or "sealed local diagnostic" in first):
        return None
    if {"vool_probe_add", "vool_probe_lookup_nonce"} <= names:
        return "", [
            {"id": "c1", "function": {"name": "vool_probe_add", "arguments": {"left": 19, "right": 23}}},
            {"id": "c2", "function": {"name": "vool_probe_lookup_nonce", "arguments": {"key": "alpha"}}},
        ]
    if "vool_probe_echo" in names:
        token = "recovered" if "required_token" in str(messages[-1].get("content")) else "repair-me"
        return "", [{"id": "c3", "function": {"name": "vool_probe_echo", "arguments": {"token": token}}}]
    nonce = ""
    for message in messages:
        if message.get("role") == "tool" and "nonce" in str(message.get("content")):
            with LOCK:
                pass
            try:
                nonce = json.loads(message["content"]).get("nonce", "")
            except (TypeError, ValueError):
                nonce = ""
    return f"The sum is 42 and the nonce is {nonce}.", []


def _reply_text(body: dict) -> str:
    fmt = body.get("format")
    if isinstance(fmt, dict):
        return json.dumps(_minimal(fmt))
    if fmt == "json":
        return "{}"
    with LOCK:
        COUNTER[0] += 1
        number = COUNTER[0]
    return f"{REPLY} {number}."


def _minimal(schema: dict) -> dict:
    if not isinstance(schema, dict):
        return {}
    defs = schema.get("$defs") or schema.get("definitions") or {}
    if "$ref" in schema:
        return _minimal(defs.get(schema["$ref"].split("/")[-1], {}))
    for combo in ("anyOf", "oneOf", "allOf"):
        if schema.get(combo):
            return _minimal(schema[combo][0])
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind in ("object", None) or "properties" in schema:
        props = schema.get("properties") or {}
        return {name: _minimal(props.get(name, {})) for name in schema.get("required", [])}
    if kind == "array":
        return [_minimal(schema.get("items") or {}) for _ in range(int(schema.get("minItems") or 0))]
    if kind == "string":
        return "x" * int(schema.get("minLength") or 0)
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def _json(self, code: int, obj) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _log({"t": time.time(), "method": "GET", "path": self.path})
        models = [{"name": m, "model": m, "size": 5_000_000_000,
                   "details": {"family": "qwen3", "families": ["qwen3"]}} for m in INSTALLED]
        if self.path.startswith("/api/tags"):
            return self._json(200, {"models": models})
        if self.path.startswith("/api/ps"):
            with LOCK:
                names = sorted(RESIDENT)
            return self._json(200, {"models": [m for m in models if m["name"] in names]})
        if self.path.startswith("/api/version"):
            return self._json(200, {"version": "0.12.0"})
        return self._json(200, {})

    def do_POST(self):
        t_in = time.time()
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
        except Exception:
            body = {"_raw": raw.decode("utf-8", "replace")}
        entry = {"t": t_in, "method": "POST", "path": self.path, "bytes": len(raw), "body": body}
        if self.path.startswith("/api/chat"):
            entry.update(_book(body, t_in))
        _log(entry)
        if self.path.startswith("/api/embed"):
            count = len(body.get("input") or [""]) if isinstance(body.get("input"), list) else 1
            return self._json(200, {"embeddings": [[0.01] * 768] * count})
        if not self.path.startswith("/api/chat"):
            return self._json(200, {})
        probe = _probe_reply(body)
        message = {"role": "assistant"}
        if probe is not None:
            content, tool_calls = probe
            message["content"] = content
            if tool_calls:
                message["tool_calls"] = tool_calls
        else:
            message["content"] = _reply_text(body)
        done = {"done": True, "done_reason": "stop", "prompt_eval_count": 0, "eval_count": 0,
                "load_duration": 0}
        if body.get("stream", True):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for obj in ({"model": body.get("model"), "message": message, "done": False},
                        {"model": body.get("model"), "message": {"role": "assistant", "content": ""}, **done}):
                chunk = (json.dumps(obj) + "\n").encode()
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        return self._json(200, {"model": body.get("model"), "message": message, **done})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
