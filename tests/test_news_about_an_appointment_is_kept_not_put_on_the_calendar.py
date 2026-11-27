"""A Calendar pick is confirmed by a question of its own; news about an appointment that drops it is kept as a fact.

iMac gate53 on efb8e474c (qwen3.5:9b): "heads up, the vet moved <pet>'s checkup to thursday 15:20" was picked
Calendar with no memory reading, the model wrote a reminder nobody asked for, and nothing was kept. On a6a5f29b7 such
lines in Manual mode got an approval card. All names and wordings here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask

_NEWS = ("fyi the groomer moved Rudis's appointment to tuesday 11:45",
         "beje, mano odontologo vizitas perkeltas į penktadienį 8:30")


def _readings(monkeypatch, *, calendar, told="yes", kind=None):
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
    monkeypatch.setattr(intent_arbiter, "arbitrate_calendar_ask", lambda *_a, **_k: asked.append("calendar") or calendar)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: asked.append("told") or told)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "no")
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: False)
    return asked


@pytest.mark.parametrize("text", _NEWS)
def test_news_that_is_no_calendar_ask_is_kept_as_a_fact(text, monkeypatch):
    asked = _readings(monkeypatch, calendar="no")
    assert plugin_ask._memory_kind_confirmed(text, ("calendar",), {}, "chat-1") == ("memory_keep",)
    assert asked == ["calendar", "told"]


def test_a_dropped_calendar_pick_that_tells_no_fact_keeps_nothing(monkeypatch):
    _readings(monkeypatch, calendar="no", told="no")
    assert plugin_ask._memory_kind_confirmed("hmm, calendars are confusing", ("calendar",), {}, "chat-1") == ()


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_calendar_ask_keeps_its_pick(verdict, monkeypatch):
    _readings(monkeypatch, calendar=verdict)
    assert plugin_ask._memory_kind_confirmed("what's on my calendar thursday?", ("calendar",), {}, "chat-1") == (
        "calendar",)


def test_the_calendar_question_reads_three_ways_in_any_language():
    prompt = intent_arbiter._calendar_ask_prompt("beje, kirpimas perkeltas į 17:00")
    assert "calendar =" in prompt and "remember =" in prompt and "kirpimas perkeltas" in prompt
    assert intent_arbiter._calendar_kind_reader('{"answer": "remember"}') == "no"
    assert intent_arbiter._calendar_kind_reader('{"answer": "calendar"}') == "yes"
