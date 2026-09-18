"""Qualified Georgia travel arithmetic outranks bounded static-code identity."""

from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.currency_travel_spend import TravelSpendRequest, travel_spend_intent
from core.currency_value_contract import maybe_answer_currency_value

FROZEN_NORMALIZED = (
    "I have 5,000 units of currency in Georgia(the US State). I travel to Georgia(the country "
    "in the Caucasus) and buy a hat for 50 units of local currency. Assuming 1 USD = 2.6 GEL, "
    "how much GEL do I have left over? Explicitly name both currencies and show the math."
)

CLEAN_QUALIFIERS = (
    ("Georgia (the US state)", "Georgia (the country in the Caucasus)"),
    ("Georgia (a state in the United States)", "Georgia (a Caucasus country)"),
    ("Georgia (an American state)", "Georgia (the sovereign country)"),
    ("Georgia (the state in the USA)", "Georgia (the Republic)"),
    ("Georgia (United States state)", "Georgia (Caucasus nation)"),
)

SLOPPY_QUALIFIERS = (
    ("Georgia(the US state)", "Georgia(the country in the Caucasus)"),
    ("Georgia( THE U.S. STATE )", "Georgia(THE COUNTRY IN THE CAUCASUS)"),
    ("Georgia(theUSstate)", "Georgia(thecountryintheCaucasus)"),
    ("Georgia(state-in-USA)", "Georgia(republic-in-the-caucasus)"),
    ("Georgia[american state]", "Georgia[caucasus nation]"),
)


def _travel_prompt(source: str, target: str) -> str:
    return (
        f"I have 5,000 units of currency in {source}. I travel to {target} and buy a hat for "
        "50 units of local currency. Assuming 1 USD = 2.6 GEL, how much GEL do I have left over? "
        "Explicitly name both currencies and show the math."
    )


def _assert_complete_travel_answer(prompt: str) -> None:
    assert maybe_answer_currency_value(prompt) is None
    request = travel_spend_intent(prompt)
    claimed = currency_fast_path(prompt)

    assert isinstance(request, TravelSpendRequest)
    assert request.source_code == "USD"
    assert request.target_code == "GEL"
    assert request.missing_rate is False
    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert claimed["codes"] == ("USD", "GEL")
    assert "United States dollar (USD)" in claimed["response"]
    assert "Georgian lari (GEL)" in claimed["response"]
    assert "5,000 USD × (2.6 GEL / 1 USD) = 13,000 GEL" in claimed["response"]
    assert "13,000 GEL − 50 GEL = 12,950 GEL" in claimed["response"]
    assert "12,950 GEL left over" in claimed["response"]
    assert "4,950 USD" not in claimed["response"]


def test_exact_normalized_reproduction_is_not_swallowed_by_static_identity() -> None:
    _assert_complete_travel_answer(FROZEN_NORMALIZED)


@pytest.mark.parametrize(("source", "target"), CLEAN_QUALIFIERS)
def test_clean_typed_qualifier_paraphrases_preserve_state_country_identity(
    source: str,
    target: str,
) -> None:
    _assert_complete_travel_answer(_travel_prompt(source, target))


@pytest.mark.parametrize(("source", "target"), SLOPPY_QUALIFIERS)
def test_sloppy_and_normalized_qualifier_variants_preserve_state_country_identity(
    source: str,
    target: str,
) -> None:
    _assert_complete_travel_answer(_travel_prompt(source, target))


@pytest.mark.parametrize(
    "location",
    (
        "Georgia (country music genre)",
        "Georgia (a state machine in software)",
        "Georgia (US state and sovereign country)",
        "Springfield (US state)",
    ),
)
def test_non_entity_or_conflicting_parentheticals_do_not_invent_a_currency(location: str) -> None:
    import core.currency_travel_spend as travel

    assert travel._location_code(location) == ""


def test_parenthetical_metadata_does_not_erase_unambiguous_location_authority() -> None:
    import core.currency_travel_spend as travel

    assert travel._location_code("Caracas, Venezuela (local office)") == "VES"
    assert travel._location_code("Tokyo, Japan (JPY)") == "JPY"


@pytest.mark.parametrize(
    "prompt",
    (
        "Calculate 25 multiplied by 4 and tell me the name of currency code GEL.",
        "I have 100 GEL in a wallet and buy an item for 5 GEL; name the currency and remainder.",
        "Travel to Georgia, spend 20 GEL, and name the GEL currency.",
    ),
)
def test_static_identity_declines_arithmetic_purchase_and_travel_siblings(prompt: str) -> None:
    reply = maybe_answer_currency_value(prompt)

    assert reply is None or reply.reason != "static_currency_identity"


def test_quoted_travel_example_does_not_turn_a_pure_code_question_into_arithmetic() -> None:
    prompt = 'What currency code is GEL? The quoted test label is "travel to Georgia and buy a hat".'
    reply = maybe_answer_currency_value(prompt)

    # Conservative whole-turn admission declines rather than swallowing the quoted residue.
    assert reply is None


def test_iso_reference_wording_cannot_grant_static_whole_turn_coverage() -> None:
    prompt = FROZEN_NORMALIZED + " Identify the ISO 4217 currency codes involved."

    assert maybe_answer_currency_value(prompt) is None
    claimed = currency_fast_path(prompt)
    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert "12,950 GEL left over" in claimed["response"]


def test_dynamic_detector_sabotage_cannot_restore_static_identity_over_complete_travel(
    monkeypatch,
) -> None:
    import core.currency_value_contract as value_contract

    monkeypatch.setattr(value_contract, "asks_for_dynamic_currency_value", lambda _text: False)

    assert value_contract.maybe_answer_currency_value(FROZEN_NORMALIZED) is None
    pure = value_contract.maybe_answer_currency_value("What currency code is GEL?")
    assert pure is not None
    assert pure.reason == "static_currency_identity"
    assert pure.response == "GEL = Georgian lari"


def test_exact_normalized_public_turn_is_zero_model_web_and_tool(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="georgia-travel-test", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("complete supplied-rate travel reached a model"))
    tool = mock.Mock(side_effect=AssertionError("complete supplied-rate travel reached a tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        FROZEN_NORMALIZED,
        session_id_override="openclaw:georgia-travel-admission",
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

    assert result["route_reason"] == "currency_travel_spend_fast_path"
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert "12,950 GEL left over" in result["response"]
    assert "GEL = Georgian lari" not in result["response"]
    model.assert_not_called()
    tool.assert_not_called()
