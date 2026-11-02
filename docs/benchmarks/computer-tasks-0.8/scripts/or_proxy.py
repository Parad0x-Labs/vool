#!/usr/bin/env python3
"""Cloud-arm proxy: an OpenAI-compatible endpoint on 127.0.0.1 that forwards to OpenRouter.

Rivals talk to http://127.0.0.1:<port>/v1 with a dummy key and stay credential-blind and network-denied (the sandbox
still allows only localhost). This parent-side process reads the OpenRouter key at call time from the Keychain item the
official memory harness used (service "openrouter", account "api-key"), never logs or writes it, and:
  - forces model z-ai/glm-5.3-flashx, temperature 0, reasoning {"effort": "low", "exclude": true} (the official
    head-to-head reader settings) and asks OpenRouter to report usage/cost;
  - records every response's usage and billed cost to or_ledger.jsonl (no prompts, no key);
  - refuses new calls once the recorded spend reaches the cap (default $6).
GET /v1/models lists the one model with its context length (Hermes checks it).
usage: python3 -I or_proxy.py --port 18765 --cap 6.0 --ledger or_ledger.jsonl
"""
import argparse, json, subprocess, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "z-ai/glm-5.3-flashx"
URL = "https://openrouter.ai/api/v1/chat/completions"
LOCK = threading.Lock()
STATE = {"spent": 0.0, "calls": 0}


def key():
    r = subprocess.run(["security", "find-generic-password", <keychain-item-args>, "-w"], capture_output=True, text=True)
    k = r.stdout.strip()
    if r.returncode or not k:
        raise RuntimeError("OpenRouter credential unavailable")
    return k


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "context_length": 200000,
                                                                "max_model_len": 200000, "owned_by": "openrouter"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "only chat/completions"}})
        with LOCK:
            if STATE["spent"] >= ARGS.cap:
                return self._json(429, {"error": {"message": "h2h cloud arm spend cap reached", "code": 429}})
        requested = body.get("model")
        body["model"] = MODEL
        body["temperature"] = 0
        body.pop("top_p", None)
        body.pop("reasoning_effort", None)  # Hermes sends it top-level; OpenRouter 400s when it differs from reasoning.effort
        body["reasoning"] = {"effort": "low", "exclude": True}
        body["usage"] = {"include": True}
        stream = bool(body.get("stream"))
        if stream:
            body["stream_options"] = {"include_usage": True}
        req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json",
                                     "Authorization": "Bearer " + key(), "X-Title": "vool-h2h-computer"})
        t0 = time.time(); usage = {}; status = 0
        try:
            resp = urllib.request.urlopen(req, timeout=290)
            status = resp.status
            self.send_response(status)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
            if stream:
                self.send_header("Cache-Control", "no-cache"); self.end_headers()
                for line in resp:
                    self.wfile.write(line); self.wfile.flush()
                    s = line.decode("utf-8", "replace").strip()
                    if s.startswith("data:") and '"usage"' in s:
                        try:
                            u = json.loads(s[5:]).get("usage")
                            if u: usage = u
                        except Exception:
                            pass
            else:
                data = resp.read()
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
                try:
                    usage = json.loads(data).get("usage") or {}
                except Exception:
                    pass
        except urllib.error.HTTPError as e:
            status = e.code
            data = e.read()
            self.send_response(e.code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        except Exception as e:
            status = -1
            try:
                self._json(502, {"error": {"message": "proxy upstream error: " + type(e).__name__}})
            except Exception:
                pass
        cost = float(usage.get("cost") or 0)
        with LOCK:
            STATE["spent"] += cost; STATE["calls"] += 1
            row = {"t": round(t0, 3), "secs": round(time.time() - t0, 2), "status": status, "stream": stream,
                   "requested_model": requested, "client": self.headers.get("X-H2H-System") or ARGS.tag,
                   "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                   "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                   "cost": cost, "spent_total": round(STATE["spent"], 6)}
            with open(ARGS.ledger, "a") as f:
                f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18765); ap.add_argument("--cap", type=float, default=6.0)
    ap.add_argument("--ledger", default="or_ledger.jsonl"); ap.add_argument("--tag", default="")
    ARGS = ap.parse_args()
    try:
        for l in open(ARGS.ledger):
            STATE["spent"] += float(json.loads(l).get("cost") or 0)
    except FileNotFoundError:
        pass
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), H).serve_forever()
