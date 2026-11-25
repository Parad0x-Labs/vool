"""A question a fact the person told VOOL answers is answered from it before it is refused as a web lookup.

Mac gate 17 on f04d8289c: "kiedy jest serwis kotła?" with "the boiler service is booked for 14 April" saved was
answered "Web lookups are off". The word reading only finds a fact that shares the question's words; the recall
judge reads the question against what VOOL holds, in any language. The judge is replaced here.
"""
from __future__ import annotations

import core.persistent_memory as pm


def _judge(monkeypatch, answer, seen):
    def judged(question, **kwargs):
        seen.append((question, kwargs))
        return answer

    monkeypatch.setattr(pm, "_judged_recall_reply", judged)
    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *a, **k: True)
    monkeypatch.setattr(pm.memory_entries, "resolve_memory_access_policy", lambda **k: object())


def test_a_question_is_put_to_the_recall_judge_without_the_whole_memory(monkeypatch):
    seen: list = []
    _judge(monkeypatch, "You told me: the boiler service is booked for 14 April.", seen)
    answer = pm.held_fact_answer_before_a_lookup("kiedy jest serwis kotła?", session_id="c", source_context={})
    assert answer == "You told me: the boiler service is booked for 14 April."
    assert seen and seen[0][1]["whole_too"] is False and seen[0][1]["forgotten_too"] is False


def test_an_order_to_look_something_up_is_the_lookups(monkeypatch):
    seen: list = []
    _judge(monkeypatch, "You told me: anything", seen)
    assert pm.held_fact_answer_before_a_lookup("search the web for ferry times to Visby", session_id="c",
                                               source_context={}) is None
    assert seen == []


def test_nothing_named_leaves_the_refusal_standing(monkeypatch):
    _judge(monkeypatch, None, [])
    assert pm.held_fact_answer_before_a_lookup("what is the tallest tree in Chile?", session_id="c",
                                               source_context={}) is None
