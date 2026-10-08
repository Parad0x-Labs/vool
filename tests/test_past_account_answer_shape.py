"""An account of a past event is delivered as text, not as a plan document.

"What did Sela hear about at the porcelain guild meeting?" is classified
`integration_orchestration` on the bare word "meeting", and that class wraps the
model reply in a {"summary", "steps"} plan. The requested answer contract owns the
wire shape of a chat-surface deliverable; a fresh plan request keeps the plan.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.runtime_checkpoint_lane_policy import model_routing_profile
from core.reasoning_engine import resolve_requested_answer_contract
from core.task_router import classify, model_execution_profile


def _route(prompt: str):
    with mock.patch("core.task_router._classify_via_model", side_effect=AssertionError("offline routing must not classify with a model")):
        classification = classify(prompt, context={"source_surface": "api", "source_platform": "api"})
        return model_routing_profile(
            SimpleNamespace(_is_chat_truth_surface=lambda context: True),
            user_input=prompt,
            classification=classification,
            interpretation=SimpleNamespace(as_context=lambda: {}),
            source_context={"surface": "api", "platform": "api"},
        )


@pytest.mark.parametrize("prompt", [
    "What did Sela hear about at the porcelain guild meeting?",
    "What did we decide at the sync meeting last week?",
    "What was discussed at the team meeting?",
])
def test_past_account_on_an_action_class_is_plain_text(prompt):
    classification, profile = _route(prompt)
    assert classification["task_class"] == "integration_orchestration"
    assert profile["output_mode"] == "plain_text"
    # Only the wire shape changes; the class's task kind and spending policy are kept.
    base = model_execution_profile("integration_orchestration")
    assert profile["task_kind"] == base["task_kind"]
    assert profile["allow_paid_fallback"] == base["allow_paid_fallback"]


def test_past_account_keeps_answer_kind():
    contract = resolve_requested_answer_contract("What did Sela hear about at the porcelain guild meeting?")
    assert contract.kind == "answer"
    assert contract.output_mode == "plain_text"
    assert not contract.preserve_source_format
    assert not contract.planner_style


@pytest.mark.parametrize("prompt", [
    "Make me an action plan for the team meeting.",
    "What steps did you take to set up the calendar sync?",
])
def test_requested_plan_keeps_the_plan_shape(prompt):
    _, profile = _route(prompt)
    assert profile["output_mode"] == "action_plan"


@pytest.mark.parametrize("prompt", [
    "Schedule a meeting with Ana tomorrow at 3pm",
    "How do I set up the telegram integration?",
])
def test_non_past_requests_are_unchanged(prompt):
    assert resolve_requested_answer_contract(prompt).output_mode == ""


def test_non_chat_surface_profile_is_unchanged():
    assert model_execution_profile("integration_orchestration")["output_mode"] == "action_plan"
