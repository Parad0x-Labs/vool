from __future__ import annotations

import uuid
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.underspecified_label_contract import underspecified_label_response
from tests.live.runtime_model_gauntlet import load_cases


def test_frozen_dry_water_label_is_explicitly_underdetermined() -> None:
    response = underspecified_label_response(load_cases((4,))[5].prompt)

    assert response is not None
    assert "product name" in response
    assert "could name" in response
    assert "cannot be determined" in response
    assert "definition, composition, or product specification is needed" in response


@pytest.mark.parametrize(
    "prompt",
    (
        'A datasheet labels one item “moon glass” without a description or composition. Can we determine its material from that sentence alone?',
        'An inventory names a product “cold flame” but gives no definition or specification. Explain what can and cannot be inferred without context.',
    ),
)
def test_unseen_undefined_labels_use_the_same_epistemic_contract(prompt: str) -> None:
    response = underspecified_label_response(prompt)
    assert response is not None
    assert "cannot be determined" in response


@pytest.mark.parametrize(
    "prompt",
    (
        'A catalog calls one product “dry water” and specifies it is a silica powder. What is it?',
        'Write a story titled “Dry Water.”',
        'Look up the composition of the product “dry water.”',
        'The phrase “dry water” is funny. Explain the joke.',
        'A catalog gives no definition for a product. What products does it sell?',
    ),
)
def test_defined_lookup_creative_or_incomplete_near_misses_decline(prompt: str) -> None:
    assert underspecified_label_response(prompt) is None


def test_public_turn_resolves_frozen_label_without_model_tool_or_web(tmp_path, monkeypatch) -> None:
    prompt = load_cases((4,))[5].prompt
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(
        backend_name="test-backend",
        device="underspecified-label-public",
        persona_id="default",
    )
    model = mock.Mock(side_effect=AssertionError("undefined label reached model"))
    tool = mock.Mock(side_effect=AssertionError("undefined label reached tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:undefined-label-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "underspecified_label_contract"
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert "cannot be determined" in result["response"]
    model.assert_not_called()
    tool.assert_not_called()
