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
    # The model here is an HTTP stand-in on loopback, so the load gate would size "qwen3:8b" against
    # this machine's real free RAM and refuse it on a busy machine. The gate has its own tests; these
    # turns must not depend on how much memory the test machine has free.
    from core import resource_governor

    monkeypatch.setattr(
        resource_governor, "plan_model_load", lambda *_a, **_k: resource_governor.GovernorDecision(ok=True)
    )
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
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "0")
    result = agent.run_once("how much do i owe the plumber?", source_context={"surface": "cli", "platform": "cli"})
    assert result.get("route_reason") != "clean_local_loop"
    assert "clean_local_loop" not in result


def test_a_current_sounding_ask_is_published_from_the_loops_own_reads(turn_setup, monkeypatch):
    # The Mac audit's F18/F46: "from this week" / "this month" opened the grounding gate and the
    # grounded answer was withheld as unsupported. (This fixture does not open that gate; the test
    # pins that the turn still publishes from the loop and that its reads are recorded.)
    agent, fake, _home = turn_setup
    fake.replies[:] = [
        '{"tool": "list_dir", "path": "Downloads"}',
        '{"answer": "This week your Downloads got plumber_invoice.txt."}',
    ]
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    result = agent.run_once(
        "any new photos in downloads from this week?", source_context={"surface": "cli", "platform": "cli"}
    )
    assert result.get("route_reason") == "clean_local_loop", result.get("route_reason")
    assert "plumber_invoice.txt" in result["response"]
    assert "can't publish" not in result["response"]
    from core.grounding_lifecycle import lifecycle_for_context

    lifecycle = lifecycle_for_context({"session_id": result.get("session_id"), "turn_id": result.get("turn_id")})
    if lifecycle is not None:
        assert any("plumber_invoice.txt" in str(row.get("summary")) for row in lifecycle.typed_observations)


def _world_turn(agent, fake, monkeypatch, replies, ask):
    from core.clean_loop import hook

    fake.replies[:] = replies
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    hook._EVIDENCE.clear()
    hook.route._RECENT.clear()
    return agent.run_once(ask, source_context={"surface": "cli", "platform": "cli"})


@pytest.mark.parametrize(
    "replies",
    [
        ['{"answer": "PDF forms are documents with fillable fields."}'],  # no tool call at all
        ['{"tool": "find", "query": "zzqq"}', '{"answer": "PDF forms have fillable fields."}'],  # empty find, no absence wording
        ['{"tool": "find", "query": "plumber"}', '{"answer": "PDF forms have fillable fields."}'],  # one unrelated file
    ],
)
def test_a_world_ask_without_related_tool_evidence_is_handed_back_to_the_turn(turn_setup, monkeypatch, replies):
    """Harness (stubbed model, real tools and agent): admitted on the word "pdf", the tools return nothing related."""
    agent, fake, _home = turn_setup
    result = _world_turn(agent, fake, monkeypatch, replies, "how do pdf forms work")
    assert result.get("route_reason") != "clean_local_loop"
    assert "clean_local_loop" not in result


def test_a_world_ask_the_tools_do_answer_stays_with_the_loop(turn_setup, monkeypatch):
    agent, fake, _home = turn_setup
    replies = [
        '{"tool": "find", "query": "plumber"}',
        '{"tool": "read_file", "path": "Downloads/plumber_invoice.txt"}',
        '{"answer": "The plumber invoice says EUR 140.00 is due."}',
    ]
    result = _world_turn(agent, fake, monkeypatch, replies, "what total is on the plumber invoice")
    assert result.get("route_reason") == "clean_local_loop", result.get("route_reason")
    assert "140.00" in result["response"]


def test_a_follow_up_to_a_found_nothing_turn_is_not_a_follow_up(turn_setup, monkeypatch):
    from core.clean_loop import hook, route

    agent, fake, _home = turn_setup
    _world_turn(agent, fake, monkeypatch, ['{"tool": "find", "query": "zzqq"}', '{"answer": "I didn\'t find a mortgage pdf in your Documents."}'], "is my mortgage pdf in documents")
    assert not route._is_follow_up("and the one before?", "")  # no session id: sanity
    assert not hook._EVIDENCE


def test_an_explicitly_own_ask_keeps_its_found_nothing_answer(turn_setup, monkeypatch):
    agent, fake, _home = turn_setup
    fake.replies[:] = [
        '{"tool": "find", "query": "mortgage"}',
        '{"answer": "I didn\'t find a mortgage pdf in your Documents. Is it under another name?"}',
    ]
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    result = agent.run_once("is my mortgage pdf in documents", source_context={"surface": "cli", "platform": "cli"})
    assert result.get("route_reason") == "clean_local_loop", result.get("route_reason")
    assert "mortgage" in result["response"]
