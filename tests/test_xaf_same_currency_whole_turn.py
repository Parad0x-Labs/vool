"""XAF travel arithmetic is a closed, whole-turn currency contract."""

from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.answer_coverage import FAMILY_CURRENCY, coverage_for
from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn
from core.currency_travel_spend import TravelSpendRequest, travel_spend_intent

FROZEN_SET5_07 = (
    "I have 5,000 XAF in Douala, Cameroon. I travel to Libreville, Gabon, and buy a meal "
    "for 2,000 XAF. How many units of local currency do I have left? Name the currencies "
    "involved. (Hint: look closely at the currencies)."
)


def _prompt(source: str, target: str, *, unit: str = "units of local currency") -> str:
    return (
        f"I have 5,000 {unit} in {source}. I travel to {target}, and buy a meal for 2,000 "
        f"{unit}. How many units of local currency do I have left? Name the currencies involved."
    )


def _assert_complete_xaf_answer(prompt: str) -> None:
    request = travel_spend_intent(prompt)
    claimed = currency_fast_path(prompt)

    assert isinstance(request, TravelSpendRequest)
    assert request.source_code == "XAF"
    assert request.target_code == "XAF"
    assert request.missing_rate is False
    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert claimed["codes"] == ("XAF",)
    assert claimed["grounded"] == "user_supplied_rate_or_local_identity"
    assert claimed["retrieval_allowed"] is False
    assert claimed["response"].count("Central African CFA franc (XAF)") == 2
    assert "The currencies are the same" in claimed["response"]
    assert "5,000 XAF − 2,000 XAF = 3,000 XAF" in claimed["response"]
    assert "You have 3,000 XAF left over" in claimed["response"]
    assert "not determined" not in claimed["response"].casefold()
    assert "could not be answered" not in claimed["response"].casefold()


def test_frozen_set5_07_reproduction_is_a_complete_closed_contract() -> None:
    _assert_complete_xaf_answer(FROZEN_SET5_07)
    coverage = coverage_for(FROZEN_SET5_07, FAMILY_CURRENCY)
    assert coverage.covers_whole_turn
    assert not coverage.conflicting
    assert closed_semantic_contract_covers_turn(
        FROZEN_SET5_07,
        session_id="set5-07-closed-contract",
        source_context={},
    )


@pytest.mark.parametrize(
    ("source", "target"),
    (
        ("Yaoundé, Cameroon", "Libreville, Gabon"),
        ("Bangui, Central African Republic", "N'Djamena, Chad"),
        ("Brazzaville, Republic of the Congo", "Malabo, Equatorial Guinea"),
        ("Libreville, Gabon", "Douala, Cameroon"),
        ("Yaounde, Cameroon", "Bangui, Central African Republic"),
    ),
)
def test_clean_xaf_jurisdiction_paraphrases(source: str, target: str) -> None:
    _assert_complete_xaf_answer(_prompt(source, target))


@pytest.mark.parametrize(
    ("source", "target", "unit"),
    (
        ("douala,cameroon", "libreville,gabon", "xaf"),
        ("DOUALA, CAMEROON", "LIBREVILLE, GABON", "XAF"),
        ("yaounde,CAMEROON", "bangui,CENTRAL AFRICAN REPUBLIC", "units"),
        ("Cameroon", "Gabon", "units of local currency"),
        ("brazzaville", "malabo", "units of local currency"),
    ),
)
def test_sloppy_xaf_jurisdiction_variants(source: str, target: str, unit: str) -> None:
    _assert_complete_xaf_answer(_prompt(source, target, unit=unit))


def test_cross_currency_jurisdictions_without_a_rate_remain_explicitly_unresolved() -> None:
    prompt = _prompt("Douala, Cameroon", "Lagos, Nigeria")
    request = travel_spend_intent(prompt)
    claimed = currency_fast_path(prompt)

    assert isinstance(request, TravelSpendRequest)
    assert (request.source_code, request.target_code) == ("XAF", "NGN")
    assert request.missing_rate is True
    assert claimed is not None
    assert claimed["grounded"] == "no_rate_declined"
    assert "cannot be calculated without a XAF/NGN exchange rate" in claimed["response"]
    assert "3,000" not in claimed["response"]


def test_a_local_currency_ask_with_foreign_holdings_needs_that_rate_not_a_usd_subtraction() -> None:
    """A foreign-unit holding is coherent (an explicit ISO unit wins for the HOLDING), but the
    question asked for units of LOCAL currency: the answer's denomination is the place's own
    money, and without a supplied USD/XAF rate the turn stays explicitly unresolved -- never a
    same-currency subtraction presented as the answer."""
    prompt = _prompt("Douala, Cameroon", "Libreville, Gabon", unit="USD")
    request = travel_spend_intent(prompt)
    claimed = currency_fast_path(prompt)

    assert isinstance(request, TravelSpendRequest)
    assert (request.source_code, request.target_code) == ("USD", "USD")
    assert request.missing_rate is True
    assert request.missing_pair == ("USD", "XAF")
    assert claimed is not None
    assert claimed["grounded"] == "no_rate_declined"
    assert "cannot be calculated without a USD/XAF exchange rate" in claimed["response"]
    assert "The local currency in Libreville, Gabon is the" in claimed["response"]
    assert "3,000" not in claimed["response"]


@pytest.mark.parametrize(
    "prompt",
    (
        _prompt("Douala, Cameroon", "Libreville, Atlantis"),
        "Send 5,000 XAF from Douala to Libreville and pay 2,000 XAF for me.",
    ),
)
def test_unknown_conflicting_or_effectful_near_matches_are_not_claimed(prompt: str) -> None:
    assert travel_spend_intent(prompt) is None
    assert currency_fast_path(prompt) is None


def test_adversarial_currency_mentions_without_the_travel_shape_do_not_route() -> None:
    prompt = (
        "Explain why a story mentioning Douala, Libreville, 5,000 XAF, and 2,000 XAF does not "
        "by itself establish a remaining balance."
    )

    assert travel_spend_intent(prompt) is None
    assert currency_fast_path(prompt) is None


def test_xaf_registry_fact_is_load_bearing(monkeypatch) -> None:
    import core.currency_travel_spend as travel

    without_xaf = {code: fact for code, fact in travel.ISO_4217.items() if code != "XAF"}
    without_xaf_places = {
        place: code for place, code in travel.LOCATION_TO_CODE.items() if code != "XAF"
    }
    monkeypatch.setattr(travel, "ISO_4217", without_xaf)
    monkeypatch.setattr(travel, "LOCATION_TO_CODE", without_xaf_places)

    assert travel.travel_spend_intent(FROZEN_SET5_07) is None


def test_frozen_local_only_turn_preempts_conductor_model_tool_and_web(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="xaf-whole-turn", persona_id="default")
    conductor = mock.Mock(side_effect=AssertionError("closed XAF turn reached conductor"))
    model = mock.Mock(side_effect=AssertionError("closed XAF turn reached model"))
    tool = mock.Mock(side_effect=AssertionError("closed XAF turn reached tool"))
    monkeypatch.setattr(agent, "_maybe_answer_conductor_turn", conductor)
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        FROZEN_SET5_07,
        session_id_override="openclaw:set5xafwholeturn",
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
    assert "You have 3,000 XAF left over" in result["response"]
    assert "Central African CFA franc (XAF)" in result["response"]
    assert "not determined" not in result["response"].casefold()
    assert "could not be answered" not in result["response"].casefold()
    conductor.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()
