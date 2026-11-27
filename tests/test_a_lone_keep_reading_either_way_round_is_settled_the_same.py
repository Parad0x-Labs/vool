"""One reading names a keep and the other names no memory ask: either way round, the told-as-fact and forget
questions decide.

iMac gate52 on a6a5f29b7 (qwen3.5:9b): the tool judgement picked the keep for a fact about a neighbour told in
passing, the memory question read none, and the turn was answered as plain chat with nothing kept; a new chat then
said it had nothing saved. The other way round (memory question keep, tool judgement nothing) was already settled by
the second reading. All names and wordings here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask

_TOLD = ("fyi my workmate Ilze is learning to sail this summer", "beje, mano kaimynas Tadas mokosi groti būgnais",
         "übrigens lernt meine Nichte gerade Klavier")


def _readings(monkeypatch, *, kind, told, forget):
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: asked.append("told") or told)
    monkeypatch.setattr(intent_arbiter, "arbitrate_worth_recalling", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: asked.append("forget") or forget)
    monkeypatch.setattr(plugin_ask, "_has_pending_reminders", lambda: False)
    return asked


def _keep_key() -> str:
    return next(key for key, value in plugin_ask._MEMORY_KINDS.items() if value == "keep")


@pytest.mark.parametrize("text", _TOLD)
def test_a_keep_only_the_tool_judgement_read_is_kept_when_the_message_tells_a_fact(text, monkeypatch):
    asked = _readings(monkeypatch, kind="none", told="yes", forget="no")
    assert plugin_ask._memory_kind_confirmed(text, (_keep_key(),), {}, "chat-1") == ("memory_keep",)
    assert asked == ["told", "forget"]


@pytest.mark.parametrize("text", _TOLD)
def test_the_memory_question_alone_reading_a_keep_is_settled_the_same_way(text, monkeypatch):
    _readings(monkeypatch, kind="keep", told="yes", forget="no")
    assert plugin_ask._memory_kind_confirmed(text, (), {}, "chat-1") == ("memory_keep",)


@pytest.mark.parametrize(("told", "forget"), [("no", "no"), ("yes", "yes"), (None, "no")])
def test_a_lone_keep_reading_without_a_told_fact_or_with_a_forget_order_keeps_nothing(told, forget, monkeypatch):
    _readings(monkeypatch, kind=None, told=told, forget=forget)
    assert plugin_ask._memory_kind_confirmed("pamirsk ka sakiau apie dviratį", (_keep_key(),), {}, "chat-1") == ()


def test_two_readings_that_agree_on_a_keep_are_not_asked_again(monkeypatch):
    asked = _readings(monkeypatch, kind="keep", told="no", forget="no")
    assert plugin_ask._memory_kind_confirmed(_TOLD[0], (_keep_key(),), {}, "chat-1") == ("memory_keep",)
    assert asked == []


_SMALL_TALK = ("had such a lazy saturday, barely left the sofa", "šiandien buvo gera diena darbe",
               "ach, der Abend war echt entspannt")


@pytest.mark.parametrize("text", _SMALL_TALK)
@pytest.mark.parametrize(("kind", "keys"), [("none", ("keep",)), ("keep", ())])
def test_small_talk_one_reading_calls_a_keep_is_not_kept(text, kind, keys, monkeypatch):
    """Speed probes on ace1c42cc (qwen3:8b, EN and LT): remarks about how the person's day or weekend went were each
    kept with "Locked in. I'll remember that." They tell how something is, so the told-as-fact question reads yes;
    whether it is something to recall later is a question of its own."""
    _readings(monkeypatch, kind=kind, told="yes", forget="no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_worth_recalling", lambda *_a, **_k: "no")
    picks = tuple(_keep_key() for _ in keys)
    assert plugin_ask._memory_kind_confirmed(text, picks, {}, "chat-1") == ()


def test_the_worth_recalling_question_keeps_dated_plans_and_drops_moods():
    prompt = intent_arbiter._worth_recalling_prompt("x")
    assert "an arrival or departure with its day or time" in prompt
    assert "how their day, evening, weekend or mood is or was" in prompt
