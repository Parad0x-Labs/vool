"""With Web lookups off, a message the turn's judge read as needing the internet (and the web's own yes/no confirmed)
gets the plain "Web lookups are off" line, in any language and any words, instead of a model answer the publication
gate then refuses ("I didn't look anything up for this ...").

Fresh-question audit of 02ff11496 (2026-11-23, qwen3:8b, web lookups off): "is it going to be windy in Porto this
afternoon?" was judged the internet, its yes/no said yes, and the reply was the publication gate's refusal. The word
readers of the web-off door know English weather words only; the judged pick seats the web for the door in any
language. Real agent on the owner's chat surface, model-less backend, judges scripted.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, online_asks, plugin_ask
from tests._person_turn_rig import make_agent, person_env, reply, turn

DESCRIPTIONS = {key: description for key, description, _seats in plugin_ask.BUILTIN_CHOICES}
_ASKS = (
    "ar šiandien popiet Kaune pūs stiprus vėjas?",
    "wird es heute Nachmittag in Porto windig?",
    "¿va a llover en Sevilla esta tarde?",
    "should i bring a brolly to Leeds later",
    "kaip sekasi Žalgiriui šį sezoną?",
    "is it going to be windy in Porto this afternoon?",
)


@pytest.fixture
def agent(person_env, monkeypatch):
    plugin_ask.forget()
    online_asks.forget_web_verdicts()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    built = make_agent()
    yield built
    plugin_ask.forget()
    online_asks.forget_web_verdicts()


def _judges(monkeypatch, picked: tuple[str, ...], web: str) -> None:
    wanted = {DESCRIPTIONS[key] for key in picked}

    def arbitrate(message, choices, **kwargs):
        return tuple(number for number, choice in enumerate(choices, 1) if choice in wanted)

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: web)


@pytest.mark.parametrize("seed", range(6))
def test_a_judged_web_ask_names_the_switch(seed, agent, monkeypatch):
    rng = random.Random(f"judged-web-off-{seed}")
    asked = rng.choice(_ASKS)
    _judges(monkeypatch, ("web",), "yes")
    said = reply(turn(agent, asked, session=f"openclaw:judged-web-{seed}", surface="openclaw"))
    assert "web lookups are off" in said.lower(), (asked, said)
    assert "didn't look anything up" not in said.lower(), (asked, said)


def test_a_web_pick_the_yes_no_refused_is_not_told_web_is_off(agent, monkeypatch):
    _judges(monkeypatch, ("web",), "no")
    said = reply(turn(agent, "ar šiandien popiet Kaune pūs stiprus vėjas?", session="openclaw:judged-web-no",
                      surface="openclaw"))
    assert "web lookups are off" not in said.lower(), said
