"""The goal card in a REAL browser: composer start, live update, expand, controls, hydration.

The hermetic lane (the same one tests/test_chat_attachments_ui.py established): the page's
own JavaScript is driven end to end through `page.route` against controlled server answers,
all clearly synthetic — the /goal turn's NDJSON is a labeled scripted stream, while the
card's hydration and control doors are captured/fulfilled exactly as the served routes
answer them (proven separately against the real backend in tests/test_goal_served_workflow.py).
The composer send here is the REAL Send path (textarea + Enter → POST /api/chat).
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

#: Where the visual evidence is preserved.
SHOTS = Path(".verification-logs/goal-card-shots")


def _goal_view(session: str, **overrides: Any) -> dict[str, Any]:
    view = {
        "goal_id": "goal-test0001",
        "session_id": session,
        "project_id": "",
        "objective": "Prepare the quarterly demo site with pages and tests",
        "acceptance": "",
        "state": "running",
        "revision": 1,
        "plan_provenance": "model",
        "current_step_id": "s3",
        "steps": [
            {"id": "s1", "title": "Write the landing page", "details": "", "state": "completed", "reason": "", "depends_on": [], "turn_id": "t1"},
            {"id": "s2", "title": "Write the pricing page", "details": "", "state": "completed", "reason": "", "depends_on": ["s1"], "turn_id": "t2"},
            {"id": "s3", "title": "Write the demo walkthrough", "details": "", "state": "running", "reason": "", "depends_on": ["s2"], "turn_id": "t3"},
            {"id": "s4", "title": "Run the link checks", "details": "", "state": "pending", "reason": "", "depends_on": ["s3"], "turn_id": ""},
        ],
        "completed": 2,
        "total": 4,
        "has_plan": True,
        "started_at": 1.0,
        "updated_at": 1.0,
        "ended_at": 0.0,
        "paused": False,
        "elapsed_seconds": 3724.0,
        "reason": "",
    }
    view.update(overrides)
    return view


class _GoalServer:
    """Controlled answers for the goal surface; every POST control is recorded."""

    def __init__(self) -> None:
        self.controls: list[dict[str, Any]] = []
        self.chat_bodies: list[dict[str, Any]] = []
        self.state_views: dict[str, dict[str, Any] | None] = {}
        self.state_fetches: list[str] = []

    def route(self, route) -> None:
        request = route.request
        url = request.url
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=HTML)
            return
        if "/api/goal/state" in url:
            session = urllib.parse.unquote(url.split("session=", 1)[1]) if "session=" in url else ""
            self.state_fetches.append(session)
            view = self.state_views.get(session)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"goal": view}))
            return
        if "/api/goal/" in url:
            action = url.rsplit("/", 1)[-1]
            body = json.loads(request.post_data or "{}")
            self.controls.append({"action": action, **body})
            session = str(body.get("session_id") or "")
            state = {"pause": "paused", "resume": "running", "stop": "cancelled", "retry": "running"}.get(action, "running")
            view = _goal_view(session, state=state, paused=state == "paused",
                              current_step_id="" if state in ("cancelled", "completed") else "s3",
                              reason="paused by operator" if state == "paused" else "")
            self.state_views[session] = view
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "state": state, "goal": view}))
            return
        if url.endswith("/api/chat") and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            self.chat_bodies.append(body)
            session = str(body.get("session_id") or "")
            text = ""
            for message in body.get("messages") or []:
                if message.get("role") == "user":
                    text = str(message.get("content") or "")
            if text.startswith("/goal demo"):
                # The labeled synthetic goal stream: control reply + a durable-shaped
                # goal.updated event carrying the view the REAL ingress emits.
                view = _goal_view(session)
                lines = [
                    json.dumps({"model": "vool", "created_at": "2026-10-02T00:00:00Z",
                                "message": {"role": "assistant", "content": "Goal created: demo objective. The plan has 4 steps."}, "done": False}),
                    json.dumps({"vool_event": {"type": "goal.updated", "raw_type": "goal_updated", "stage": None,
                                               "summary": "Goal created", "tool": None, "status": None, "goal": view}}),
                    json.dumps({"model": "vool", "created_at": "2026-10-02T00:00:00Z",
                                "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}),
                    "",
                ]
                route.fulfill(status=200, content_type="application/x-ndjson", body="\n".join(lines))
                return
            lines = [
                json.dumps({"model": "vool", "created_at": "2026-10-02T00:00:00Z",
                            "message": {"role": "assistant", "content": "A short ordinary reply."}, "done": False}),
                json.dumps({"model": "vool", "created_at": "2026-10-02T00:00:00Z",
                            "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}),
                "",
            ]
            route.fulfill(status=200, content_type="application/x-ndjson", body="\n".join(lines))
            return
        if "/api/cloud/model" in url and "models" not in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "model": "", "cost_state": "free"}))
            return
        if "/api/runtime/events" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"session_id": "", "events": [], "next_after": 0, "execution_truth": {}}))
            return
        if "/api/chat/history" in url:
            session = urllib.parse.unquote(url.split("session=", 1)[1]) if "session=" in url else ""
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"session_id": session, "messages": []}))
            return
        route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def browser():
    manager, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        manager.stop()


@pytest.fixture
def page(browser):
    context_page = browser.new_page()
    try:
        yield context_page
    finally:
        context_page.close()


def _boot(page, server: _GoalServer, *, session_state: dict[str, dict[str, Any] | None] | None = None, viewport: tuple[int, int] = (1280, 900)) -> None:
    if session_state is not None:
        server.state_views = session_state
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.route("**/*", server.route)
    page.goto("https://vool.test/chat")


def test_composer_goal_start_shows_live_card_expand_and_controls(page) -> None:
    server = _GoalServer()
    _boot(page, server)
    card = page.locator("#voolGoalCard")
    assert card.is_hidden(), "no goal yet: the card must not paint"

    # The REAL composer path: type the goal command, press Enter to send.
    page.fill("#input", "/goal demo objective")
    page.keyboard.press("Enter")
    page.wait_for_selector("#voolGoalCard:not(.vgc-hidden)", timeout=8000)
    assert len(server.chat_bodies) == 1
    assert server.chat_bodies[0]["messages"][-1]["content"] == "/goal demo objective"
    session = str(server.chat_bodies[0].get("session_id") or "")

    # Collapsed truth: title, labeled elapsed, count from the REAL versioned plan, state.
    head = page.locator("#vgcHead")
    assert head.get_attribute("aria-expanded") == "false"
    assert "2/4" in page.locator("#vgcCount").inner_text()
    assert "1:02:04" in page.locator("#vgcElapsed").inner_text()
    assert page.locator("#vgcState").inner_text().strip() == "Running"

    # Expand is a presentation choice; the accessible full label is on the objective.
    head.click()
    assert head.get_attribute("aria-expanded") == "true"
    assert "Prepare the quarterly demo site" in page.locator("#vgcObjective").get_attribute("title")
    assert "Write the demo walkthrough" in page.locator("#vgcCurrent").inner_text()
    pending = page.locator("#vgcPending .vgc-step")
    assert pending.count() == 1
    assert "Run the link checks" in pending.first.inner_text()
    # The completed group is its own collapsible, collapsed by default.
    completed_toggle = page.locator("#vgcCompletedToggle")
    assert "2" in completed_toggle.inner_text()
    assert completed_toggle.get_attribute("aria-expanded") == "false"
    assert not page.locator("#vgcCompletedList").is_visible()
    completed_toggle.click()
    assert page.locator("#vgcCompletedList").is_visible()
    assert "Write the landing page" in page.locator("#vgcCompletedList").inner_text()

    # Pause is a REAL control call carrying the chat binding.
    page.get_by_role("button", name="Pause").click()
    page.wait_for_timeout(300)
    assert server.controls and server.controls[-1]["action"] == "pause"
    assert server.controls[-1]["goal_id"] == "goal-test0001"
    assert server.controls[-1]["session_id"] == session
    page.wait_for_function("document.getElementById('vgcState').textContent.trim() === 'Paused'")
    assert page.get_by_role("button", name="Resume").is_visible()

    # Resume re-enters through the same door.
    page.get_by_role("button", name="Resume").click()
    page.wait_for_timeout(300)
    assert server.controls[-1]["action"] == "resume"

    # A short ordinary chat keeps the composer compact: the card is the only extra surface.
    page.fill("#input", "thanks")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    boxes = page.evaluate("""() => {
      const card = document.getElementById('voolGoalCard').getBoundingClientRect();
      const composer = document.getElementById('input').getBoundingClientRect();
      return {card: {top: card.top, bottom: card.bottom, left: card.left, right: card.right},
              composer: {top: composer.top}, width: window.innerWidth};
    }""")
    assert boxes["card"]["bottom"] <= boxes["composer"]["top"] + 8, "the card must not obstruct the composer"
    assert boxes["card"]["right"] <= boxes["width"]


def test_reload_hydrates_paused_goal_and_does_not_resurrect_it(page) -> None:
    server = _GoalServer()
    # Pre-existing state: the server KNOWS a paused goal for the chat this page will open.
    _boot(page, server)
    # The page opens on a fresh chat id; hydrate through the SAME id the page uses by
    # serving state for whatever session asks — the paused view under its own session.
    page.wait_for_function("String(window.VoolPageActions.displayedChat() || '').length > 0", timeout=8000)
    # Seed the paused view under the page's chat id, then reload for the real hydration path.
    session = page.evaluate("window.VoolPageActions.displayedChat()")
    server.state_views[session] = _goal_view(session, state="paused", paused=True, reason="paused by operator")
    page.reload()
    page.wait_for_selector("#voolGoalCard:not(.vgc-hidden)", timeout=8000)
    page.wait_for_function("document.getElementById('vgcState').textContent.trim() === 'Paused'")
    # The card renders interrupted/paused truth — never running, never a resurrected goal.
    assert "1:02:04" in page.locator("#vgcElapsed").inner_text()
    page.locator("#vgcHead").click()
    assert page.get_by_role("button", name="Resume").is_visible()
    assert page.get_by_role("button", name="Stop").is_visible()
    # Keyboard toggle: focus + Enter flips the expanded state both ways (the card is
    # expanded from the click above, so the first Enter collapses, the second expands).
    page.focus("#vgcHead")
    page.keyboard.press("Enter")
    assert page.locator("#vgcHead").get_attribute("aria-expanded") == "false"
    page.keyboard.press("Enter")
    assert page.locator("#vgcHead").get_attribute("aria-expanded") == "true"
    assert "paused by operator" in page.locator("#vgcReason").inner_text()


def test_card_stays_usable_at_narrow_and_wide_widths(page) -> None:
    server = _GoalServer()
    shots = SHOTS
    shots.mkdir(parents=True, exist_ok=True)
    for width, name in ((480, "goal-card-480"), (900, "goal-card-900"), (1440, "goal-card-1440")):
        _boot(page, server, viewport=(width, 900))
        page.wait_for_function("String(window.VoolPageActions.displayedChat() || '').length > 0", timeout=8000)
        session = page.evaluate("window.VoolPageActions.displayedChat()")
        server.state_views[session] = _goal_view(session)
        page.reload()
        page.wait_for_selector("#voolGoalCard:not(.vgc-hidden)", timeout=8000)
        page.locator("#vgcHead").click()
        boxes = page.evaluate("""() => {
          const card = document.getElementById('voolGoalCard').getBoundingClientRect();
          const composer = document.getElementById('input').getBoundingClientRect();
          return {cardBottom: card.bottom, cardRight: card.right, cardLeft: card.left,
                  composerTop: composer.top, width: window.innerWidth, cardTop: card.top};
        }""")
        assert boxes["cardRight"] <= boxes["width"] + 1, f"{name}: card overflows the viewport"
        assert boxes["cardBottom"] <= boxes["composerTop"] + 8, f"{name}: card obstructs the composer"
        assert boxes["cardTop"] >= 0, f"{name}: card grows past the top"
        page.screenshot(path=str(shots / f"{name}.png"))
