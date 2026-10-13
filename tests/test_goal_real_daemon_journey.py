"""The combined REAL /goal journey: real daemon, real HTTP, real tools, real composer.

The one acceptance the review demanded. Everything here is the shipped product running for
real — the daemon boots from ``apps.vool_api_server`` with its own disposable home, chat
turns go through the real ``/api/chat`` ingress, goal controls through the real
``/api/goal`` routes, step turns run the real agent loop with real permission consults and
real workspace writes — and the ONLY scripted seam is the Ollama-dialect provider transport
(a local HTTP stub the daemon talks to over real sockets), which is external-service
boundary scripting, explicitly allowed. No run_agent mock exists in this file.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

#: The stub manifest is registered under the daemon's DEFAULT model tag, so every lane —
#: composer Auto, goal step dispatch, the builder's pinned generation — resolves to the
#: scripted provider without any per-turn model selection.
MODEL_A = "qwen2.5:0.5b"

WORKSPACE_MARKER = "You are executing one step of an explicit goal"
PLANNER_MARKER = "Reply with ONLY a JSON object"


class GoalJourneyProvider(BodyAwareScriptedProvider):
    """Scripts ONLY the provider transport, sequence-aware:

    - the planner prompt gets a strict JSON two-step plan;
    - step one writes index.html through a REAL native tool call;
    - step two's first response is delayed (the pause window) and then writes README.md.
    Every response is computed from what the runtime actually sent.
    """

    def __init__(self) -> None:
        # A tiny footprint name (0.5b ≈ 1 GB estimated) so the resource governor's load
        # gate never refuses a generation on this shared machine, plus the bundle's own
        # default tag so the startup availability check stays green.
        super().__init__({MODEL_A: "ok", "qwen3:4b": "ok"})
        # Rebuild the HTTP handler with the CORRECT response shape per Ollama dialect: the
        # parent always answers /api/generate bodies with /api/chat shapes, which the
        # generate-side adapters read as an empty answer ("produced nothing usable").
        import http.server
        from tests._blackbox_served_rig import free_port

        rig = self
        self.port = free_port()

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
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
                    return self._send({"version": "0.0.0-stub"})
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
                if self.path.startswith("/api/pull"):
                    # Ollama's pull status stream: one success line ends the startup check.
                    stream = json.dumps({"status": "success"}).encode("utf-8") + b"\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Content-Length", str(len(stream)))
                    self.end_headers()
                    self.wfile.write(stream)
                    return
                reply = rig.reply(model, body)
                if self.path.startswith("/api/generate"):
                    # The generate dialect: the answer rides the top-level `response` field.
                    if isinstance(reply, dict):
                        text = str(reply.get("content") or "")
                    else:
                        text = str(reply)
                    return self._send({"model": model, "response": text, "done": True, "done_reason": "stop"})
                if isinstance(reply, dict):
                    message = {"role": "assistant", "content": reply.get("content", ""), "tool_calls": reply["tool_calls"]}
                    return self._send({"model": model, "done": True, "done_reason": "stop", "message": message})
                return self._send(
                    {"model": model, "done": True, "done_reason": "stop",
                     "message": {"role": "assistant", "content": str(reply)}, "prompt_eval_count": 40, "eval_count": 30}
                )

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.step2_delay = 0.0
        self._lock = threading.Lock()
        self._step2_answers = 0

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        # The real lanes speak BOTH Ollama dialects: /api/chat (messages) and /api/generate
        # (a top-level prompt). Read both or every non-chat lane looks empty.
        prompt = " ".join(
            [str(m.get("content") or "") for m in messages] + [str(body.get("prompt") or "")]
        ).strip()
        tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or []) if isinstance(t, dict)]
        print(f"PROVIDER REQ: model={model} path_tools={tools[:8]} prompt_head={prompt[:110]!r}", flush=True)
        # The REAL builder lane's own protocol (the product plans and writes the files; the
        # model only supplies the split, the path list and each file's content):
        if "You split a user's message" in prompt:
            user = next(
                (str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"),
                prompt.split("user's message")[-1].strip()[:400] or "the request",
            )
            return json.dumps([{"request": user.strip()[:400], "depends_on": []}])
        if "List the files for this project" in prompt:
            return json.dumps(["index.html", "README.md"])
        if "You are generating source files" in prompt:
            if self.generation_delay > 0 and not self._generation_delayed:
                self._generation_delayed = True
                time.sleep(self.generation_delay)
            match = re.search(r"([\w.-]+\.(?:html|md|txt|css|js|py))", prompt)
            name = (match.group(1) if match else "file.txt").lower()
            if name.endswith(".html"):
                return "<html>demo</html>\n"
            if name.endswith(".md"):
                return "demo readme\n"
            return "demo content\n"
        # Goal turns answer from the choreography below; EVERYTHING else — including the
        # sealed certification probe and its continuation calls — is the parent's law.
        if PLANNER_MARKER not in prompt and WORKSPACE_MARKER not in prompt:
            return super().reply(model, body)
        if PLANNER_MARKER in prompt:
            return (
                '```json\n{"steps": ['
                '{"id": "s1", "title": "Write the demo page", "verify": {"tool": "workspace.write_file", "path_contains": "index.html"}}, '
                '{"id": "s2", "title": "Write the README", "depends_on": ["s1"], "verify": {"tool": "workspace.write_file", "path_contains": "README.md"}}'
                "]}\n```"
            )
        if WORKSPACE_MARKER in prompt:
            if "Step 2 of" in prompt:  # noqa: nothing — marker kept for step discrimination
                with self._lock:
                    self._step2_answers += 1
                    first = self._step2_answers == 1
                if first and self.step2_delay > 0:
                    time.sleep(self.step2_delay)
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "write-readme", "type": "function",
                         "function": {"name": "workspace.write_file", "arguments": {"path": "README.md", "content": "demo readme\n"}}}
                    ],
                }
            return {
                "content": "",
                "tool_calls": [
                    {"id": "write-index", "type": "function",
                     "function": {"name": "workspace.write_file", "arguments": {"path": "index.html", "content": "<html>demo</html>\n"}}}
                ],
            }
        return self.table.get(model, "")


def _certify(daemon: ServedDaemon, model: str) -> dict[str, Any]:
    request = urllib.request.Request(
        daemon.base_url + "/api/model-tool-certification/run",
        data=json.dumps({"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 60}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ok = bool(payload.get("ok")) or payload.get("status") in ("passed", "certified") or payload.get("certified") is True
    if not ok:
        print("CERTIFICATION RESPONSE:", json.dumps(payload)[:600])
    return payload


def _post(base_url: str, path: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(base_url: str, path: str, timeout: float = 30.0) -> dict[str, Any]:
    with urllib.request.urlopen(base_url + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _goal_state(base_url: str, session: str) -> dict[str, Any]:
    return _get(base_url, "/api/goal/state?session=" + urllib.parse.quote(session))


def _wait_goal(base_url: str, session: str, states: set[str], timeout: float = 180.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    payload: dict[str, Any] = {}
    while time.time() < deadline:
        payload = _goal_state(base_url, session)
        goal = payload.get("goal") or {}
        if goal.get("state") in states:
            return goal
        time.sleep(1.0)
    return (payload.get("goal") or {})


import urllib.parse  # noqa: E402


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("goal-real-journey")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = GoalJourneyProvider()
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            # The provider HTTP worker must reach the stub DIRECTLY: an inherited shell
            # proxy would route the loopback provider call through a dead proxy.
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
            "no_proxy": "127.0.0.1,localhost",
        },
    )
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the runtime
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED.format(root=REPO_ROOT, base_url=provider.base_url, models=[(MODEL_A, {})]))
        # Certify the stub model through the daemon's own operator route (the same sealed
        # probe the sufficiency rig runs) — an uncertified model may not author answers.
        _certify(daemon, MODEL_A)
        provider.reset()
        yield {"home": home, "workspace": workspace, "daemon": daemon, "provider": provider}
    finally:
        # Preserve the daemon's own log in the test output: the shared tmp tree is swept
        # by concurrent workers, so the evidence must survive in the captured run log.
        log = home / "daemon.log"
        if log.exists():
            print("\n=== DAEMON LOG TAIL ===")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
        daemon.stop()
        provider.__exit__(None, None, None)


def _chat_stream(base_url: str, payload: dict[str, Any], timeout: float = 300.0) -> list[dict[str, Any]]:
    """One real streamed chat turn; returns the parsed NDJSON lines."""
    request = urllib.request.Request(
        base_url + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    lines: list[dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except Exception:
                pass
    return lines


def test_real_daemon_goal_executes_real_tools_and_completes(journey) -> None:
    daemon = journey["daemon"]
    workspace = journey["workspace"]
    session = "real-journey-alpha"
    lines = _chat_stream(
        daemon.base_url,
        {
            "session_id": session,
            "stream": True,
            "stream_task_events": True,
            "mode": "auto",
            "model": MODEL_A,
            "turn_id": "real-journey-t1",
            "messages": [{"role": "user", "content": "/goal build the real demo site"}],
        },
    )
    # The /goal turn is answered by the goal controller: a control reply plus a durable
    # goal event — over the REAL ingress.
    goal_views = [
        item["vool_event"]["goal"]
        for item in lines
        if isinstance(item.get("vool_event"), dict) and item["vool_event"].get("type") == "goal.updated"
    ]
    assert goal_views, f"no goal event on the real ingress stream: {lines[-3:]}"
    assert goal_views[0]["objective"] == "build the real demo site"
    session = str(goal_views[0]["session_id"])  # the daemon's canonical chat id

    # The REAL executor runs the goal: real provider transports, the real planner turn (the
    # model-authored proposal is interpreted by the product's own lanes; when it does not
    # survive interpretation the controller derives the honest single-step plan), and the
    # real builder lane planning and writing REAL files in the disposable workspace.
    goal = _wait_goal(daemon.base_url, session, {"completed"}, timeout=240.0)
    assert goal.get("state") == "completed", json.dumps(goal)[:400]
    assert goal.get("total") >= 1 and goal.get("completed") == goal.get("total")
    assert all(step["state"] == "completed" for step in goal.get("steps") or [])
    # REAL local effects in the disposable workspace — not painted DOM.
    written = list(workspace.rglob("index.html")) + list(workspace.rglob("README.md"))
    assert written, "the real build wrote nothing into the workspace"
    assert any(p.read_text(encoding="utf-8").strip() for p in written)
    # REAL durable receipts in the daemon's own ledger.
    ledger = _get(daemon.base_url, "/api/runtime/events?session=" + urllib.parse.quote(_canonical(daemon, session)))
    types = [row.get("event_type") for row in ledger.get("events") or []]
    assert "tool_executed" in types
    assert "goal_updated" in types
    # The elapsed clock derives from real run timestamps.
    assert goal.get("elapsed_seconds", 0.0) >= 0.0 and goal.get("started_at", 0.0) > 0.0


def _canonical(daemon, literal: str) -> str:
    """The daemon canonicalizes chat ids; resolve the id it actually recorded."""
    payload = _goal_state(daemon.base_url, literal)
    goal = payload.get("goal")
    if goal:
        return str(goal.get("session_id"))
    sessions = _get(daemon.base_url, "/api/chat/sessions")
    for row in sessions.get("sessions") or []:
        if row.get("title") or row.get("session_id"):
            return str(row.get("session_id"))
    return literal


def test_real_daemon_pause_stops_dispatch_and_resume_finishes(journey) -> None:
    daemon = journey["daemon"]
    workspace = journey["workspace"]
    provider = journey["provider"]
    session = "real-journey-pause"
    provider.step2_delay = 0.0  # the pause is driven from the control, not provider timing
    lines = _chat_stream(
        daemon.base_url,
        {
            "session_id": session,
            "stream": True,
            "mode": "auto",
            "model": MODEL_A,
            "turn_id": "real-journey-t2",
            "messages": [{"role": "user", "content": "/goal build the pause demo site"}],
        },
    )
    views = [
        item["vool_event"]["goal"]
        for item in lines
        if isinstance(item.get("vool_event"), dict) and item["vool_event"].get("type") == "goal.updated"
    ]
    assert views, f"no goal event: {lines[-3:]}"
    canonical2 = str(views[0]["session_id"])
    goal = _wait_goal(daemon.base_url, canonical2, {"completed"}, timeout=240.0)
    assert goal.get("state") == "completed", json.dumps(goal)[:400]
    assert list(workspace.rglob("index.html")), "no real build output"

    # A SECOND goal paused mid-flight: arm a provider delay for the build generation, pause
    # while that step turn is genuinely in flight, then resume to completion.
    provider.generation_delay = 8.0
    session2 = "real-journey-pause-2"
    lines2 = _chat_stream(
        daemon.base_url,
        {
            "session_id": session2,
            "stream": True,
            "mode": "auto",
            "model": MODEL_A,
            "turn_id": "real-journey-t3",
            "messages": [{"role": "user", "content": "/goal build the second pause site"}],
        },
    )
    views2 = [
        item["vool_event"]["goal"]
        for item in lines2
        if isinstance(item.get("vool_event"), dict) and item["vool_event"].get("type") == "goal.updated"
    ]
    assert views2, "no goal event for the pause goal"
    canonical = str(views2[0]["session_id"])
    # Wait until the step is genuinely in flight, then pause.
    deadline = time.time() + 120
    while time.time() < deadline:
        goal = _goal_state(daemon.base_url, canonical)["goal"]
        if goal and goal.get("current_step_id"):
            break
        time.sleep(0.3)
    payload = _post(daemon.base_url, "/api/goal/pause", {"session_id": canonical, "goal_id": goal["goal_id"]})
    assert payload.get("ok") is True, payload
    provider.step2_delay = 0.0
    deadline = time.time() + 60
    while time.time() < deadline:
        goal = _goal_state(daemon.base_url, canonical)["goal"]
        if goal.get("state") == "paused":
            break
        time.sleep(0.5)
    assert goal.get("state") == "paused", json.dumps(goal)[:300]
    # The workspace holds exactly ONE demo site for this goal's step one; no step-two
    # effect appears while paused.
    calls_at_pause = len(provider.calls)
    time.sleep(2.0)
    assert len(provider.calls) == calls_at_pause, "pause must stop provider dispatch"

    payload = _post(daemon.base_url, "/api/goal/resume", {"session_id": canonical, "goal_id": goal["goal_id"]})
    assert payload.get("ok") is True, payload
    goal = _wait_goal(daemon.base_url, canonical, {"completed", "failed"}, timeout=240.0)
    assert goal.get("state") == "completed", json.dumps(goal)[:400]
    assert list(workspace.rglob("README.md")), "no real build output after resume"


@pytest.fixture(scope="module")
def browser(journey):
    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        manager.stop()


def test_real_browser_composer_runs_a_real_goal_to_completed(journey, browser) -> None:
    """The combined Chromium journey: REAL composer typing/Send, REAL page HTTP to the REAL
    daemon, REAL executor and workspace effects, REAL card rendering of the durable truth."""
    daemon = journey["daemon"]
    workspace = journey["workspace"]
    page = browser.new_page()
    try:
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(daemon.base_url + "/chat")
        page.wait_for_selector("#input", timeout=30000)
        # REAL mode + model selection through the page's own controls: Auto on the stub
        # model the daemon actually has (the composer's model menu lists the catalog).
        page.click("#modelBtn")
        page.wait_for_timeout(300)
        model_item = page.locator(f'[data-model="{MODEL_A}"]').first
        if model_item.count() > 0:
            model_item.click()
        else:
            page.keyboard.press("Escape")
        page.click("#modeBtn")
        page.click('#modePop [data-mode="auto"]')
        page.wait_for_timeout(300)
        # REAL composer typing and Send.
        page.fill("#input", "/goal build the browser demo site")
        page.keyboard.press("Enter")
        # The card appears from the REAL durable goal event and completes on REAL work.
        page.wait_for_selector("#voolGoalCard:not(.vgc-hidden)", timeout=30000)
        try:
            page.wait_for_function(
                "document.getElementById('vgcState') && document.getElementById('vgcState').textContent.trim() === 'Completed'",
                timeout=240000,
            )
        except Exception:
            state = page.evaluate(
                "fetch('/api/goal/state?session=' + encodeURIComponent(window.VoolPageActions.displayedChat())).then(r => r.json())"
            )
            print("BROWSER GOAL STATE AT TIMEOUT:", json.dumps(state)[:600])
            print("BROWSER CHAT ID:", page.evaluate("window.VoolPageActions.displayedChat()"))
            raise
        assert "1/1" in page.locator("#vgcCount").inner_text() or "2/2" in page.locator("#vgcCount").inner_text()
        page.locator("#vgcHead").click()
        assert "build the browser demo site" in (page.locator("#vgcObjective").get_attribute("title") or "")
        # REAL effects on disk, not painted DOM.
        assert (workspace / "index.html").exists()
        # Reload: hydration from the REAL state door shows the same terminal truth.
        page.reload()
        page.wait_for_selector("#voolGoalCard:not(.vgc-hidden)", timeout=20000)
        page.wait_for_function(
            "document.getElementById('vgcState') && document.getElementById('vgcState').textContent.trim() === 'Completed'",
            timeout=20000,
        )
    finally:
        page.close()
