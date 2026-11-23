"""A short message the turn's judge read as asking for no tool does not inherit the previous turn's tools.

Mac gate on f3771b6b7 (qwen3:8b): after a calendar turn, "kiek valandų turi para?" ("how many hours has a day?") was
judged to ask for no tool, but being four words long it counted as a follow-up and inherited the calendar tools; the
model raised a "check when you are free" approval card and answered that no calendar is connected. The judge reads the
previous message beside this one, so its "none" stands over the word count. Messages are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import plugin_ask
from core.tool_offer_state import followup_inherited_families, note_family_offer, reset_offer_state

SHORT_QUESTIONS = ("kiek valandų turi para?", "wie viel ist 7 mal 8?", "¿qué hora es en Tokio?", "how tall is Everest?",
                   "kas yra fotosintezė?", "c'est quoi un haïku ?")
FOLLOW_UPS = ("and on friday?", "o penktadienį?", "und am Freitag?", "same for the other one")


@pytest.fixture(autouse=True)
def _clean():
    reset_offer_state()
    plugin_ask.forget()
    yield
    reset_offer_state()
    plugin_ask.forget()


@pytest.mark.parametrize("seed", range(4))
def test_judged_none_inherits_nothing(seed) -> None:
    text = random.Random(f"short-{seed}").choice(SHORT_QUESTIONS)
    session = f"s-short-{seed}"
    note_family_offer(session, ("calendar",))
    plugin_ask._record_builtins(text, ())
    assert followup_inherited_families(text, {"session_id": session}) == ()


@pytest.mark.parametrize("seed", range(3))
def test_a_judged_follow_up_and_an_unjudged_one_still_inherit(seed) -> None:
    rng = random.Random(f"follow-{seed}")
    session = f"s-follow-{seed}"
    note_family_offer(session, ("calendar",))
    judged, unjudged = rng.sample(FOLLOW_UPS, 2)
    plugin_ask._record_builtins(judged, ("calendar",))
    assert followup_inherited_families(judged, {"session_id": session}) == ("calendar",)
    assert followup_inherited_families(unjudged, {"session_id": session}) == ("calendar",)


def test_the_recall_choice_says_the_persons_reminders_are_their_tools() -> None:
    recall = dict((key, text) for key, text, _seats in plugin_ask.MEMORY_CHOICES)["memory_recall"]
    assert "reminders" in recall and "calendar" in recall
