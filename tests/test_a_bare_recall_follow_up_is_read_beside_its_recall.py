"""A bare follow-up on a recall is read by the recall judge beside the recall it follows.

iMac gate62 on e8503d939 (qwen3:8b): right after "wann ist das Klassentreffen?" was answered from memory, "und wo?"
was read alone and the recall judge named a sister's wedding instead; on 50edac5e3 "o kur?" named nothing and went to
a file look-up. A follow-up that names something held, or a message after anything but a recall, is read alone.
Wordings invented for this contract.
"""
from __future__ import annotations

import pytest

from core import persistent_memory as pm
from core import plugin_ask

HELD = ["my aunt Daiva retires on 30 june in Klaipeda", "das Sommerfest ist am 12. Juli im Gasthaus Krone"]
EARLIER = "wann ist das Sommerfest?"


def _context(previous: str = EARLIER) -> dict:
    return {"conversation_history": [{"role": "user", "content": previous}, {"role": "assistant", "content": "12. Juli."}]}


@pytest.fixture(autouse=True)
def judged():
    plugin_ask.forget()
    pm._RECALLED.clear()
    plugin_ask._record_builtins(EARLIER, ("memory_recall",))
    yield
    plugin_ask.forget()


@pytest.mark.parametrize("text", ["und wo?", "o kur?", "and at what hour?"])
def test_a_bare_follow_up_is_read_beside_the_recall_before_it(text):
    assert pm._recall_question_with_its_subject(text, HELD, _context()) == f"{EARLIER}\n{text}"


def test_a_follow_up_that_names_something_held_is_read_alone():
    text = "and when does Daiva retire?"
    assert pm._recall_question_with_its_subject(text, HELD, _context()) == text


def test_a_message_after_anything_but_a_recall_is_read_alone():
    plugin_ask.forget()
    plugin_ask._record_builtins(EARLIER, ("find",))
    assert pm._recall_question_with_its_subject("und wo?", HELD, _context()) == "und wo?"
    assert pm._recall_question_with_its_subject("und wo?", HELD, {}) == "und wo?"


def test_the_recall_judge_is_asked_the_joined_question(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *_a: True)
    monkeypatch.setattr(pm, "_held_for_judgement", lambda **_k: list(HELD))
    monkeypatch.setattr(pm, "_judged_forgotten_named", lambda *_a, **_k: [])
    monkeypatch.setattr(pm, "_pending_reminder_lines", lambda _s: [])
    monkeypatch.setattr(pm.memory_entries, "resolve_memory_access_policy", lambda **_k: None)

    def named(question, items, **_k):
        asked.append(question)
        return [HELD[1]] if "Sommerfest" in question else [HELD[0]]

    monkeypatch.setattr(pm, "_judged_items_named", named)
    assert pm.recall_names("und wo?", session_id="chat", source_context=_context()) == "fact"
    assert asked == [f"{EARLIER}\nund wo?"]


def test_a_bare_follow_up_is_about_what_the_recall_before_it_named(monkeypatch):
    """iMac gate63 on 816dcf780 (qwen3:8b): "and where is it?" after the school concert was recalled, read beside that
    question, named all three held facts. The facts the recall named answer its follow-up; no judge is asked."""
    asked: list[str] = []

    def named(question, items, **_k):
        asked.append(question)
        return [HELD[1]]

    monkeypatch.setattr(pm, "_judged_items_named", named)
    assert pm._judged_recall_items(EARLIER, list(HELD), session_id="chat", source_context={}) == [HELD[1]]
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: list(HELD))
    for text in ("und wo?", "o kur?", "and where is it?"):
        assert pm._judged_recall_items(text, list(HELD), session_id="chat", source_context=_context()) == [HELD[1]]
    assert asked == [EARLIER]


def test_a_follow_up_whose_fact_was_forgotten_is_judged_again(monkeypatch):
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [HELD[1]])
    pm._judged_recall_items(EARLIER, list(HELD), session_id="chat", source_context={})
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [])
    assert pm._judged_recall_items("und wo?", [HELD[0]], session_id="chat", source_context=_context()) == []


def test_another_chat_has_its_own_recalls(monkeypatch):
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [HELD[1]])
    pm._judged_recall_items(EARLIER, list(HELD), session_id="chat-a", source_context={})
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [HELD[0]])
    assert pm._judged_recall_items("und wo?", list(HELD), session_id="chat-b", source_context=_context()) == [HELD[0]]


def test_a_follow_up_on_a_follow_up_is_about_the_same_facts(monkeypatch):
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [HELD[1]])
    pm._judged_recall_items(EARLIER, list(HELD), session_id="chat", source_context={})
    plugin_ask._record_builtins("und wo?", ("memory_recall",))
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: list(HELD))
    assert pm._judged_recall_items("und wo?", list(HELD), session_id="chat", source_context=_context()) == [HELD[1]]
    later = {"conversation_history": [{"role": "user", "content": "und wo?"}, {"role": "assistant", "content": "Krone."}]}
    assert pm._judged_recall_items("und wann?", list(HELD), session_id="chat", source_context=later) == [HELD[1]]


@pytest.mark.parametrize("text", [
    "¿quién tiene la llave de repuesto del garaje?",
    "kas turi atsarginį buto raktą?",
    "kto ma zapasowy klucz do mieszkania?",
    "who keeps the spare garage key these days?",
])
def test_a_whole_question_in_another_language_than_the_facts_is_read_alone(text, monkeypatch):
    """Mac gate 65 on b259d3e55 (qwen3:8b): "¿quién tiene la llave de repuesto del piso?" right after a recall, with the
    spare-key fact told in English, shared no word with anything held and was answered with the fact the recall before
    it had named. A question that names its own subject in more than two words of four letters is judged alone."""
    assert pm._recall_question_with_its_subject(text, HELD, _context()) == text
    asked: list[str] = []
    pm._note_recalled("chat", EARLIER, [HELD[1]])

    def named(question, items, **_k):
        asked.append(question)
        return [HELD[0]]

    monkeypatch.setattr(pm, "_judged_items_named", named)
    assert pm._judged_recall_items(text, list(HELD), session_id="chat", source_context=_context()) == [HELD[0]]
    assert asked == [text]
