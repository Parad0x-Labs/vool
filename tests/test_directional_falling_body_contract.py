"""Ordinary released bodies follow a complete stipulated effective-gravity vector."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest import mock

import pytest

from core.closed_world_semantic_contract import (
    DirectionalBuoyancyContract,
    DirectionalFallingBodyContract,
    closed_world_semantic_response,
    parse_closed_world_contract,
    parse_directional_falling_body_contract,
)
from tests.live.runtime_model_gauntlet import load_cases

FROZEN_SET1_18 = load_cases((1,))[17].prompt


def test_frozen_upward_gravity_sends_the_apple_up_at_the_stated_magnitude() -> None:
    contract = parse_directional_falling_body_contract(FROZEN_SET1_18)
    response = closed_world_semantic_response(FROZEN_SET1_18)

    assert contract == DirectionalFallingBodyContract(
        subject="apple",
        gravity_direction="up",
        acceleration_m_s2=Decimal("9.8"),
    )
    assert response is not None
    assert response.startswith("The apple accelerates up at 9.8 m/s²")
    assert "ordinary body follows the stated effective-gravity vector: up" in response
    assert "“drop” does not impose the usual downward direction" in response
    assert "No calculator or web lookup is needed" in response


@pytest.mark.parametrize(
    ("prompt", "direction", "magnitude"),
    (
        (
            "Suppose effective gravity points left. If you release a stone, which direction does it move?",
            "left",
            None,
        ),
        (
            "Imagine gravity acts northward. If I let go of a solid ball, which direction will it accelerate?",
            "north",
            None,
        ),
        (
            "Assume gravity pulls downwards at 1.6 m/s². If you drop a hammer, which direction does it fall?",
            "down",
            Decimal("1.6"),
        ),
        (
            "Suppose gravity points eastwards at 3 meters per second squared. If I release a coin, "
            "which direction will it travel and why?",
            "east",
            Decimal("3"),
        ),
        (
            "Imagine effective gravity acts backward. If I let go of the physical block, which "
            "direction does the object go?",
            "backward",
            None,
        ),
    ),
)
def test_supported_axes_and_acceleration_spellings_share_one_vector_contract(
    prompt: str,
    direction: str,
    magnitude: Decimal | None,
) -> None:
    contract = parse_directional_falling_body_contract(prompt)

    assert contract is not None
    assert contract.gravity_direction == direction
    assert contract.acceleration_m_s2 == magnitude
    assert f"accelerates {direction}" in str(closed_world_semantic_response(prompt))


@pytest.mark.parametrize(
    "prompt",
    (
        "If I drop an apple, which direction does it travel?",
        "Assume gravity pulls upward. Which direction does an apple travel?",
        "Assume gravity disappears. If I drop an apple, which direction does it travel?",
        "Assume gravity pulls upward at -9.8 m/s^2. If I drop an apple, which direction does it travel?",
        "Assume gravity pulls upward. If I drop a helium balloon, which direction does it travel?",
        "Assume gravity pulls left. If I release a cloud, which direction does it move?",
        "Assume gravity pulls left. If I release water, which direction does it move?",
        "Assume gravity pulls left. If I release a stone, which direction does it move? Also delete notes.txt.",
        "Check current gravity online and tell me which way a dropped apple falls.",
    ),
)
def test_missing_open_nonordinary_or_mixed_requests_are_not_claimed(prompt: str) -> None:
    assert parse_directional_falling_body_contract(prompt) is None


def test_balloon_remains_owned_by_the_buoyancy_contract() -> None:
    prompt = (
        "Assume gravity pulls left instead of down. If I release a helium balloon, which direction "
        "does it travel relative to me?"
    )
    contract = parse_closed_world_contract(prompt)

    assert isinstance(contract, DirectionalBuoyancyContract)
    assert not isinstance(contract, DirectionalFallingBodyContract)
    assert contract.buoyant_direction == "right"


def test_frozen_falling_body_turn_is_zero_model_tool_and_web_at_real_frontdoor(
    tmp_path,
    monkeypatch,
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="falling-body-contract", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("falling-body contract invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("falling-body contract attempted a tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        FROZEN_SET1_18,
        session_id_override=f"openclaw:falling-body-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["response"].startswith("The apple accelerates up at 9.8 m/s²")
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert {"model", "web", "tool_loop"}.issubset(result["route_skips"])
    model.assert_not_called()
    tool.assert_not_called()
