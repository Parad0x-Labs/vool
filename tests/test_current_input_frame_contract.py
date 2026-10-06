"""Current background state is not an instruction to obtain a live measurement.

The camera row is a known-failure replay. Other rows were authored before the
first repair run. These tests distinguish routing/withdrawal from reader quality.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for
from core.execution_requirements import requirements_for
from core.temporal_question_scope import question_time_scope
from core.unsourced_current_claim import inspect_unsourced_current_claim

CAMERA_QUESTION = "Can you suggest some accessories that would complement my current photography setup?"
CAMERA_REPLY = "For your Sony A7R IV and 24-70mm, I'd suggest a solid tripod with an L-bracket, a circular polarizer and ND filter."

@pytest.mark.parametrize("question", [
    CAMERA_QUESTION,
    "Can you recommend mounting accessories for my current telescope assembly?",
    "Please suggest a stand compatible with our current microscope assembly.",
    "Recommend packing materials for her current sculpture collection.",
    "Could you suggest a carrying case suited to my current field recorder?",
])
def test_proposal_uses_current_state_as_input_not_observation(question):
    scope = question_time_scope(question)
    assert scope.current_input_only
    assert not scope.asks_current
    assert scope.current_input_spans
    assert all(question[a:b].lower() == "current" for a, b in scope.current_input_spans)
    req = requirements_for(question)
    assert req.answer_mode == "DIRECT"
    assert not req.current_information_required
    assert not req.tools_required
    assert "current_input_frame_not_observation" in req.reason_codes
    assert answer_mode_for(question) is AnswerMode.DIRECT

@pytest.mark.parametrize("suffix", [" with sources", " and do not guess"])
def test_evidence_promise_remains_grounded_without_inventing_freshness(suffix):
    question = CAMERA_QUESTION.rstrip("?") + suffix + "?"
    req = requirements_for(question)
    assert req.answer_mode == "GROUNDED"
    assert req.tools_required
    assert req.external_evidence_required
    assert not req.current_information_required

@pytest.mark.parametrize("question", [
    "What is my current photography setup?",
    "Can you recommend the current best-value telescope?",
    "Can you recommend accessories for my current telescope assembly and tell me their prices today?",
    "Can you suggest a stand for my current microscope assembly and what does it cost now?",
    "Can you recommend a stand for my current microscope assembly; measure its temperature?",
    "Can you recommend a stand for my current microscope assembly and install it?",
    "Can you recommend a way to measure the current pressure in my tank?",
    "How much liquid is in my current reservoir right now?",
    "Can you recommend a lens for my current camera using the latest compatibility sources?",
    "Can you recommend accessories for my current rig based on compatibility news this month?",
    "Can you recommend accessories for my current rig based on compatibility news this quarter?",
    "Can you recommend accessories for my current rig based on compatibility news this year?",
])
def test_other_requested_observations_and_actions_are_not_exempt(question):
    assert not question_time_scope(question).current_input_only
    assert requirements_for(question).current_information_required


def test_known_camera_raw_reply_is_not_withdrawn_as_a_live_reading():
    req = requirements_for(CAMERA_QUESTION)
    verdict = inspect_unsourced_current_claim(answer=CAMERA_REPLY,
        requires_current=req.current_information_required, user_turn_text=CAMERA_QUESTION)
    assert not verdict.unsupported


def test_recommendation_cannot_establish_a_present_private_quantity():
    question = "How much water is in my current tank right now?"
    req = requirements_for(question)
    verdict = inspect_unsourced_current_claim(answer="Your tank contains 410 litres right now.",
        requires_current=req.current_information_required, user_turn_text=question)
    assert verdict.unsupported


def test_q338_comparison_asymmetry_is_preserved_and_not_counted_as_rescued():
    # Frozen live regression: mpg is outside the guard unit vocabulary, while
    # the candidate's extra percent trips it. The camera fix cannot reclassify
    # a mixed past/now quantity ask as ordinary proposal input.
    question = "How much more miles per gallon was my car getting a few months ago compared to now?"
    req = requirements_for(question)
    assert req.current_information_required
    control = "Your car was getting 30 mpg a few months ago vs. 28 mpg now — that's 2 mpg better back then."
    candidate = "A few months ago it was getting 30 mpg in the city, and you're at 28 now — so it was 2 mpg better back then (about a 7% drop)."
    assert not inspect_unsourced_current_claim(answer=control, requires_current=True).unsupported
    assert inspect_unsourced_current_claim(answer=candidate, requires_current=True).unsupported
