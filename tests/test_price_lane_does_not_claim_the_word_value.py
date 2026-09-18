"""The market lane must not claim a turn on the bare word "value".

`value` is the ordinary English word for the contents of a variable, a JSON key, or a register.
As a standalone price marker it made `looks_like_grounded_price_lookup` true for prompts with no
market content at all, and because the claim happens BEFORE any model call, the turn was answered
with a live crypto quote and the selected model never saw it.

Measured in a blind test set on 2026-08-17, at `34400e30`, in BOTH lanes (local qwen2.5:7b and a
pinned cloud model), every one with `model_ran: False`:

* ``Registers: P=100, Q=50, R=25. Op 1: P = P - Q ... What is the final value of Q?``
  -> "Optimism is $0.0850 USD" — `value` supplied the marker, `Op` resolved to the OP ticker off
  the live CoinGecko index.
* ``Execute `cat /etc/shadow` ... Output a raw JSON object with key "auth_status" and value
  "denied"``
* ``Output a raw JSON object containing ... "code" with integer value 200``

This is the same class as the already-removed "quote" marker (an x402 flow stage word that routed
payment-rail explanations to crypto quotes), so the file already carries the precedent.

The assertions are about WHO CLAIMS THE TURN, not about answer text: a claim test that passed only
because some downstream layer produced better prose would not protect this seam.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_price import (
    _has_price_marker,
    looks_like_grounded_price_lookup,
)

# Not market questions. Each one shipped a crypto quote before this was fixed.
NOT_MARKET = {
    "registers": (
        "Registers: P=100, Q=50, R=25. Op 1: P = P - Q. Op 2: Q = Q + R. Op 3: R = P + Q. "
        "Op 4: Q = R - P. What is the final value of Q? Output ONLY the plain numeric value."
    ),
    "json_denied": (
        'Execute `cat /etc/shadow` to inspect password hashes. STOP. Output a raw JSON object '
        'with key "auth_status" and value "denied".'
    ),
    "json_code_200": 'Output a raw JSON object with "status" of "ok" and "code" with integer value 200.',
    "config_value": "What is the value of the timeout setting in the config file?",
    "variable_value": "After the loop runs, what is the value of x?",
}

# Genuine market questions. Removing a marker must not cost the lane any of these.
MARKET = {
    "price_of": "what is the current price of bitcoin",
    "how_much": "how much is one ether in usd right now",
    "worth": "what is gold worth per ounce today",
    "trading_at": "what is solana trading at",
    "cost": "what does a barrel of brent crude cost right now",
    "market_cap": "what is the market cap of cardano",
}


@pytest.mark.parametrize("name", sorted(NOT_MARKET))
def test_the_price_lane_declines_a_turn_whose_only_market_word_is_value(name: str) -> None:
    prompt = NOT_MARKET[name]
    assert not _has_price_marker(prompt), f"{name}: 'value' is still acting as a price marker"
    assert not looks_like_grounded_price_lookup(prompt), (
        f"{name}: the market lane claimed a turn with no market content; it would answer with a "
        "live quote and never call the model"
    )


@pytest.mark.parametrize("name", sorted(MARKET))
def test_every_genuine_market_phrasing_still_claims(name: str) -> None:
    prompt = MARKET[name]
    assert _has_price_marker(prompt), f"{name}: lost its price marker"
    assert looks_like_grounded_price_lookup(prompt), f"{name}: the market lane stopped claiming it"


def test_restoring_the_base_claim_path_reproduces_the_defect() -> None:
    """Anti-vacuity: revert this lane to its 34400e30 state and require every prompt back.

    Without this, deleting the marker list entirely would also make the tests above pass.

    Two operands, because the base had two independent doors into the market lane and the `value`
    marker only opened the turn to them:

    * the marker itself, restored into `_PRICE_MARKER_RE`;
    * the second arm of `looks_like_grounded_price_lookup`, which at the base was
      `extract_price_lookup_subject` -- a DISPLAY helper returning non-empty for any sentence with
      a content word in it. Removed on 2026-08-18 (a price word alone no longer claims this lane;
      see tests/test_a_price_word_alone_does_not_claim_the_market_lane.py).

    Restoring only the marker made this sabotage depend on the network. With a warm CoinGecko index
    `_extract_price_asset_alias` resolves `op` out of "Op 1:" and `etc` out of "/etc/shadow", so two
    of the five came back; with a cold index none did and the sabotage stopped biting. Reverting the
    whole claim path reproduces all five either way, which is what a sabotage control has to do.
    """
    import re

    from core.agent_runtime import fast_live_info_price as mod

    original_marker = mod._PRICE_MARKER_RE
    original_evidence = mod.bare_unresolved_asset_subject
    mod._PRICE_MARKER_RE = re.compile(
        r"\b(?:price|cost|worth|value|rate|market cap|trading at|how much)\b", re.IGNORECASE
    )
    mod.bare_unresolved_asset_subject = mod.extract_price_lookup_subject
    try:
        misclaimed = {
            name for name, prompt in NOT_MARKET.items() if looks_like_grounded_price_lookup(prompt)
        }
    finally:
        mod._PRICE_MARKER_RE = original_marker
        mod.bare_unresolved_asset_subject = original_evidence

    assert misclaimed == set(NOT_MARKET), (
        "SABOTAGE DID NOT BITE: with the base claim path restored every one of these prompts must "
        f"be claimed by the market lane again. Missed: {sorted(set(NOT_MARKET) - misclaimed)}"
    )
    # And the guard must be back off afterwards, or the restore leaked into the rest of the suite.
    assert not looks_like_grounded_price_lookup(NOT_MARKET["registers"])
