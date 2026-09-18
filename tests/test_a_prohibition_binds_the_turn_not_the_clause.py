"""A ban stated in one sentence binds every node the turn was split into.

The conductor splits a message into clauses and builds a node per clause. `_weather_run` and
`_market_run` consulted no constraint authority at all -- `analyze_retrieval_constraints` appeared
zero times in `core/conductor/operations.py` -- so a prohibition stated in a different sentence
never reached them.

MEASURED live 2026-08-18, `route=conductor_multi_intent_plan`, `model_ran=False`:

    "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from
     utilizing any external data retrieval, web functions, or weather tools."

    clause 1 -> weather node, own text carries no ban -> FETCHED "Kampen, Finland ... wttr.in"
    clause 2 -> the prohibition itself planned as a REQUEST, then "could not be answered"

The turn-level analysis was already correct. It had no consumer in this lane, which is the
producer-wired / consumer-absent shape this repository keeps finding.
"""

from __future__ import annotations

import pytest

from core.conductor.operations import _turn_forbids
from core.conductor.registry import NodeContext
from core.conductor.shared_context import SharedTurnContext


class _Node:
    def __init__(self, request_text: str) -> None:
        self.request_text = request_text
        self.arguments: dict = {}


def _ctx(original: str) -> NodeContext:
    return NodeContext(shared_context=SharedTurnContext(original_request=original))


BANNED_TURNS = {
    "weather_forbidden": (
        "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from "
        "utilizing any external data retrieval, web functions, or weather tools.",
        "Tell me the current ambient temperature in Helsinki, Finland",
        "weather",
    ),
    "market_forbidden": (
        "What was the final closing price of Brent Crude oil on yesterday's market close? You are "
        "strictly forbidden from executing any web searches, network requests, or finance tools.",
        "What was the final closing price of Brent Crude oil on yesterday's market close",
        "market_prices",
    ),
}


@pytest.mark.parametrize("name", sorted(BANNED_TURNS))
def test_a_ban_in_a_sibling_clause_stops_this_node(name: str) -> None:
    turn, clause, toolset = BANNED_TURNS[name]
    node = _Node(clause)
    assert _turn_forbids(node, _ctx(turn), toolset), (
        f"{name}: the node fetches because its OWN clause carries no ban -- the runtime performs "
        f"retrieval the user explicitly forbade, with no model in the loop"
    )


@pytest.mark.parametrize("name", sorted(BANNED_TURNS))
def test_the_clause_alone_is_not_enough_to_stop_it(name: str) -> None:
    """Anti-vacuity: the clause on its own must NOT forbid, or the test above proves nothing."""
    _turn, clause, toolset = BANNED_TURNS[name]
    node = _Node(clause)
    assert not _turn_forbids(node, _ctx(clause), toolset), (
        "fixture drift: this clause now bans the toolset by itself, so it no longer demonstrates "
        "that the TURN-level reading is what stops the fetch"
    )


def test_scoped_negation_still_lets_the_other_node_run() -> None:
    """The half that must not break: a ban on one domain does not ban the others."""
    turn = "Don't look up the weather in Vilnius, but get me the current gold price."
    weather = _Node("look up the weather in Vilnius")
    market = _Node("get me the current gold price")
    assert _turn_forbids(weather, _ctx(turn), "weather"), "the banned domain must be stopped"
    assert not _turn_forbids(market, _ctx(turn), "market_prices"), (
        "a weather ban stopped the market node -- scoped negation has become a turn-wide veto"
    )


def test_an_unrestricted_turn_is_untouched() -> None:
    for turn, toolset in (
        ("What is the current price of Bitcoin?", "market_prices"),
        ("What's the weather in Oslo right now?", "weather"),
        ("Get the current Bitcoin and Ethereum prices. Calculate BTC divided by ETH.", "market_prices"),
    ):
        assert not _turn_forbids(_Node(turn), _ctx(turn), toolset), turn


def test_a_node_with_no_shared_context_falls_back_to_its_own_clause() -> None:
    """No shared context must not mean no gate -- it means the old, narrower reading."""
    clause = "Get the weather in Oslo. Do not use the internet."
    assert _turn_forbids(_Node(clause), NodeContext(), "weather")


def test_the_runners_themselves_refuse_not_just_the_helper() -> None:
    """The gate must be WIRED, not merely present.

    An earlier version of this file tested `_turn_forbids` only. Both `if _turn_forbids(...)` call
    sites could then be deleted from `_weather_run` and `_market_run` -- restoring the exact measured
    production defect -- and this file still reported all-green. A helper with no consumer is the
    shape this repository keeps re-finding; testing the helper alone reproduces it in the tests.

    Neither runner reaches the network here: the gate raises before the subtask is built.
    """
    import pytest

    from core.conductor.operations import _market_run, _weather_run

    weather_turn = (
        "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from "
        "utilizing any external data retrieval, web functions, or weather tools."
    )
    market_turn = (
        "What was the final closing price of Brent Crude oil on yesterday's market close? You are "
        "strictly forbidden from executing any web searches, network requests, or finance tools."
    )

    weather_node = _Node("Tell me the current ambient temperature in Helsinki, Finland")
    weather_node.arguments = {"location": "Helsinki"}
    with pytest.raises(ValueError, match="forbids weather retrieval"):
        _weather_run(weather_node, _ctx(weather_turn))

    market_node = _Node("What was the final closing price of Brent Crude oil")
    market_node.arguments = {"asset_key": "brent", "entity": "Brent Crude", "kind": "commodity"}
    with pytest.raises(ValueError, match="forbids market retrieval"):
        _market_run(market_node, _ctx(market_turn))
