"""Time streamed "Hi" turns against an isolated daemon: cold, with client history, with persisted dialogue.

Usage: python tools/profile_chat_turn_stream.py <repo-root> <scratch-home> [seed]

Prints the arrival offset of every stream event relative to the request start, the Proof Chip
read for each turn, and the three page-boot routes. "seed" plants the served startup test's
existing-history fixture first. The greeting is the production no-model fast path; the scripted
loopback provider is registered only so the daemon boots with a local lane and is never asked.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, sys.argv[1])           # worktree root
from tests import _reader_served_rig as rig
from tests.test_chat_startup_served import _seed_history

work = Path(sys.argv[2])
if work.exists():
    shutil.rmtree(work)

with rig.CapturingProvider(default="Hello! How can I help you today?") as provider:
    daemon = rig.ServedDaemon(work, provider=provider, env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
    daemon.start(timeout=120)
    try:
        if len(sys.argv) > 3 and sys.argv[3] == "seed":
            _seed_history(daemon.home)
        session = rig.canonical_session("timing-drive")
        client_history = [
                {"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hey."},
                {"role": "user", "content": "Write one friendly sentence about a harbor lantern."},
                {"role": "assistant", "content": "Hello! How can I help you today?"}]
        for label, history in (("hi_cold", []), ("hi_with_client_history", client_history),
                               ("hi_with_persisted_dialogue", client_history)):
            body = json.dumps({"messages": [*history, {"role": "user", "content": "Hi"}], "stream": True,
                               "stream_task_events": True, "session_id": session, "model": "vool",
                               "model_selection": "auto", "mode": "manual"}).encode()
            req = Request(daemon.base_url + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
            t0 = time.monotonic()
            events = []
            request_id = ""
            with urlopen(req, timeout=120) as resp:
                first = None
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    at = round(time.monotonic() - t0, 3)
                    if first is None:
                        first = at
                    kind = line[:60]
                    if line.startswith("data:") or line.startswith("{"):
                        try:
                            payload = json.loads(line[5:].strip() if line.startswith("data:") else line)
                            ev = payload.get("vool_event") if isinstance(payload.get("vool_event"), dict) else {}
                            kind = ev.get("type") or payload.get("type") or payload.get("event") or ",".join(sorted(payload.keys()))[:60]
                            def _find(o):
                                if isinstance(o, dict):
                                    for k, v in o.items():
                                        if k == "request_id" and isinstance(v, str) and v:
                                            return v
                                        found = _find(v)
                                        if found:
                                            return found
                                elif isinstance(o, list):
                                    for v in o:
                                        found = _find(v)
                                        if found:
                                            return found
                                return ""
                            request_id = _find(payload) or request_id
                        except Exception:
                            pass
                    events.append((at, kind))
            total = round(time.monotonic() - t0, 3)
            print(json.dumps({"turn": label, "first_byte_s": first, "total_s": total, "events": len(events)}))
            for at, kind in events:
                print(f"    {at:7.3f}  {kind}")
            if request_id:
                for attempt in ("first", "second"):
                    t1 = time.monotonic()
                    with urlopen(f"{daemon.base_url}/api/chat/proof?session={session}&request_id={request_id}", timeout=120) as r:
                        r.read()
                    print(json.dumps({"proof_read": attempt, "seconds": round(time.monotonic() - t1, 3)}))
            else:
                print(json.dumps({"proof_read": "skipped", "reason": "no request_id in stream"}))
        for route in ("/api/runtime/sessions", "/api/chat/sessions", "/api/chat/history?session=" + session):
            t1 = time.monotonic()
            with urlopen(daemon.base_url + route, timeout=120) as r:
                r.read()
            print(json.dumps({"route": route.split("?")[0], "seconds": round(time.monotonic() - t1, 3)}))
    finally:
        daemon.stop()
