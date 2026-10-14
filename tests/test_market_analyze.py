"""market.analyze: patterns computed from a stored dataset, every number from code.

The datasets are hand-built rows with known shapes (a known volume spike, known early buyers), so
the assertions check the arithmetic, not a fixture echo. Everything runs through the model's door.
"""
from __future__ import annotations

import pytest

SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
EARLY = "EaR1yAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
LATE = "Lat3BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
HODL = "Hod1CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
T0 = 1791878400  # 2026-10-13 08:00 UTC
H = 3600


def _candles() -> list[dict]:
    """24 hourly candles: price 1.0 -> 1.5 at hour 5 -> 2.0 at hour 12; one 10x volume spike at hour 10."""
    rows = []
    for i in range(24):
        close = 1.0 if i <= 4 else (1.5 if i <= 11 else 2.0)
        rows.append({"ts": T0 + i * H, "kind": "candle", "wallet": "", "amount": None,
                     "usd": 10000.0 if i == 10 else 1000.0, "price": close, "tx": ""})
    return rows


def _trades() -> list[dict]:
    return [
        # EARLY buys inside the hour before the hour-5 rise; LATE buys far from any rise
        {"ts": T0 + 4 * H + 1800, "kind": "buy", "wallet": EARLY, "amount": 500.0, "usd": 500.0, "price": 1.0, "tx": "t1"},
        {"ts": T0 + 8 * H, "kind": "buy", "wallet": LATE, "amount": 100.0, "usd": 100.0, "price": 1.0, "tx": "t2"},
        {"ts": T0 + 20 * H, "kind": "sell", "wallet": EARLY, "amount": 150.0, "usd": 300.0, "price": 2.0, "tx": "t3"},
    ]


def _transfers() -> list[dict]:
    return [{"ts": T0 + 20 * H, "kind": "in", "wallet": HODL, "amount": 10.0, "usd": 20.0, "price": 2.0, "tx": "t4"}]


def _store_dataset(rows_by_source: dict[str, list[dict]], window: str = "24h") -> str:
    from core import market_data

    coverage = {"token": SOL, "chain": "solana", "window": window, "from": "", "to": "",
                "sources": {name: {"rows": len(rows)} for name, rows in rows_by_source.items()}, "partial": False}
    dataset_id, _dropped = market_data._store_dataset(SOL, "solana", window, coverage, "chat-1", "", rows_by_source)
    return dataset_id


def _known_dataset() -> str:
    return _store_dataset({"candles": _candles(), "trades": _trades(), "transfers": _transfers()})


def _wipe_datasets() -> None:
    from core import market_data

    market_data._init_tables()
    conn = market_data._conn()
    try:
        for table in ("market_rows", "market_analyses", "market_datasets"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def clean_market_tables():
    _wipe_datasets()
    yield
    _wipe_datasets()


def _run(intent: str, args: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, args, source_context={})


# --------------------------------------------------------------------------- refusals


def test_analyze_needs_a_dataset_it_knows():
    missing = _run("market.analyze", {})
    assert not missing.ok and missing.status == "missing_dataset"
    unknown = _run("market.analyze", {"dataset_id": "md_nope"})
    assert not unknown.ok and unknown.status == "unknown_dataset"


def test_five_rows_say_not_enough_data_never_numbers():
    dataset_id = _store_dataset({"candles": _candles()[:3], "trades": _trades()[:2]})
    out = _run("market.analyze", {"dataset_id": dataset_id})
    assert out.ok, out.response_text
    assert "not enough data: 5 row(s) collected; the analysis needs at least 6" in out.response_text
    assert "return over the window" not in out.response_text and "buy share" not in out.response_text
    assert set(out.details["metrics"]["values"]) == {"rows_candles", "rows_trades", "rows_transfers"}


# --------------------------------------------------------------------------- the known dataset


def test_the_known_spike_and_early_buyers_are_found_exactly():
    dataset_id = _known_dataset()
    out = _run("market.analyze", {"dataset_id": dataset_id})
    assert out.ok, out.response_text
    values = out.details["metrics"]["values"]
    assert values["window_return_pct"] == 100.0
    assert values["daily_return_pct:2026-10-13"] == 100.0 and values["daily_return_pct:2026-10-14"] == 0.0
    assert values["max_hourly_price_rise_pct"] == 50.0
    assert values["volume_spike_count"] == 1
    assert "2026-10-13 18:00 UTC ($10,000)" in out.response_text  # the spike names its time and size
    assert values["buy_count"] == 2 and values["sell_count"] == 1
    assert values["buy_share_pct"] == pytest.approx(66.7, abs=0.1)
    assert values["buy_usd_total"] == 600.0 and values["sell_usd_total"] == 300.0
    assert values["net_usd_flow:" + EARLY] == 200.0 and values["net_usd_flow:" + LATE] == 100.0
    assert values["net_usd_flow:" + HODL] == 20.0
    assert values["trade_count:" + EARLY] == 2 and values["trade_count:" + LATE] == 1
    assert values["first_buyers_count"] == 2 and values["early_buyers_still_holding"] == 2
    assert values["buyers_before_top_rises_count"] == 1  # EARLY, before the hour-5 rise; never LATE
    assert "1 wallet(s) bought within 1h before each of the 2 biggest hourly rise(s)" in out.response_text
    assert values["top10_volume_share_pct"] == 100.0


def test_every_section_names_its_row_count_and_the_full_result_is_saved():
    dataset_id = _known_dataset()
    out = _run("market.analyze", {"dataset_id": dataset_id})
    assert "[prices (24 row(s))]" in out.response_text and "[buy/sell (3 row(s))]" in out.response_text
    assert "[volume (24 row(s))]" in out.response_text
    assert len(out.response_text.encode("utf-8")) <= 6_200  # the compact table stays compact
    from core import market_data

    market_data._init_tables()
    conn = market_data._conn()
    try:
        saved = conn.execute("SELECT COUNT(*) FROM market_analyses WHERE dataset_id = ?", (dataset_id,)).fetchone()[0]
    finally:
        conn.close()
    assert saved == 1
    assert "The full result is saved with the dataset" in out.response_text


def test_focus_keeps_only_the_matching_sections():
    dataset_id = _known_dataset()
    out = _run("market.analyze", {"dataset_id": dataset_id, "focus": "volume"})
    assert out.ok and "[volume" in out.response_text and "[prices" not in out.response_text
    broad = _run("market.analyze", {"dataset_id": dataset_id, "focus": "nothingmatches"})
    assert "[volume" in broad.response_text and "No section matched 'nothingmatches'" in broad.response_text


def test_metrics_with_missing_rows_say_not_enough_data():
    dataset_id = _store_dataset({"candles": _candles(), "transfers": _transfers()})  # no trades at all
    out = _run("market.analyze", {"dataset_id": dataset_id})
    assert out.ok, out.response_text
    values = out.details["metrics"]["values"]
    assert "buy_share_pct" not in values and "first_buyers_count" not in values
    assert "not enough data: no buy/sell rows" in out.response_text
    assert "no row carries both a wallet and a USD figure" not in out.response_text  # transfers carry USD here
    assert values["rows_trades"] == 0


def test_candles_alone_price_nothing_for_wallets():
    dataset_id = _store_dataset({"candles": _candles()})
    out = _run("market.analyze", {"dataset_id": dataset_id})
    assert out.ok and "not enough data: no row carries both a wallet and a USD figure" in out.response_text
    assert "net_usd_flow" not in " ".join(out.details["metrics"]["values"])
