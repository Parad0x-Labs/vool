"""The watch poller: what a watched wallet or price does while VOOL runs.

Providers are loopback fixtures scripted with each provider's documented answer shape; no real
host is contacted. Wallets read through the chain-data provider path (Etherscan's txlist here);
prices read the public pool data the same way token.performance does.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home
from tests.pa_beta_gate._pc_calendar_rig import T0, Clock

ETHERSCAN_KEY = "ETHERSCANFIXTUREKEY0123456789ABCD"
EVM_ADDRESS = "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"
OTHER_ADDRESS = "0x1111111111111111111111111111111111111111"
SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
POOL = "PooL1111111111111111111111111111111111111111"


def _tx(hash_: str, *, direction: str = "sent", value: str = "0.5", ts: int = 1_800_000_000):
    """One Etherscan txlist row, the documented shape chain_data reads (value in raw units)."""
    if direction == "sent":
        source, target = EVM_ADDRESS.lower(), OTHER_ADDRESS
    else:
        source, target = OTHER_ADDRESS, EVM_ADDRESS.lower()
    return {"hash": hash_, "from": source, "to": target, "value": str(int(float(value) * 10**18)),
            "timeStamp": str(ts), "isError": "0", "blockNumber": "1"}


class FakeEtherscan:
    """Answers the account txlist read; the rows change between polls like real history."""

    def __init__(self):
        self.rows: list[dict] = []
        self.status = 200
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.requests.append({"path": parts.path, "query": query})
                if outer.status != 200:
                    raw, status = b"too many requests", outer.status
                else:
                    raw, status = json.dumps({"status": "1", "message": "OK", "result": outer.rows}).encode(), 200
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


class FakePrices:
    """The public price source: one pool with a settable price, and candles for move rules."""

    def __init__(self, price: float = 1.0):
        self.price = price
        self.start_price = price
        self.requests: list[dict] = []
        self.status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                outer.requests.append({"path": parts.path})
                if outer.status != 200:
                    body, status = {"errors": [{"title": "limit"}]}, outer.status
                elif parts.path.endswith("/pools") and "/tokens/" in parts.path:
                    body = {"data": [{"attributes": {
                        "address": POOL, "name": "FDOG / SOL", "reserve_in_usd": "50000",
                        "base_token_price_usd": f"{outer.price:.8f}",
                        "pool_created_at": "2026-10-01T00:00:00Z"}}]}
                    status = 200
                elif "/ohlcv/" in parts.path:
                    # hourly candles for the six hours before now, flat at the start price
                    step = 3600
                    now = int(T0.timestamp())
                    candles = [[now - i * step, outer.start_price, outer.start_price, outer.start_price,
                                outer.start_price, 100.0] for i in range(6, 0, -1)]
                    body = {"data": {"attributes": {"ohlcv_list": candles}}}
                    status = 200
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
def clock(monkeypatch):
    return Clock(T0, monkeypatch)


@pytest.fixture
def watch_home(isolated_home, monkeypatch):
    """Isolated credential home; the shared test database cleared of watch and bell rows.

    The conftest pins one database for the whole session and resets its own table
    list between tests; the watch tables are not on that list, so this file clears
    them (and the notification centre) itself, or one test's watches would poll in
    the next test's sweep.
    """
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("PRAGMA secure_delete=ON")
        for table in ("market_watches", "market_watch_events", "notification_items",
                      "notification_deliveries", "notification_preferences"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
    return isolated_home


def _eth(monkeypatch, server):
    from core import chain_providers as cp

    monkeypatch.setattr(cp, "ETHERSCAN_API_URL", server.url + "/v2/api")


def _store_etherscan_key():
    from core.credential_store import store_credential

    store_credential("chain.data.etherscan", ETHERSCAN_KEY, label="fixture")


def _add_wallet(clock, *, label="Team wallet"):
    from core import market_watch

    return market_watch.add_watch({"kind": "wallet", "chain": "ethereum", "subject": EVM_ADDRESS, "label": label},
                                  now_fn=clock)


def _add_price(clock, rule, *, subject=SOL, label="FDOG"):
    from core import market_watch

    return market_watch.add_watch({"kind": "price", "chain": "solana", "subject": subject, "label": label,
                                   "rule": rule}, now_fn=clock)


def _poller(clock):
    from core import market_watch

    return market_watch.MarketWatchPoller(now_fn=clock, sleep_fn=lambda _seconds: None)


def _clear_price_cache():
    from core import token_performance

    token_performance._cache = {}


def _items():
    from core.operator import notification_center

    return [item for item in notification_center.list_items(include_dismissed=True)["items"]
            if item["source_kind"] == "market_watch"]


def _events():
    from storage.db import get_connection

    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM market_watch_events").fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------ wallets


def test_no_watches_means_no_requests_at_all(watch_home, clock):
    with FakeEtherscan() as server:
        outcome = _poller(clock).sweep_once()
        assert outcome["checked"] == 0 and server.requests == []


def test_baseline_poll_never_alerts(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        outcome = poller.sweep_once()
        assert outcome["checked"] == 1 and outcome["events"] == 0
        assert server.requests and _items() == []


def test_one_new_tx_is_exactly_one_notification_with_direction_and_amount(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        assert _items() == []
        clock.advance(minutes=6)
        server.rows = [_tx("0x" + "bb" * 32, direction="received", value="12.5"), _tx("0x" + "aa" * 32)]
        outcome = poller.sweep_once()
        assert outcome["events"] == 1
        [item] = _items()
        assert item["title"] == "Team wallet received 12.5 ETH"
        assert "received 12.5 ETH from " + OTHER_ADDRESS in item["body"]
        assert "https://etherscan.io/tx/0x" + "bb" * 32 in item["body"]
        assert [event["kind"] for event in _events()] == ["wallet_tx"]
        assert ETHERSCAN_KEY not in item["body"]


def test_the_same_tx_is_never_reported_twice(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        clock.advance(minutes=6)
        poller.sweep_once()
        clock.advance(minutes=6)
        poller.sweep_once()
        assert _items() == []


def test_many_new_tx_become_one_summary(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        clock.advance(minutes=6)
        fresh = [_tx("0x" + f"{i:02x}" * 32, value=str(i)) for i in range(1, 26)]
        server.rows = [*fresh, _tx("0x" + "aa" * 32)]
        outcome = poller.sweep_once()
        assert outcome["events"] == 1
        [item] = _items()
        assert item["title"] == "Team wallet: 25 new transactions"
        assert "25 new transactions appeared" in item["body"]
        [event] = _events()
        assert event["kind"] == "wallet_summary"


def test_a_rate_limit_backs_off_and_makes_no_alert(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        server.status = 429
        clock.advance(minutes=6)
        server.rows = [_tx("0x" + "bb" * 32), _tx("0x" + "aa" * 32)]
        outcome = poller.sweep_once()
        assert outcome["events"] == 0 and _items() == []
        from core import market_watch

        watch = market_watch.list_watches()[0]
        # a reason code, worded by the Settings card's catalog (vmw.err.rate_limited), no provider text
        assert watch["last_error"] == "rate_limited"


def test_a_removed_key_stops_the_watch_with_one_notice_and_no_more_calls(watch_home, clock, monkeypatch):
    from core.credential_store import delete_credential

    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        assert delete_credential("chain.data.etherscan")
        clock.advance(minutes=6)
        outcome = poller.sweep_once()
        assert outcome["needs_setup"] == 1
        [notice] = _items()
        assert notice["title"] == "Team wallet stopped checking"
        assert "key in Settings" in notice["body"]
        from core import market_watch

        assert market_watch.list_watches()[0]["state"] == "needs_setup"
        calls_after_removal = len(server.requests)
        clock.advance(minutes=12)
        poller.sweep_once()
        assert len(server.requests) == calls_after_removal  # no further provider calls
        assert len(_items()) == 1  # the notice fired once, not per poll


# ------------------------------------------------------------------ prices


def test_price_crossing_fires_once_not_every_poll(watch_home, clock, enable_web, monkeypatch):
    from core import token_performance

    _add_price(clock, {"above": 0.5})
    poller = _poller(clock)
    with FakePrices(price=0.4) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["checked"] == 1 and outcome["events"] == 0  # below: nothing
        clock.advance(minutes=2)
        server.price = 0.53
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["events"] == 1
        [item] = _items()
        assert item["title"] == "FDOG is above 0.5: now 0.53"
        clock.advance(minutes=2)  # still above: no second alert
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["events"] == 0 and len(_items()) == 1
        clock.advance(minutes=2)  # back past the threshold by 1%: re-armed
        server.price = 0.49
        _clear_price_cache()
        poller.sweep_once()
        clock.advance(minutes=2)
        server.price = 0.55
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["events"] == 1 and len(_items()) == 2  # crossed again: one more


def test_price_move_rule_compares_to_the_window_start(watch_home, clock, enable_web, monkeypatch):
    from core import token_performance

    _add_price(clock, {"move_pct": -20, "window": "6h"})
    poller = _poller(clock)
    with FakePrices(price=0.39) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        server.start_price = 0.5  # the candles: it was 0.5 six hours ago
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["events"] == 1
        [item] = _items()
        assert "dropped 22" in item["title"] and "6h" in item["title"] and "now 0.39" in item["title"]


def test_a_missing_pool_prices_nothing_and_says_why(watch_home, clock, enable_web, monkeypatch):
    from core import token_performance

    _add_price(clock, {"below": 0.1})
    poller = _poller(clock)
    with FakePrices() as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        server.status = 404
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["events"] == 0 and _items() == []
        from core import market_watch

        watch = market_watch.list_watches()[0]
        assert watch["last_error"] == "no_pool"
        assert watch["state"] == "active"  # a missing price is not a broken watch


def test_a_price_rate_limit_backs_off_and_holds(watch_home, clock, enable_web, monkeypatch):
    """A 429 doubles the rest: after two refusals the source is not asked again inside the window."""
    from core import token_performance

    _add_price(clock, {"above": 0.5})
    poller = _poller(clock)
    with FakePrices() as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        server.status = 429
        _clear_price_cache()
        outcome = poller.sweep_once()  # first refusal: a 60 second rest
        assert outcome["events"] == 0 and _items() == []
        from core import market_watch

        watch = market_watch.list_watches()[0]
        assert watch["last_error"] == "rate_limited"
        calls = len(server.requests)
        clock.advance(seconds=70)  # past the first rest, due again: asked, refused, doubled
        _clear_price_cache()
        poller.sweep_once()
        assert len(server.requests) == calls + 1
        calls = len(server.requests)
        clock.advance(seconds=70)  # inside the doubled rest: not asked
        _clear_price_cache()
        poller.sweep_once()
        assert len(server.requests) == calls


def test_a_price_watch_stops_when_web_access_goes_off(watch_home, clock, enable_web, monkeypatch):
    from core import policy_engine, token_performance

    _add_price(clock, {"above": 0.5})
    poller = _poller(clock)
    with FakePrices(price=0.6) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        _clear_price_cache()
        outcome = poller.sweep_once()
        assert outcome["events"] == 1
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base
    clock.advance(minutes=2)
    outcome = poller.sweep_once()
    assert outcome["needs_setup"] == 1
    from core import market_watch

    assert market_watch.list_watches()[0]["state"] == "needs_setup"


# ------------------------------------------------------------------ restart


def test_time_missed_while_closed_is_at_most_one_notification_per_watch(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        clock.advance(hours=20)  # VOOL was closed for a long time
        fresh = [_tx("0x" + f"{i:02x}" * 32, value=str(i)) for i in range(1, 26)]
        server.rows = [*fresh, _tx("0x" + "aa" * 32)]
        outcome = poller.sweep_once()
        assert outcome["events"] == 1
        assert len(_items()) == 1 and _items()[0]["title"] == "Team wallet: 25 new transactions"


def test_a_restart_with_five_missed_transactions_is_one_notification(watch_home, clock, monkeypatch):
    """Five transactions while VOOL was closed: one summary, not five alerts (below the >10 rule)."""
    _store_etherscan_key()
    _add_wallet(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        _poller(clock).sweep_once()  # the run before VOOL was closed: the baseline
        clock.advance(hours=20)
        server.rows = [*[_tx("0x" + f"{i:02x}" * 32, value=str(i)) for i in range(1, 6)], _tx("0x" + "aa" * 32)]
        outcome = _poller(clock).sweep_once()  # a new process: the first sweep after the restart
        assert outcome["events"] == 1
        [item] = _items()
        assert item["title"] == "Team wallet: 5 new transactions"
        assert "since VOOL last checked" in item["body"]
        assert [event["kind"] for event in _events()] == ["wallet_summary"]


def test_a_quick_restart_still_folds_what_it_missed_into_one(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        _poller(clock).sweep_once()
        clock.advance(minutes=6)  # restarted inside two intervals: still the process's first sweep
        server.rows = [_tx("0x" + "b1" * 32), _tx("0x" + "b2" * 32), _tx("0x" + "b3" * 32), _tx("0x" + "aa" * 32)]
        assert _poller(clock).sweep_once()["events"] == 1
        assert [item["title"] for item in _items()] == ["Team wallet: 3 new transactions"]


def test_a_long_gap_in_one_process_is_one_notification(watch_home, clock, monkeypatch):
    """The machine slept for hours with VOOL open: the gap is more than two intervals."""
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        clock.advance(hours=3)
        server.rows = [_tx("0x" + "c1" * 32), _tx("0x" + "c2" * 32), _tx("0x" + "aa" * 32)]
        assert poller.sweep_once()["events"] == 1
        assert len(_items()) == 1


def test_a_summary_event_keeps_valid_json(watch_home, clock, monkeypatch):
    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.rows = [_tx("0x" + "aa" * 32)]
        poller.sweep_once()
        clock.advance(minutes=6)
        server.rows = [*[_tx("0x" + f"{i:02x}" * 32, value=str(i)) for i in range(1, 26)], _tx("0x" + "aa" * 32)]
        poller.sweep_once()
    [event] = _events()
    assert len(event["facts_json"]) <= 2000
    facts = json.loads(event["facts_json"])
    assert facts["count"] == 25 and facts["rows"] and facts["trimmed"] is True


@pytest.mark.parametrize(("status", "code", "words"), [
    (401, "key_rejected", "was refused"),
    (402, "quota_exhausted", "no quota left"),
    (403, "plan_refused", "does not include"),
])
def test_a_provider_refusal_stops_a_wallet_watch_with_one_notice(watch_home, clock, monkeypatch, status, code, words):
    from core import market_watch

    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.status = status
        outcome = poller.sweep_once()
        assert outcome["needs_setup"] == 1
        [notice] = _items()
        assert notice["title"] == "Team wallet stopped checking" and words in notice["body"]
        assert "Etherscan" not in notice["body"]
        watch = market_watch.list_watches()[0]
        assert watch["state"] == "needs_setup" and watch["last_error"] == code
        calls = len(server.requests)
        for _ in range(3):  # the key is still stored, but the watch does not retry by itself
            clock.advance(minutes=6)
            poller.sweep_once()
        assert len(server.requests) == calls and len(_items()) == 1
        server.status = 200  # the user fixed the key and pressed Resume
        server.rows = [_tx("0x" + "aa" * 32)]
        market_watch.resume_watch({"id": watch["id"]})
        clock.advance(minutes=6)
        assert poller.sweep_once()["checked"] == 1
        assert market_watch.list_watches()[0]["last_error"] == ""


def test_local_only_stops_a_wallet_watch_with_one_notice(watch_home, clock, monkeypatch):
    from core import market_watch, policy_engine

    _store_etherscan_key()
    _add_wallet(clock)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["local_only_mode"] = True
    base["system"] = system
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", base)
    outcome = _poller(clock).sweep_once()  # the real provider address: refused before any network
    assert outcome["needs_setup"] == 1
    [notice] = _items()
    assert "Local Only" in notice["body"]
    assert market_watch.list_watches()[0]["last_error"] == "local_only"


def test_a_failed_wallet_call_counts_against_the_hourly_budget(watch_home, clock, monkeypatch):
    from core import chain_providers as cp
    from core import market_watch

    _store_etherscan_key()
    _add_wallet(clock)
    poller = _poller(clock)
    provider = cp.config_for("etherscan").registry_id
    now = market_watch._epoch(clock())
    for _ in range(market_watch.MAX_CALLS_PER_HOUR - 1):
        poller._budget.spent(provider, now)
    with FakeEtherscan() as server:
        _eth(monkeypatch, server)
        server.status = 404  # answered, failed, no backoff: only the budget can hold the next call
        poller.sweep_once()
        assert len(server.requests) == 1
        clock.advance(minutes=6)
        poller.sweep_once()
        assert len(server.requests) == 1  # the failed call was the sixtieth
        assert market_watch.list_watches()[0]["last_error"] == "budget"


@pytest.mark.parametrize(("price", "rule"), [
    (0.0, {"below": 0.1}), (-1.0, {"below": 0.1}), (float("nan"), {"above": 0.5}), (float("inf"), {"above": 0.5}),
])
def test_a_price_that_is_not_a_positive_finite_number_never_alerts(watch_home, clock, enable_web, monkeypatch,
                                                                    price, rule):
    from core import market_watch, token_performance

    _add_price(clock, rule)
    with FakePrices(price=price) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        _clear_price_cache()
        outcome = _poller(clock).sweep_once()
    assert outcome["events"] == 0 and _items() == []
    assert market_watch.list_watches()[0]["last_error"] == "bad_answer"


def test_a_move_rule_holds_when_the_candle_read_has_no_budget(watch_home, clock, enable_web, monkeypatch):
    from core import market_watch, token_performance

    _add_price(clock, {"move_pct": -20, "window": "6h"})
    poller = _poller(clock)
    now = market_watch._epoch(clock())
    for _ in range(market_watch.MAX_CALLS_PER_HOUR - 1):
        poller._budget.spent("market.geckoterminal", now)
    with FakePrices(price=0.39) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        server.start_price = 0.5
        _clear_price_cache()
        outcome = poller.sweep_once()  # the pools read takes the last call; the candles may not
    assert outcome["events"] == 0 and _items() == []
    assert not any("/ohlcv/" in request["path"] for request in server.requests)
    assert market_watch.list_watches()[0]["last_error"] == "budget"


def test_three_price_watches_share_one_read_per_minute_and_none_starves(watch_home, clock, enable_web, monkeypatch):
    from core import market_watch, token_performance

    subjects = [SOL, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"]
    for index, subject in enumerate(subjects):
        _add_price(clock, {"above": 5}, subject=subject, label=f"T{index}")
    poller = _poller(clock)
    with FakePrices(price=0.4) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        for _tick in range(10):  # 0s .. 180s, a sweep every 20 seconds
            _clear_price_cache()
            poller.sweep_once()
            clock.advance(seconds=20)
        pool_reads = [r for r in server.requests if r["path"].endswith("/pools")]
    assert len(pool_reads) == 4  # one per minute: at 0, 60, 120 and 180 seconds
    assert {r["path"].split("/tokens/")[1].split("/")[0] for r in pool_reads[:3]} == set(subjects)
    assert all(watch["last_checked_at"] for watch in market_watch.list_watches())


def test_a_new_threshold_on_an_existing_price_watch_starts_armed(watch_home, clock, enable_web, monkeypatch):
    from core import token_performance

    _add_price(clock, {"above": 0.5})
    poller = _poller(clock)
    with FakePrices(price=0.6) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        _clear_price_cache()
        assert poller.sweep_once()["events"] == 1  # fired: the 0.5 rule is now disarmed
        _add_price(clock, {"above": 0.7})  # the same token, a new rule
        clock.advance(minutes=2)
        server.price = 0.75
        _clear_price_cache()
        assert poller.sweep_once()["events"] == 1
    assert sorted(item["title"] for item in _items()) == ["FDOG is above 0.5: now 0.6", "FDOG is above 0.7: now 0.75"]


def test_repeat_rearms_only_past_the_one_percent_margin(watch_home, clock, enable_web, monkeypatch):
    from core import token_performance

    _add_price(clock, {"above": 0.5, "repeat": True})
    poller = _poller(clock)
    with FakePrices(price=0.53) as server:
        monkeypatch.setattr(token_performance, "API_ORIGIN", server.url)
        for price in (0.53, 0.499, 0.51):  # a wobble at the line is not a new crossing
            server.price = price
            _clear_price_cache()
            poller.sweep_once()
            clock.advance(minutes=2)
        assert len(_items()) == 1
        for price in (0.49, 0.52):  # back past the line by the margin, then across: one more
            server.price = price
            _clear_price_cache()
            poller.sweep_once()
            clock.advance(minutes=2)
        assert len(_items()) == 2


def _traced_connection(path, statements):
    import sqlite3

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(statements.append)
        return conn

    return connect


def _writes(statements):
    verbs = ("CREATE", "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "REPLACE")
    return [s for s in statements if s.strip().upper().startswith(verbs)]


def test_a_fresh_home_with_no_watches_writes_nothing(tmp_path, clock):
    from core import market_watch

    statements: list[str] = []
    path = tmp_path / "fresh.db"
    poller = market_watch.MarketWatchPoller(get_connection_fn=_traced_connection(path, statements), now_fn=clock)
    for _ in range(3):
        assert poller.sweep_once()["errors"] == 0
        clock.advance(seconds=20)
    assert _writes(statements) == []
    assert path.stat().st_size == 0  # sqlite never wrote a page: no table, no file content


def test_a_list_with_only_paused_watches_writes_nothing(tmp_path, clock):
    from core import market_watch

    statements: list[str] = []
    path = tmp_path / "paused.db"
    connect = _traced_connection(path, statements)
    market_watch._ensure_tables(connect)
    conn = connect()
    conn.execute("INSERT INTO market_watches (id, kind, chain, subject, label, state, created_at) "
                 "VALUES ('mw-p', 'wallet', 'ethereum', ?, 'Paused', 'paused', ?)", (EVM_ADDRESS, clock()))
    conn.commit()
    conn.close()
    statements.clear()
    poller = market_watch.MarketWatchPoller(get_connection_fn=connect, now_fn=clock)
    for _ in range(3):
        poller.sweep_once()
        clock.advance(seconds=20)
    assert _writes(statements) == []
