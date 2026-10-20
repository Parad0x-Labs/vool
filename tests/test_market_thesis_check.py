"""market.thesis_check: the user's thesis turned into claims, each judged by code from stored rows.

The dataset is the hand-built one from test_market_analyze (a known spike, known early buyers), so
every verdict is checkable by hand. A claim the metrics cannot answer says so; nothing is coerced.
"""
from __future__ import annotations

import pytest

from tests.test_market_analyze import EARLY, HODL, LATE, _known_dataset, _store_dataset, _wipe_datasets


@pytest.fixture(autouse=True)
def clean_market_tables():
    _wipe_datasets()
    yield
    _wipe_datasets()


def _run(args: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("market.thesis_check", args, source_context={})


def test_one_true_one_false_one_unknown_metric_claim():
    dataset_id = _known_dataset()
    out = _run({"dataset_id": dataset_id, "claims": [
        {"metric": "window_return_pct", "comparator": ">", "value": 50, "note": "the token doubled"},
        {"metric": "buy_share_pct", "comparator": "<", "value": 50, "note": "sellers dominated"},
        {"metric": "moon_phase", "comparator": ">", "value": 0, "note": "the moon did it"},
    ]})
    assert out.ok, out.response_text
    verdicts = out.details["verdicts"]
    assert [v["verdict"] for v in verdicts] == ["supported", "contradicted", "not checkable"]
    assert verdicts[0]["actual"] == 100.0 and "window_return_pct is 100.0, claimed > 50" in verdicts[0]["detail"]
    assert verdicts[1]["actual"] == pytest.approx(66.7, abs=0.1)
    assert "VOOL does not compute 'moon_phase'" in verdicts[2]["detail"]
    # the reply shows each claim as written next to its verdict
    assert "the token doubled" in out.response_text and "the moon did it" in out.response_text
    assert "supported." in out.response_text and "contradicted." in out.response_text


def test_within_needs_a_tolerance_and_equals_uses_one():
    dataset_id = _known_dataset()
    out = _run({"dataset_id": dataset_id, "claims": [
        {"metric": "buy_share_pct", "comparator": "within", "value": 65, "tolerance": 5},
        {"metric": "buy_share_pct", "comparator": "within", "value": 60},
        {"metric": "buy_share_pct", "comparator": "=", "value": 70, "tolerance": 5},
        {"metric": "buy_share_pct", "comparator": "=", "value": 70, "tolerance": 0.1},
        {"metric": "buy_share_pct", "comparator": "~", "value": 70},
    ]})
    assert out.ok, out.response_text
    assert [v["verdict"] for v in out.details["verdicts"]] == [
        "supported", "not checkable", "supported", "contradicted", "not checkable"]
    assert "'within' needs a tolerance" in out.details["verdicts"][1]["detail"]
    assert "not one of > < >= <= = within" in out.details["verdicts"][4]["detail"]


def test_a_claim_window_looks_at_only_that_slice():
    dataset_id = _known_dataset()  # 24h dataset: 100% over the window, but flat prices in the last 5h
    out = _run({"dataset_id": dataset_id, "claims": [
        {"metric": "window_return_pct", "comparator": ">", "value": 50},
        {"metric": "window_return_pct", "comparator": ">", "value": 50, "window": "5h"},
    ]})
    assert [v["verdict"] for v in out.details["verdicts"]] == ["supported", "contradicted"]
    assert out.details["verdicts"][1]["actual"] == 0.0


def test_per_wallet_and_per_day_metrics_are_claimable():
    dataset_id = _known_dataset()
    out = _run({"dataset_id": dataset_id, "claims": [
        {"metric": "net_usd_flow:" + EARLY, "comparator": ">", "value": 150},
        {"metric": "net_usd_flow:" + HODL, "comparator": "<=", "value": 20},
        {"metric": "trade_count:" + LATE, "comparator": "=", "value": 1, "tolerance": 0},
        {"metric": "daily_return_pct:2026-10-14", "comparator": "<", "value": 1},
        {"metric": "volume_spike_count", "comparator": ">=", "value": 1},
        {"metric": "buyers_before_top_rises_count", "comparator": ">", "value": 0},
    ]})
    assert out.ok and all(v["verdict"] == "supported" for v in out.details["verdicts"]), out.response_text


def test_a_metric_the_rows_cannot_fill_is_not_checkable_never_coerced():
    thin = _store_dataset({"candles": [], "trades": [], "transfers": [
        {"ts": 1791882000, "kind": "in", "wallet": EARLY, "amount": 1.0, "usd": None, "price": None, "tx": "x"},
        {"ts": 1791885600, "kind": "out", "wallet": EARLY, "amount": 1.0, "usd": None, "price": None, "tx": "y"},
    ]})
    out = _run({"dataset_id": thin, "claims": [
        {"metric": "window_return_pct", "comparator": ">", "value": 0},
        {"metric": "buy_share_pct", "comparator": ">", "value": 0},
    ]})
    assert out.ok and [v["verdict"] for v in out.details["verdicts"]] == ["not checkable", "not checkable"]
    assert "not enough data" in out.details["verdicts"][0]["detail"]


def test_bad_requests_are_refused_without_touching_anything():
    dataset_id = _known_dataset()
    missing = _run({"claims": [{"metric": "buy_count", "comparator": ">", "value": 0}]})
    assert not missing.ok and missing.status == "missing_dataset"
    unknown = _run({"dataset_id": "md_nope", "claims": [{"metric": "buy_count", "comparator": ">", "value": 0}]})
    assert not unknown.ok and unknown.status == "unknown_dataset"
    noclaims = _run({"dataset_id": dataset_id})
    assert not noclaims.ok and noclaims.status == "invalid_arguments" and "Write the thesis as claims" in noclaims.response_text
    toomany = _run({"dataset_id": dataset_id, "claims": [
        {"metric": "buy_count", "comparator": ">", "value": 0}] * 21})
    assert not toomany.ok and toomany.status == "too_many"
    notnumber = _run({"dataset_id": dataset_id, "claims": [
        {"metric": "buy_count", "comparator": ">", "value": "many"}]})
    assert notnumber.ok and "not a number" in notnumber.response_text


def test_a_thesis_sentence_reaches_the_tool():
    from core.tool_demand_signals import resolve_demand_signals

    assert "market.thesis_check" in resolve_demand_signals(
        "check my thesis on $BONK: smart wallets bought before every pump").explicit_intents
    # a thesis with no token or dataset named is a school essay as often as a trade idea
    assert "market.thesis_check" not in resolve_demand_signals("check my thesis draft for typos").explicit_intents


def test_a_thesis_window_applies_to_claims_without_their_own():
    dataset_id = _known_dataset()  # 100% over 24h, flat over the last 5h
    out = _run({"dataset_id": dataset_id, "window": "5h", "claims": [
        {"metric": "window_return_pct", "comparator": ">", "value": 50},
        {"metric": "window_return_pct", "comparator": ">", "value": 50, "window": "24h"},
    ]})
    assert out.ok, out.response_text
    assert [v["verdict"] for v in out.details["verdicts"]] == ["contradicted", "supported"]
    assert out.details["verdicts"][0]["actual"] == 0.0
    assert "claims without their own window look at the last 5h" in out.response_text


def test_an_unusable_thesis_window_is_refused_not_ignored():
    dataset_id = _known_dataset()
    out = _run({"dataset_id": dataset_id, "window": "banana", "claims": [
        {"metric": "window_return_pct", "comparator": ">", "value": 50}]})
    assert not out.ok and out.status == "invalid_arguments"
    assert "thesis window is not usable" in out.response_text and "Nothing was evaluated" in out.response_text
