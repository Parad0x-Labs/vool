"""A question about reminders already set reads them; it never sets a new one.

Tooling audit on fe98e849: asking which reminders are pending got only the tool that sets one. The parser read
"what are you going to remind me of tomorrow" and "anything I asked you to remind me about?" as reminders to set,
and every seat that set a reminder came without the read of the pending ones.
"""

from __future__ import annotations

import itertools
import random

import pytest

from core.operator.parser import parse_operator_action_intent
from core.tool_demand_signals import MAX_EXPLICIT_INTENTS, owner_local_operator_intents

_QUESTION_HEADS = ("what", "which things", "what exactly", "is there anything", "is there something")
_MIDDLES = ("are you going to", "will you", "did I ask you to", "should you", "did I tell you to")
_PREPOSITIONS = ("of", "about")
_TAILS = ("", " tomorrow", " on friday", " this week", " next weekend", " at 6", " tonight", " later")
_ENDS = ("?", "", " ?")


def _pending_questions() -> list[str]:
    rows = [
        f"{head} {middle} remind me {prep}{tail}{end}"
        for head, middle, prep, tail, end in itertools.product(_QUESTION_HEADS, _MIDDLES, _PREPOSITIONS, _TAILS, _ENDS)
    ]
    return random.Random(20261122).sample(rows, 40)


@pytest.mark.parametrize("text", _pending_questions())
def test_a_question_whose_object_is_the_question_word_lists_reminders(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "list_reminders", text


_SUBJECTS = ("the haircut", "the bins", "my sister's birthday", "something nice", "what I said about rent")
_TIMES = ("at 4", "tomorrow at 9", "on friday at 6pm", "in 20 minutes")
_LEADS = ("", "what a day, ", "ok whatever, ", "anything else? ")


@pytest.mark.parametrize(
    "text",
    random.Random(7).sample(
        [f"{lead}remind me about {subject} {when}" for lead, subject, when in itertools.product(_LEADS, _SUBJECTS, _TIMES)],
        24,
    ),
)
def test_a_reminder_with_its_subject_after_remind_me_is_still_set(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "schedule_reminder", text


def test_a_question_about_pending_reminders_seats_the_read_and_not_a_new_reminder():
    for text in ("what are you going to remind me of tomorrow", "what will you remind me about on friday"):
        seats = owner_local_operator_intents(text)
        assert "operator.list_reminders" in seats, (text, seats)
        assert "operator.schedule_reminder" not in seats, (text, seats)


def test_setting_a_reminder_also_seats_the_read_of_pending_ones():
    seats = owner_local_operator_intents("remind me about the haircut at 4")
    assert seats[:2] == ("operator.schedule_reminder", "operator.list_reminders"), seats


def test_the_read_never_pushes_out_a_seat_the_words_asked_for(monkeypatch):
    import core.tool_demand_signals as signals

    # Every asked-for seat taken: the read is the one that waits.
    assert len(owner_local_operator_intents("remind me about the haircut at 4")) <= MAX_EXPLICIT_INTENTS
    monkeypatch.setattr(signals, "MAX_EXPLICIT_INTENTS", 1)
    assert signals.owner_local_operator_intents("remind me about the haircut at 4") == ("operator.schedule_reminder",)
