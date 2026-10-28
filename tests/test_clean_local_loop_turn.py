"""One whole turn on the clean local path: the real agent, the real router seam and adapter, the real
tools over a real home folder, and an owned HTTP fixture standing in for Ollama's /api/chat.

What it pins: with the flag on and a ready local model, a files ask is answered by the loop (route
reason `clean_local_loop`) from tool evidence, every model call goes out at temperature 0 with
thinking off, and the tools that ran are on the result. With the flag off the same turn never
reaches the loop.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import final_answer_authorship as authorship

MODEL = "qwen3:8b"


class _FakeOllama:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.chats: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _send(self, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    self._send({"models": [{"name": MODEL, "details": {"families": ["qwen3"]}}]})
                elif self.path.startswith("/api/ps"):
                    self._send({"models": []})
                else:
                    self._send({"data": [{"id": MODEL}]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self.path.startswith("/api/chat") or len(body.get("messages") or []) < 2:
                    self._send({"model": MODEL, "message": {"role": "assistant", "content": " "}, "done": True})
                    return
                owner.chats.append(body)
                index = min(len(owner.chats), len(owner.replies)) - 1
                self._send(
                    {
                        "model": MODEL,
                        "message": {"role": "assistant", "content": owner.replies[index]},
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 50,
                        "eval_count": 20,
                    }
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def turn_setup(tmp_path, monkeypatch, make_agent):
    home = (tmp_path / "home").resolve()
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    (home / "Downloads" / "plumber_invoice.txt").write_text("Plumber invoice\nTotal due: EUR 140.00\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    fake = _FakeOllama(
        [
            '{"tool": "find", "query": "plumber"}',
            '{"tool": "read_file", "path": "Downloads/plumber_invoice.txt"}',
            '{"answer": "You owe the plumber EUR 140.00, from the invoice in your Downloads."}',
        ]
    )
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", fake.url)
    monkeypatch.setenv("VOOL_CLEAN_LOOP_MODEL", MODEL)

    def certified(manifest, **_kwargs):
        ready = str(getattr(manifest, "model_name", "")) == MODEL
        return authorship.AuthorCertification(
            certified=ready,
            state="verified" if ready else "unknown",
            source=authorship.CERTIFICATION_SOURCE_MEASURED,
            provider_id=getattr(manifest, "provider_id", ""),
        )

    monkeypatch.setattr(authorship, "author_certification", certified)
    agent = make_agent()
    from core.runtime_provider_defaults import register_installed_local_model

    manifest = register_installed_local_model(agent.memory_router.registry, model_tag=MODEL, env={"VOOL_RAW_OLLAMA_API_URL": fake.url})
    assert manifest is not None
    yield agent, fake, home
    fake.close()


def test_a_files_ask_is_answered_by_the_clean_loop_from_tool_evidence(turn_setup, monkeypatch):
    agent, fake, _home = turn_setup
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    result = agent.run_once("how much do i owe the plumber?", source_context={"surface": "cli", "platform": "cli"})
    assert result.get("route_reason") == "clean_local_loop", result.get("route_reason")
    assert "140.00" in result["response"]
    assert len(fake.chats) == 3
    for chat in fake.chats:
        assert chat["options"]["temperature"] == 0
        assert chat.get("think") is False
        assert chat["messages"][0]["role"] == "system"
    assert fake.chats[0]["messages"][0]["content"] == fake.chats[2]["messages"][0]["content"]
    assert "140.00" in fake.chats[2]["messages"][-1]["content"]
    trace = result["clean_local_loop"]
    assert trace["tools_used"] == ["find", "read_file"] and trace["model_id"] == MODEL
    assert {r["tool_name"] for r in result["tool_receipts"]} == {"machine.find_file", "machine.read_file"}


def test_with_the_flag_off_the_turn_never_reaches_the_loop(turn_setup, monkeypatch):
    agent, fake, _home = turn_setup
    monkeypatch.delenv("VOOL_CLEAN_LOCAL_LOOP", raising=False)
    result = agent.run_once("how much do i owe the plumber?", source_context={"surface": "cli", "platform": "cli"})
    assert result.get("route_reason") != "clean_local_loop"
    assert "clean_local_loop" not in result
