"""A message typed while the chat's task is still working, through the real page.

`tests/test_followup_while_task_runs.py` proves the server's follow-up door. That door existed
since 0.7.2 and the chat page never called it: every message typed mid-task went into the queue
and ran after the task. These tests drive the served page in a real browser against the served
daemon, with the task's provider call genuinely held open, and check what the person sees.
"""

from __future__ import annotations

import json
import time
from urllib.request import urlopen

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_queue_cross_tab_served import _MEMORY_HELPER_PROMPTS, _GatedProvider, _queue_state, _wait_for

TASK = "Write one friendly sentence about a harbor lantern."
TASK_ANSWER = "LANTERN-ONE: The lantern kept its small promise to the dark harbor."
SIDE = "how many days are in a week?"
SIDE_ANSWER = "SEVEN-SIDE: A week has seven days."
NOTE = "also mention the tide"
NOTE_ANSWER = "TIDE-NOTE: The lantern kept its promise as the tide came in."
OTHER = "Write one friendly sentence about a fog bell."
OTHER_ANSWER = "BELL-TWO: The fog bell answered every ship that could not see."
SWITCH = "pause that and write one friendly sentence about a fog bell first"
SWITCH_WORK = "write one friendly sentence about a fog bell"
SWITCH_ANSWER = "BELL-NOW: The fog bell rang out across the water."
REPLACE = "forget that, write one friendly sentence about a gull instead"
REPLACE_WORK = "write one friendly sentence about a gull"
REPLACE_ANSWER = "GULL-NOW: The gull took the last chip and no blame."


class _Provider(_GatedProvider):
    asked: list[str]

    """The gated provider, which also answers the entity check a real question goes through
    ("is this name ambiguous?") the way a model does for a question that names nothing: no."""

    def _reply(self, body):
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        everything = rig._all_text(messages)
        if "The question to judge:" in everything:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        if not everything.lstrip().startswith(_MEMORY_HELPER_PROMPTS):
            # The message this call is answering: the LAST user message, not any earlier one that
            # merely rides along as history.
            last = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
            self.asked.append(last.strip())
            # A change the task never reached runs next as a continuation of the task's request
            # ("Same as my previous request ... but for: mention the tide"), which is the line's
            # follow-up rewriting at work: it is still the change, answered once.
            if last.strip() != NOTE and last.strip().endswith(NOTE.removeprefix("also ")):
                return NOTE_ANSWER
        return super()._reply(body)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    answers = {
        TASK: TASK_ANSWER, SIDE: SIDE_ANSWER, NOTE: NOTE_ANSWER, OTHER: OTHER_ANSWER,
        SWITCH_WORK: SWITCH_ANSWER, REPLACE_WORK: REPLACE_ANSWER,
    }
    with _Provider(answers, hold_texts={TASK}) as provider:
        provider.asked = []
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
                session_id = page.evaluate("displayedChat")
                _send(page, "Hi")
                _wait_for(lambda: page.evaluate("!!(view.run && view.run.released && view.run.status === 'completed')"), 20, "greeting never completed")
                _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "greeting slot never freed")
                yield provider, daemon, page, session_id, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


def _send(page, text: str) -> None:
    page.locator("#input").fill(text)
    page.locator("#send").click()


def _bubbles(page) -> list[tuple[str, str]]:
    return [
        (row["role"], row["text"])
        for row in page.evaluate(
            "Array.from(document.querySelectorAll('#log .msg')).map((el) => ({"
            " role: el.classList.contains('user') ? 'user' : 'assistant',"
            " text: (el.querySelector('.msg-text') || el).textContent.trim() }))"
        )
    ]


def _texts(page) -> list[str]:
    return [text for _, text in _bubbles(page)]


def _calls_for(provider, message: str) -> int:
    """How many times the model was asked to answer exactly this message."""
    return len([asked for asked in list(provider.asked) if asked.endswith(message)])


def _start_task(provider, daemon, page, session_id) -> None:
    _send(page, TASK)
    _wait_for(lambda: _queue_state(daemon, session_id).get("turn_active"), 20, "the task never went live")
    _wait_for(lambda: _calls_for(provider, TASK) >= 1, 30, "the task never reached the provider")


def _history(daemon, session_id: str) -> list[str]:
    with urlopen(daemon.base_url + "/api/chat/history?session=" + session_id, timeout=5) as response:
        return [str(m.get("content") or "").strip() for m in json.load(response).get("messages") or []]


def _task_is_live(page) -> bool:
    return bool(page.evaluate("!!(view.run && !view.run.released && !view.run.ended)"))


def test_status_change_and_side_question_are_handled_while_the_task_keeps_working(chat):
    provider, daemon, page, session_id, errors = chat
    _start_task(provider, daemon, page, session_id)

    # 1. "What are you doing", typed the way people type it. Answered at once, no model call.
    calls_before = len(provider.generation_calls())
    _send(page, "wat r u doin")
    _wait_for(lambda: any("Still working on" in text for text in _texts(page)), 10, "no status reply")
    assert len(provider.generation_calls()) == calls_before, "a status question cost a model call"
    assert _task_is_live(page), "the status question ended the task"
    assert _queue_state(daemon, session_id).get("queue") == [], "the status question was queued"

    # 2. A change to the task. Taken in, never a second task of its own while this one runs.
    _send(page, NOTE)
    _wait_for(lambda: any(text.startswith("Got it.") for text in _texts(page)), 10, "no reply to the change")
    assert _task_is_live(page)

    # 3. Another question. Answered beside the task, by the model, while the task is still held.
    _send(page, SIDE)
    _wait_for(lambda: SIDE_ANSWER in _texts(page), 30, "the side question was not answered while the task ran")
    assert _task_is_live(page), "the side answer ended the task"
    assert TASK_ANSWER not in _texts(page)

    # The working answer is still the last thing in the log; what was said meanwhile sits above it.
    roles = _bubbles(page)
    assert roles[-1][0] == "assistant" and roles[-1][1] != SIDE_ANSWER
    assert _texts(page).index("wat r u doin") > _texts(page).index(TASK)

    # 4. The task finishes with its own answer, last.
    provider.release(TASK)
    _wait_for(lambda: TASK_ANSWER in _texts(page), 30, "the task never finished")
    assert _calls_for(provider, TASK) == 1, "the task was run again"

    # 5. The change did not reach this task (it had no further step), so it runs right after, once.
    _wait_for(lambda: NOTE_ANSWER in _texts(page), 45, "the change the task never reached did not run next")
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "slot never freed")

    # 6. A reload shows the same conversation in the same order.
    shown = [text for text in _texts(page) if text]
    _wait_for(lambda: NOTE_ANSWER in _history(daemon, session_id), 20, "the transcript never got the last answer")
    stored = _history(daemon, session_id)
    def _order(texts):
        keys = [TASK, "wat r u doin", NOTE, SIDE, SIDE_ANSWER, TASK_ANSWER, NOTE_ANSWER]
        return [key for text in texts for key in keys if text == key]
    assert _order(stored) == _order(shown) == [TASK, "wat r u doin", NOTE, SIDE, SIDE_ANSWER, TASK_ANSWER, NOTE, NOTE_ANSWER]
    assert not errors, errors


def test_a_typed_stop_stops_the_task_like_the_stop_button(chat):
    provider, daemon, page, session_id, errors = chat
    _start_task(provider, daemon, page, session_id)
    _send(page, "sotp")
    _wait_for(lambda: "Stopped." in _texts(page), 10, "no reply to the typed stop")
    _wait_for(lambda: page.evaluate("!!(view.run && view.run.released && view.run.status === 'cancelled')"), 20, "the task was not stopped")
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "slot never freed")
    provider.release(TASK)
    time.sleep(1.0)
    assert TASK_ANSWER not in _texts(page), "the stopped task still answered"
    assert _queue_state(daemon, session_id).get("queue") == [], "the stop was queued as a message"
    # The next message is an ordinary turn again.
    _send(page, OTHER)
    _wait_for(lambda: OTHER_ANSWER in _texts(page), 45, "the chat did not take a new message after the stop")
    assert not errors, errors


def test_do_this_first_sets_the_task_aside_then_runs_it_again(chat):
    provider, daemon, page, session_id, errors = chat
    _start_task(provider, daemon, page, session_id)
    _send(page, SWITCH)
    # The message runs now...
    _wait_for(lambda: SWITCH_ANSWER in _texts(page), 60, "the message that was to go first never ran")
    # ...and the task that was set aside is started again afterwards, on its own.
    provider.release(TASK)
    _wait_for(lambda: TASK_ANSWER in _texts(page), 60, "the task that was set aside was never started again")
    texts = _texts(page)
    assert texts.index(SWITCH_ANSWER) < texts.index(TASK_ANSWER)
    # The new work was run in the user's own words, without the part about the old task.
    assert SWITCH_WORK in texts and _calls_for(provider, SWITCH_WORK) == 1
    assert _calls_for(provider, TASK) == 2, "the set-aside task should have been started exactly once more"
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "slot never freed")
    assert _queue_state(daemon, session_id).get("queue") == []
    assert not errors, errors


def test_do_this_instead_drops_the_task_and_runs_the_message(chat):
    provider, daemon, page, session_id, errors = chat
    _start_task(provider, daemon, page, session_id)
    _send(page, REPLACE)
    _wait_for(lambda: REPLACE_ANSWER in _texts(page), 60, "the replacement never ran")
    provider.release(TASK)
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "slot never freed")
    time.sleep(1.0)
    assert TASK_ANSWER not in _texts(page), "the dropped task still answered"
    assert _calls_for(provider, TASK) == 1, "the dropped task was run again"
    assert not errors, errors


def test_with_the_door_unreachable_the_message_is_queued_as_before(chat):
    provider, daemon, page, session_id, errors = chat
    _start_task(provider, daemon, page, session_id)
    # The follow-up door fails (old server, network blip): nothing is lost, the message waits its turn.
    page.route("**/api/chat/followup", lambda route: route.abort())
    _send(page, OTHER)
    _wait_for(lambda: len(_queue_state(daemon, session_id).get("queue") or []) == 1, 10, "the message was not queued")
    provider.release(TASK)
    _wait_for(lambda: TASK_ANSWER in _texts(page), 30, "the task never finished")
    _wait_for(lambda: OTHER_ANSWER in _texts(page), 45, "the queued message never ran")


def _queue_rows(daemon, session_id: str) -> list[dict]:
    """Every row of the chat's message queue as the daemon's own database holds it, any status
    (the page's queue read shows only rows that can run; a kept note is not one until its task ends)."""
    import sqlite3

    rows: list[dict] = []
    for path in sorted(daemon.home.rglob("*.db")) + sorted(daemon.home.rglob("*.sqlite3")):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            continue
        try:
            conn.row_factory = sqlite3.Row
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_queue'").fetchone():
                continue
            rows += [dict(r) for r in conn.execute("SELECT * FROM message_queue WHERE session_id = ? ORDER BY seq", (session_id,))]
        finally:
            conn.close()
    return rows


def _note_calls(provider) -> int:
    """How many times the model was asked to answer the change, in either of the forms it runs in
    (as typed, or as the continuation of the task's request)."""
    return len([asked for asked in list(provider.asked) if asked.endswith(NOTE.removeprefix("also "))])


def test_a_change_typed_mid_task_survives_vool_stopping_and_runs_once(chat):
    # The person types a change in a second window while the task works, and VOOL is killed before
    # the task ends (crash, force quit, power). The change was accepted ("Got it."), so it must not
    # be lost with the process: after the restart it runs, exactly once.
    provider, daemon, page, session_id, errors = chat
    _start_task(provider, daemon, page, session_id)
    second = page.context.new_page()
    second.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    second.wait_for_selector("#input", timeout=5000)
    _wait_for(lambda: second.evaluate("typeof displayedChat === 'string' && !!displayedChat"), 5, "second window has no chat")
    if second.evaluate("displayedChat") != session_id:
        second.evaluate("(id) => openSession(id)", session_id)
        _wait_for(lambda: second.evaluate("displayedChat") == session_id, 10, "second window never opened the chat")
    _send(second, NOTE)
    _wait_for(lambda: any(text.startswith("Got it.") for text in _texts(second)), 15, "the change was not taken in")
    kept = [row for row in _queue_rows(daemon, session_id) if row["status"] == "held"]
    assert len(kept) == 1, _queue_rows(daemon, session_id)
    assert _note_calls(provider) == 0

    # VOOL is killed mid-task: no shutdown path runs.
    second.close()
    daemon.process.kill()
    daemon.process.wait(timeout=20)
    provider.release(TASK)

    daemon.start(timeout=120)
    daemon.certify(timeout=60)
    reopened = page.context.new_page()
    reopened.on("pageerror", lambda error: errors.append(str(error)))
    reopened.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    reopened.wait_for_selector("#input", timeout=5000)
    _wait_for(lambda: reopened.evaluate("typeof displayedChat === 'string' && !!displayedChat"), 10, "no chat after restart")
    if reopened.evaluate("displayedChat") != session_id:
        reopened.evaluate("(id) => openSession(id)", session_id)
    _wait_for(lambda: _note_calls(provider) >= 1, 60, "the change typed before the stop never ran after the restart")
    _wait_for(lambda: NOTE_ANSWER in _texts(reopened), 45, "the change's answer never showed")
    _wait_for(lambda: not _queue_state(daemon, session_id).get("turn_active"), 45, "slot never freed")
    time.sleep(2.0)
    assert _note_calls(provider) == 1, "the change ran more than once"
    statuses = [row["status"] for row in _queue_rows(daemon, session_id) if row.get("idempotency_key", "").startswith("followup-")]
    assert statuses == ["completed"], statuses
    assert _texts(reopened).count(NOTE_ANSWER) == 1
    assert not errors, errors
