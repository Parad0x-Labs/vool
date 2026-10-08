"""What a model agent's turn sends to /api/chat: the wire contract the server relies on.

A real HTTP server records the exact body the runner posts. The body must name the agent as the turn's author
(so the server keeps agent turns out of the owner's profile and preferences), carry the team's folder, the
agent's own session and model, and PLAN mode for read agents.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.agent_team.model_agent import HttpChatRunner


def test_agent_turn_names_its_author_folder_session_and_mode():
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            lines = [
                json.dumps({"message": {"content": 'RESULT: {"status": "done", "summary": "ok", "changed": []}'}}),
                json.dumps({"done": True}),
            ]
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runner = HttpChatRunner(f"http://127.0.0.1:{server.server_address[1]}")
        turn = runner.run(session_id="openclaw:" + "7" * 20, model="local-model:7b", prompt="Read a.py and report",
                          mode="read", turn_id="t-1", cancel=threading.Event(), on_response=lambda _r: None,
                          workspace="/srv/team-folder")
    finally:
        server.shutdown()
        server.server_close()
    assert len(seen) == 1
    body = seen[0]
    assert body["turn_author"] == "agent"
    assert body["workspace"] == "/srv/team-folder"
    assert body["session_id"] == "openclaw:" + "7" * 20 and body["model"] == "local-model:7b"
    assert body["mode"] == "plan" and body["model_selection"] == "sticky"
    assert turn.text.startswith("RESULT:")


def test_the_report_is_read_with_or_without_its_label_but_never_from_a_withheld_bullet():
    from core.agent_team.model_agent import parse_result_line

    assert parse_result_line('RESULT: {"status": "done", "summary": "x"}\nmore')["summary"] == "x"
    # VOOL's reply shaping can drop the label and keep the object (served run, 2026-10-07)
    assert parse_result_line('{"status": "done", "summary": "y", "changed": []}\nChecked it.')["summary"] == "y"
    withheld = ("Step 3: load() parses the path aliases at line 3.\n\nWithheld from this answer: 2 statements that "
                'the sources retrieved for this turn do not support.\n\n- {"status": "done", "summary": "z"}')
    assert parse_result_line(withheld) == {}
    assert parse_result_line('a {"status": "done"} inline') == {}
