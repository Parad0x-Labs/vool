"""A word the live coin index lists is still ordinary English in a clause that asks no price.

On 2026-10-06 CoinGecko's market-cap top 250 listed a coin called Rain at rank 19. With that index
loaded, "Tell me the weather in Vilnius, write a rhyme about rain, and give me BTC price" planned a
market quote for Rain beside Bitcoin: the live-data plan resolved every index token in the message
under the TURN's market classification, so one priced clause made "rain" in another clause an
asset. Offline the same test passed only because the index was empty.

This file pins a real-shaped index containing Rain at the reader every resolver uses
(`coin_index._index`), seals the network, and asserts both directions: the ordinary word is not
planned, and the same index still serves real requests, including index-only tickers.
"""
from __future__ import annotations

import pytest

from tests import _network_seal as network_seal

#: Read off CoinGecko /coins/markets (vs_currency=usd, market_cap_desc, top 250) on 2026-10-06 for
#: the symbols used below. "rain" is the collision under test; the rest are positive controls.
_INDEX = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "rain": "rain",
    "arb": "arbitrum",
    "sui": "sui",
    "tia": "celestia",
}


@pytest.fixture(autouse=True)
def _pinned_index_and_no_network(monkeypatch):
    from tools.web import coin_index

    monkeypatch.setattr(coin_index, "_index", lambda: dict(_INDEX), raising=True)

    def _offline(_kind, address):
        if isinstance(address, tuple) and len(address) >= 2:
            host = str(address[0]).lower()
            if host not in {"127.0.0.1", "::1", "localhost"} or int(address[1] or 0) == 11434:
                raise OSError(101, f"network sealed in this test: {address[:2]}")

    token = network_seal.push("test:coin-index-word-collision", _offline)
    try:
        yield
    finally:
        network_seal.release(token)


def _planned_assets(text: str) -> list[str]:
    from core.agent_runtime.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    assert plan is not None, text
    return sorted({str(t.arguments.get("asset_key") or "") for t in plan.market_subtasks()})


def test_the_pinned_index_really_lists_rain():
    """Anti-vacuity: every negative below is meaningless unless the reader resolves "rain"."""
    from tools.web import coin_index

    assert coin_index.resolve_symbol("rain") == "rain"
    assert [coin for _pos, coin in coin_index.resolve_tokens("rain")] == ["rain"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tell me the weather in Vilnius, write a rhyme about rain, and give me BTC price", ["bitcoin"]),
        ("write a poem about rain and tell me the ARB price", ["arbitrum"]),
        ("the rain in spain, and how much is TIA trading at", ["celestia"]),
        ("is it going to rain in London tomorrow and what is sui worth", ["sui"]),
    ],
)
def test_rain_in_an_unpriced_clause_is_not_an_asset(text, expected):
    assert _planned_assets(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rain price", ["rain"]),
        ("what is the price of rain", ["rain"]),
        ("$rain", ["rain"]),
        ("price of ARB and SUI", ["arbitrum", "sui"]),
        ("market data for ARB, SUI and TIA", ["arbitrum", "celestia", "sui"]),
        ("btc price and also arb", ["arbitrum", "bitcoin"]),
        ("1000 usd to eur and then to gold? also btc price and 24 change on eth", ["bitcoin", "ethereum", "gold"]),
    ],
)
def test_the_same_index_still_serves_a_real_request(text, expected):
    assert _planned_assets(text) == expected
