"""How calls did: price at a post's time, now, and peak and low after; and the X callers flow that uses it.

The public price source (GeckoTerminal's documented v2 shapes) and X are loopback fixtures; no real
host is contacted. Everything goes through the model's door (``execute_runtime_tool``).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_api_reads import EVM, SOL, _connect, fake_x

POOL = "PooL1111111111111111111111111111111111111111"
T0 = 1791878400  # 2026-10-13 08:00 UTC, the first fixture post on SOL
NOW = T0 + 6 * 3600


def _candles(start, n, step, prices):
    """Newest first, like the source: [ts, open, high, low, close, volume]."""
    rows = []
    for i in range(n):
        ts = start + i * step
        p = prices(i)
        rows.append([ts, p, p * 1.1, p * 0.9, p, 1000.0])
    return list(reversed(rows))


class FakePrices:
    def __init__(self):
        self.requests: list[dict] = []
        self.pools = {SOL: [{"attributes": {"address": POOL, "name": "FDOG / SOL", "reserve_in_usd": "50000",
                                             "pool_created_at": "2026-10-13T07:00:00Z"}}]}
        # 5-minute candles from 07:00 for 7h: price 1.0 until 08:00, then 3.0 peak at 10:00, then 2.0
        self.ohlcv = _candles(T0 - 3600, 84, 300,
                              lambda i: 1.0 if i < 12 else (3.0 if i == 36 else 2.0))
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.requests.append({"path": parts.path, "query": query, "auth": self.headers.get("Authorization")})
                if outer.status != 200:
                    body, status = {"errors": [{"title": "x"}]}, outer.status
                elif parts.path.endswith("/pools") and "/tokens/" in parts.path:
                    token = parts.path.split("/tokens/")[1].split("/")[0]
                    body, status = {"data": outer.pools.get(token, [])}, 200
                elif "/ohlcv/" in parts.path:
                    body, status = {"data": {"attributes": {"ohlcv_list": outer.ohlcv}}}, 200
                elif parts.path.endswith("/search/pools"):
                    body, status = {"data": []}, 200
                else:
                    body, status = {"errors": [{"title": "Not found"}]}, 404
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def prices(monkeypatch):
    from core import token_performance

    with FakePrices() as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        monkeypatch.setattr(token_performance, "_cache", {})
        monkeypatch.setattr(token_performance, "_clock", lambda: float(NOW))
        yield server


def _run(intent, args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, args, source_context={})


def test_web_off_means_nothing_goes_online(prices):
    out = _run("token.performance", {"token": SOL, "at": "2026-10-13T08:00:00Z"})
    assert not out.ok and out.status == "web_off" and "Turn on web access" in out.response_text
    assert prices.requests == []


def test_price_then_now_peak_and_low(prices, enable_web):
    out = _run("token.performance", {"token": SOL, "at": "2026-10-13T08:00:00Z"})
    assert out.ok, out.response_text
    [call] = out.details["calls"]
    assert call["price_at"] == pytest.approx(1.0)  # the close of the 07:55 candle, the last one done by 08:00
    assert call["price_now"] == pytest.approx(2.0) and call["change_now_pct"] == 100.0
    assert call["peak_after"] == pytest.approx(3.3) and call["peak_pct"] == pytest.approx(230.0)
    assert call["low_after"] == pytest.approx(1.8)  # only candles after the post count, not the 1.0 before it
    assert call["peak_at"] == "2026-10-13 10:00 UTC" and call["symbol"] == "FDOG" and call["chain"] == "solana"
    assert "from 5-minute candles" in out.response_text and "last trade" not in out.response_text
    ohlcv = next(r for r in prices.requests if "/ohlcv/" in r["path"])
    assert ohlcv["path"].endswith(f"/networks/solana/pools/{POOL}/ohlcv/minute")
    assert ohlcv["query"]["token"] == SOL and ohlcv["query"]["currency"] == "usd"
    assert all(r["auth"] is None for r in prices.requests)  # keyless: nothing of the user's is sent


def test_a_move_inside_the_posts_own_candle_is_not_credited(prices, enable_web):
    """A post at 08:02 lands inside the 08:00 candle; that candle's high happened partly before the
    post, so the price then is the 07:55 close and the peak counts only from 08:05 on."""
    prices.ohlcv = _candles(T0 - 3600, 84, 300, lambda i: 1.0 if i < 12 else (5.0 if i == 12 else 2.0))
    out = _run("token.performance", {"token": SOL, "at": "2026-10-13T08:02:00Z"})
    [call] = out.details["calls"]
    assert call["price_at"] == pytest.approx(1.0)
    assert call["peak_after"] == pytest.approx(2.2) and call["peak_at"] == "2026-10-13 08:05 UTC"


def test_a_token_that_stopped_trading_says_when_now_is(prices, enable_web, monkeypatch):
    from core import token_performance

    monkeypatch.setattr(token_performance, "_clock", lambda: float(NOW + 3600))
    out = _run("token.performance", {"token": SOL, "at": "2026-10-13T08:00:00Z"})
    [call] = out.details["calls"]
    assert "last trade 2026-10-13 13:55 UTC" in call["note"] and call["price_now"] == pytest.approx(2.0)


def test_gaps_are_named_never_filled(prices, enable_web):
    out = _run("token.performance", {"calls": [
        {"token": "$FDOG", "at": "2026-10-13T08:00:00Z"},
        {"token": "So11111111111111111111111111111111111111112", "at": "2026-10-13T08:00:00Z"},
        {"token": SOL, "at": "2026-10-13T05:00:00Z"},
        {"token": SOL, "at": "2030-01-01T00:00:00Z"},
    ]})
    rows = out.details["calls"]
    assert rows[0]["status"] == "no_address" and "ticker alone" in rows[0]["message"]
    assert rows[1]["status"] == "not_found" and "price_at" not in rows[1]
    assert rows[2]["status"] == "ok" and "before its main pool started trading" in rows[2]["note"]
    assert rows[3]["status"] == "invalid_arguments" and "future" in rows[3]["message"]


def test_a_rate_limit_stops_the_rest_and_says_so(prices, enable_web):
    prices.status = 429
    out = _run("token.performance", {"calls": [{"token": SOL, "at": "2026-10-13T08:00:00Z"},
                                               {"token": SOL, "at": "2026-10-13T09:00:00Z"}]})
    assert not out.ok and [r["status"] for r in out.details["calls"]] == ["rate_limited", "not_checked"]
    assert len(prices.requests) == 1


def test_too_many_calls_are_refused_before_going_online(prices, enable_web):
    out = _run("token.performance", {"calls": [{"token": SOL, "at": "2026-10-13T08:00:00Z"}] * 16})
    assert not out.ok and out.status == "too_many" and prices.requests == []


def test_callers_with_min_followers_and_how_their_calls_did(fake_x, prices, enable_web):
    """sls's case: search a token, keep accounts with N+ followers, read their earlier calls, price them."""
    _connect()
    out = _run("x.token_callers", {"token": SOL, "min_followers": 1000, "history_for": 2, "evaluate_calls": True})
    assert out.ok, out.response_text
    callers = out.details["callers"]
    assert [c["username"] for c in callers] == ["early_ape", "late_whale"]  # both have 1,000+
    early = callers[0]
    assert early["this_call_performance"]["status"] == "ok"
    assert early["this_call_performance"]["peak_pct"] == pytest.approx(230.0)
    evm_call = next(c for c in early["other_calls"] if c["token"] == EVM.lower())
    assert evm_call["performance"]["status"] == "not_found"  # no pool in the fixture: named, not guessed
    assert early["calls_priced"] == 1
    assert "ticker-only call(s) can't be priced" in out.response_text
    out = _run("x.token_callers", {"token": SOL, "min_followers": 50_000})
    assert [c["username"] for c in out.details["callers"]] == ["late_whale"]
    assert out.details["callers_below_min_followers"] == 1 and "1 smaller account(s) left out" in out.response_text


def test_pricing_calls_with_web_off_reads_no_extra_histories(fake_x, prices):
    _connect()
    out = _run("x.token_callers", {"token": SOL, "evaluate_calls": True})
    assert out.ok and "web access is off" in out.response_text
    assert [r["path"] for r in fake_x.requests] == ["/2/tweets/search/recent"]  # no callers' timelines read
    assert prices.requests == []


@pytest.mark.parametrize("text,seated", [
    ("how did @early_ape's calls do?", True),
    ("check his track record on token calls", True),
    ("how did the meeting go", False),
])
def test_performance_requests_seat_the_tool(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    assert ("token.performance" in resolve_demand_signals(text).explicit_intents) is seated
