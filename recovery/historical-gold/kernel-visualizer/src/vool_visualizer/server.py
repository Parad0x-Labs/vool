"""Standalone local server for the VOOL trace visualizer (stdlib only).

Endpoints:
  GET  /                     UI (index.html)
  GET  /api/turns            all turns (projected, no full transcript)
  GET  /api/turn/<n>         one turn incl. graph (nodes/edges/layers) + transcript
  GET  /api/events           tail of the live event side-channel (if configured)
  GET  /api/health           quick status

Run:
  python -m vool_visualizer.server --journal <repl_sessions.jsonl>
    [--events <vis_events.jsonl>] [--port 8123]
  --demo uses the sample journal bundled in round0_raw_evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vool_visualizer import graph as graphmod
from vool_visualizer.observer import load_journal, read_live_events, turns_to_json

_HERE = Path(__file__).resolve().parent
_STATIC = _HERE / "static"

_DEMO_JOURNAL = Path("review-bus/dm-delta-review-20260821/round0_raw_evidence/hardmix_repl_sessions.jsonl")


class _State:
    """Thread-safe holder for journal path + cached turns."""
    def __init__(self, journal, events):
        self.journal_path = Path(journal)
        self.events_path = Path(events) if events else None
        self._turns = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        self._turns = load_journal(self.journal_path)

    def refresh(self):
        with self._lock:
            self._load()

    def turns(self):
        with self._lock:
            return list(self._turns)

    def turn(self, n):
        with self._lock:
            return next((t for t in self._turns if t.turn_index == n), None)

    def events(self):
        if not self.events_path:
            return []
        return read_live_events(self.events_path)


class Handler(BaseHTTPRequestHandler):
    state: _State = None  # set at server start

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, obj)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/" or path == "/index.html":
                self._file(_STATIC / "index.html")
            elif path == "/lab" or path == "/lab.html":
                self._file(_STATIC / "lab.html")
            elif path.startswith("/static/"):
                self._file(_STATIC / path[len("/static/"):])
            elif path == "/api/state":
                self.state.refresh()
                turns = self.state.turns()
                self._json(200, {
                    "journal": str(self.state.journal_path),
                    "events": str(self.state.events_path) if self.state.events_path else None,
                    "turns": turns_to_json(turns),
                })
            elif path.startswith("/api/turn/"):
                n = int(path.rsplit("/", 1)[1])
                t = self.state.turn(n)
                if t is None:
                    self._json(404, {"error": "turn not found"})
                    return
                g = graphmod.build_graph(t)
                self._json(200, {
                    "turn_index": t.turn_index,
                    "question": t.question,
                    "answer": t.answer,
                    "stamps": t.stamps,
                    "commit": t.commit,
                    "crashed": t.crashed,
                    "crash_exception": t.crash_exception,
                    "terminal_state": [
                        {"id": o.id, "lane": o.lane, "status": o.status,
                         "status_label": o.label} for o in t.terminal_state],
                    "graph": {
                        "nodes": [{"id": nd.id, "label": nd.label, "layer": nd.layer,
                                   "kind": nd.kind, "ok": nd.ok, "detail": nd.detail}
                                  for nd in g.nodes],
                        "edges": [{"src": e.src, "dst": e.dst} for e in g.edges],
                        "layers": graphmod.LAYERS,
                    },
                    "transcript": t.transcript,
                })
            elif path == "/api/events":
                self._json(200, {"events": self.state.events()})
            elif path == "/api/health":
                self._json(200, {"ok": True, "ts": time.strftime("%H:%M:%S")})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001 - http handler must not 500 silently
            try:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass

    def _file(self, p: Path):
        if not p.exists():
            self._json(404, {"error": f"missing {p}"})
            return
        ctype = "text/html; charset=utf-8" if p.suffix == ".html" else \
                ("text/css" if p.suffix == ".css" else "text/javascript")
        self._send(200, p.read_bytes(), ctype=ctype)

    def log_message(self, fmt, *args):  # quieter
        pass


def _serve(state, port, host="127.0.0.1"):
    Handler.state = state
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        # Address already in use — a stale vool server is hogging the port.
        # Kill it (only our own process) and rebind so the app is never blank.
        if exc.errno == 48 or "in use" in str(exc).lower():
            print(f"[vool-vis] port {port} busy — killing stale vool server and retrying", flush=True)
            _kill_own_servers(port)
            time.sleep(.6)
            srv = ThreadingHTTPServer((host, port), Handler)
        else:
            raise
    print(f"[vool-vis] serving on http://{host}:{port}  journal={state.journal_path}", flush=True)
    print(f"[vool-vis] open the lab at http://{host}:{port}/lab  (or / for the 2D DAG)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def _kill_own_servers(port: int) -> None:
    """Best-effort kill of stale `vool_visualizer.server` processes on a port."""
    import signal
    try:
        import subprocess
        out = subprocess.run(["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout.strip()
        for pid in out.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                continue
        time.sleep(.4)
    except Exception:
        pass  # best-effort; server may already be gone


def _resolve_journal(args) -> tuple[str, str]:
    """Return (journal_path, source_label). Priority: --journal > --demo >
    $NULLA_HOME > the most recent journals/*/repl_sessions.jsonl > the demo."""
    candidates = []

    if args.journal:
        candidates.append((args.journal, "--journal"))
    elif args.demo:
        candidates.append((str(_DEMO_JOURNAL), "demo"))

    # always append discovery so the app is never left empty by a bad --journal
    if not args.demo and (not args.journal or not Path(args.journal).exists()):
        journals_dir = Path.home() / "nulla" / "journals"
        if journals_dir.is_dir():
            for p in sorted(journals_dir.glob("*/repl_sessions.jsonl"),
                            key=lambda x: x.stat().st_mtime, reverse=True):
                candidates.append((str(p), f"journals/{p.parent.name}"))
        candidates.append((str(_DEMO_JOURNAL), "demo"))
        home = os.environ.get("NULLA_HOME", "/tmp/vool-kernel-repl-home")
        candidates.append((str(Path(home) / "repl_sessions.jsonl"), "$NULLA_HOME"))

    found = None
    for path, source in candidates:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            found = (path, source)
            break
        if source == "--journal" and path:
            print(f"[vool-vis] WARN: --journal not found, using auto-discovery: {path}")
    if found is None:
        raise SystemExit("no journal found; pass --journal <path> or --demo")
    return found


def main() -> None:
    ap = argparse.ArgumentParser(prog="vool_visualizer.server")
    ap.add_argument("--journal", help="path to repl_sessions.jsonl")
    ap.add_argument("--events", help="optional live event file (env-gated writer)")
    ap.add_argument("--demo", action="store_true",
                    help="use the bundled demo journal (round0_raw_evidence)")
    ap.add_argument("--port", type=int, default=8123)
    args = ap.parse_args()

    journal, source = _resolve_journal(args)
    state = _State(journal, args.events)
    turns = len(state.turns())
    if turns == 0:
        print(f"[vool-vis] WARN: journal has 0 turns: {journal}", flush=True)
    print(f"[vool-vis] journal source: {source}", flush=True)
    print(f"[vool-vis] loaded {turns} turns from {journal}", flush=True)
    _serve(state, args.port)


if __name__ == "__main__":
    main()
