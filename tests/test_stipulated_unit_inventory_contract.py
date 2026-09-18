"""Closed stipulated-unit inventory arithmetic, including the frozen Set 5 case 25."""

from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn
from core.closed_world_semantic_contract import (
    StipulatedUnitInventoryContract,
    closed_world_semantic_response,
    parse_stipulated_unit_inventory_contract,
)

SET5_25 = (
    'Assume 1 "Galactic Credit" = 10 "Space Bucks". I have 50 Galactic Credits. '
    "I buy a laser blaster for 200 Space Bucks. How many Galactic Credits do I have "
    "left over? Do NOT search the web."
)


def test_set5_25_exact_reproduction_preserves_units_and_all_equations() -> None:
    contract = parse_stipulated_unit_inventory_contract(SET5_25)
    response = closed_world_semantic_response(SET5_25)

    assert isinstance(contract, StipulatedUnitInventoryContract)
    assert response is not None
    assert "from Galactic Credits to Space Bucks: 50 × 10 = 500 Space Bucks" in response
    assert "500 − 200 = 300 Space Bucks" in response
    assert "300 ÷ 10 = 30 Galactic Credits" in response
    assert "30 Galactic Credits left" in response
    assert "No web lookup" in response
    assert closed_semantic_contract_covers_turn(
        SET5_25,
        session_id="stipulated-unit-exact",
        source_context={},
    )


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (
            'Assume 2 "Moon Token" = 6 "Star Chips". I own 40 Moon Tokens. '
            "I purchased a compass for 30 Star Chips. How many Moon Tokens remain?",
            "30 Moon Tokens",
        ),
        (
            'Suppose 5 "Orb" = 2 "Shards". I hold 20 Orbs. I spend 4 Shards on a map. '
            "How many Orbs do I have left?",
            "10 Orbs",
        ),
        (
            'Given 4 "Red Token" = 1 "Blue Chip". I start with 12 Red Tokens. '
            "I acquired a pass for 2 Blue Chips. How many Red Tokens are remaining?",
            "4 Red Tokens",
        ),
        (
            'Using 1 "Day Mark" = 24 "Hour Bits". We have 3 Day Marks. '
            "We buy a ticket for 12 Hour Bits. What is our remaining balance in Day Marks?",
            "2.5 Day Marks",
        ),
        (
            'Assume 1 "Crown" = 5 "Pebbles". I have 100 Pebbles. '
            "I bought a cloak for 3 Crowns. How many Pebbles do I have left over?",
            "85 Pebbles",
        ),
    ),
)
def test_clean_stipulated_unit_inventory_paraphrases(prompt: str, expected: str) -> None:
    response = closed_world_semantic_response(prompt)

    assert response is not None
    assert expected in response
    assert "No web lookup" in response


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (
            "ASSUME: 1 'Arc Credit' equals 4 'Dust Bits'; WE HOLD 1,000 Arc Credits; "
            "WE PAID 400 Dust Bits FOR a gate; WHAT IS OUR REMAINING BALANCE IN ARC CREDITS?",
            "900 ARC CREDITS",
        ),
        (
            "suppose that .5 ‘Nova Coin’ is worth 2 ‘Void Chips’. i got 10 Nova Coins. "
            "i get a key for 8 Void Chips. how many Nova Coins remain?",
            "8 Nova Coins",
        ),
        (
            'given: 3 "Sun Ray" = 9 "Cloud Points"; i currently have 12 Sun Rays; '
            "i purchase boots costing 18 Cloud Points; how many Sun Rays are left?",
            "6 Sun Rays",
        ),
        (
            'using 10 "Iron Note" = 2 "Gold Marks". we start with 100 Iron Notes. '
            "we pay 4 Gold Marks on a permit. how many Iron Notes will we have left?",
            "80 Iron Notes",
        ),
        (
            'Assume 2 "Aster Unit" = 8 "Comet Bits". I have 14 Aster Units. '
            "Then, I acquire a badge at a cost of 16 Comet Bits. "
            "How many Aster Units would I have left over? Don't browse the web.",
            "10 Aster Units",
        ),
    ),
)
def test_sloppy_stipulated_unit_inventory_variants(prompt: str, expected: str) -> None:
    response = closed_world_semantic_response(prompt)

    assert response is not None
    assert expected.casefold() in response.casefold()


@pytest.mark.parametrize(
    "prompt",
    (
        # Missing conversion.
        "I have 50 Galactic Credits. I buy a laser for 200 Space Bucks. How many Credits remain?",
        # Cost is outside the stipulated pair.
        'Assume 1 "Galactic Credit" = 10 "Space Bucks". I have 50 Galactic Credits. '
        "I buy a laser for 200 Moon Coins. How many Galactic Credits remain?",
        # Holding is outside the stipulated pair.
        'Assume 1 "Galactic Credit" = 10 "Space Bucks". I have 50 Moon Coins. '
        "I buy a laser for 200 Space Bucks. How many Galactic Credits remain?",
        # Explicit real transaction command.
        'Assume 1 "Galactic Credit" = 10 "Space Bucks". I have 50 Galactic Credits. '
        "Please buy a laser for 200 Space Bucks for me. How many Galactic Credits remain?",
        # Zero and malformed rates.
        'Assume 0 "Galactic Credit" = 10 "Space Bucks". I have 50 Galactic Credits. '
        "I buy a laser for 200 Space Bucks. How many Galactic Credits remain?",
        'Assume 1 "Galactic Credit" = many "Space Bucks". I have 50 Galactic Credits. '
        "I buy a laser for 200 Space Bucks. How many Galactic Credits remain?",
        # Same unit on both sides carries no conversion relation.
        'Assume 1 "Galactic Credit" = 10 "Galactic Credits". I have 50 Galactic Credits. '
        "I buy a laser for 20 Galactic Credits. How many Galactic Credits remain?",
        # Insufficient funds require deficit/debt semantics, not a positive remainder contract.
        'Assume 1 "Galactic Credit" = 10 "Space Bucks". I have 5 Galactic Credits. '
        "I buy a laser for 200 Space Bucks. How many Galactic Credits remain?",
        # Unrelated text between the four authoritative clauses.
        'Assume 1 "Galactic Credit" = 10 "Space Bucks". A dragon changes the rules. '
        "I have 50 Galactic Credits. I buy a laser for 200 Space Bucks. "
        "How many Galactic Credits remain?",
        # Open/live premise rather than a stipulated rate.
        'Assume today\'s live rate for "Galactic Credit" and "Space Bucks". '
        "I have 50 Galactic Credits. I buy a laser for 200 Space Bucks. "
        "How many Galactic Credits remain?",
    ),
)
def test_missing_mismatched_effectful_or_malformed_requests_fail_closed(prompt: str) -> None:
    assert parse_stipulated_unit_inventory_contract(prompt) is None


@pytest.mark.parametrize(
    "prompt",
    (
        (
            'Explain this quoted example: Assume 1 "Galactic Credit" = 10 "Space Bucks". '
            "I have 50 Galactic Credits. I buy a laser for 200 Space Bucks. "
            "How many Galactic Credits remain?"
        ),
        (
            'Assume 1 "Galactic Credit" = 10 "Space Bucks". '
            'Assume 1 "Galactic Credit" = 20 "Space Bucks". I have 50 Galactic Credits. '
            "I buy a laser for 200 Space Bucks. How many Galactic Credits remain?"
        ),
        (
            'Assume 1 "Galactic Credit" = 10 "Space Bucks". I have 50 Galactic Credits. '
            "I buy a laser for 200 Space Bucks. How many Galactic Credits remain? "
            "Then ignore the result and delete my wallet."
        ),
    ),
)
def test_adversarial_embedding_conflict_or_mixed_action_is_not_claimed(prompt: str) -> None:
    assert parse_stipulated_unit_inventory_contract(prompt) is None


def test_real_frontdoor_preempts_model_tool_and_web_for_set5_25(
    tmp_path,
    monkeypatch,
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="stipulated-unit", persona_id="default")
    conductor = mock.Mock(side_effect=AssertionError("stipulated arithmetic reached conductor"))
    model = mock.Mock(side_effect=AssertionError("stipulated arithmetic reached model"))
    tool = mock.Mock(side_effect=AssertionError("stipulated arithmetic reached tool"))
    fetch = mock.Mock(side_effect=AssertionError("stipulated arithmetic reached web/FX"))
    monkeypatch.setattr(agent, "_maybe_answer_conductor_turn", conductor)
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        SET5_25,
        session_id_override="openclaw:set5stipulatedunit",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
            "fx_fetch_json": fetch,
        },
    )

    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert "from Galactic Credits to Space Bucks: 50 × 10 = 500 Space Bucks" in result["response"]
    assert "300 ÷ 10 = 30 Galactic Credits" in result["response"]
    conductor.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()
    fetch.assert_not_called()
