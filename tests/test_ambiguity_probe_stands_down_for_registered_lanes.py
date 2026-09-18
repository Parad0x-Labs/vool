"""The entity-ambiguity probe has no jurisdiction over a demand a registered capability lane claims.

Measured on the owner's turn (2026-09-10, build 772256a7): "what are the largest files on this
machine?" -- one KNOW clause to Turn IR -- was adjudicated for entity ambiguity before the
deterministic front door ran. The probe raised twice (82 s on a free cloud reasoning model) and the
turn asked back "If you meant one specific place, person or thing, tell me which", while the
registry named `machine_fact_fast_path` for that exact unit and `machine.find_largest` never ran.

The jurisdiction test is the registry's, not a word list: `registered_lane_for` says whether a
canonical lane claims the unit. Plain open-domain questions stay eligible (the Springfield contract
is untouched); this-machine facts, whichever words they use, are not.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.demand_ownership import registered_lane_for
from core.entity_ambiguity import single_plain_know_question
from core.execution.constants import machine_largest_intent


@pytest.mark.parametrize(
    "question",
    [
        "what are the largest files on this machine?",
        "which folders take the most space on my mac?",
        "what is the biggest file on this computer",
        "how much free disk space do I have?",
    ],
)
def test_a_this_machine_fact_is_not_adjudicated_for_entity_ambiguity(question):
    assert registered_lane_for(question) == "machine_fact_fast_path"
    assert not single_plain_know_question(question)


def test_the_owner_question_reaches_the_bounded_largest_files_tool():
    assert machine_largest_intent("what are the largest files on this machine?") == "machine.find_largest"


@pytest.mark.parametrize(
    "question",
    [
        "What is the population of Springfield?",
        "In what year did the Berlin Wall fall?",
        "what is the capital of Georgia?",
    ],
)
def test_open_domain_questions_keep_their_eligibility(question):
    assert registered_lane_for(question) == ""
    assert single_plain_know_question(question)
