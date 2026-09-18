"""A query naming both commodities and crypto answers every asset, not just the crypto ones.

Reproduces the second half of the reported incident: "price and 24-hour change for gold, silver,
Bitcoin, and BNB" only ever answered bitcoin and bnb. Root cause: `lookup_live_quotes` checked
`_looks_like_price_query_all` (crypto) first and returned immediately on any match, never checking
commodities at all -- confirmed by the existing test file's own docstring
(`test_a_message_asking_several_things_is_planned_into_all_of_them.py`), which documents "ok what
about gold and silver price?" answering gold alone. That case (crypto absent) already worked once a
SINGLE market target matched; this case (crypto AND commodities both present) never reached the
commodity lookup at all.
"""

from __future__ import annotations

from unittest import mock

from core.live_quote_contract import LiveQuoteResult
from tools.web.web_research import (
    _looks_like_market_quote_query_all,
    _looks_like_price_query_all,
    lookup_live_quotes,
)


def test_all_four_assets_are_recognized_in_one_query() -> None:
    query = "price and 24-hour change for gold, silver, Bitcoin, and BNB"
    market = [t.asset_key for t in _looks_like_market_quote_query_all(query)]
    crypto = _looks_like_price_query_all(query)
    assert market == ["gold", "silver"]
    assert crypto == ["bitcoin", "binancecoin"]


def test_lookup_live_quotes_unions_crypto_and_commodities_instead_of_stopping_at_crypto() -> None:
    """Sabotage target: restore the early `if results: return results` right after the crypto
    block (before the commodity block runs) and this goes red with only 2 quotes, not 4."""
    crypto_quotes = [
        LiveQuoteResult(
            asset_key="bitcoin", asset_name="Bitcoin", symbol="BITCOIN", value=64000.0,
            currency="USD", as_of="", source_label="CoinGecko", source_url="https://cg/btc", kind="crypto",
        ),
        LiveQuoteResult(
            asset_key="binancecoin", asset_name="Binancecoin", symbol="BINANCECOIN", value=590.0,
            currency="USD", as_of="", source_label="CoinGecko", source_url="https://cg/bnb", kind="crypto",
        ),
    ]
    market_quotes = [
        LiveQuoteResult(
            asset_key="gold", asset_name="Gold", symbol="GC=F", value=4200.0,
            currency="USD", as_of="", source_label="Yahoo Finance", source_url="https://yf/gold", kind="market",
        ),
        LiveQuoteResult(
            asset_key="silver", asset_name="Silver", symbol="SI=F", value=61.0,
            currency="USD", as_of="", source_label="Yahoo Finance", source_url="https://yf/silver", kind="market",
        ),
    ]
    with (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", return_value=crypto_quotes),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", return_value=market_quotes),
    ):
        quotes = lookup_live_quotes("price and 24-hour change for gold, silver, Bitcoin, and BNB")

    keys = {quote.asset_key for quote in quotes}
    assert keys == {"bitcoin", "binancecoin", "gold", "silver"}
    assert len(quotes) == 4


def test_a_failed_commodity_fetch_does_not_erase_the_crypto_results() -> None:
    crypto_quotes = [
        LiveQuoteResult(
            asset_key="bitcoin", asset_name="Bitcoin", symbol="BITCOIN", value=64000.0,
            currency="USD", as_of="", source_label="CoinGecko", source_url="https://cg/btc", kind="crypto",
        ),
    ]
    with (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", return_value=crypto_quotes),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=RuntimeError("yahoo down")),
    ):
        quotes = lookup_live_quotes("bitcoin and gold price")

    # The commodity fetch raising must not propagate past lookup_live_quotes and must not cost
    # bitcoin its own already-fetched result.
    assert any(quote.asset_key == "bitcoin" for quote in quotes)
