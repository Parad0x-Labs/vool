"""User-supplied computation turns -- the 2026-09-16 refusal and carving incident.

Two logged failure shapes, one root family: the runtime read the user's OWN numbers as a
request for live information.

* The $18,400 allocation turn ("Do not browse or use external tools. ... Assume: - EUR/USD =
  1.175 ...") was marked current-information, two web retrievals ran behind the explicit
  prohibition, and the computed answer was then REFUSED for lacking the retrieval the user
  had forbidden.
* The provider-comparison turn ("A test run produced: Input tokens: 184,500 ... Calculate:
  - total tokens - cost per 1 million total tokens") was carved into fragments, each fragment
  lost the data block that made it arithmetic, the leftovers read as market tickers ('INPUT',
  'OUTPUT', 'TOTAL'), and the merge served a Markets table of unresolvable symbols.

The repairs, each at its owner: the prohibition vocabulary recognizes "external tools" and
disjoined imperatives; premise headers ("Assume:", "Given:") and labelled data blocks count as
a stipulated frame; the M1 retrieval door declines a turn the user closed by prohibition; and
the two decomposers (the generic planner and the demand-ownership seam) refuse to split a
stipulated computation.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import (
    require_current_information_for_retrieval,
    requirements_for,
)
from core.hypothetical_frame import (
    detect_hypothetical_frame,
    supplies_computation_premises,
)
from core.retrieval_constraints import analyze_retrieval_constraints

ALLOCATION_TURN = (
    "Do not browse or use external tools. I have $18,400 USD. Allocate: - 30% to EUR - 25% to gold "
    "- 20% to silver - the remainder to cash Assume: - EUR/USD = 1.175 - FX fee = 0.6% - Gold = $3,680 per troy ounce "
    "- Gold premium = 1.8% - Silver = $44.50 per troy ounce - Silver premium = 3.2% - 1 troy ounce = 31.1034768 grams "
    "Calculate: 1. Exact USD allocation to each asset. 2. EUR received after the FX fee. "
    "7. Confirm that all four USD allocations sum to exactly $18,400."
)

PROVIDER_COMPARISON_TURN = (
    "Do not browse or use external tools. A test run produced: Provider North: Input tokens: 184,500 "
    "Output tokens: 27,600 Cost: $0.0827 Latency: 18.4 seconds Provider South: Input tokens: 184,500 "
    "Output tokens: 31,250 Cost: $0.1462 Latency: 12.7 seconds Calculate: - total tokens for each provider "
    "- cost per 1 million total tokens - tokens generated per second using output tokens "
    "- percentage cost difference between the providers"
)


# ---------------------------------------------------------------- the prohibition vocabulary


@pytest.mark.parametrize(
    "text",
    [
        "Do not browse or use external tools.",
        "Do not use external tools",
        "never use external tools",
        "Do not browse.",
        "No search.",
    ],
)
def test_external_tools_and_disjoined_imperatives_prohibit_retrieval(text: str) -> None:
    constraints = analyze_retrieval_constraints(text)
    assert constraints.forbids_external_retrieval, text


@pytest.mark.parametrize(
    "text",
    [
        # A qualifier the prohibition families do not own: "Python tools" is not the web.
        "You can use Python tools to check; do not use Python tools to format.",
        "Use the dev tools you like.",
    ],
)
def test_unrelated_tool_talk_does_not_prohibit(text: str) -> None:
    assert not analyze_retrieval_constraints(text).forbids_external_retrieval


# ---------------------------------------------------------------- stipulated computation frames


def test_a_premise_header_with_a_colon_is_a_frame() -> None:
    # "cash Assume:" -- whitespace normalization glues the verb to the previous clause, so the
    # clause-opener test alone cannot see it. The colon is what makes it a header.
    frame = detect_hypothetical_frame(
        "the remainder to cash Assume: - EUR/USD = 1.175 - FX fee = 0.6%"
    )
    assert frame.stipulated and frame.supplies_premises


def test_a_labelled_data_block_is_computation_premises() -> None:
    assert supplies_computation_premises(PROVIDER_COMPARISON_TURN)
    assert supplies_computation_premises(ALLOCATION_TURN)
    assert detect_hypothetical_frame(PROVIDER_COMPARISON_TURN).supplies_premises


@pytest.mark.parametrize(
    "text",
    [
        # One quoted value is a market ask's subject, not a block of inputs.
        "Is bitcoin overvalued at $64,000?",
        "What is the price of gold per ounce?",
        # A compute ask with no supplied numbers still needs the world.
        "Calculate the current market cap of Apple.",
    ],
)
def test_market_asks_are_not_computation_premises(text: str) -> None:
    assert not supplies_computation_premises(text)


@pytest.mark.parametrize("text", [ALLOCATION_TURN, PROVIDER_COMPARISON_TURN])
def test_user_supplied_computation_turns_are_stipulated_direct(text: str) -> None:
    requirements = requirements_for(text)
    assert requirements.reason_codes[0] == "user_stipulated_frame"
    assert not requirements.current_information_required
    assert not requirements.tools_required
    assert requirements.inference_allowed


def test_a_genuine_live_lookup_still_requires_current_information() -> None:
    requirements = requirements_for("What is the price of Bitcoin right now?")
    assert requirements.current_information_required
    assert requirements.allowed_toolsets == ("market_prices",)


def test_a_stipulated_frame_with_a_real_live_target_stays_live() -> None:
    # Premises present, but the ask itself targets a live value: the frame must NOT close it.
    requirements = requirements_for(
        "Given: BTC = 2.5, ETH = 10. Calculate my portfolio's current value in USD."
    )
    assert requirements.current_information_required


# ---------------------------------------------------------------- the retrieval door


def _frozen_context(text: str) -> dict:
    context: dict = {}
    requirements_for(text, source_context=context)
    return context


def test_the_retrieval_door_declines_a_prohibited_turn() -> None:
    text = "What is the EUR/USD rate? Do not browse or use external tools."
    context = _frozen_context(text)
    assert require_current_information_for_retrieval(context, text, lane="web_search") is False


def test_the_retrieval_door_still_admits_a_live_turn() -> None:
    text = "What is the price of Bitcoin right now?"
    context = _frozen_context(text)
    assert require_current_information_for_retrieval(context, text, lane="market_prices") is True


def test_the_door_declines_a_prohibited_turn_with_no_context() -> None:
    text = "What is the EUR/USD rate? Do not use external tools."
    assert (
        require_current_information_for_retrieval(None, text, lane="web_search") is False
    )


# ---------------------------------------------------------------- the decomposers


def test_the_generic_planner_refuses_to_split_a_stipulated_computation() -> None:
    from core.agent_runtime.turn_planner import plan_turn

    def _must_not_be_called(_system: str, _prompt: str) -> str:
        raise AssertionError("the planner model must not be consulted for a stipulated turn")

    assert plan_turn(PROVIDER_COMPARISON_TURN, ask_model=_must_not_be_called) == []
