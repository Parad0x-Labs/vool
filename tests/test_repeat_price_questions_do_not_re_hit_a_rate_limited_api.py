"""Every price question funnels through one keyless CoinGecko endpoint, so it must not re-ask it.

Measured 2026-08-04, driving the running daemon: after a short burst of price questions,
`simple/price` returned a hard `HTTP 429 Too Many Requests` on five consecutive calls while the
same host still served `coins/markets` and a single-id `simple/price` normally. Both callers wrap
the fetch in `except Exception`, so the 429 became an empty result rather than an error, and the
turn fell through to the model lane:

    "what is the price of bitcoin right now?"  -> 34.25s, "I couldn't ground a confident answer"
    "how much is HBAR worth right now"         -> 22.05s, answered from a general web search

Both of those coins answer in ~0.3s from the deterministic lane when the call succeeds. A silent
rate limit is indistinguishable from a broken feature, and asking the same question twice is the
single most likely thing a person does when an answer looks wrong -- which is exactly the pattern
that trips the limit.

The fix is to stop making the call, not to retry it: a short TTL cache keyed per coin id, so a
repeat or overlapping question is served from memory. Freshness is preserved in the only sense that
matters -- `as_of` comes from CoinGecko's own `last_updated_at`, so a cached quote reports the
observation time it actually has and can never claim to be newer than it is.

These tests never touch the network: `urlopen` is replaced so the call count IS the assertion.
"""
from __future__ import annotations

import json
import threading
import urllib.error

import pytest

from tools.web import web_research


def _payload_for(ids: list[str]) -> dict:
    return {
        coin_id: {
            "usd": 1.23,
            "eur": 1.0,
            "usd_24h_change": -0.5,
            "usd_market_cap": 1000.0,
            "last_updated_at": 1_800_000_000,
        }
        for coin_id in ids
    }


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, _n: int = 0) -> bytes:
        return self._body


@pytest.fixture
def counted_api(monkeypatch):
    """Replace the network with a counter. Records the ids each call actually requested."""
    web_research.reset_quote_cache_for_test()
    calls: list[list[str]] = []

    def _fake_urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        ids_part = url.split("ids=", 1)[1].split("&", 1)[0]
        requested = [item for item in ids_part.replace("%2C", ",").split(",") if item]
        calls.append(requested)
        return _FakeResponse(json.dumps(_payload_for(requested)).encode())

    monkeypatch.setattr(web_research.urllib.request, "urlopen", _fake_urlopen)
    yield calls
    web_research.reset_quote_cache_for_test()


def test_asking_the_same_pair_again_costs_no_api_call(counted_api) -> None:
    first = web_research._crypto_price_fallback_multi(["arbitrum", "litecoin"], timeout_s=8)
    assert [quote.asset_key for quote in first] == ["arbitrum", "litecoin"]
    for _ in range(4):
        repeat = web_research._crypto_price_fallback_multi(["arbitrum", "litecoin"], timeout_s=8)
        assert [quote.asset_key for quote in repeat] == ["arbitrum", "litecoin"]
    assert len(counted_api) == 1, f"the repeat questions re-hit the API {len(counted_api)} times"


def test_an_overlapping_pair_only_requests_the_coin_it_is_missing(counted_api) -> None:
    """"ARB and LTC" then "LTC and SOL" must not re-fetch LTC -- that is the burst that 429s."""
    web_research._crypto_price_fallback_multi(["arbitrum", "litecoin"], timeout_s=8)
    second = web_research._crypto_price_fallback_multi(["litecoin", "solana"], timeout_s=8)
    assert [quote.asset_key for quote in second] == ["litecoin", "solana"], "both assets answered"
    assert counted_api[1] == ["solana"], f"re-requested an already-cached coin: {counted_api[1]}"


def test_the_singular_and_plural_paths_share_one_cache(counted_api) -> None:
    """Both callers hit the same endpoint, so caching only one of them still trips the limit."""
    web_research._crypto_price_fallback_multi(["bitcoin"], timeout_s=8)
    result = web_research._crypto_price_fallback("btc price", "bitcoin", timeout_s=8)
    assert result is not None and result[0] == "coingecko_api"
    assert len(counted_api) == 1, "the singular path re-fetched what the plural path already had"


def test_a_stale_entry_is_refetched_rather_than_served_forever(counted_api, monkeypatch) -> None:
    """The control: a cache that never expires would answer with yesterday's price."""
    web_research._crypto_price_fallback_multi(["arbitrum"], timeout_s=8)
    assert len(counted_api) == 1
    aged = {
        coin_id: (stamp - (web_research._QUOTE_CACHE_TTL_S + 1), data)
        for coin_id, (stamp, data) in web_research._quote_cache.items()
    }
    monkeypatch.setattr(web_research, "_quote_cache", aged, raising=False)
    web_research._crypto_price_fallback_multi(["arbitrum"], timeout_s=8)
    assert len(counted_api) == 2, "a stale quote was served instead of refetched"


def test_a_failed_first_call_caches_nothing_and_fabricates_nothing(monkeypatch) -> None:
    """A 429 must surface as no data, never as a fabricated or empty-shaped quote."""
    web_research.reset_quote_cache_for_test()

    def _rate_limited(*_a, **_k):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(web_research.urllib.request, "urlopen", _rate_limited)
    with pytest.raises(urllib.error.HTTPError):
        web_research._crypto_price_fallback_multi(["arbitrum"], timeout_s=8)
    assert web_research._quote_cache == {}, "a failed fetch poisoned the cache"
    # The caller's own guard is what turns this into an empty result; the point here is that
    # nothing invented a value on the way.
    assert web_research.lookup_live_quotes("arb price now", timeout_s=8) == []
    web_research.reset_quote_cache_for_test()


def test_concurrent_price_questions_do_not_stampede_the_endpoint(counted_api) -> None:
    """Eight simultaneous askers is a realistic multi-client daemon, and the limit is per-host."""
    errors: list[BaseException] = []
    results: list[int] = []

    def _worker() -> None:
        try:
            for _ in range(5):
                quotes = web_research._crypto_price_fallback_multi(["arbitrum", "litecoin"], timeout_s=8)
                results.append(len(quotes))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors, f"concurrent quote fetches raised: {errors}"
    assert set(results) == {2}, f"a concurrent caller got a partial answer: {set(results)}"
    # Without the lock every thread races past the empty cache and issues its own request.
    assert len(counted_api) <= 8, f"{len(counted_api)} calls for one pair under 8 threads"
