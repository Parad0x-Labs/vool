"""A message the judgement over the whole list read as the web, and only a second reading without the web called a
recall, is not put to the reminder question: it goes to the memory lane, which hands the turn on.

iMac gate39 on f31c60b65 (Offline, qwen3:8b, a reminder pending from an earlier chat): a follow-up pointing back at a
question about a painting was judged the web, the web yes/no said no, the reading without the web said recall, the
reminder question read it as a reminder ask, and the turn ended as a calendar approval card. Probes g39/g39b: the
reminder question reads 4 of 8 such follow-ups "remind", in four languages, with or without the earlier message.
The judges are scripted here.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, online_asks, plugin_ask
from core.request_trust import OWNER_LOCAL_KEY

OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-world-follow-up"}
DESCRIPTIONS = {key: description for key, description, _seats in (*plugin_ask.BUILTIN_CHOICES,
                                                                    *plugin_ask.MEMORY_CHOICES)}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugin_ask.forget()
    online_asks.forget_web_verdicts()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: True)
    monkeypatch.setattr(plugin_ask, "_judged_a_clock_ask", lambda *_a, **_k: False)
    yield
    plugin_ask.forget()
    online_asks.forget_web_verdicts()


def _script(monkeypatch, first, second, *, kind: str, reminder: str = "yes", forget: str = "yes") -> list[str]:
    shown: list[int] = []
    asked: list[str] = []

    def arbitrate(message, choices, **_kwargs):
        shown.append(1)
        wanted = {DESCRIPTIONS[key] for key in (first if len(shown) == 1 else second)}
        return tuple(number for number, choice in enumerate(choices, 1) if choice in wanted)

    def reminder_ask(text, **_k):
        asked.append(text)
        return reminder

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
    monkeypatch.setattr(intent_arbiter, "arbitrate_reminder_ask", reminder_ask)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: forget)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "no")
    return asked


@pytest.mark.parametrize("text", ["and which harbour is it moored in?", "o kokiame muziejuje ji kabo?",
                                  "und wann wurde sie gebaut?"])
def test_a_recall_read_only_without_the_web_is_not_a_reminder_order(monkeypatch, text):
    asked = _script(monkeypatch, ("web",), ("memory_recall",), kind="recall")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert asked == []
    assert plugin_ask.judged_builtins(text) == ("memory_recall",)


def test_a_recall_judged_so_from_the_start_is_still_asked(monkeypatch):
    # The whole-list judgement itself read a recall: with a reminder pending it is asked, as before
    # ("lösche meine Erinnerung" / "Erinnerung" is both words).
    text = "was war nochmal meine Erinnerung von gestern?"
    asked = _script(monkeypatch, ("memory_recall",), (), kind="recall")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert asked == [text]
    assert plugin_ask.judged_builtins(text) == ("reminders",)


def test_a_forget_first_read_as_the_web_is_still_asked(monkeypatch):
    # One reading forget, the other recall, the forget question no: the forget reading still reaches the reminder
    # question, as before.
    text = "nimm die Erinnerung zum Training wieder raus"
    asked = _script(monkeypatch, ("web",), ("memory_forget",), kind="recall", forget="no")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert asked == [text]
    assert plugin_ask.judged_builtins(text) == ("reminders",)
