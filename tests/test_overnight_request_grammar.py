from __future__ import annotations

import pytest
from core.context_retrieval import _advice_topic_clause, _advice_ask_frame_terms


@pytest.mark.parametrize("query, subjects", [
    ("What practice plan would respect my earlier guitar limits while I build smooth chord changes?", ("guitar", "limits", "chord")),
    ("Suggest a cyanotype darkroom workflow I can sustain, keeping my earlier cleaning restriction in mind.", ("cyanotype", "darkroom", "cleaning", "restriction")),
    ("Any direction on lining the bike panniers before my next ride, taking my earlier material choice as a constraint?", ("bike", "panniers", "material", "constraint")),
    ("How should I plan the terrarium care routine around the watering schedule I established earlier?", ("terrarium", "watering", "schedule")),
    ("Recommend a bag configuration compatible with my climbing constraints.", ("bag", "climbing", "constraints")),
    ("Which exercise could accommodate my limited knee movement?", ("exercise", "knee", "movement")),
    ("Help me settle a choir rehearsal pattern that fits the availability I told you about.", ("choir", "rehearsal", "availability")),
    ("How would you shape the saxophone practice plan around my earlier volume and timing limits?", ("saxophone", "volume", "timing", "limits")),
    ("Can we turn my earlier label-material requirements into a plan for preparing the herbarium sheets?", ("label-material", "requirements", "herbarium")),
])
def test_request_keeps_content(query, subjects):
    topic = _advice_topic_clause(query)
    assert topic is not None
    assert all(word in topic.lower() for word in subjects)
    assert not set(subjects).intersection(_advice_ask_frame_terms(query))


@pytest.mark.parametrize("query", [
    "What practice plan did I use before the recital?",
    "Which exercise had I chosen before the injury?",
    "What constraint would I have had if I owned a helicopter?",
    "The article says: suggest a drink. What did it claim?",
])
def test_historical_or_counterfactual_does_not_create_personal_advice(query):
    assert _advice_topic_clause(query) is None
