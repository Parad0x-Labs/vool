"""Bypass approved for one task ends with that task, and the next messages run (served, real page).

Measured on the installed 0.8 (2026-11-21, owner's Mac): an inbox question asked for approval, the
person approved Bypass permissions for that task, the task answered, and then every next message in
the chat ("from whom ?", and each one after it) showed "VOOL did not run this turn." The page kept
the chat in Bypass and sent the grant's token with every turn; the server honours a task grant only
for the turn it was approved in, so it refused each new turn at the door (HTTP 409) until the
grant's 15-minute timer ran out on the page.

Nothing here depends on what the next message says: several different follow-ups are sent, and a
chat that holds a stale grant of any scope is checked too.
"""

from __future__ import annotations

import json

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_queue_cross_tab_served import _GatedProvider, _queue_state, _wait_for

TASK = "Write one friendly sentence about a harbor lantern."
TASK_ANSWER = "LANTERN-ONE: The lantern kept its small promise to the dark harbor."
FOLLOW_UPS = {
    "who wrote that?": "AUTHOR-ONE: I wrote it just now.",
    "and make it shorter": "SHORT-TWO: The lantern kept faith.",
    "translate the first one to French": "FRENCH-THREE: La lanterne a tenu sa promesse.",
}
AFTER_STALE = "how many days are in a week?"
AFTER_STALE_ANSWER = "SEVEN-FOUR: A week has seven days."
REFUSED = "VOOL did not run this turn."


class _Provider(_GatedProvider):
    """The gated provider, which also answers the entity check a question goes through the way a
    model does for a question that names nothing ambiguous."""

    def _reply(self, body):
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if "The question to judge:" in rig._all_text(messages):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return super()._reply(body)


@pytest.fixture
def chat(tmp_path):
    answers = {TASK: TASK_ANSWER, AFTER_STALE: AFTER_STALE_ANSWER, **FOLLOW_UPS}
    with _Provider(answers, hold_texts={TASK}) as provider:
        daemon = rig.ServedDaemon(
            tmp_path / "home",
            provider=provider,
            env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback", "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:1"},
        )
        daemon.start(timeout=60)
        try:
            daemon.certify(timeout=30)
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_context().new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=5000)
                _wait_for(lambda: page.evaluate("typeof displayedChat === 'string' && !!displayedChat"), 5, "no chat")
                yield provider, daemon, page, page.evaluate("displayedChat"), errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


def _send(page, text: str) -> None:
    page.locator("#input").fill(text)
    page.locator("#send").click()


def _texts(page) -> list[str]:
    return page.evaluate(
        "Array.from(document.querySelectorAll('#log .msg')).map((el) =>"
        " (el.querySelector('.msg-text') || el).textContent.trim())"
    )


def _idle(page, daemon, session_id: str) -> None:
    _wait_for(lambda: page.evaluate("!!(view.run && view.run.released)"), 45, "the turn never finished on the page")
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "the chat's slot never freed")


# The page's own two-step activation (the Bypass dialog's confirm), for the task running now.
_APPROVE_BYPASS_FOR_THIS_TASK = """async () => {
  const context = modeContext(displayedChat);
  const minted = await postMode({ op: 'request_bypass_confirmation', scope: 'task', duration_seconds: 900, until_off: false, workspace_root: '' }, context);
  const data = await postMode({ op: 'activate_bypass', scope: 'task', duration_seconds: 900, until_off: false, workspace_root: '', confirmation_id: minted.confirmation_id }, context);
  view.bypassGrant = data.grant;
  await setModeController('bypass_permissions', view.bypassGrant.token, context);
  return { mode: view.mode, scope: data.grant.scope, task: data.grant.task_id, turn: context.turn_id };
}"""


def test_after_a_task_approved_with_bypass_the_next_messages_run(chat):
    provider, daemon, page, session_id, errors = chat
    _send(page, TASK)
    _wait_for(lambda: page.evaluate("!!(view.run && !view.run.ended && view.run.turnId)"), 20, "the task never started")
    _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active"), 20, "the task never went live")
    approved = page.evaluate(_APPROVE_BYPASS_FOR_THIS_TASK)
    assert approved["mode"] == "bypass_permissions" and approved["scope"] == "task"
    assert approved["task"] and approved["task"] == approved["turn"]
    provider.release(TASK)
    _wait_for(lambda: TASK_ANSWER in _texts(page), 45, "the approved task never answered")
    _idle(page, daemon, session_id)
    # The task is over, so is its grant: the chat is back in its own mode.
    assert page.evaluate("view.mode") != "bypass_permissions"
    assert page.evaluate("view.bypassGrant") is None

    for text in FOLLOW_UPS:
        before = len(_texts(page))
        _send(page, text)
        _wait_for(
            lambda before=before: len(_texts(page)) >= before + 2 and _texts(page)[-1] not in {"", "…"},
            45, f"{text!r} got no reply after the bypass task",
        )
        _idle(page, daemon, session_id)
        reply = _texts(page)[-1]
        assert reply != REFUSED and REFUSED not in _texts(page), f"{text!r} was refused"
        assert page.evaluate("view.run.status") == "completed", f"{text!r} did not run"
    assert not errors, errors


@pytest.mark.parametrize("scope", ["task", "session"])
def test_a_stale_grant_of_any_scope_does_not_block_the_chat(chat, scope):
    # A grant the server no longer holds, whatever made it stale (reload, expiry, revoked in another
    # window): the server refuses the turn with a typed reason before anything runs, and the page
    # drops the grant and sends the same message again once under the chat's own mode.
    provider, daemon, page, session_id, errors = chat
    page.evaluate(
        "(scope) => { view.bypassGrant = { token: 'stale-token-not-held', scope: scope, until_off: false,"
        " task_id: 'some-old-turn', expires_at: Date.now() / 1000 + 600 }; view.mode = 'bypass_permissions'; }",
        scope,
    )
    _send(page, AFTER_STALE)
    _wait_for(lambda: AFTER_STALE_ANSWER in _texts(page), 45, "the message never ran after the stale grant")
    texts = _texts(page)
    assert REFUSED not in texts
    assert texts.count(AFTER_STALE) == 1, "the resend added a second copy of the message"
    assert page.evaluate("view.mode") != "bypass_permissions" and page.evaluate("view.bypassGrant") is None
    _idle(page, daemon, session_id)
    assert not errors, errors
