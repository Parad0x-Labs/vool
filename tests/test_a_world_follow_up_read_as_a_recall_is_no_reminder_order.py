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


def test_a_recall_judged_so_from_the_start_goes_to_the_reminders_when_the_recall_judge_names_one(monkeypatch):
    # The whole-list judgement itself read a recall ("Erinnerung" is both words): with a reminder pending, the recall
    # judge, shown the reminders beside what the person told, says which it is about.
    from core import persistent_memory

    text = "was war nochmal meine Erinnerung von gestern?"
    asked = _script(monkeypatch, ("memory_recall",), (), kind="recall")
    monkeypatch.setattr(persistent_memory, "recall_names", lambda *_a, **_k: "reminder")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert asked == []
    assert plugin_ask.judged_builtins(text) == ("reminders",)


@pytest.mark.parametrize("text", ["when is the chimney sweep coming?", "kada atvažiuoja kaminkrėtys?",
                                  "wann kommt nochmal der Schornsteinfeger?"])
def test_a_recall_of_a_told_fact_is_never_put_to_the_reminder_question(monkeypatch, text):
    # iMac gate56 on 3382964ea: with a vet reminder pending, "when is the boiler service?" (saved) was read as a
    # reminder ask by the reminder question and ended at an agenda approval card.
    from core import persistent_memory

    asked = _script(monkeypatch, ("memory_recall",), (), kind="recall")
    monkeypatch.setattr(persistent_memory, "recall_names", lambda *_a, **_k: "fact")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert asked == []
    assert plugin_ask.judged_builtins(text) == ("memory_recall",)


def test_a_recall_the_recall_judge_names_nothing_for_is_put_to_the_reminder_question(monkeypatch):
    from core import persistent_memory

    text = "zeig mir mal meine Erinnerungen"
    asked = _script(monkeypatch, ("memory_recall",), (), kind="recall")
    monkeypatch.setattr(persistent_memory, "recall_names", lambda *_a, **_k: "")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert asked == [text]
    assert plugin_ask.judged_builtins(text) == ("reminders",)
