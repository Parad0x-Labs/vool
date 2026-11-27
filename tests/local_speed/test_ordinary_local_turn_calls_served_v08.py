"""What one ordinary local turn asks Ollama, counted on the wire of a real served turn.

Real daemon (`apps.vool_api_server`) in its own home, real `/api/chat` door, and a recording Ollama that answers the
runtime's own questions in their own shapes and counts what a real server would load: a post whose runner keys
(num_ctx, num_thread, num_gpu) differ from the model's resident runner is a reload (Ollama, server/sched.go).

Measured on b304cabd with this rig, three ordinary questions (EN, LT, DE), the chat pinned to qwen3:8b: seven model
posts a turn, the done-claim question asked twice with the same reply (once in the seal, once at the buffered door),
and six to nine embedding posts a turn with no keep_alive while every chat post held 30 minutes, so the embedding model was
dropped to Ollama's 5-minute default after every message. The owner's 24 GB iMac (2026-11-27) showed the cost: about
ten seconds a judgement, and the embedding model reloaded 13 times in a day.

What is pinned here holds in any wording: every model has one runner across every lane, every post of a turn holds one
residency, no judgement is asked twice in a turn, and an ordinary turn asks at most five judgements around its answer.
A stub answering is not a claim about any model's quality; it proves what the runtime sends.
"""
from __future__ import annotations

import json
import tempfile
import textwrap
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests._authorship_served_rig import REPO_ROOT, ServedDaemon, free_port, seed_in_home

CHAT_MODEL = "qwen3:8b"
INSTALLED = ["qwen3:8b", "qwen3:0.6b", "nomic-embed-text:latest"]
ANSWER = "Hazelnuts keep best in a cool, dark, airtight jar."
TURNS = [
    ("en", "How long do shelled hazelnuts usually stay fresh in a pantry?"),
    ("lt", "Kiek laiko lukštenti lazdyno riešutai paprastai išlieka švieži sandėliuke?"),
    ("de", "Wie lange bleiben geschälte Haselnüsse in der Speisekammer normalerweise frisch?"),
]
#: The judgements one ordinary question gets around its answer on this rig: the tool ask, the memory question beside
#: it, the entity check before a knowledge answer, the web question (Web lookups are off here, and the answer's own
#: out-of-date check reads it), and the done-claim reading of the reply.
MAX_JUDGEMENTS_PER_TURN = 5


class RecordingOllama:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.resident: dict[str, tuple] = {}
        self.lock = threading.Lock()
        self.port = free_port()
        rig = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                return

            def _send(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": n, "model": n, "size": 5_000_000_000} for n in INSTALLED]})
                if self.path.startswith("/api/ps"):
                    with rig.lock:
                        names = list(rig.resident)
                    return self._send({"models": [{"name": n, "size": 5_000_000_000, "size_vram": 5_000_000_000}
                                                  for n in names]})
                if self.path.startswith(("/v1/models", "/models")):
                    return self._send({"data": [{"id": n} for n in INSTALLED]})
                if self.path.startswith("/api/version"):
                    return self._send({"version": "0.12.0"})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
                except Exception:
                    body = {}
                if self.path.startswith("/api/show"):
                    return self._send({"capabilities": ["completion", "tools", "thinking"], "template": "t",
                                       "details": {"family": "qwen3", "quantization_level": "Q4_K_M"}})
                model = str(body.get("model") or "")
                options = body.get("options") if isinstance(body.get("options"), dict) else {}
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                text = "\n".join(str(m.get("content") or "") for m in messages)
                runner = (options.get("num_ctx"), options.get("num_thread"), options.get("num_gpu"))
                with rig.lock:
                    reload = bool(model) and rig.resident.get(model) not in (None, runner)
                    rig.resident[model] = runner
                    rig.calls.append({
                        "path": self.path, "model": model, "runner": runner, "reload": reload,
                        "keep_alive": body.get("keep_alive", "<none>"), "messages": len(messages),
                        "prompt": text[-4000:], "head": text[:200],
                    })
                if self.path.startswith("/api/embed"):
                    count = len(body.get("input")) if isinstance(body.get("input"), list) else 1
                    return self._send({"embeddings": [[0.01] * 768 for _ in range(count)]})
                return self._send({"model": model, "done": True, "done_reason": "stop",
                                   "message": {"role": "assistant", "content": rig.reply(text, body, messages)},
                                   "prompt_eval_count": 40, "eval_count": 30})

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def reply(text: str, body: dict[str, Any], messages: list[dict[str, Any]]) -> str:
        """The runtime's own questions answered in their own shapes; anything else is the answer."""
        if '"ambiguous": true or false' in text:
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        if "Reply with ONLY" in text or body.get("format") == "json":
            if '{"answer": [0]}' in text:
                return '{"answer": [0]}'
            if '{"answer": "none"}' in text:
                return '{"answer": "none"}'
            return '{"answer": "no"}'
        return ANSWER

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def take(self) -> list[dict[str, Any]]:
        with self.lock:
            calls, self.calls = self.calls, []
        return calls


_SEED = textwrap.dedent(
    """
    import os, sys
    sys.path.insert(0, {root!r})
    os.environ["OLLAMA_HOST"] = {base!r}
    from storage.migrations import run_migrations
    run_migrations()
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _ensure_local_ollama_provider
    from tests._authorship_certification import certify_for_authorship
    registry = ModelRegistry()
    for tag, role in (({chat!r}, "general"), ("qwen3:0.6b", "lightweight_utility")):
        _ensure_local_ollama_provider(registry, model_tag=tag, bundle_role=role, env=dict(os.environ))
        certify_for_authorship(registry.get_manifest("ollama-local", tag))
    print("seeded")
    """
)


@pytest.fixture(scope="module")
def served_turns() -> list[tuple[str, list[dict[str, Any]], str]]:
    ollama = RecordingOllama()
    home = Path(tempfile.mkdtemp(prefix="turn-calls-served-"))
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": ollama.base_url, "VOOL_OLLAMA_URL": ollama.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{ollama.base_url}/api/chat",
        "VOOL_INSTALLED_OLLAMA_MODELS": ",".join(INSTALLED), "VOOL_DISABLE_WEB": "1",
        "VOOL_MODEL_LOAD_FLOOR_GB": "0", "VOOL_CONTEXT_BUCKET": "C", "VOOL_AGENT_NAME": "Atlas",
        # The bundled packs the app ships, served as the product serves them (the suite points this at an empty tree).
        "VOOL_BUNDLED_PLUGINS_DIR": str(REPO_ROOT / "plugins"),
    })
    daemon.start(timeout=300)
    try:
        assert "seeded" in seed_in_home(home, _SEED.format(root=str(REPO_ROOT), base=ollama.base_url, chat=CHAT_MODEL))
        time.sleep(3.0)
        ollama.take()
        turns = []
        for language, text in TURNS:
            payload = daemon.chat(text, session_id=f"turn-calls-{language}", model=CHAT_MODEL, timeout=300)
            time.sleep(2.0)  # the post-answer lanes of the turn finish after the reply is sent
            turns.append((language, ollama.take(), str((payload.get("message") or {}).get("content") or "")))
        return turns
    finally:
        daemon.stop()
        ollama.server.shutdown()
        ollama.server.server_close()


def _model_posts(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in calls if c["path"].startswith(("/api/chat", "/api/generate", "/v1/chat"))]


def test_every_turn_is_answered_by_the_chat_model(served_turns) -> None:
    for language, calls, reply in served_turns:
        assert reply.strip() == ANSWER, (language, reply)
        assert any(c["model"] == CHAT_MODEL and c["messages"] > 2 for c in _model_posts(calls)), language


def test_every_model_has_one_runner_across_every_lane(served_turns) -> None:
    runners: dict[str, set[tuple]] = {}
    for _language, calls, _reply in served_turns:
        for call in _model_posts(calls):
            assert call["runner"][0] is not None, f"posted with no window: {call['model']} {call['prompt'][:80]!r}"
            runners.setdefault(call["model"], set()).add(call["runner"])
            assert not call["path"].startswith("/v1/"), "the OpenAI-compatible door takes no window"
    assert runners and all(len(keys) == 1 for keys in runners.values()), runners


def test_no_turn_reloads_a_model_it_already_loaded(served_turns) -> None:
    for language, calls, _reply in served_turns[1:]:
        assert not any(c["reload"] for c in calls), (language, [c for c in calls if c["reload"]])


def test_every_post_of_a_turn_holds_one_residency(served_turns) -> None:
    # Inside the suite the daemon's embedding lane falls back to its hash vectors before it posts (its log reads
    # "embedding backend unavailable"), so here this holds the chat lanes; the embedding post's own residency is pinned
    # in test_one_runner_per_local_model_v08. Run outside the suite, this rig counts six embeddings a turn.
    for language, calls, _reply in served_turns:
        held = Counter(str(c["keep_alive"]) for c in calls if c["path"].startswith(("/api/", "/v1/")))
        assert len(held) == 1, (language, held)


def test_an_ordinary_turn_asks_each_judgement_once_and_few_of_them(served_turns) -> None:
    from core.fact_extractor import FACT_EXTRACT_SYSTEM

    for language, calls, _reply in served_turns:
        # The post-turn memory extraction is the turn's write-back, batched by its own schedule, not a judgement.
        judgements = [
            c for c in _model_posts(calls)
            if c["messages"] <= 2 and not c["head"].startswith(FACT_EXTRACT_SYSTEM[:60])
        ]
        asked = Counter(c["prompt"] for c in judgements)
        assert all(count == 1 for count in asked.values()), (
            language, [prompt[:120] for prompt, count in asked.items() if count > 1])
        assert len(judgements) <= MAX_JUDGEMENTS_PER_TURN, (language, [c["prompt"][:90] for c in judgements])
