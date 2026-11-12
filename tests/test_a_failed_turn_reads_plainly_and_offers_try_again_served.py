"""A failed turn reads as plain words and offers Try again: the served journey, on the real page.

App bugs 2026-11-12, seen on the real app (247124ed, a local model behind a fault proxy):

  C1  a fault that persisted through the retry ended the turn with a line that read like a log:
      "`qwen3.5:4b` failed before producing a usable answer. No cached or remembered text was
      substituted. Retry the turn." The card offered only View activity and Report a problem.
  C3  a 401 from the LOCAL model was explained as a cloud key problem: "ollama-local rejected the
      stored API key (authentication failed), so no cloud model could run this turn ... Check the
      key in Settings → API Keys". A model on this computer has no key to check.

One real daemon (``apps.vool_api_server``) in a disposable VOOL_HOME, one Ollama-dialect loopback
provider behind the real HTTP door with a fault queue for the answer call, the model certified
through the daemon's own probe, and Chromium on the real chat page's composer:

  S1  the answer call fails with 503 twice (the first call and its one same-model retry): the
      answer is one plain sentence that names the model, says nothing was made up, and carries no
      code span or log wording; the card shows Try again first, then Report a problem; the
      runtime's own verdict for the turn is ``failed``;
  S2  Try again on that card sends the same question once more through the composer, and the
      healthy model answers it in the same chat;
  S3  the local model answers 401: the reply says the model app on this computer asked for a
      sign-in, and never mentions an API key, a cloud model or Settings → API Keys.

The faults are scripted loopback statuses: this proves what the person reads and can do on the
real served path, not any provider's reliability.
"""

from __future__ import annotations

import json
import os
import re
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
PROVIDER_NAME = "ollama-local"
ANSWER = "A heat pump moves warmth from outside air into your home."
ANSWER_LANE_MARKER = "You are VOOL"
SPLIT_LANE_MARKER = "You split a user's message into the separate requests"
SPLIT_WIRE_FORMAT = 'Wire format: return ONLY {"requests": [...]}'
#: Where a run keeps its screenshots (optional): kept out of the repository.
EVIDENCE_ENV = "VOOL_FAILED_TURN_EVIDENCE"

#: Log wording a person must never read as the answer to a failed turn.
LOG_WORDING = (
    "Retry the turn",
    "cached or remembered",
    "before producing a usable answer",
    "usable model response",
    "HTTP",
)


class FaultableProvider:
    """An Ollama-dialect loopback: a fault queue for the answer call, and a ledger of every call.

    A queued fault is consumed by the next answer-lane generation call before any reply. The
    certification probe's tool choreography is the served rigs' own, so the daemon's sealed probe
    can certify this provider's model.
    """

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.faults: list[int] = []
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: Any) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/api/version"):
                    return self._send({"version": "0.40.16"})
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": MODEL}]})
                if self.path.startswith("/api/ps"):
                    return self._send({"models": [{"name": MODEL, "size": 0, "size_vram": 0}]})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
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
                with rig._lock:
                    call = {"t": time.time(), "lane": lane, "status": 200, "prompt": prompt[:2000]}
                    rig.calls.append(call)
                    fault = rig.faults.pop(0) if lane == "answer" and rig.faults else None
                    if fault is not None:
                        call["status"] = fault
                if fault is not None:
                    return self._send({"error": f"scripted fault {fault}"}, status=fault)
                reply = rig.reply(messages, prompt, tools, lane)
                if isinstance(reply, dict):
                    message = {"role": "assistant", "content": "", "tool_calls": reply["tool_calls"]}
                    return self._send({"model": MODEL, "done": True, "done_reason": "stop", "message": message})
                return self._send(
                    {
                        "model": MODEL,
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

    def reply(self, messages: list[dict[str, Any]], prompt: str, tool_names: list[str], lane: str) -> Any:
        if '"ambiguous": true or false' in prompt and "well-known referents" in prompt:
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        if lane == "split":
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
            token = "recovered" if ("synthetic_validation_error" in prompt or "recovered" in prompt) else "repair-me"
            return {"tool_calls": [{"id": f"probe-echo-{token}", "type": "function", "function": {"name": "vool_probe_echo", "arguments": {"token": token}}}]}
        if not tool_names and has_tool_result:
            nonces = sorted(set(re.findall(r"\b[0-9a-f]{12}\b", prompt)))
            if nonces:
                return f"The sum is 42 and the sealed nonce is {nonces[0]}."
        return self.answer

    def queue(self, *statuses: int) -> None:
        with self._lock:
            self.faults.extend(statuses)

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.faults.clear()

    def answer_statuses(self, *, since: float) -> list[Any]:
        with self._lock:
            return [call["status"] for call in self.calls if call["lane"] == "answer" and call["t"] >= since]

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
from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

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
'''


def _evidence_dir(tmp_path: Path) -> Path:
    chosen = os.environ.get(EVIDENCE_ENV, "").strip()
    path = Path(chosen) if chosen else tmp_path / "evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def _mark(home: Path) -> int:
    rows = _rows(home, "SELECT COALESCE(MAX(rowid), 0) FROM runtime_session_events", ())
    return int(rows[0][0]) if rows else 0


def _outcomes(home: Path, *, after: int, turns: int = 1, timeout: float = 30.0) -> list[str]:
    """The runtime's own verdict for each turn that finished after ``after`` (read, never assumed)."""
    deadline = time.monotonic() + timeout
    while True:
        rows = _rows(
            home,
            "SELECT details_json FROM runtime_session_events WHERE event_type=? AND rowid>? ORDER BY rowid",
            ("turn.trace_completed", after),
        )
        verdicts = [str(json.loads(row[0]).get("outcome") or "") for row in rows if row[0]]
        if len(verdicts) >= turns or time.monotonic() >= deadline:
            return verdicts
        time.sleep(0.1)


def _certify(daemon: ServedDaemon) -> dict[str, Any]:
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": PROVIDER_NAME, "model_name": MODEL, "timeout_seconds": 60}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = FaultableProvider(ANSWER)
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
    provider.__enter__()
    try:
        daemon.start(timeout=240)
        run_in_home(home, SEED.format(root=REPO_ROOT, provider_name=PROVIDER_NAME, model=MODEL, base_url=provider.base_url))
        provider.reset()
        yield {"home": home, "daemon": daemon, "provider": provider, "evidence": _evidence_dir(tmp_path)}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


_LAST_ANSWER_JS = """() => {
  const all = Array.from(document.querySelectorAll('#log .msg.assistant .msg-text'));
  return all.length ? all[all.length - 1].innerText : '';
}"""


def _send(page: Any, text: str) -> None:
    composer = page.locator("#input")
    composer.fill(text)
    page.locator("#send").click()


def _answers(page: Any) -> int:
    return int(page.locator("#log .msg.assistant .msg-text").count())


def _wait_for_answer_with(page: Any, needle: str, *, after: int, timeout_ms: int = 120000) -> str:
    """The text of the newest answer once a NEW answer (past the first ``after``) carries ``needle``."""
    page.wait_for_function(
        "([needle, after]) => { const all = Array.from(document.querySelectorAll('#log .msg.assistant .msg-text'));"
        " return all.length > after && all[all.length - 1].innerText.includes(needle); }",
        arg=[needle, after],
        timeout=timeout_ms,
    )
    return str(page.evaluate(_LAST_ANSWER_JS))


def _assert_plain(text: str) -> None:
    for wording in LOG_WORDING:
        assert wording not in text, f"log wording {wording!r} reached the person: {text!r}"
    assert "`" not in text, text


def test_a_failed_turn_reads_plainly_and_its_card_offers_try_again(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: FaultableProvider = served["provider"]
    home: Path = served["home"]
    evidence: Path = served["evidence"]

    certified = _certify(daemon)
    assert certified.get("state") == "verified", certified
    provider.reset()

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        page.locator("#input").wait_for(state="visible", timeout=30000)
        question = "Summarize what a heat pump does in one sentence."

        # ------------------------------------------------ S1: the fault persists through the retry
        provider.queue(503, 503)
        started, mark, before = time.time(), _mark(home), _answers(page)
        _send(page, question)
        failed_text = _wait_for_answer_with(page, "couldn't get an answer", after=before)
        assert provider.answer_statuses(since=started) == [503, 503], provider.answer_statuses(since=started)
        assert _outcomes(home, after=mark) == ["failed"]
        assert failed_text.startswith("I couldn't get an answer this time:"), failed_text
        assert MODEL in failed_text, failed_text
        assert "Nothing was made up in its place." in failed_text, failed_text
        assert "You can try again." in failed_text, failed_text
        assert ANSWER not in failed_text
        _assert_plain(failed_text)
        # The model name is plain text in the answer, not a code chip.
        last = page.locator("#log .msg.assistant").last
        assert last.locator(".msg-text code").count() == 0
        # The failed card's row offers the way forward first, in plain sight (no hover needed).
        card = page.locator("#log .task-card").last
        retry = card.locator(".tc-actions .tc-retry")
        retry.wait_for(state="visible", timeout=10000)
        assert retry.inner_text().strip() == "Try again"
        labels = [label.strip() for label in card.locator(".tc-actions button").all_inner_texts()]
        assert labels[0] == "Try again" and "Report a problem" in labels, labels
        page.screenshot(path=str(evidence / "s1-failed-turn-plain-with-try-again.png"), full_page=True)

        # ------------------------------------------------ S2: Try again asks the same question once more
        provider.reset()
        started, mark, before = time.time(), _mark(home), _answers(page)
        retry.click()
        answered = _wait_for_answer_with(page, ANSWER, after=before)
        assert ANSWER in answered
        assert provider.answer_statuses(since=started) == [200] * len(provider.answer_statuses(since=started))
        assert provider.answer_statuses(since=started), "Try again never reached the model"
        users = [text.strip() for text in page.locator("#log .msg.user .msg-text").all_inner_texts()]
        assert users == [question, question], users
        assert _outcomes(home, after=mark) == ["completed"]
        page.screenshot(path=str(evidence / "s2-try-again-answered.png"), full_page=True)

        # ------------------------------------------------ S3: the local model wants a sign-in (401)
        provider.reset()
        provider.queue(401, 401)
        started, mark, before = time.time(), _mark(home), _answers(page)
        _send(page, "Name one planet with rings.")
        refused = _wait_for_answer_with(page, "couldn't get an answer", after=before)
        assert provider.answer_statuses(since=started)[0] == 401
        assert _outcomes(home, after=mark) == ["failed"]
        assert "the model app on this computer would not run" in refused, refused
        assert MODEL in refused and "sign in" in refused, refused
        for cloud_wording in ("API key", "API Keys", "cloud", "ollama-local", "authentication failed"):
            assert cloud_wording not in refused, f"{cloud_wording!r} in a local model's refusal: {refused!r}"
        _assert_plain(refused)
        page.locator("#log .task-card").last.locator(".tc-actions .tc-retry").wait_for(state="visible", timeout=10000)
        page.screenshot(path=str(evidence / "s3-local-401-plain.png"), full_page=True)
    finally:
        try:
            browser.close()
        finally:
            manager.stop()
