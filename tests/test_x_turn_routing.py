"""An X question reaches the X tools: the turn leaves the tools-less lane and the offer seats them.

Without this, "who called $WIF first on X" classified as plain chat (or a price lookup) and the
model was never offered an X tool. The recognizer is verb+noun anchored: a passing mention of X
seats nothing, and nothing changes while X is not connected.
"""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_api_reads import SOL, _connect

ASKS = [
    ("who called $WIF first on X", "x.token_callers"),
    (f"find the callers of {SOL}", "x.token_callers"),
    ("search twitter for BONK", "x.search"),
    ("show me @ansem's past calls", "x.user_posts"),
    ("post this on X: gm everyone", "x.action.propose"),
    ("follow @XDevelopers", "x.action.propose"),
]
NOT_X = [
    "I read on X that rates are rising, what do you think?",
    "what's the price of $WIF",
    "write a tweet about my launch",
]


@pytest.mark.parametrize("text,intent", ASKS)
def test_x_asks_seat_the_x_tool_and_release_the_tool_lane(vault_home, text, intent):
    from core.capability_graph import model_visible_specs
    from core.execution_requirements import classify_requirements

    _connect()
    req = classify_requirements(text)
    assert req.tools_required and req.allowed_toolsets == ("x",), req
    offered = [s["intent"] for s in model_visible_specs(user_text=text, toolset_hints=req.allowed_toolsets)]
    assert intent in offered, offered


@pytest.mark.parametrize("text", NOT_X)
def test_passing_mentions_do_not_route_to_x(vault_home, text):
    from core.execution_requirements import classify_requirements

    _connect()
    assert "x_action_request" not in classify_requirements(text).reason_codes


def test_nothing_changes_while_x_is_not_connected(vault_home):
    from core.execution_requirements import classify_requirements

    req = classify_requirements("who called $WIF first on X")
    assert "x_action_request" not in req.reason_codes
