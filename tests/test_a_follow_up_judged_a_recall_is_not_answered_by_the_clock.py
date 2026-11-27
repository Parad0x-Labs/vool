"""A message the turn's judge settled as a recall is not answered by the clock, whatever clock words it holds.

iMac gate56 on 3382964ea (qwen3:8b): "and what time again?", asked right after "when is <niece>'s play?", was judged a
recall of something the person told, and the clock fast path answered "Today is Saturday ... Current time is 00:16".
A follow-up keeps its chat's context: the turn goes on to the memory lane and the model, with the chat's history. A
recall beside the clock's own reading of the time now is dropped by the judgement before this (core.plugin_ask), so
"what time is it in Warsaw?" is still the clock's. A sum that follows one is no file search either (the Find
question's wording)."""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask
from core.agent_runtime import turn_frontdoor


@pytest.mark.parametrize("text", ["and what time again?", "o kelintą valandą?", "und um wie viel Uhr nochmal?"])
def test_a_judged_recall_keeps_the_clock_out(monkeypatch, text):
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda _t: "recall")
    assert turn_frontdoor._memory_recall_has_priority(text, session_id="c", access_policy=None)


def test_a_message_judged_no_recall_is_left_to_the_clock(monkeypatch):
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda _t: "")
    assert not turn_frontdoor._memory_recall_has_priority("what time is it now?", session_id="c", access_policy=None)


def test_the_find_question_reads_a_sum_that_follows_one_as_something_to_answer(monkeypatch):
    prompt = intent_arbiter._find_ask_prompt("and if it's 20 minutes late?", "the train leaves 8:05, ride 50 min, when?")
    assert 'Earlier: "the train leaves 8:05, ride 50 min, when?"' in prompt
    assert "a follow-up that asks again about what the earlier message asked" in " ".join(prompt.split())
    # Two named kinds, read back as the yes/no core.plugin_ask asks for; any other answer judges nothing.
    for raw, verdict in (('{"answer": "file"}', "yes"), ('{"answer": "answer"}', "no"), ('{"answer": "yes"}', None)):
        monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda _c: "qwen3:8b")
        monkeypatch.setattr(intent_arbiter, "arbitrate_followup",
                            lambda *_a, parse=None, _raw=raw, **_k: parse(_raw) if parse else _raw)
        assert intent_arbiter.arbitrate_find_ask("and if it's 20 minutes late?") == verdict
