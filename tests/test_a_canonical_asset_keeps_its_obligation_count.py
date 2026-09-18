"""Canonicalization must preserve obligation cardinality and identity.

Measured live at d5cc8637: "what is price of trx, bnb and usdc?" produced a live-data plan
with FIVE subtasks -- market:tron, market:binancecoin, market:usd-coin (all executed and
succeeded) PLUS two phantom rows: "Trx -- not a recognized market entity" and "Usdc -- not a
recognized market entity". Three requested items became five accounted obligations.

The mechanism: the plan mints market subtasks from CANONICAL ids (the coin index resolves
trx -> tron), then mints unsupported-entity subtasks from RAW surface tokens, and the
resolution authority that loop consulted (`_resolve_price_alias`) covered only the two static
alias tables -- so an index-only ticker was "unresolvable" to the very loop that runs after
its canonical form was already planned. BNB escaped the duplication only by happening to sit
in the static alias table, which is exactly the asymmetry that located the defect.

The invariant: for N requested market entities the plan holds exactly N market obligations,
unless a genuinely unknown name produces an honest UNSUPPORTED row for a name that resolves
NOWHERE -- never for a name whose canonical form is already planned.
"""
from __future__ import annotations

import time

import pytest

from core.agent_runtime.live_data_plan import build_live_data_plan
from tools.web import coin_index

_FAKE_INDEX = {
    "bnb": "binancecoin",
    "usdt": "tether",
    "usdc": "usd-coin",
    "trx": "tron",
    "xrp": "ripple",
    "hype": "hyperliquid",
    "doge": "dogecoin",
    "xmr": "monero",
    "xlm": "stellar",
    "bch": "bitcoin-cash",
    "usd1": "usd1",
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
}


@pytest.fixture(autouse=True)
def injected_index(monkeypatch):
    coin_index.reset_cache_for_test()
    monkeypatch.setattr(coin_index, "_memory_index", dict(_FAKE_INDEX), raising=False)
    monkeypatch.setattr(coin_index, "_memory_stamp", time.time(), raising=False)
    yield
    coin_index.reset_cache_for_test()


def _operations(text: str) -> list[str]:
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a", source_context=None)
    assert plan is not None
    return [task.operation for task in plan.subtasks]


CARDINALITY_CASES = [
    # The human repro: index-only aliases beside a static-table one.
    ("what is price of trx, bnb and usdc?", 3),
    ("price of ETH, BTC and USDT", 3),
    ("trx and usdc price", 2),
    ("ok price bnb usdt and TRX", 3),
    ("what is usdc worth today", 1),
    # Coordinator-linked and comma shapes must dedupe the same way.
    ("price of trx and usdc and bnb", 3),
    ("price of eth and btc now? also price of SOL", 3),
    # A single static-table asset never duplicated either (the pre-existing escape).
    ("price of bnb", 1),
]


@pytest.mark.parametrize("text, expected", CARDINALITY_CASES)
def test_requested_count_equals_obligation_count(text: str, expected: int) -> None:
    operations = _operations(text)
    assert operations == ["market_quote"] * expected, operations


def test_a_genuinely_unknown_name_is_one_honest_row_not_a_duplicate() -> None:
    operations = _operations("price of bnb and zzzcoin")
    assert operations == ["market_quote", "unsupported_market_entity"]


def test_no_alias_survives_beside_its_canonical_entity() -> None:
    plan = build_live_data_plan(
        "what is price of trx, bnb and usdc?", plan_id="p", attempt_id="a", source_context=None
    )
    assert plan is not None
    requested = {
        str(task.arguments.get("requested_text") or "").lower()
        for task in plan.subtasks
        if task.operation == "unsupported_market_entity"
    }
    assert requested == set(), f"raw aliases survived beside canonical entities: {requested}"


def test_sabotage_without_the_index_leg_the_phantoms_return() -> None:
    """Cutting the alias->canonical binding must recreate the duplicate obligations."""
    from core.agent_runtime import live_data_plan

    real = live_data_plan._resolve_price_alias

    def static_only(alias: str):
        from tools.web.web_research import _CRYPTO_ALIASES, _MARKET_QUOTE_TARGETS

        crypto_id = _CRYPTO_ALIASES.get(alias)
        if crypto_id:
            return crypto_id, "crypto", crypto_id.replace("-", " ").title()
        for target in _MARKET_QUOTE_TARGETS:
            if alias in target.aliases:
                return target.asset_key, "commodity", target.asset_name
        return None

    live_data_plan._resolve_price_alias = static_only
    try:
        operations = _operations("what is price of trx, bnb and usdc?")
        assert operations.count("unsupported_market_entity") == 2, (
            f"without the binding the phantoms did not return: {operations}"
        )
    finally:
        live_data_plan._resolve_price_alias = real
    assert _operations("what is price of trx, bnb and usdc?") == ["market_quote"] * 3


# --- fused residual segments: one source mention -> one obligation ---------------------------
#
# Measured live at d5cc8637: "wat is the price of xrp hype and doge?" executed ripple,
# hyperliquid AND dogecoin -- and still printed "Xrp Hype -- not a recognized market entity",
# because the residual loop judged the fused raw segment "xrp hype" as one indivisible
# candidate and never subtracted the mentions the canonical pass already owned. The same
# mechanism fused a valid item with its unknown neighbor into "Bch Usde".

FUSION_CASES = [
    # human repro A: fused pair, both served -> no residual row at all
    ("wat is the price of xrp hype and doge?", ["market_quote"] * 3),
    ("price of xrp, hype, doge", ["market_quote"] * 3),
    ("xrp hype doge price please", ["market_quote"] * 3),
    ("what is the price of xrp and hype and doge", ["market_quote"] * 3),
    ("price xrp hype and doge now", ["market_quote"] * 3),
    # human repro B: a valid item jammed against an unknown one -> the UNKNOWN keeps its own
    # identity, and the valid item is never part of an unresolved row
    (
        "ok what is the the price of xmr, xlm, bch usde and usd1",
        ["market_quote"] * 3 + ["unsupported_market_entity", "market_quote"],
    ),
    ("price of bch usde", ["market_quote", "unsupported_market_entity"]),
    ("price of xmr xlm and bch usde", ["market_quote"] * 3 + ["unsupported_market_entity"]),
    # comma-separated and "and"-linked fused shapes must agree
    ("price of trx hype and bnb", ["market_quote"] * 3),
]


@pytest.mark.parametrize("text, expected", FUSION_CASES)
def test_a_fused_segment_is_judged_per_mention(text: str, expected: list[str]) -> None:
    assert _operations(text) == expected


def test_the_unknown_neighbor_keeps_its_own_identity() -> None:
    plan = build_live_data_plan(
        "ok what is the the price of xmr, xlm, bch usde and usd1",
        plan_id="p",
        attempt_id="a",
        source_context=None,
    )
    assert plan is not None
    unsupported = [
        str(task.arguments.get("requested_text") or "")
        for task in plan.subtasks
        if task.operation == "unsupported_market_entity"
    ]
    assert unsupported == ["usde"]


def test_a_served_item_is_never_also_unresolved() -> None:
    plan = build_live_data_plan(
        "wat is the price of xrp hype and doge?", plan_id="p", attempt_id="a", source_context=None
    )
    assert plan is not None
    assert all(task.operation == "market_quote" for task in plan.subtasks)


def test_sabotage_judging_the_whole_segment_recreates_the_fusion() -> None:
    """Whole-segment judging must bring back the phantom fused row."""
    from core.agent_runtime import live_data_plan

    real = live_data_plan._residual_mentions
    live_data_plan._residual_mentions = lambda candidate, owned: [
        (candidate, live_data_plan._resolve_price_alias(candidate))
    ]
    try:
        operations = _operations("wat is the price of xrp hype and doge?")
        assert "unsupported_market_entity" in operations, (
            f"whole-segment judging did not recreate the phantom: {operations}"
        )
        operations_b = _operations("price of bch usde")
        assert operations_b.count("unsupported_market_entity") == 1
        plan = build_live_data_plan(
            "price of bch usde", plan_id="p", attempt_id="a", source_context=None
        )
        fused = [
            str(t.arguments.get("requested_text") or "")
            for t in plan.subtasks
            if t.operation == "unsupported_market_entity"
        ]
        assert fused == ["bch usde"]
    finally:
        live_data_plan._residual_mentions = real
    assert _operations("price of bch usde") == ["market_quote", "unsupported_market_entity"]
