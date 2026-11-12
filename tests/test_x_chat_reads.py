"""Ordinary chat words for reading your own X and asking what someone posted reach the X read
tools, and questions, how-tos and quoted words never seat an X action proposal.

Pure routing: ``resolve_demand_signals`` is what decides which tools a turn is offered.
"""
from __future__ import annotations

import pytest


def _x_intents(text):
    from core.tool_demand_signals import resolve_demand_signals

    return {i for i in resolve_demand_signals(text).explicit_intents if i.startswith("x.")}


@pytest.mark.parametrize("text", [
    "fetch my last 2 tweets",
    "show my latest tweets",
    "check my x mentions",
    "who mentioned me on twitter",
    "show my likes on x",
    "what did @elonmusk post today",
    "what did elon post today on x",
])
def test_reading_your_own_x_and_what_someone_posted_seats_the_read(text):
    intents = _x_intents(text)
    assert "x.user_posts" in intents
    assert "x.action.propose" not in intents  # a question about a post is not an ask to post


@pytest.mark.parametrize("text", [
    "how do I post on x?",
    'write a reply that says "post this on x now"',
])
def test_how_tos_and_quoted_words_never_propose_an_action(text):
    assert "x.action.propose" not in _x_intents(text)


@pytest.mark.parametrize("text", [
    "post this on x: gm",
    "can you tweet this",
    "like that tweet",
    "follow @bob on x",
    "what did he tweet about $wif on x and repost it",
])
def test_real_asks_to_act_still_propose(text):
    assert "x.action.propose" in _x_intents(text)


@pytest.mark.parametrize("text", [
    "look up the X account @vool_garden",
    "who is @vool_garden on X?",
    "show me @vool_garden's X profile",
    "the twitter profile of @vool_garden",
])
def test_asking_about_one_x_account_seats_the_account_read(text):
    intents = _x_intents(text)
    assert "x.user" in intents
    assert "x.action.propose" not in intents


@pytest.mark.parametrize("text", ["my last 3 posts on my blog", "what did the market do today",
                                  "who is @bob", "check the box account balance"])
def test_words_without_x_seat_no_x_tool(text):
    assert _x_intents(text) == set()


@pytest.mark.parametrize("text", ["Look up the X account @vool_garden", "who is @vool_garden on X?"])
def test_with_x_connected_an_account_ask_leaves_the_toolless_chat_lane(text, monkeypatch):
    """Measured 2026-11-12 in the running app (X connected, account read on): "Look up the X account
    @vool_garden" was kept in the tools-less chat lane as a live-information question, and the model,
    offered no tools, answered "I can't use X from this chat right now". With X connected the ask
    reaches the tool gate; without X it stays the ordinary question it was."""
    from core import x_api
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent
    from tests.test_production_route_matrix import PRODUCTION_CONTEXT, _lane_probe_agent

    def kept() -> bool:
        return should_keep_ai_first_chat_lane(
            _lane_probe_agent(), user_input=text, classification={"task_class": "unknown"},
            interpretation=None, source_context=dict(PRODUCTION_CONTEXT), checkpoint_state=None,
        )

    monkeypatch.setattr(x_api, "connected", lambda: True)
    assert kept() is False
    assert should_attempt_tool_intent(text, task_class="unknown", source_context=dict(PRODUCTION_CONTEXT)) is True
    monkeypatch.setattr(x_api, "connected", lambda: False)
    assert kept() is True
