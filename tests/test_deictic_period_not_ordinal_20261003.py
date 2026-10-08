"""A deictic calendar period is not an ordinal ask.

"When did Dave sell the car he restored last year?" was read as the ordinal "latest" (fork-v5 wiring
audit; the slot selector then pruned the asked record's history to its latest member). Law: a
deictic period - last/next/this/past/coming + a calendar unit, weekday, season or month ("last
year", "next weekend", "the past few weeks", "last Friday") - dates an event relative to when it was
said and is removed before the ordinal grammar reads the question. "The last time", "the latest",
"first" stay ordinal.

All questions are synthetic.
"""

from __future__ import annotations

import pytest

from core.temporal_selection import question_ordinal

# ───────────────────────── deictic periods are not ordinals ───────────────

@pytest.mark.parametrize("question", [
    "When did Dara sell the boat she restored last year?",
    "When did Dara sell the boat she fixed up last summer?",
    "what did dara cook last weekend",
    "Where did Dara go last Friday?",
    "Who did Dara meet last night at the pier?",
    "When did dara move the hives last month",
    "Which film did Dara watch this week?",
    "What is Dara planning for next weekend?",
    "When did Dara visit the islands over the past few weeks?",
    "when did dara finish the deck, the one she started last spring",
    "What did Dara buy last December?",
])
def test_deictic_period_is_not_an_ordinal(question):
    assert question_ordinal(question) == "", question


@pytest.mark.parametrize("question,expected", [
    ("When was the last time Dara sailed to the islands?", "latest"),
    ("What was the latest price Dara paid for diesel?", "latest"),
    ("Which boat did Dara buy most recently?", ""),
    ("When did Dara first sail to the islands?", "earliest"),
    ("What was the last photo Dara took last year?", "latest"),
    ("When was the last time Dara sailed last summer?", "latest"),
    ("Where did Dara originally keep the boat?", "earliest"),
])
def test_ordinal_asks_stay_ordinal(question, expected):
    assert question_ordinal(question) == expected, question
