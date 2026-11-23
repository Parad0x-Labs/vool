"""A message the turn's judge read as about the person's own things (a fact they told, their reminders, contacts)
is never sent to the internet by a second reading.

Fresh-question audit of 02ff11496 (new chat, rent told earlier): "how much is my rent these days" was judged a recall,
then the separate web yes/no said yes, and the online door refused it ("I didn't look anything up for this"). Asks and
languages are drawn at random; the judges are scripted.
"""
from __future__ import annotations

import random

import pytest

import core.intent_arbiter as intent_arbiter
from core import online_asks, plugin_ask
from core.turn_contract import TURN_REQUEST_KEY


class _Turn:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id


def _context(turn_id: str) -> dict:
    return {TURN_REQUEST_KEY: _Turn(turn_id)}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugin_ask.forget()
    online_asks.forget_web_verdicts()
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "yes")
    yield
    plugin_ask.forget()
    online_asks.forget_web_verdicts()


@pytest.mark.parametrize("seed", range(8))
def test_a_judged_recall_or_personal_tool_needs_no_web(seed):
    rng = random.Random(f"own-{seed}")
    text = rng.choice(["how much is my rent these days", "kiek dabar moku už nuomą?", "Wie hoch ist meine Miete gerade?",
                       "kokius priminimus turiu šiandien?", "what's Jörg's latest number"])
    picked = rng.choice([("memory_recall",), ("reminders",), ("contacts",), ("memory_recall", "contacts")])
    plugin_ask._record_builtins(text, picked)
    context = _context(f"turn-{seed}")
    assert online_asks.web_ask_verdict(text, source_context=context) == "no"
    assert online_asks.judged_to_need_no_web(text, context)


@pytest.mark.parametrize("picked", [("web",), ("memory_recall", "web"), ()])
def test_the_web_reading_still_decides_otherwise(picked):
    text = "what's the weather in Kaunas tomorrow"
    if picked:
        plugin_ask._record_builtins(text, picked)
    assert online_asks.web_ask_verdict(text, source_context=_context("turn-web")) == "yes"
