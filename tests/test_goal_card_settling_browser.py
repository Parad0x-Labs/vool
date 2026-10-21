"""The goal card after a Resume pressed while the paused step is still finishing.

The server answers such a Resume with ``settling``: the goal still reads paused until the old
step hands over to its successor (proven against the real backend in
tests/test_goal_review_served_guards.py). The card must keep reading the goal state until it
moves on, instead of freezing on "Paused" while the goal runs. Hermetic lane: the page's own
JavaScript in Chromium, with the goal doors answered by the scripted server of
tests/test_goal_card_browser.py.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests import served_browser
from tests.test_goal_card_browser import _boot, _goal_view, _GoalServer


class _SettlingServer(_GoalServer):
    """Resume answers ``settling`` with the goal still paused; the next state reads finish it."""

    def __init__(self) -> None:
        super().__init__()
        self.after_settle: dict[str, Any] | None = None
        self.paused_reads_left = 2

    def route(self, route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/goal/resume"):
            body = json.loads(request.post_data or "{}")
            self.controls.append({"action": "resume", **body})
            session = str(body.get("session_id") or "")
            paused = _goal_view(session, state="paused", paused=True, reason="paused by operator")
            self.state_views[session] = paused
            self.after_settle = _goal_view(
                session, state="completed", paused=False, current_step_id="", ended_at=2.0,
                completed=4, steps=[dict(step, state="completed") for step in paused["steps"]],
            )
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "state": "paused", "settling": True, "goal": paused}))
            return
        if "/api/goal/state" in url and self.after_settle is not None:
            if self.paused_reads_left > 0:
                self.paused_reads_left -= 1
            else:
                self.state_views[str(self.after_settle["session_id"])] = self.after_settle
        super().route(route)


@pytest.fixture(scope="module")
def browser():
    manager, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        manager.stop()


def test_card_follows_a_settling_resume_to_the_real_outcome(browser) -> None:
    page = browser.new_page()
    try:
        server = _SettlingServer()
        _boot(page, server)
        page.fill("#input", "/goal demo objective")
        page.keyboard.press("Enter")
        page.wait_for_selector("#voolGoalCard:not(.vgc-hidden)", timeout=8000)
        session = str(server.chat_bodies[0].get("session_id") or "")
        server.state_views[session] = _goal_view(session, state="paused", paused=True, reason="paused by operator")
        page.evaluate("window.VoolGoalCard.refresh()")
        page.wait_for_function("document.getElementById('vgcState').textContent.trim() === 'Paused'", timeout=8000)
        page.locator("#vgcHead").click()
        page.locator("#vgcActions button", has_text="Resume").click()
        # No reload, no other control: the card reaches the goal's real outcome by itself.
        page.wait_for_function("document.getElementById('vgcState').textContent.trim() === 'Completed'", timeout=15000)
        assert [c["action"] for c in server.controls] == ["resume"]
        assert "4/4" in page.locator("#vgcCount").inner_text()
    finally:
        page.close()
