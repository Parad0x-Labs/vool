"""A message that states every fact the recall judge names, and asks something from them, is worked out by the model.

Gate55 on ace1c42cc (iMac, qwen3:8b): "my bus leaves at 6:40 and the ride is 3 hours 25 minutes, when do I get in?",
asked a second time in the chat, was answered "You told me: my bus leaves at 6:40 ..." with no arrival time. The
first asking had kept the facts; the second time the recall judge named them, and the recall lane showed them back
as if they were the answer. A fact whose every word and number the message already says is the message's premise,
not its answer. A short fact asked back to confirm it, or a question that lacks the number a fact holds, is still a
recall. All wordings here are invented for this contract."""
from __future__ import annotations

import pytest

from core import persistent_memory

_OWN_PREMISE = (
    ("the ferry departs at 7:15 and crossing takes 2 hours 50 minutes, so what time do we land?",
     ["the ferry departs at 7:15", "crossing takes 2 hours 50 minutes"]),
    ("traukinys išvyksta 9:05, kelionė trunka 4 valandas 20 minučių. kada atvyksiu?",
     ["traukinys išvyksta 9:05", "kelionė trunka 4 valandas 20 minučių"]),
    ("mein Flug startet um 13:40 und dauert 5 Stunden 10 Minuten, wann lande ich?",
     ["mein Flug startet um 13:40 und dauert 5 Stunden 10 Minuten"]),
)
_RECALLS = (
    ("what time does the ferry depart?", ["the ferry departs at 7:15"]),
    ("is my scooter blue?", ["my scooter is blue"]),
    ("is my gym locker 41?", ["my gym locker is 41"]),
    ("kada išvyksta mano traukinys?", ["traukinys išvyksta 9:05"]),
)


@pytest.mark.parametrize(("question", "named"), _OWN_PREMISE)
def test_facts_the_message_states_are_its_premise(question, named):
    assert persistent_memory._named_facts_are_the_message_itself(question, named)


@pytest.mark.parametrize(("question", "named"), _RECALLS)
def test_a_question_a_fact_answers_is_still_a_recall(question, named):
    assert not persistent_memory._named_facts_are_the_message_itself(question, named)


def _held(monkeypatch, held):
    monkeypatch.setattr(persistent_memory, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(persistent_memory, "_held_for_judgement", lambda **_k: list(held))
    monkeypatch.setattr(persistent_memory, "_judged_forgotten_named", lambda *_a, **_k: [])
    monkeypatch.setattr(persistent_memory, "_pending_reminder_lines", lambda *_a, **_k: [])
    monkeypatch.setattr(persistent_memory, "_judged_items_named", lambda *_a, **_k: list(held))
    monkeypatch.setattr(persistent_memory, "_recall_beside_a_file_pick", lambda *_a, **_k: True)


def test_the_recall_lane_hands_a_self_contained_question_to_the_model(monkeypatch):
    question, named = _OWN_PREMISE[0]
    _held(monkeypatch, named)
    assert persistent_memory._judged_recall_reply(question, session_id="c", policy=None, source_context={}) is None


def test_the_recall_lane_still_answers_a_question_a_held_fact_answers(monkeypatch):
    question, named = _RECALLS[0]
    _held(monkeypatch, named)
    shown = persistent_memory._judged_recall_reply(question, session_id="c", policy=None, source_context={})
    assert shown and "7:15" in shown


@pytest.mark.parametrize(("picked", "expected"), [([0], "fact"), ([2], "reminder"), ([0, 2], "reminder"), ([], "")])
def test_the_recall_judge_says_whether_a_recall_is_about_a_reminder(monkeypatch, picked, expected):
    held = ["the chimney sweep comes on the 4th at 8", "my locker is 41"]
    reminders = ["take the cat to the vet (Tue 19:10)"]
    items = [*held, *reminders]
    monkeypatch.setattr(persistent_memory, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(persistent_memory, "_held_for_judgement", lambda **_k: list(held))
    monkeypatch.setattr(persistent_memory, "_pending_reminder_lines", lambda *_a, **_k: list(reminders))
    monkeypatch.setattr(persistent_memory.memory_entries, "resolve_memory_access_policy", lambda **_k: None)
    asked: list[str] = []

    def judge(question, shown, **_k):
        asked.append(question)
        assert shown == items
        return [number + 1 for number in picked]

    from core import intent_arbiter

    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", judge)
    context: dict = {}
    assert persistent_memory.recall_names("when is the sweep coming?", session_id="c", source_context=context) == expected
    # The recall lane asks the same question of the same list in the same turn without a second judgement.
    persistent_memory._judged_items_named("when is the sweep coming?", items, session_id="c", source_context=context)
    assert asked == ["when is the sweep coming?"]
