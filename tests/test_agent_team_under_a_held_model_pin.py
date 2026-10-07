"""An agent turn while a council owns the global model pin is refused, and the agent says why.

Agents never touch the global pin: each one's model rides its own chat session. But while a council owns
the pin, every ordinary /api/chat turn is refused before a model is selected (core/council/pin_lock.py), so
an agent cannot run then. It must fail closed: no model called for it, and the agent ends refused with the
council named, not "running" and not an opaque transport fault ("HTTPError: HTTP Error 409").

The agent's turn goes through the app's real request handler (core.web.api.service.dispatch_post) behind a
loopback HTTP server, so the fence that answers it is the server's own.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX")

LIMITS = {"max_usd": 1.0, "max_tokens": 20_000, "max_calls": 3, "wall_clock_seconds": 60}


@pytest.fixture
def served_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core.council import pin_lock

    pin_lock.reset_on_startup()
    calls: list[str] = []

    def _agent(runtime, user_text, *, session_id, **_):  # would mean a model was selected
        calls.append(session_id)
        return {"response": "RESULT: {\"status\": \"done\", \"summary\": \"ran\"}", "confidence": 1.0}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            res = dispatch_post(path=self.path, body=body, headers={"content-type": "application/json"},
                                runtime=RuntimeServices(display_name="N"), model_name="vool",
                                workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1",
                                run_agent_provider=_agent)
            self.send_response(res.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(res.body or b"")

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()
    pin_lock.reset_on_startup()


def test_an_agent_turn_under_a_held_pin_is_refused_and_names_the_council(served_handler, tmp_path):
    from core.agent_team.coordinator import TeamCoordinator
    from core.agent_team.model_agent import HttpChatRunner
    from core.council import pin_lock

    base, calls = served_handler
    ws = tmp_path / "ws"
    ws.mkdir()
    pin_lock.acquire("run-held-pin")
    team = TeamCoordinator(tmp_path / "team", workspace=ws, team_limits=LIMITS, tick_seconds=0.1,
                           model_runner=HttpChatRunner(base), chat_text="review the folder")
    try:
        team.start([{"key": "r", "objective": "Folder review", "importance": "normal", "kind": "model",
                     "mode": "read", "model": "local-model:7b", "prompt": "Review the folder.", "limits": LIMITS}])
        team.run_until_ended(60)
        agents = team.status()["agents"]
    finally:
        team.close()
    assert calls == [], "a model was called while a council owned the pin"
    assert len(agents) == 1
    state = str(agents[0].get("state"))
    text = json.dumps(agents[0])
    assert state not in ("running", "done"), agents[0]
    assert "council" in text.lower(), agents[0]
