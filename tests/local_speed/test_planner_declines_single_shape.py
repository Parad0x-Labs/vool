"""A planner call on a structurally single turn can only return the turn itself.

The baseline measured one-line tells and one-line imperatives paying a ~1.8K-token planner call
(the catalog-bearing conductor variant is larger) to learn they are one request. The decline is
structural and shared by both planners: one demand unit, one Turn-IR clause, one sentence --
three readers the runtime already computed, not a word list over the person's words.
"""
from __future__ import annotations

from core.agent_runtime.turn_planner import (
    plan_turn,
    single_shape_needs_no_planner,
    turn_may_hold_several_requests,
)


def test_only_supplied_material_declines_and_every_pin_stays_green() -> None:
    """The reordered fetch-then-compute pin reads as one clause, one slot, no question --
    exactly like a one-line tell -- so no wording-free rule separates them and only
    supplied-material turns decline.
    """
    assert single_shape_needs_no_planner("please remember that my train leaves at seven") is False
    assert single_shape_needs_no_planner("ok what about gold and silver price?") is False
    assert single_shape_needs_no_planner(
        "Work out how many Solana one Ethereum buys — first fetch the live prices of "
        "Ethereum and Solana."
    ) is False


def test_a_paste_carrying_single_demand_turn_declines() -> None:
    """The paste is the request's OBJECT (Turn-IR's own verdict), not more requests."""
    assert single_shape_needs_no_planner(
        "Summarise this in two bullet points:\n\nThe council met on Thursday. Three proposals "
        "were presented. The first would convert the hall into flats. The council voted to ask "
        "for a joint bid."
    ) is True


def test_multi_shape_turns_keep_the_planner_reachable() -> None:
    multi = "what is the euro to usd today and will it rain and where i can change my tyres in vilnius"
    assert single_shape_needs_no_planner(multi) is False  # several units
    assert single_shape_needs_no_planner(
        "check the weather in vilnius, compare gold and silver, find a tyre shop"
    ) is False  # several clauses
    assert single_shape_needs_no_planner("") is False
    assert single_shape_needs_no_planner("hi") is False


def test_plan_turn_spends_no_model_call_on_supplied_material() -> None:
    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("a one-operation summarize-with-paste turn bought a decomposition call")

    assert plan_turn(
        "Summarise this in two bullet points:\n\nThe council met. Three proposals came. "
        "The vote was to ask for a joint bid.",
        ask_model=_must_not_run,
    ) == []


def test_the_gate_stays_permissive_for_plausibly_multi_part_messages() -> None:
    # The pinned permissive gate is unchanged; the decline lives one level deeper, in plan_turn.
    assert turn_may_hold_several_requests(
        "ok what about gold and silver price?"
    ) is True
