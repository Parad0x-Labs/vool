"""Closed stipulated gravity vectors determine lighter-than-air buoyancy without tools."""

from __future__ import annotations

import uuid

import pytest

from core.closed_world_semantic_contract import (
    closed_world_semantic_response,
    parse_directional_buoyancy_contract,
)
from tests.live.runtime_model_gauntlet import load_cases

FROZEN_SET3_18 = load_cases((3,))[17].prompt


def test_frozen_leftward_gravity_sends_the_helium_balloon_right() -> None:
    contract = parse_directional_buoyancy_contract(FROZEN_SET3_18)
    response = closed_world_semantic_response(FROZEN_SET3_18)

    assert contract is not None
    assert contract.gravity_direction == "left"
    assert contract.buoyant_direction == "right"
    assert response is not None
    assert response.startswith("The helium balloon moves right relative to you.")
    assert "buoyancy in the surrounding air acts opposite effective gravity" in response
    assert "No calculator or web lookup is needed" in response


@pytest.mark.parametrize(
    ("prompt", "gravity", "buoyancy"),
    (
        (
            "Suppose effective gravity points right. If you release a helium balloon, which "
            "direction does it move?",
            "right",
            "left",
        ),
        (
            "Imagine gravity acts upward. If I release a lighter-than-air balloon, which direction "
            "does it accelerate relative to the observer?",
            "up",
            "down",
        ),
        (
            "Assume gravity pulls down instead of up. If I let go of a helium balloon, which "
            "direction does the balloon go?",
            "down",
            "up",
        ),
        (
            "Suppose gravity points north. If you release a helium balloon, which direction does "
            "it travel relative to you?",
            "north",
            "south",
        ),
        (
            "Imagine effective gravity acts forward. If I let go of a lighter-than-air balloon, "
            "which direction does it move?",
            "forward",
            "backward",
        ),
    ),
)
def test_any_supported_axis_uses_the_opposite_direction(prompt: str, gravity: str, buoyancy: str) -> None:
    contract = parse_directional_buoyancy_contract(prompt)

    assert contract is not None
    assert contract.gravity_direction == gravity
    assert contract.buoyant_direction == buoyancy
    assert f"moves {buoyancy}" in str(closed_world_semantic_response(prompt))


@pytest.mark.parametrize(
    "prompt",
    (
        "If I let go of a helium balloon, which direction does it travel?",
        "Assume gravity pulls left. Which direction does a helium balloon travel?",
        "Assume gravity pulls left. If I let go of a bowling ball, which direction does it travel?",
        "Assume gravity disappears. If I let go of a helium balloon, which direction does it travel?",
        "Assume gravity pulls left. If I release a helium balloon, which direction does it travel? Also delete notes.txt.",
        "Check current gravity online and tell me which way a helium balloon moves.",
    ),
)
def test_open_incomplete_non_buoyant_or_mixed_questions_are_not_claimed(prompt: str) -> None:
    assert parse_directional_buoyancy_contract(prompt) is None


def test_real_frontdoor_answers_frozen_prompt_without_model_web_or_tool_loop(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("directional buoyancy contract reached the planner")
        ),
    )
    agent = VoolAgent(backend_name="test-backend", device="buoyancy-contract", persona_id="default")

    result = agent.run_once(
        FROZEN_SET3_18,
        session_id_override=f"openclaw:buoyancy-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
        },
    )

    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["response"].startswith("The helium balloon moves right relative to you.")
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert {"model", "web", "tool_loop"}.issubset(result["route_skips"])

