"""Asking for several assets at once is a comparison, and a comparison reads as a table.

The cross-lane fix made "gold, silver and BNB price now?" answer all three -- as three stacked
sentences, which leaves the reader aligning the numbers themselves:

    Gold is $4,210.00 USD per troy ounce as of ... Session change: +1.38%. Source: ...
    Silver is $61.48 USD per troy ounce as of ... Session change: +2.05%. Source: ...
    Binancecoin is $596.10 USD as of ... 24h change: +1.40%. Source: ...

Rows are scannable; sentences are not. What must NOT happen is a tidier table bought by dropping
the source or the observation time -- those are the two things that make a live number checkable.
"""
from __future__ import annotations

from core.agent_runtime.fast_live_info_generic_rendering import quotes_as_table
from core.live_quote_contract import LiveQuoteResult


def _quote(name, value, **kw):
    return LiveQuoteResult(
        asset_key=name.lower(), asset_name=name, symbol=name.upper(), value=value,
        currency=kw.pop("currency", "USD"), as_of=kw.pop("as_of", "2026-08-05 10:14 UTC"),
        source_label=kw.pop("source_label", "CoinGecko"),
        source_url=kw.pop("source_url", "https://www.coingecko.com/en/coins/x"),
        kind=kw.pop("kind", "crypto"), **kw,
    )


def test_several_quotes_render_as_one_table_with_a_row_each() -> None:
    table = quotes_as_table([_quote("Gold", 4210.0), _quote("Silver", 61.48), _quote("Binancecoin", 596.10)])
    lines = table.splitlines()
    assert lines[0].startswith("| Asset |")
    assert lines[1].startswith("| ---")
    assert len(lines) == 5, f"expected header + divider + 3 rows, got {len(lines)}"
    for asset in ("Gold", "Silver", "Binancecoin"):
        assert any(line.startswith(f"| {asset} |") for line in lines), f"{asset} has no row"


def test_the_table_never_drops_the_source_or_the_observation_time() -> None:
    """A tidier table is not worth losing what makes the number checkable."""
    table = quotes_as_table([
        _quote("Gold", 4210.0, source_label="Yahoo Finance", source_url="https://finance.yahoo.com/quote/GC=F"),
        _quote("Silver", 61.48, source_label="Yahoo Finance", source_url="https://finance.yahoo.com/quote/SI=F"),
    ])
    assert "As of" in table and "2026-08-05 10:14 UTC" in table
    assert "[Yahoo Finance](https://finance.yahoo.com/quote/GC=F)" in table


def test_a_column_no_quote_fills_is_omitted_not_left_as_dashes() -> None:
    """A crypto-only set must not carry an empty unit column, nor a wall of '-'."""
    table = quotes_as_table([_quote("Bitcoin", 64106.0), _quote("Ethereum", 1870.44)])
    assert "Change" not in table, "no quote carries a change; the column should be gone"
    assert table.count("| - |") == 0


def test_a_unit_label_survives_into_the_price_cell() -> None:
    table = quotes_as_table([
        _quote("Gold", 4210.0, unit_label="per troy ounce"),
        _quote("Silver", 61.48, unit_label="per troy ounce"),
    ])
    assert "per troy ounce" in table


def test_a_micro_priced_token_keeps_its_digits_in_a_table() -> None:
    """The formatter fix must survive the table path too -- $0.0000 in a row is still zero."""
    table = quotes_as_table([_quote("Pepe", 0.00000292), _quote("Bitcoin", 64106.0)])
    assert "$0.00000292" in table


def test_an_empty_quote_set_renders_nothing_rather_than_an_empty_table() -> None:
    assert quotes_as_table([]) == ""


def test_the_change_window_is_named_so_24h_and_session_are_not_conflated() -> None:
    """A metals session change and a crypto 24h change in one table are different measurements."""
    table = quotes_as_table([
        _quote("Gold", 4210.0, change_percent=1.38, change_window="session"),
        _quote("Binancecoin", 596.10, change_percent=1.40, change_window="24h"),
    ])
    assert "(session)" in table and "(24h)" in table
