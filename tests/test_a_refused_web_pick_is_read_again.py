"""When the turn's judge picked only the internet and the web's own yes/no says the message needs none, the message
is read once more against the person's own tools, so a reminder, contact or note asked for in any language still
gets its tool.

Fresh-question audit of 02ff11496 (2026-11-23, qwen3:8b): "kokius priminimus turiu?" ("which reminders do I have?")
was judged the internet, the web yes/no said no, no reminder tool was offered, and the model told the person to use
"operator.list_reminders" itself. The judges are scripted; asks and languages are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, online_asks, plugin_ask
from core.request_trust import OWNER_LOCAL_KEY

OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-web-read-again"}
DESCRIPTIONS = {key: description for key, description, _seats in plugin_ask.BUILTIN_CHOICES}
_ASKS = {
    "reminders": ("kokius priminimus turiu?", "Welche Erinnerungen stehen an?", "¿qué recordatorios tengo?",
                  "wat voor herinneringen heb ik", "any reminders left for me"),
    "contacts": ("koks Rūtos telefono numeris?", "Wie ist Jörgs Nummer?", "¿cuál es el número de Ana?"),
    "notes": ("parodyk mano užrašus apie remontą", "Zeig meine Notizen zum Umzug", "mis notas de la boda"),
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugin_ask.forget()
    online_asks.forget_web_verdicts()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    yield
    plugin_ask.forget()
    online_asks.forget_web_verdicts()


def _script(monkeypatch, first: tuple[str, ...], second: tuple[str, ...], web: str | None) -> list[list[str]]:
    """The judge picks ``first`` over the full list and ``second`` over any later list; the web yes/no says ``web``."""
    shown: list[list[str]] = []

    def arbitrate(message, choices, **kwargs):
        shown.append(list(choices))
        wanted = {DESCRIPTIONS[key] for key in (first if len(shown) == 1 else second)}
        return tuple(number for number, choice in enumerate(choices, 1) if choice in wanted)

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: web)
    return shown


@pytest.mark.parametrize("seed", range(9))
def test_a_personal_ask_misread_as_the_web_gets_its_own_tool(seed, monkeypatch):
    rng = random.Random(f"read-again-{seed}")
    key = rng.choice(sorted(_ASKS))
    text = rng.choice(_ASKS[key])
    shown = _script(monkeypatch, ("web",), (key,), web=rng.choice(("no", None)))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert len(shown) == 2, text
    assert DESCRIPTIONS["web"] in shown[0] and DESCRIPTIONS["web"] not in shown[1], shown
    assert plugin_ask.judged_builtins(text) == (key,), text
    assert set(plugin_ask._BUILTIN_SEATS[key]) <= set(plugin_ask.judged_builtin_seats(text)), text


@pytest.mark.parametrize("text", ["Kas yra fotosintezė?", "Was ist ein schwarzes Loch?", "who wrote Don Quixote"])
def test_a_plain_question_read_again_stays_without_tools(text, monkeypatch):
    shown = _script(monkeypatch, ("web",), (), web="no")
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert len(shown) == 2 and plugin_ask.judged_builtins(text) == ()


def test_a_confirmed_web_ask_is_not_read_again(monkeypatch):
    shown = _script(monkeypatch, ("web",), ("reminders",), web="yes")
    text = "paieškok internete orų prognozės Klaipėdai"
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert len(shown) == 1 and plugin_ask.judged_builtins(text) == ("web",)


def test_a_pick_left_beside_the_web_is_not_read_again(monkeypatch):
    shown = _script(monkeypatch, ("web", "calendar"), ("reminders",), web="no")
    text = "ar rytoj turiu ką nors kalendoriuje?"
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert len(shown) == 1 and plugin_ask.judged_builtins(text) == ("calendar",)


def test_a_failed_second_reading_leaves_no_tool(monkeypatch):
    calls = []

    def arbitrate(message, choices, **kwargs):
        calls.append(choices)
        if len(calls) == 1:
            return (list(choices).index(DESCRIPTIONS["web"]) + 1,)
        raise TimeoutError("judge timed out")

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "no")
    plugin_ask.judge_turn("kokius priminimus turiu?", source_context=OWNER, session_id="x")
    assert len(calls) == 2 and plugin_ask.judged_builtins("kokius priminimus turiu?") == ()
