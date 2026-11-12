"""Auto recovers a transient provider fault in the SAME turn: the served journey, end to end.

One real daemon (``apps.vool_api_server``) in its own disposable VOOL_HOME, one faultable
Ollama-dialect loopback provider behind the real HTTP door, and the model certified through the
daemon's own probe. Then the user journey, on the real chat page's composer where it says so:

  S1  composer: one ordinary Auto question; the answer call gets a 503; the same turn shows the
      answer (two answer dispatches on the same model, a ``model.call_retrying`` receipt);
  S2  composer: a follow-up in the same chat works with exactly one answer dispatch;
  S3  the answer call's connection drops with no response at all: recovered in the same turn;
  S4  429 with Retry-After: the capped wait is honoured, then the same turn answers;
  S5  cancel during that wait: no answer dispatch after the cancel is acknowledged, and the same
      chat answers its next question;
  S6  the fault persists through the retry: one bounded retry, then the turn says it failed
      (no invented answer) inside its deadline, and the same chat answers its next question;
  S7  an auth failure (401) is not retried and is not answered for;
  S8  a healthy turn is one dispatch and no retry;
  S9  a paid endpoint is registered and reachable but never authorized: it receives zero
      requests across the whole journey, fault windows included.

Each step also reads the runtime's own verdict for the turn (``turn.trace_completed``: completed,
cancelled or failed) and its ``model.call_retrying`` receipts, so a page that looks right over a
wrong turn record fails.

The faults are scripted loopback statuses. This proves the recovery contract on the real served
path; it says nothing about any live provider's reliability or about answer quality. The same
turns were run against a real local model behind a fault proxy for the v0.8 line (evidence kept
outside the repository).
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, free_port, run_in_home

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"
PAID_MODEL = "paid-model-x"
PROVIDER_NAME = "ollama-local"
ANSWER = "The regional coordinators keep the swarm resilient."
# The call that writes the answer text is the persona-prompted chat turn. The splitter, labeller
# and memory calls the same chat also runs are not faulted, so a scripted fault always lands on the
# call that writes the answer.
ANSWER_LANE_MARKER = "You are VOOL"
SPLIT_LANE_MARKER = "You split a user's message into the separate requests"
# The splitter's wire format (core.agent_runtime.turn_planner_hook): one object, the plan inside.
SPLIT_WIRE_FORMAT = 'Wire format: return ONLY {"requests": [...]}'
# Where a run keeps its screenshots and call ledgers (optional): kept out of the repository.
EVIDENCE_ENV = "VOOL_SERVED_JOURNEY_EVIDENCE"


class FaultableProvider:
    """An Ollama-dialect loopback with a fault queue for the answer lane and a full call ledger.

    A queued fault is consumed by the next answer-lane generation call, before any reply, so a
    queue of [503] is exactly "the first answer call fails transiently, the next one works".
    The certification probe's tool choreography is the served rigs' own, so the daemon's sealed
    probe can certify this provider's model.
    """

    def __init__(self, table: dict[str, str]) -> None:
        self.table = dict(table)
        self.faults: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: Any) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/api/version"):
                    return self._send({"version": "0.40.16"})
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": name} for name in rig.table]})
                if self.path.startswith("/api/ps"):
                    return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in rig.table]})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                model = str(body.get("model") or "")
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                system = str(body.get("system") or "")
                prompt = " ".join([system, *(str(m.get("content") or "") for m in messages)]).strip()
                tools = [
                    str(((t or {}).get("function") or {}).get("name") or "")
                    for t in (body.get("tools") or [])
                    if isinstance(t, dict)
                ]
                generation = self.path.endswith("/chat") or self.path.endswith("/generate")
                lane = ""
                if generation:
                    head = prompt[:400]
                    if ANSWER_LANE_MARKER in head:
                        lane = "answer"
                    elif SPLIT_LANE_MARKER in head:
                        lane = "split"
                answer_lane = lane == "answer"
                with rig._lock:
                    call = {
                        "t": time.time(),
                        "model": model,
                        "path": self.path,
                        "lane": lane,
                        "answer_lane": answer_lane,
                        "tools": tools,
                        "prompt": prompt[:4000],
                        "status": 200,
                    }
                    rig.calls.append(call)
                    fault = None
                    if answer_lane and rig.faults:
                        fault = rig.faults.pop(0)
                    if fault is not None:
                        call["status"] = fault.get("code", "dropped")
                if fault is not None:
                    if fault.get("mode") == "drop":
                        # No status line, no body: the connection just goes away.
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        self.close_connection = True
                        return None
                    headers = {}
                    if fault.get("retry_after") is not None:
                        headers["Retry-After"] = str(int(fault["retry_after"]))
                    return self._send({"error": f"scripted fault {fault['code']}"}, status=int(fault["code"]), headers=headers)
                reply = rig.reply(model, messages, prompt, tools, lane)
                if isinstance(reply, dict):
                    message = {"role": "assistant", "content": "", "tool_calls": reply["tool_calls"]}
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": message})
                return self._send(
                    {
                        "model": model,
                        "done": True,
                        "done_reason": "stop",
                        "message": {"role": "assistant", "content": str(reply)},
                        "prompt_eval_count": 40,
                        "eval_count": 30,
                    }
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def reply(self, model: str, messages: list[dict[str, Any]], prompt: str, tool_names: list[str], lane: str = "") -> Any:
        if '"ambiguous": true or false' in prompt and "well-known referents" in prompt:
            # The runtime's entity-ambiguity probe (core.entity_ambiguity): every question here names
            # one plain thing, so the stand-in judges it the way a model would and never answers it.
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        if lane == "split":
            # Every question in this journey is one request, which is what a model returns for it.
            user = [str(m.get("content") or "") for m in messages if str(m.get("role") or "") == "user"]
            clauses = [{"request": (user[-1] if user else "").strip(), "operation": "factual_explanation", "depends_on": []}]
            return json.dumps({"requests": clauses} if SPLIT_WIRE_FORMAT in prompt else clauses)
        has_tool_result = any(str(m.get("role") or "") == "tool" for m in messages)
        if "vool_probe_add" in tool_names:
            return {"tool_calls": [
                {"id": "probe-add-1", "type": "function", "function": {"name": "vool_probe_add", "arguments": {"left": 19, "right": 23}}},
                {"id": "probe-nonce-1", "type": "function", "function": {"name": "vool_probe_lookup_nonce", "arguments": {"key": "alpha"}}},
            ]}
        if "vool_probe_echo" in tool_names:
            repair = "synthetic_validation_error" in prompt or "recovered" in prompt
            token = "recovered" if repair else "repair-me"
            return {"tool_calls": [{"id": f"probe-echo-{token}", "type": "function", "function": {"name": "vool_probe_echo", "arguments": {"token": token}}}]}
        if not tool_names and has_tool_result:
            import re

            nonces = sorted(set(re.findall(r"\b[0-9a-f]{12}\b", prompt)))
            if nonces:
                return f"The sum is 42 and the sealed nonce is {nonces[0]}."
        return self.table.get(model, "")

    def queue(self, *faults: dict[str, Any]) -> None:
        with self._lock:
            self.faults.extend(faults)

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.faults.clear()

    def answer_calls(self, *, since: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(call) for call in self.calls if call["answer_lane"] and call["t"] >= since]

    def generation_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(call) for call in self.calls if call["path"].endswith(("/chat", "/generate"))]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FaultableProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


SEED = '''
import sys
sys.path.insert(0, "{root}")
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()
conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
upsert_provider_manifest(ModelProviderManifest(
    provider_name="{provider_name}", model_name="{model}", source_type="http",
    adapter_type="openai_compatible", license_name="Apache-2.0",
    license_reference="https://ollama.com/library/qwen3",
    weight_location="external", runtime_dependency="ollama",
    capabilities=["summarize", "classify", "format", "extract", "structured_json"],
    runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30}},
    metadata={{
        "runtime_family": "ollama", "cost_class": "free_local",
        "model_digest": "sha256:stub-{model}", "chat_template_hash": "tmpl-{model}",
        "quantization": "q4_K_M", "parameter_billions": 2.0,
    }},
))
# A paid remote lane that is registered and reachable but has no key and no reservation.
upsert_provider_manifest(ModelProviderManifest(
    provider_name="paid-remote", model_name="{paid_model}", source_type="http",
    adapter_type="openai_compatible", license_name="Provider",
    license_reference="user-managed", weight_location="external",
    runtime_dependency="remote-openai-compatible-provider",
    capabilities=["summarize", "structured_json"],
    runtime_config={{"base_url": "{paid_url}", "timeout_seconds": 30, "api_key_env": "VOOL_SERVED_JOURNEY_PAID_KEY_ABSENT"}},
    metadata={{"deployment_class": "remote", "cost_class": "paid_cloud"}},
))
print(sorted(m.provider_id for m in list_provider_manifests()))
'''


def _evidence_dir(tmp_path: Path) -> Path:
    chosen = os.environ.get(EVIDENCE_ENV, "").strip()
    path = Path(chosen) if chosen else tmp_path / "evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = FaultableProvider({MODEL: ANSWER})
    paid = FaultableProvider({PAID_MODEL: "PAID ANSWER THAT MUST NEVER SHIP"})
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    evidence = _evidence_dir(tmp_path)
    provider.__enter__()
    paid.__enter__()
    try:
        daemon.start(timeout=240)
        run_in_home(
            home,
            SEED.format(
                root=REPO_ROOT,
                provider_name=PROVIDER_NAME,
                model=MODEL,
                base_url=provider.base_url,
                paid_model=PAID_MODEL,
                paid_url=paid.base_url,
            ),
        )
        provider.reset()
        paid.reset()
        yield {"home": home, "daemon": daemon, "provider": provider, "paid": paid, "evidence": evidence}
    finally:
        try:
            (evidence / "provider-calls.json").write_text(json.dumps(provider.calls, indent=2), encoding="utf-8")
            (evidence / "paid-provider-calls.json").write_text(json.dumps(paid.calls, indent=2), encoding="utf-8")
        except OSError:
            pass
        daemon.stop()
        provider.__exit__(None, None, None)
        paid.__exit__(None, None, None)


def _certify(daemon: ServedDaemon) -> dict[str, Any]:
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": PROVIDER_NAME, "model_name": MODEL, "timeout_seconds": 60}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _rows(home: Path, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    db = home / "data" / "vool_web0_v2.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _events(home: Path, event_type: str, *, after: int = 0) -> list[dict[str, Any]]:
    rows = _rows(
        home,
        "SELECT details_json FROM runtime_session_events WHERE event_type=? AND rowid>? ORDER BY rowid",
        (event_type, after),
    )
    return [json.loads(row[0]) for row in rows if row[0]]


def _mark(home: Path) -> int:
    rows = _rows(home, "SELECT COALESCE(MAX(rowid), 0) FROM runtime_session_events", ())
    return int(rows[0][0]) if rows else 0


def _outcomes(home: Path, *, after: int, turns: int = 1, timeout: float = 30.0) -> list[str]:
    """The runtime's own verdict for each turn that finished after ``after``.

    The verdict is written as the turn closes, which can land just after the page shows the text;
    it is read until ``turns`` verdicts are there (or the timeout passes), never assumed.
    """
    deadline = time.monotonic() + timeout
    while True:
        verdicts = [str(event.get("outcome") or "") for event in _events(home, "turn.trace_completed", after=after)]
        if len(verdicts) >= turns or time.monotonic() >= deadline:
            return verdicts
        time.sleep(0.1)


def _repairs(home: Path, *, after: int) -> int:
    """Bounded output repairs the response control ran (``model.response_control_retry``).

    A repair re-asks the same model when a reply misses the request's own constraints. It is a
    separate, receipted mechanism, so each one accounts for exactly one extra successful dispatch;
    anything beyond that would be an unreceipted retry.
    """
    return len(_events(home, "model.response_control_retry", after=after))


def _session_for_turn(home: Path, turn_id: str, *, timeout: float = 30.0) -> str:
    """The derived runtime session that owns a client turn id, read from the turn's own events.

    The stop button names the chat the page holds; a client that only knows its own handle reads
    the owning session from the durable events, the same way the other served cancel journeys do.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _rows(
            home,
            "SELECT session_id FROM runtime_session_events WHERE details_json LIKE ? AND session_id != '' LIMIT 1",
            (f"%{turn_id}%",),
        )
        if rows and rows[0][0]:
            return str(rows[0][0])
        time.sleep(0.05)
    return ""


def _answer_text(reply: dict[str, Any]) -> str:
    return str(((reply.get("message") or {}).get("content")) or "")


def test_auto_recovers_a_transient_provider_fault_in_the_same_turn(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: FaultableProvider = served["provider"]
    paid: FaultableProvider = served["paid"]
    home: Path = served["home"]
    evidence: Path = served["evidence"]

    certified = _certify(daemon)
    assert certified.get("state") == "verified", certified
    provider.reset()

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        composer = page.locator("#input")
        composer.wait_for(state="visible", timeout=30000)

        # ------------------------------------------------ S1: composer, 503 once, same turn answers
        provider.queue({"code": 503})
        started, mark = time.time(), _mark(home)
        composer.fill("Design a resilient swarm topology and answer in one sentence.")
        page.locator("#send").click()
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('.msg.assistant .msg-text'))"
            ".some((el) => el.innerText.includes('regional coordinators'))",
            timeout=120000,
        )
        page.screenshot(path=str(evidence / "s1-same-turn-recovery.png"), full_page=True)
        s1 = provider.answer_calls(since=started)
        assert [call["status"] for call in s1] == [503, 200] + [200] * _repairs(home, after=mark), s1
        assert {call["model"] for call in s1} == {MODEL}, "the retry stays on the same model"
        retries = _events(home, "model.call_retrying", after=mark)
        assert len(retries) == 1, retries
        assert retries[0].get("error_class") == "PROVIDER_INTERNAL_ERROR", retries[0]
        assert retries[0].get("retry_policy") == "same_provider_model", retries[0]
        # One user message, one answer: the person never had to send it again.
        assert page.locator(".msg.user").count() == 1
        assert _outcomes(home, after=mark) == ["completed"]

        # ------------------------------------------------ S2: composer, same chat, healthy follow-up
        provider.table[MODEL] = "Follow-up answered in the same chat."
        started, mark = time.time(), _mark(home)
        composer.fill("Say that again more simply.")
        page.locator("#send").click()
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('.msg.assistant .msg-text'))"
            ".some((el) => el.innerText.includes('Follow-up answered'))",
            timeout=120000,
        )
        page.screenshot(path=str(evidence / "s2-same-chat-follow-up.png"), full_page=True)
        assert [call["status"] for call in provider.answer_calls(since=started)] == [200] * (1 + _repairs(home, after=mark))
        assert _events(home, "model.call_retrying", after=mark) == [], "a healthy answer was retried"
        assert _outcomes(home, after=mark) == ["completed"]
        provider.table[MODEL] = ANSWER
    finally:
        try:
            browser.close()
        finally:
            manager.stop()

    # ------------------------------------------------ S3: dropped connection, same turn answers
    started, mark = time.time(), _mark(home)
    provider.queue({"mode": "drop"})
    reply = daemon.chat("Why does the sky look blue? One sentence.", session_id="journey-drop", mode="auto")
    assert ANSWER in _answer_text(reply), reply
    assert [call["status"] for call in provider.answer_calls(since=started)] == ["dropped", 200] + [200] * _repairs(home, after=mark)
    assert [e.get("error_class") for e in _events(home, "model.call_retrying", after=mark)] == ["PROVIDER_CONNECTION_ERROR"]
    assert _outcomes(home, after=mark) == ["completed"]

    # ------------------------------------------------ S4: 429 + Retry-After, honoured, same turn
    started, mark = time.time(), _mark(home)
    provider.queue({"code": 429, "retry_after": 1})
    reply = daemon.chat("Give me a dinner idea with chickpeas.", session_id="journey-429", mode="auto")
    assert ANSWER in _answer_text(reply), reply
    s4 = provider.answer_calls(since=started)
    assert [call["status"] for call in s4] == [429, 200] + [200] * _repairs(home, after=mark), s4
    assert s4[1]["t"] - s4[0]["t"] >= 0.9, "the Retry-After pause was not honoured"
    waited = _events(home, "model.call_retrying", after=mark)
    assert len(waited) == 1, waited
    assert waited[0].get("error_class") == "PROVIDER_RATE_LIMIT", waited
    assert 0.9 <= float(waited[0].get("retry_wait_seconds") or 0) <= 2.0, waited
    assert _outcomes(home, after=mark) == ["completed"]

    # ------------------------------------------------ S5: cancel during the Retry-After wait
    session, turn_id = "journey-cancel", "journey-cancel-turn"
    provider.queue({"code": 429, "retry_after": 2}, {"code": 429, "retry_after": 2})
    stream_done = threading.Event()

    def _stream() -> None:
        try:
            daemon.chat_stream("Write a short note on the history of the bicycle.", session_id=session,
                               turn_id=turn_id, mode="auto", timeout=240.0)
        except Exception:
            pass
        finally:
            stream_done.set()

    started, mark = time.time(), _mark(home)
    threading.Thread(target=_stream, daemon=True).start()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not provider.answer_calls(since=started):
        time.sleep(0.02)
    assert provider.answer_calls(since=started), "the turn never reached the provider"
    owner = _session_for_turn(home, turn_id)
    assert owner, "the streamed turn left no event naming its session"
    acknowledged = daemon.cancel_turn(session_id=owner, turn_id=turn_id)
    acknowledged_at = time.time()
    assert acknowledged.get("state") == "cancelled", acknowledged
    assert stream_done.wait(timeout=120), "the cancelled stream never ended"
    time.sleep(3.0)   # longer than the capped wait: a retry the cancel failed to stop would land now
    assert provider.answer_calls(since=acknowledged_at) == [], "an answer dispatch after the acknowledged cancel"
    assert len(provider.answer_calls(since=started)) == 1
    assert _events(home, "model.call_retrying", after=mark) == [], "a retry went out for a cancelled turn"
    assert _outcomes(home, after=mark) == ["cancelled"]
    provider.reset()
    reply = daemon.chat("And one sentence about the first bicycle?", session_id=session, mode="auto")
    assert ANSWER in _answer_text(reply), "the chat is not usable after the cancel"

    # ------------------------------------------------ S6: the fault persists: one retry, then a plain failure
    started, mark = time.time(), _mark(home)
    provider.queue({"code": 503}, {"code": 503}, {"code": 503})
    began = time.monotonic()
    reply = daemon.chat("Summarize what a heat pump does in one sentence.", session_id="journey-exhausted", mode="auto")
    elapsed = time.monotonic() - began
    assert ANSWER not in _answer_text(reply), "an answer was shipped that the provider never gave"
    assert _answer_text(reply).strip(), "the failed turn said nothing at all"
    assert [call["status"] for call in provider.answer_calls(since=started)] == [503, 503], (
        "one bounded same-model retry, no more"
    )
    assert len(_events(home, "model.call_retrying", after=mark)) == 1
    assert _outcomes(home, after=mark) == ["failed"]
    assert elapsed < 120.0, f"the failed turn took {elapsed:.0f}s"
    provider.reset()
    reply = daemon.chat("What does a heat pump do? One sentence.", session_id="journey-exhausted", mode="auto")
    assert ANSWER in _answer_text(reply), "the chat is not usable after the failed turn"

    # ------------------------------------------------ S7: auth failure: no retry, no invented answer
    started, mark = time.time(), _mark(home)
    provider.queue({"code": 401}, {"code": 401})
    reply = daemon.chat("Name one planet with rings.", session_id="journey-auth", mode="auto")
    assert [call["status"] for call in provider.answer_calls(since=started)] == [401], "an auth refusal was retried"
    assert ANSWER not in _answer_text(reply)
    assert _events(home, "model.call_retrying", after=mark) == []
    assert _outcomes(home, after=mark) == ["failed"]
    provider.reset()

    # ------------------------------------------------ S8: healthy: one dispatch, no retry
    started, mark = time.time(), _mark(home)
    reply = daemon.chat("How many legs does a spider have?", session_id="journey-healthy", mode="auto")
    assert ANSWER in _answer_text(reply), reply
    assert [call["status"] for call in provider.answer_calls(since=started)] == [200] * (1 + _repairs(home, after=mark))
    assert _events(home, "model.call_retrying", after=mark) == []
    assert _outcomes(home, after=mark) == ["completed"]

    # ------------------------------------------------ S9: the unauthorized paid lane got nothing
    assert paid.generation_calls() == [], "an unauthorized paid endpoint was dispatched"
