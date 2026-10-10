from __future__ import annotations

import pytest

from core.context_retrieval import _source_supports_asked_relation
from core.memory.admission import classify_user_text
from core.temporal_selection import retraction_marker


def test_disclaimed_advice_stays_source_material_beside_an_owned_preference():
    own = "For cycling, I prefer a quiet river route."
    reported = "I read that steep climbs are best, but that is the guide's advice, not mine."
    origin = classify_user_text(own + " " + reported)
    assert origin.authored_text == own
    assert origin.source_text == reported


def test_physical_ownership_disclaimer_is_still_an_authored_fact():
    text = "This bicycle is not mine.\nI borrow it from my sister."
    origin = classify_user_text(text)
    assert origin.authored_text == text
    assert not origin.source_text


@pytest.mark.parametrize("question, body, supported", [
    ("What must happen when the alarm bells ring?",
     "Whenever the alarm bell rings, the loading hatch must close.", True),
    ("What must happen when the alarm bells ring?",
     "Whenever the school bell rings, the loading hatch must close.", False),
    ("What must happen when the alarm bells ring at noon?",
     "Whenever the alarm bell rings, the loading hatch must close.", False),
    ("What must happen when the alarm bells ring?",
     "The alarm bells ring. The loading hatch must close in heavy rain.", False),
    ("What must happen when the alarm bells ring?",
     "Whenever the alarm bells do not ring, the loading hatch must close.", False),
    ("What must happen when the alarm bells do not ring?",
     "Whenever the alarm bells ring, the loading hatch must close.", False),
    ("What must happen when the alarm bells do not ring?",
     "Whenever the alarm bells do not ring, the loading hatch must close.", True),
])
def test_conditional_relation_is_bound_to_the_asked_condition(question, body, supported):
    assert _source_supports_asked_relation(question, body) is supported


def test_a_long_condition_without_a_consequent_licenses_nothing():
    body = "Whenever the alarm bells ring " * 5000
    assert not _source_supports_asked_relation("What must happen when the alarm bells ring?", body)


@pytest.mark.parametrize("text, withdraws", [
    ("Scrap that estimate, the new figure is 45 credits.", True),
    ("Please scrap that code.", True),
    ("Don't scrap that estimate.", False),
    ("Scrap that broken bicycle for me.", False),
    ("The workshop will scrap that estimate next week.", False),
])
def test_scrap_follows_the_same_speech_act_grammar_as_scratch(text, withdraws):
    assert (retraction_marker(text) is not None) is withdraws
