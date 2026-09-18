from __future__ import annotations

import uuid
from unittest import mock

import pytest

from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import NodeContext
from core.conductor.safe_content_shape import (
    ExactPoemShape,
    parse_exact_poem_shape,
    seal_poem_artifact,
)
from core.conductor.scheduler import run_conductor_plan
from tests.conductor_product import compose_product

FROZEN = (
    "Send a text message to my mom saying I love her. Then, write a 2-line poem about mothers."
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        ("write a 2-line poem about mothers", ExactPoemShape(2, "mothers")),
        ("create two line verse about my mom", ExactPoemShape(2, "mom")),
        ("provide a 3-line poem about ocean storms", ExactPoemShape(3, "ocean storms")),
        ("return four lines of verse about the moon", ExactPoemShape(4, "moon")),
        ("draft a five-line poem about resilient nurses", ExactPoemShape(5, "resilient nurses")),
    ),
)
def test_exact_poem_shape_derives_line_count_and_subject(prompt, expected) -> None:
    assert parse_exact_poem_shape(prompt) == expected


def test_sealer_requires_exact_lines_and_explicit_subject_family_term() -> None:
    shape = ExactPoemShape(2, "mothers")
    assert seal_poem_artifact(
        "Mom, your steady love makes every hard day light\n"
        "Your patient voice still guides me through the night",
        shape,
    ) is not None
    assert seal_poem_artifact(
        "Your steady love makes every hard day light\n"
        "Your patient voice still guides me through the night",
        shape,
    ) is None


def test_poem_fence_is_unwrapped_and_unrelated_prose_is_removed() -> None:
    artifact = seal_poem_artifact(
        "Here is the poem:\n```poem\nMother, you anchor every restless shore\n"
        "Your love keeps teaching me what home is for\n```\nHope this helps.",
        ExactPoemShape(2, "mother"),
    )
    assert artifact is not None
    assert artifact.poem.splitlines() == [
        "Mother, you anchor every restless shore",
        "Your love keeps teaching me what home is for",
    ]
    assert artifact.unrelated_output_removed is True


@pytest.mark.parametrize(
    "draft",
    (
        "Mother, one line only",
        "Mother, line one\nLine two\nLine three",
        "A patient light still leads me home\nA steady hand when I would roam",
        "Mother, line one\n\nLine two",
        "```python\n# mother\nprint('love')\n```",
        "```poem\nMother, first\nMother, second\n```\n```text\nMother, duplicate\nMother, duplicate\n```",
    ),
)
def test_wrong_count_missing_subject_blank_lines_or_ambiguous_fences_fail_closed(draft) -> None:
    assert seal_poem_artifact(draft, ExactPoemShape(2, "mother")) is None


def test_frozen_mixed_turn_keeps_action_truth_and_only_two_poem_lines() -> None:
    planner = mock.Mock(side_effect=AssertionError("deterministic mixed shape reached planner"))
    tool = mock.Mock(side_effect=AssertionError("outbound message reached tool execution"))
    model = mock.Mock(
        return_value=(
            "Here is the poem:\n```poem\nMom, your love is shelter in the rain\n"
            "Your courage helps me rise through every pain\n```\nExtra commentary."
        )
    )
    plan = plan_conductor_turn(FROZEN, ask_model=planner, plan_id="exact-poem-sibling")
    assert plan is not None
    assert plan.operations == frozenset({"unavailable_action", "safe_content_generation"})
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=tool, run_generation=model),
    )
    answer = compose_product(plan, outcomes)
    assert answer.complete
    assert answer.unserved_count == 0
    assert "did not attempt" in answer.text
    lines = [line for line in answer.text.splitlines() if line.strip()]
    assert len(lines) == 3  # one action-status line plus exactly two poem lines
    assert len(lines[-2:]) == 2
    assert any(term in "\n".join(lines[-2:]).casefold() for term in ("mother", "mom"))
    assert "extra commentary" not in answer.text.casefold()
    planner.assert_not_called()
    tool.assert_not_called()


def test_invalid_first_poem_gets_one_bounded_repair() -> None:
    drafts = iter(
        (
            "Love guides me through the rain\nHope helps me rise again",
            "Mother, your love guides me through the rain\nYour hope helps me rise again",
        )
    )
    plan = plan_conductor_turn(FROZEN, ask_model=lambda *_: "", plan_id="poem-repair")
    assert plan is not None
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=None, run_generation=lambda *_: next(drafts)),
    )
    answer = compose_product(plan, outcomes)
    assert answer.unserved_count == 0
    assert "Mother" in answer.text


def test_two_invalid_poems_preserve_action_truth_and_report_poem_unserved() -> None:
    model = mock.Mock(return_value="Love guides me through the rain\nHope helps me rise again")
    plan = plan_conductor_turn(FROZEN, ask_model=lambda *_: "", plan_id="poem-failed")
    assert plan is not None
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=None, run_generation=model),
    )
    answer = compose_product(plan, outcomes)
    assert "did not attempt" in answer.text
    assert answer.unserved_count == 1
    assert "could not be answered" in answer.text
    assert "Love guides" not in answer.text
    assert model.call_count == 2


@pytest.mark.parametrize(
    "prompt",
    (
        "write a poem about mothers",
        "write two lines about mothers",
        "explain a two-line poem about mothers",
        "write a 0-line poem about mothers",
        "write a 99-line poem about mothers",
        "write a 2-line Python script about mothers",
        "write a 2-line poem",
    ),
)
def test_non_poem_unbounded_nonsense_or_subjectless_requests_are_not_exact_contracts(prompt) -> None:
    assert parse_exact_poem_shape(prompt) is None


def test_real_runtime_has_two_poem_lines_subject_and_no_tool_effect(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    planner = mock.Mock(side_effect=AssertionError("frozen mixed shape reached planner"))
    generation = mock.Mock(
        return_value=(
            "Mother, your love is shelter in the rain\n"
            "Your courage helps me rise through every pain\nUnrelated third line"
        )
    )
    repaired = mock.Mock(
        return_value=(
            "Mother, your love is shelter in the rain\n"
            "Your courage helps me rise through every pain"
        )
    )
    calls = iter((generation.return_value, repaired.return_value))
    tool = mock.Mock(side_effect=AssertionError("outbound text reached tool execution"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: planner,
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *_args, **_kwargs: lambda *_: next(calls),
    )
    agent = VoolAgent(backend_name="test-backend", device="exact-poem-test", persona_id="default")
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)
    result = agent.run_once(
        FROZEN,
        session_id_override=f"exact-poem-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "allow_remote_fetch": False,
            "local_only": True,
            "requested_model": "vool-local-only",
        },
    )
    assert result is not None
    assert result["route_reason"] == "conductor_multi_intent_plan"
    lines = [line for line in result["response"].splitlines() if line.strip()]
    assert len(lines) == 3
    assert len(lines[-2:]) == 2
    assert "mother" in "\n".join(lines[-2:]).casefold()
    assert "did not attempt" in lines[0]
    planner.assert_not_called()
    tool.assert_not_called()
