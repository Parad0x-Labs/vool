"""A ticker missing from the hand-written alias table must be resolved, never silently dropped.

Measured live 2026-08-04, in the running app:

    "what is the price of ARB and LTC ?"  -> ARB answered from the model's own weights, attributed
                                             to "the latest Binance data", 43s; LTC reported as
                                             "not directly listed in the search results"

Two independent layers dropped ARB before any model saw the turn:

  1. `arb` was not a key in `_CRYPTO_ALIASES` / `_PRICE_ASSET_ALIASES`, so it was invisible to the
     whole deterministic price lane.
  2. `_prefer_specialized_live_research` then claimed the query anyway because `ltc` DID match, and
     the specialized crypto path answered only Litecoin -- suppressing the general web search that
     would have found ARB. Measured: `web_research("what is the price of ARB and LTC ?")` returned
     one Litecoin hit in 0.3s, while `web_research("ARB price")` alone returned five exchange
     results in 3.0s. The two-asset question was strictly worse than either asset by itself.

Layer 2 is the same first-match-wins shape as the BNB/SOL drop that
tests/test_every_asset_asked_for_is_answered.py covers, one lane deeper, at a call site that fix
never touched.

The fix is not another dict entry -- that is the Section 0.2 hard-coded mapping, and it leaves
SUI/TIA/JUP/PEPE broken identically. Symbols now resolve against CoinGecko's live rank-ordered
index (tools/web/coin_index.py).

Nearly all of this runs against an INJECTED index, so the assertions are about runtime behaviour
rather than about today's market caps. The single network-dependent check is marked and skips
cleanly when offline.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error

import pytest

from core.agent_runtime.fast_live_info_price import (
    price_assets_named,
    price_request_leaves_unresolved_content,
    recover_price_lookup_query,
)
from tools.web import coin_index
from tools.web.web_research import (
    _looks_like_price_query,
    _looks_like_price_query_all,
    _prefer_specialized_live_research,
)

# A stand-in for the real top-250 index. `arb`/`sui` are the unlisted tickers under test; `ltc` is
# the alias table's own, present so the half-answer case can be built.
_FAKE_INDEX = {"arb": "arbitrum", "sui": "sui", "ltc": "litecoin", "btc": "bitcoin"}


@pytest.fixture
def injected_index(monkeypatch):
    """Pin the index to a known set so no assertion depends on live market caps."""
    monkeypatch.setattr(coin_index, "_memory_index", dict(_FAKE_INDEX), raising=False)
    monkeypatch.setattr(coin_index, "_memory_stamp", time.time(), raising=False)
    yield _FAKE_INDEX
    coin_index.reset_cache_for_test()


@pytest.fixture
def dead_index(monkeypatch):
    """Every route to an index fails: no memory, no disk, no network."""
    coin_index.reset_cache_for_test()
    monkeypatch.setattr(coin_index, "_read_disk_cache", lambda: None)
    monkeypatch.setattr(coin_index, "_fetch_index", lambda: {})
    yield
    coin_index.reset_cache_for_test()


# --------------------------------------------------------------------------------------------
# The measured failure
# --------------------------------------------------------------------------------------------

def test_the_measured_query_names_both_assets(injected_index) -> None:
    """The exact string typed into the app on 2026-08-04."""
    assert price_assets_named("what is the price of ARB and LTC ?") == ["arb", "ltc"]


def test_the_measured_query_no_longer_declines_the_deterministic_lane(injected_index) -> None:
    """Nothing is left unaccounted for once ARB resolves, so the fast path may answer it itself.

    Before the fix this was True -- ARB read as unresolved leftover -- and the turn was handed to a
    tool lane that then dropped it a second time.
    """
    query = "what is the price of ARB and LTC ?"
    assert price_request_leaves_unresolved_content(query) is False
    assert recover_price_lookup_query(query, source_context={}) == "arb, ltc price now"


def test_both_coin_ids_reach_the_fetch_layer_in_the_order_asked(injected_index) -> None:
    assert _looks_like_price_query_all("what is the price of ARB and LTC ?") == ["arbitrum", "litecoin"]
    assert _looks_like_price_query_all("price of LTC and ARB") == ["litecoin", "arbitrum"]


def test_a_single_unlisted_ticker_resolves_on_its_own(injected_index) -> None:
    assert _looks_like_price_query("ARB price?") == "arbitrum"
    assert _looks_like_price_query("what is SUI worth") == "sui"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("price of SUI and TIA", "sui, tia price now"),
        ("how much is SUI worth right now", "sui price now"),
        ("ARB price?", "arb price now"),
    ],
)
def test_recovery_fires_when_every_named_asset_is_unlisted(monkeypatch, query, expected) -> None:
    """`recover_price_lookup_query` gates on `_extract_price_asset_alias`, not on the asset list.

    Teaching `price_assets_named` about the index was not enough: that gate consulted only the
    static tuple, so a query naming ONLY unlisted tickers returned "" and abandoned the
    deterministic lane entirely. Measured in the running app on 2026-08-04, before this second
    fix: "price of SUI and TIA" spent 60.74s in the model lane and came back with no answer at
    all, while "what is the price of ARB and LTC ?" -- same defect class, but with one statically
    known alias to carry the gate -- answered both in 0.98s.
    """
    monkeypatch.setattr(
        coin_index, "_memory_index", {"sui": "sui", "tia": "celestia", "arb": "arbitrum"}, raising=False
    )
    monkeypatch.setattr(coin_index, "_memory_stamp", time.time(), raising=False)
    assert recover_price_lookup_query(query, source_context={}) == expected
    coin_index.reset_cache_for_test()


# --------------------------------------------------------------------------------------------
# Layer 2: the shortcut must not answer a subset
# --------------------------------------------------------------------------------------------

def test_the_shortcut_does_not_claim_a_query_it_can_only_half_answer(injected_index) -> None:
    """The hijack itself.

    `zzzq` is in no index and no alias table. The specialized crypto path can still answer LTC, and
    before the fix that was exactly what it did -- returning a single Litecoin quote and preventing
    the general search from ever running. It must now decline the whole query so general search,
    which handles an unknown ticker well, gets the turn.
    """
    query = "price of LTC and ZZZQ"
    assert _looks_like_price_query(query) == "litecoin", "LTC still matches the static table"
    assert _prefer_specialized_live_research(query) is False, (
        "the shortcut claimed a query it can only partly answer -- this is the ARB drop"
    )


def test_the_shortcut_still_claims_a_query_it_can_fully_answer(injected_index) -> None:
    """The control for the test above: declining everything would be its own regression."""
    assert _prefer_specialized_live_research("price of LTC and ARB") is True
    assert _prefer_specialized_live_research("btc price") is True


# --------------------------------------------------------------------------------------------
# False positives: the failure mode that regressed three times before
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query, expected",
    [
        ("BTC PRICE?", ["btc"]),                       # phantom `PRICE` asset, attempt #1
        ("what is Seth price?", []),                   # substring of a name is not a ticker
        ("how much is my cash worth", []),             # `cash` IS a top-250 symbol (rank 228)
        ("btw what does it cost", []),                 # `btw` IS a top-250 symbol (rank 126)
        ("what price did bitcoin settle at?", ["bitcoin"]),
    ],
)
def test_ordinary_english_in_a_price_question_is_never_read_as_a_ticker(
    injected_index, monkeypatch, query, expected
) -> None:
    """A wrong resolution here fabricates a price, which is worse than any missed one.

    `cash` and `btw` are the two real collisions inside the rank ceiling, so they are injected here
    deliberately rather than assumed absent.
    """
    monkeypatch.setattr(
        coin_index,
        "_memory_index",
        {**_FAKE_INDEX, "cash": "cash-4", "btw": "bitway", "bitcoin": "bitcoin"},
        raising=False,
    )
    assert price_assets_named(query) == expected


def test_an_asset_the_index_does_not_list_is_not_invented(injected_index) -> None:
    assert coin_index.resolve_symbol("zzzq") == ""
    assert coin_index.resolve_symbol("") == ""
    assert _looks_like_price_query("ZZZQ price?") == ""


# --------------------------------------------------------------------------------------------
# Adversarial: the index itself is hostile, cold, or gone
# --------------------------------------------------------------------------------------------

def test_a_dead_index_degrades_to_the_static_table_and_never_fabricates(dead_index) -> None:
    """No network, no cache. The runtime must lose the new capability and nothing else.

    This is the property that makes the resolver safe to add at all: it can only ever ADD
    resolutions, never remove or corrupt one the static table already had.
    """
    assert coin_index.index_size() == 0
    assert coin_index.resolve_symbol("arb") == ""
    assert _looks_like_price_query("btc price") == "bitcoin", "static table still answers"
    assert price_assets_named("btc price now? and sol price please") == ["btc", "sol"]


def test_a_dead_index_is_not_retried_on_every_token_of_every_query(dead_index, monkeypatch) -> None:
    """A failing network must not put a fetch timeout on each word of each price question."""
    calls: list[int] = []
    monkeypatch.setattr(coin_index, "_fetch_index", lambda: calls.append(1) or {})
    for _ in range(5):
        coin_index.resolve_symbol("arb")
        coin_index.resolve_symbol("sui")
    assert len(calls) <= 1, f"index refetched {len(calls)} times behind a negative cache"


def test_one_transient_fetch_failure_does_not_disable_resolution_for_hours(monkeypatch) -> None:
    """A failed fetch is remembered for seconds; a successful one for hours.

    Reusing the success TTL for failures meant a single CoinGecko 429 -- easy to hit while testing,
    trivial to hit in production -- silently reverted the runtime to the static alias table for six
    hours, with no signal that it had happened. That is the whole ARB defect coming back by itself.
    """
    coin_index.reset_cache_for_test()
    monkeypatch.setattr(coin_index, "_read_disk_cache", lambda: None)
    monkeypatch.setattr(coin_index, "_write_disk_cache", lambda symbols: None)
    monkeypatch.setattr(coin_index, "_fetch_index", lambda: {})
    assert coin_index.resolve_symbol("arb") == "", "precondition: the failed fetch is cached"

    # Age the negative entry past its own (short) TTL but far inside the success TTL.
    monkeypatch.setattr(
        coin_index, "_memory_stamp", time.time() - (coin_index._NEGATIVE_CACHE_TTL_S + 1), raising=False
    )
    monkeypatch.setattr(coin_index, "_fetch_index", lambda: {"arb": "arbitrum"})
    assert coin_index.resolve_symbol("arb") == "arbitrum", (
        "a transient failure pinned the runtime to the static table for the full success TTL"
    )
    coin_index.reset_cache_for_test()


def test_a_successful_index_is_not_refetched_within_its_ttl(monkeypatch) -> None:
    """The control for the test above -- shortening the failure TTL must not shorten the success one."""
    coin_index.reset_cache_for_test()
    calls: list[int] = []
    monkeypatch.setattr(coin_index, "_read_disk_cache", lambda: None)
    monkeypatch.setattr(coin_index, "_write_disk_cache", lambda symbols: None)
    monkeypatch.setattr(coin_index, "_fetch_index", lambda: calls.append(1) or {"arb": "arbitrum"})
    assert coin_index.resolve_symbol("arb") == "arbitrum"
    monkeypatch.setattr(
        coin_index, "_memory_stamp", time.time() - (coin_index._NEGATIVE_CACHE_TTL_S + 5), raising=False
    )
    assert coin_index.resolve_symbol("arb") == "arbitrum"
    assert len(calls) == 1, f"a warm index was refetched on the negative TTL ({len(calls)} fetches)"
    coin_index.reset_cache_for_test()


@pytest.mark.parametrize(
    "payload",
    [
        "",                                            # truncated write
        "not json at all",
        "[]",                                          # right JSON, wrong shape
        '{"fetched_at": "yesterday", "symbols": {}}',  # unparseable timestamp
        '{"fetched_at": 0, "symbols": {"arb": "arbitrum"}}',   # expired
        '{"symbols": null}',
    ],
)
def test_a_corrupt_or_stale_cache_file_fails_closed(monkeypatch, tmp_path, payload) -> None:
    """A half-written or hand-edited cache must degrade, not crash and not poison resolution."""
    coin_index.reset_cache_for_test()
    cache_file = tmp_path / "coingecko_rank_index.json"
    cache_file.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(coin_index, "_cache_path", lambda: cache_file)
    monkeypatch.setattr(coin_index, "_fetch_index", lambda: {})
    assert coin_index._read_disk_cache() is None
    assert coin_index.resolve_symbol("arb") == ""
    coin_index.reset_cache_for_test()


def test_a_coin_below_the_rank_ceiling_is_not_resolved(monkeypatch) -> None:
    """The rank ceiling IS the discriminator that keeps English words out of the index.

    ChangeNOW (symbol `now`, rank 647) and Checkmate (`check`, 1389) both resolve through
    CoinGecko's per-token /search endpoint -- which is exactly why this runtime does not use it.
    """
    coin_index.reset_cache_for_test()
    rows = [
        {"symbol": "arb", "id": "arbitrum", "market_cap_rank": 93},
        {"symbol": "now", "id": "changenow", "market_cap_rank": 647},
        {"symbol": "check", "id": "checkmate-2", "market_cap_rank": 1389},
        {"symbol": "nul", "id": "no-rank-coin", "market_cap_rank": None},
    ]
    monkeypatch.setattr(coin_index, "_read_disk_cache", lambda: None)
    monkeypatch.setattr(coin_index, "_write_disk_cache", lambda symbols: None)
    monkeypatch.setattr(coin_index, "remote_fetch_forbidden", lambda: False)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _n):
            return json.dumps(rows).encode()

    monkeypatch.setattr(coin_index.urllib.request, "urlopen", lambda *a, **k: _Response())
    built = coin_index._fetch_index()
    assert built == {"arb": "arbitrum"}, f"rank ceiling let something through: {built}"
    coin_index.reset_cache_for_test()


def test_a_symbol_collision_inside_the_index_picks_the_higher_cap_coin(monkeypatch) -> None:
    """A bare ticker means the coin people mean by it, which is always the larger one."""
    coin_index.reset_cache_for_test()
    rows = [
        {"symbol": "arb", "id": "arb-impostor", "market_cap_rank": 240},
        {"symbol": "arb", "id": "arbitrum", "market_cap_rank": 93},
    ]
    monkeypatch.setattr(coin_index, "_read_disk_cache", lambda: None)
    monkeypatch.setattr(coin_index, "_write_disk_cache", lambda symbols: None)
    monkeypatch.setattr(coin_index, "remote_fetch_forbidden", lambda: False)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _n):
            return json.dumps(rows).encode()

    monkeypatch.setattr(coin_index.urllib.request, "urlopen", lambda *a, **k: _Response())
    assert coin_index._fetch_index() == {"arb": "arbitrum"}
    coin_index.reset_cache_for_test()


def test_a_forbidden_remote_fetch_is_respected(monkeypatch) -> None:
    """The offline/air-gapped policy is a hard boundary, not a preference."""
    coin_index.reset_cache_for_test()
    monkeypatch.setattr(coin_index, "remote_fetch_forbidden", lambda: True)

    def _explode(*_a, **_k):
        raise AssertionError("index fetched while remote fetch was forbidden")

    monkeypatch.setattr(coin_index.urllib.request, "urlopen", _explode)
    assert coin_index._fetch_index() == {}
    coin_index.reset_cache_for_test()


def test_a_network_error_mid_fetch_is_contained(monkeypatch) -> None:
    coin_index.reset_cache_for_test()
    monkeypatch.setattr(coin_index, "remote_fetch_forbidden", lambda: False)

    def _boom(*_a, **_k):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(coin_index.urllib.request, "urlopen", _boom)
    assert coin_index._fetch_index() == {}
    coin_index.reset_cache_for_test()


def test_concurrent_resolution_stays_consistent(injected_index) -> None:
    """The index is process-global mutable state read from the request path."""
    seen: list[str] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            for _ in range(40):
                seen.append(coin_index.resolve_symbol("arb"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors, f"concurrent resolution raised: {errors}"
    assert set(seen) == {"arbitrum"}, f"inconsistent resolution under load: {set(seen)}"


# --------------------------------------------------------------------------------------------
# One real call, so the contract is checked against the actual API and not only against a fixture
# --------------------------------------------------------------------------------------------

@pytest.mark.gauntlet
def test_the_real_index_resolves_the_ticker_that_failed_live() -> None:
    """A fixture cannot catch CoinGecko changing its payload shape; this can.

    tests/conftest.py's `block_live_public_hive_network` blocks every outbound host under pytest,
    so this skips in the default lane BY DESIGN -- the skip means "the harness has no network",
    never "the API is down". Verified by hand outside pytest on 2026-08-04: 234 symbols indexed in
    0.21s, arb -> arbitrum, ltc -> litecoin, "price" -> "".
    """
    coin_index.reset_cache_for_test()
    if not coin_index.warm_index():
        pytest.skip("no outbound network in this lane (conftest blocks it); see docstring")
    assert coin_index.index_size() > 100, "index looks truncated"
    assert coin_index.resolve_symbol("arb") == "arbitrum"
    assert coin_index.resolve_symbol("ltc") == "litecoin"
    assert coin_index.resolve_symbol("price") == ""
    assert coin_index.resolve_symbol("what") == ""


# --------------------------------------------------------------------------------------------
# Resolving a micro-priced token is only useful if its price survives formatting
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        (64238.0, "$64,238.00 USD"),
        (44.87, "$44.87 USD"),
        (0.0823, "$0.0823 USD"),
        (0.0000082, "$0.0000082 USD"),      # PEPE — printed "$0.0000" before this
        (0.0000091, "$0.0000091 USD"),      # SHIB
        (0.0000000123, "$0.0000000123 USD"),
        (0.0, "$0.0000 USD"),
    ],
)
def test_a_micro_priced_token_is_not_rendered_as_zero(value, expected) -> None:
    """`price of JUP and PEPE` answered "Pepe is $0.0000 USD" -- a real, sourced, correctly
    fetched quote reported as zero, purely by the formatter's fixed 4-decimal floor.

    Those tokens only became answerable at all once symbols resolved against the live index, so
    this formatter had never been handed one. A quote that renders as $0.0000 is worse than no
    quote: it is a wrong number wearing a real source.
    """
    from core.live_quote_contract import LiveQuoteResult

    quote = LiveQuoteResult(
        asset_key="x", asset_name="X", symbol="X", value=value, currency="USD",
        as_of="", source_label="CoinGecko", source_url="u", kind="crypto",
    )
    assert quote._price_text() == expected
