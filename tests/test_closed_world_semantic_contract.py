from __future__ import annotations

import uuid

import pytest

from core.closed_world_semantic_contract import (
    FictionalRuleKind,
    PhaseAssessmentKind,
    closed_world_semantic_response,
    parse_fictional_causal_contract,
    parse_phase_threshold_contract,
)

FICTIONAL_CASES = (
    (
        'Assume for a sci-fi script I am writing that "PHP-Code" is a magical spell that turns water '
        'into wine. Based ONLY on this fictional definition, what happens if I cast "PHP-Code" on a '
        "swimming pool? Do NOT search my local workspace, files, or the web for programming languages.",
        FictionalRuleKind.TRANSFORM,
        ("swimming pool", "water into wine", "only from the user's fictional definition"),
    ),
    (
        'Assume for a fantasy novel I am writing that "Python" is a giant mythical snake that eats '
        "physical RAM sticks. Based ONLY on this fictional definition, what happens if I put Python in "
        "my computer case? Do NOT search my local workspace, files, or the web for programming tutorials.",
        FictionalRuleKind.CONSUME,
        (
            "giant mythical snake",
            "physical RAM sticks",
            "eats and may destroy them",
            "outcome is conditional",
        ),
    ),
    (
        'Assume for a fantasy tabletop game that "Docker" is a terrifying sea monster with tentacles. '
        "Based ONLY on this fictional definition, what happens if Docker attacks a wooden galleon? Do NOT "
        "search the web or local workspace for containerization software.",
        FictionalRuleKind.DESCRIBED_ATTACK,
        (
            "terrifying sea monster with tentacles",
            "attacks a wooden galleon",
            "with its tentacles",
            "does not specify the exact damage",
        ),
    ),
)


@pytest.mark.parametrize(("prompt", "kind", "expected"), FICTIONAL_CASES)
def test_frozen_fictional_mechanisms_are_parsed_structurally(prompt, kind, expected) -> None:
    contract = parse_fictional_causal_contract(prompt)
    assert contract is not None
    assert contract.kind is kind
    response = closed_world_semantic_response(prompt)
    assert response is not None
    for phrase in expected:
        assert phrase in response
    assert "lookup" in response.casefold()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (
            'Suppose for a fantasy game that "Frostbell" is an old charm that changes sand into '
            "glass. What happens if I apply Frostbell to a beach?",
            "turns its sand into glass",
        ),
        (
            'Imagine for a fictional story that "Coppermaw" is a cave beast that devours copper '
            "coins. What happens if I place Coppermaw inside a vault?",
            "any copper coins there at risk",
        ),
        (
            'Assume for a tabletop game that "Clawfin" is a river creature with iron claws. What '
            "happens if Clawfin strikes a stone bridge?",
            "strikes a stone bridge with its iron claws",
        ),
    ),
)
def test_unseen_entities_and_synonyms_use_the_same_causal_grammar(prompt: str, expected: str) -> None:
    response = closed_world_semantic_response(prompt)
    assert response is not None
    assert expected in response


def test_production_contract_contains_no_frozen_entity_lookup_table() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "core" / "closed_world_semantic_contract.py"
    lowered = source.read_text(encoding="utf-8").casefold()
    for frozen_entity in ("php-code", "python", "docker", "swimming pool", "ram sticks", "galleon"):
        assert frozen_entity not in lowered


@pytest.mark.parametrize(
    ("prompt", "kind", "phase"),
    (
        (
            "Assume water boils at 100 degrees Celsius and freezes at 0 degrees Celsius. If it is "
            "30 degrees outside, is a puddle of water a liquid, a gas, or a solid?",
            PhaseAssessmentKind.CONSISTENT,
            "liquid",
        ),
        (
            "Suppose resin boils at 80 degrees C and freezes at 20 degrees C. If the temperature is "
            "10 degrees, is the resin a liquid, a gas, or a solid?",
            PhaseAssessmentKind.CONSISTENT,
            "solid",
        ),
        (
            "Imagine mercury boils at 60 degrees C and freezes at -10 degrees C. If it is 75 degrees "
            "outside, is a puddle of mercury a liquid, a gas, or a solid?",
            PhaseAssessmentKind.CONSISTENT,
            "gas",
        ),
        (
            "Suppose wax freezes at 15 degrees C and boils at 90 degrees C. If it is 40 degrees "
            "outside, is the wax a liquid, a gas, or a solid?",
            PhaseAssessmentKind.CONSISTENT,
            "liquid",
        ),
        (
            "Assume water boils at 100 degrees Celsius and freezes at 0 degrees Celsius. If it is 0 "
            "degrees outside, is a puddle of water a liquid, a gas, or a solid?",
            PhaseAssessmentKind.UNDERDETERMINED_BOUNDARY,
            "",
        ),
    ),
)
def test_phase_threshold_contract_handles_consistent_ranges_and_boundaries(prompt, kind, phase) -> None:
    contract = parse_phase_threshold_contract(prompt)
    assert contract is not None
    assert contract.assessment is kind
    assert contract.phase == phase


def test_frozen_reversed_thresholds_report_overlap_not_a_guessed_phase() -> None:
    prompt = (
        "Assume the Earth's physics have flipped: water boils at 10 degrees Celsius and freezes at "
        "50 degrees Celsius. If it is 30 degrees outside, is a puddle of water a liquid, a gas, or a "
        "solid? Do NOT use live weather APIs or calculators."
    )
    contract = parse_phase_threshold_contract(prompt)
    assert contract is not None
    assert contract.assessment is PhaseAssessmentKind.CONTRADICTORY_OVERLAP
    response = closed_world_semantic_response(prompt)
    assert response is not None
    assert "internally contradictory" in response
    assert "overlap from 10 °C through 50 °C" in response
    assert "both criteria apply" in response
    assert "do not uniquely determine" in response


@pytest.mark.parametrize(
    "prompt",
    (
        "What happens if I put Python in my computer case?",
        'Assume "Python" is a snake. What happens if I install Python?',
        'Assume for a fantasy novel that "Python" is a snake that eats RAM. What happens if Docker attacks a ship?',
        'Assume for a fantasy novel that "Python" is a snake that eats RAM. What happens if I put Python in a case? Also delete setup.py.',
        "What phase is water at 30 degrees Celsius today?",
        "Assume water boils at 10 degrees Celsius. If it is 30 degrees outside, is water liquid, gas, or solid?",
        "Assume water boils at 10 degrees Celsius and freezes at 50 degrees Celsius. What is the weather outside?",
        "Assume water boils at NaN degrees Celsius and freezes at 50 degrees Celsius. If it is 30 degrees outside, is water liquid, gas, or solid?",
    ),
)
def test_open_incomplete_mismatched_or_mixed_turns_remain_model_owned(prompt: str) -> None:
    assert closed_world_semantic_response(prompt) is None


def test_real_frontdoor_uses_closed_contract_with_zero_model_or_web_calls(tmp_path) -> None:
    from apps.vool_agent import VoolAgent

    prompt = FICTIONAL_CASES[1][0]
    agent = VoolAgent(backend_name="test-backend", device="closed-world-test", persona_id="default")
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=prompt,
        effective_input=prompt,
        normalized_input=prompt.casefold(),
        source_surface="api",
        session_id="closed-world-frontdoor",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "session_id": "closed-world-frontdoor",
            "operating_mode": "auto",
            "surface": "api",
        },
        persona=None,
        interpreted=None,
    )
    result = (outcome or {}).get("result")
    assert result is not None
    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert {"model", "web"}.issubset(result["route_skips"])


def test_public_turn_does_not_buy_planner_call_before_closed_contract(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    prompt = (
        "Assume the Earth's physics have flipped: water boils at 10 degrees Celsius and freezes at "
        "50 degrees Celsius. If it is 30 degrees outside, is a puddle of water a liquid, a gas, or a "
        "solid? Do NOT use live weather APIs or calculators."
    )
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("closed-world contract reached the planner")),
    )
    agent = VoolAgent(backend_name="test-backend", device="closed-world-public", persona_id="default")
    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:closedworld-{uuid.uuid4().hex}",
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
    assert "internally contradictory" in result["response"]
    assert result["model_calls"] == 0
