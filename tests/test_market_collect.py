"""market.collect: a token's candles, pool trades and transfer history stored as a local dataset.

The public price source is a loopback fixture answering the documented GeckoTerminal shapes; the
chain providers are loopback fakes answering each provider's documented request shape. No real host
is contacted. Everything goes through the model's door (``execute_runtime_tool``).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import FakeProviderServer, isolated_home, vault_home

SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
EVM_TOKEN = "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"
POOL = "PooL1111111111111111111111111111111111111111"
ROUTER = "rOut111111111111111111111111111111111111111"
HELIUS_KEY = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
ETHERSCAN_KEY = "ETHERSCANFIXTUREKEY0123456789ABCD"
WALLET_A = "Wa11etAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
T0 = 1791878400  # 2026-10-13 08:00 UTC
NOW = T0 + 24 * 3600


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candle(ts: float, close: float, volume: float) -> list[float]:
    return [ts, close, close * 1.05, close * 0.95, close, volume]


def _trade(ts: int, kind: str, usd: float, base_amount: float, wallet: str, tx: str) -> dict:
    """One trade as the source documents it: from_amount is the token sold, to_amount the bought."""
    return {"id": tx, "type": "trade", "attributes": {
        "tx_id": tx, "block_number": "123", "block_timestamp": _iso(ts), "kind": kind,
        "from_amount": repr(base_amount if kind == "sell" else usd / 2),
        "to_amount": repr(base_amount if kind == "buy" else usd / 2),
        "volume_in_usd": usd, "tx_from_address": wallet, "tx_to_address": ROUTER,
    }}


class FakeMarket:
    """Loopback GeckoTerminal: pools (with the base-token relationship), candles, trade pages."""

    def __init__(self):
        self.requests: list[dict] = []
        self.pools = {SOL: [{"attributes": {"address": POOL, "name": "FDOG / SOL", "reserve_in_usd": "50000",
                                            "pool_created_at": "2026-10-01T07:00:00Z"},
                             "relationships": {"tokens": {"data": [{"id": SOL, "type": "token"},
                                                                  {"id": "So11111111111111111111111111111111111111112",
                                                                   "type": "token"}]}}}]}
        self.ohlcv = [_candle(T0 + i * 3600, 1.0 + i * 0.05, 1000.0) for i in range(24)]
        self.trade_pages: list[list[dict]] = [[
            _trade(NOW - 600, "buy", 500.0, 250.0, WALLET_A, "t" * 60),
            _trade(NOW - 1200, "buy", 300.0, 150.0, "Buy2BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "u" * 60),
            _trade(NOW - 1800, "sell", 200.0, 90.0, "Se11333333333333333333333333333333333333333", "v" * 60),
            _trade(NOW - 3600, "buy", 100.0, 50.0, WALLET_A, "w" * 60),
            _trade(NOW - 7200, "sell", 50.0, 25.0, "Se11333333333333333333333333333333333333333", "x" * 60),
            _trade(NOW - 9000, "buy", 25.0, 12.5, "Buy4DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD", "y" * 60),
        ]]
        self.trade_status: dict[int, int] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.requests.append({"path": parts.path, "query": query, "auth": self.headers.get("Authorization")})
                page_no = int(query.get("page") or 1)
                if "/trades" in parts.path:
                    status = outer.trade_status.get(page_no, 200)
                    body = ({"errors": [{"title": "rate limited"}]} if status != 200
                            else {"data": outer.trade_pages[page_no - 1] if page_no <= len(outer.trade_pages) else []})
                elif parts.path.endswith("/pools") and "/tokens/" in parts.path:
                    token = parts.path.split("/tokens/")[1].split("/")[0]
                    body, status = {"data": outer.pools.get(token, [])}, 200
                elif "/ohlcv/" in parts.path:
                    body, status = {"data": {"attributes": {"ohlcv_list": list(reversed(outer.ohlcv))}}}, 200
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


class FakeChainRPC:
    """Loopback JSON-RPC endpoint (Helius-shaped): signatures and transactions for a mint."""

    def __init__(self):
        self.requests: list[dict] = []
        self.signatures = [{"signature": "5" * 88, "slot": 90, "blockTime": NOW - 3600},
                           {"signature": "6" * 88, "slot": 80, "blockTime": NOW - 7200}]
        self.transactions = {
            "5" * 88: {"slot": 90, "blockTime": NOW - 3600, "meta": {"err": None, "fee": 5000,
                      "preTokenBalances": [{"accountIndex": 1, "mint": SOL, "owner": WALLET_A,
                                            "uiTokenAmount": {"uiAmount": 0.0, "decimals": 6}}],
                      "postTokenBalances": [{"accountIndex": 1, "mint": SOL, "owner": WALLET_A,
                                             "uiTokenAmount": {"uiAmount": 100.0, "decimals": 6}}]}},
            "6" * 88: {"slot": 80, "blockTime": NOW - 7200, "meta": {"err": None, "fee": 5000,
                      "preTokenBalances": [{"accountIndex": 2, "mint": SOL, "owner": WALLET_A,
                                            "uiTokenAmount": {"uiAmount": 100.0, "decimals": 6}}],
                      "postTokenBalances": [{"accountIndex": 2, "mint": SOL, "owner": WALLET_A,
                                             "uiTokenAmount": {"uiAmount": 40.0, "decimals": 6}}]}},
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({"path": self.path.split("?")[0], "query": self.path.split("?")[-1],
                                       "method": body.get("method"), "params": body.get("params")})
                if body.get("method") == "getSignaturesForAddress":
                    before = ((body.get("params") or [{}, {}])[1] or {}).get("before")
                    result = [row for row in outer.signatures if not before or row["signature"] < before]
                elif body.get("method") == "getTransaction":
                    result = outer.transactions.get((body.get("params") or [None])[0])
                else:
                    result = None
                raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
                self.send_response(200)
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


@pytest.fixture
def market(monkeypatch):
    from core import market_data
    from core import token_performance as tp

    with FakeMarket() as server:
        monkeypatch.setattr(tp, "API_ORIGIN", server.url)
        monkeypatch.setattr(tp, "_cache", {})
        monkeypatch.setattr(market_data, "_clock", lambda: float(NOW))
        yield server


@pytest.fixture
def helius(monkeypatch):
    from core import chain_providers as cp

    with FakeChainRPC() as server:
        monkeypatch.setattr(cp, "HELIUS_RPC_URL", f"{server.url}/helius/")
        yield server


def _run(intent: str, args: dict, source_context: dict | None = None):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, args, source_context=source_context or {})


def _store(slot: str, key: str) -> None:
    from core.credential_store import store_credential

    store_credential(slot, key, label="fixture")


# --------------------------------------------------------------------------- registration


def test_the_five_market_tools_are_registered_with_honest_contracts():
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for intent in ("market.collect", "market.datasets", "market.dataset_delete", "market.analyze",
                   "market.thesis_check"):
        assert contracts[intent].supported, intent
        assert not contracts[intent].renders_final_answer, intent
    assert contracts["market.collect"].side_effect_class == "creative_state"
    assert "use_network_access" in contracts["market.collect"].permission_actions
    assert contracts["market.analyze"].read_only and contracts["market.thesis_check"].read_only
    assert "metric" in contracts["market.thesis_check"].description  # the claimable metrics are listed


# --------------------------------------------------------------------------- refusals first


def test_no_key_and_web_off_refuses_and_stores_nothing(market, helius, vault_home):
    out = _run("market.collect", {"token": SOL})
    assert not out.ok and out.status == "nothing_available"
    assert "Turn on web access" in out.response_text and "Nothing was collected" in out.response_text
    assert market.requests == [] and helius.requests == []  # nothing went online
    from core import market_data

    conn = market_data._conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM market_datasets").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize(("arguments", "code", "phrase"), [
    ({"token": "not-an-address"}, "bad_subject", "contract address"),
    ({"token": SOL, "window": "45d"}, "invalid_arguments", "1h to 30d"),
    ({"token": SOL, "window": "banana"}, "invalid_arguments", "12h or 7d"),
    ({"token": SOL, "chain": "dogechain"}, "unknown_chain", "not one of them"),
    ({"token": EVM_TOKEN}, "chain_needed", "Which chain"),
    ({}, "bad_subject", "contract address"),
])
def test_bad_requests_are_refused_before_anything_happens(market, vault_home, arguments, code, phrase):
    out = _run("market.collect", arguments)
    assert not out.ok and out.status == code, out.response_text
    assert phrase in out.response_text and market.requests == []


# --------------------------------------------------------------------------- the sources


def test_web_on_and_no_key_keeps_candles_and_trades_and_names_the_missing_key(market, vault_home, enable_web):
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    sources = out.details["coverage"]["sources"]
    assert sources["candles"]["rows"] == 24 and sources["candles"]["status"] == "ok"
    assert sources["trades"]["rows"] == 6 and sources["trades"]["status"] == "ok"
    assert sources["transfers"]["status"] == "skipped" and "Helius or Alchemy" in sources["transfers"]["detail"]
    assert out.details["coverage"]["partial"] and "Partial dataset" in out.response_text
    assert "not read: To look up token transfer history" in out.response_text
    trades_requested = [r for r in market.requests if "/trades" in r["path"]]
    assert len(trades_requested) == 2 and trades_requested[0]["query"]["page"] == "1"
    assert all(r["query"].get("auth") is None for r in market.requests)  # keyless: nothing of the user's sent


def test_key_only_and_web_off_keeps_transfers_only(market, helius, vault_home):
    _store("chain.data.helius", HELIUS_KEY)
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    sources = out.details["coverage"]["sources"]
    assert sources["candles"]["status"] == "skipped" and "web access is off" in sources["candles"]["detail"]
    assert sources["trades"]["status"] == "skipped" and "web access is off" in sources["trades"]["detail"]
    assert sources["transfers"]["rows"] == 2 and sources["transfers"]["provider"] == "helius"
    assert "via Helius" in out.response_text
    from core import market_data

    [dataset_id] = [d["id"] for d in _datasets()]
    stored = market_data._load_rows(dataset_id)
    kinds = sorted((r["ts"], r["kind"], r["amount"]) for r in stored)
    assert kinds == [(NOW - 7200, "out", 60.0), (NOW - 3600, "in", 100.0)]
    assert all(r["usd"] is None and r["price"] is None for r in stored)  # no candles: no guessed price
    assert any("api-key" in r["query"] for r in helius.requests)  # the key rode the documented placement


def _datasets() -> list[dict]:
    from core import market_data

    market_data._init_tables()
    conn = market_data._conn()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM market_datasets").fetchall()]
    finally:
        conn.close()


def test_transfers_get_a_price_from_the_collected_candles(market, helius, vault_home, enable_web):
    _store("chain.data.helius", HELIUS_KEY)
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    from core import market_data

    [dataset_id] = [d["id"] for d in _datasets()]
    stored = {(r["source"], r["kind"]): r for r in market_data._load_rows(dataset_id)}
    transfer_in = stored[("transfers", "in")]
    assert transfer_in["price"] == pytest.approx(2.15)  # the last hourly close at or before it
    assert transfer_in["usd"] == pytest.approx(215.0)


def test_trades_direction_follows_the_pools_base_token(market, vault_home, enable_web):
    market.pools[SOL][0]["relationships"]["tokens"]["data"].reverse()  # the pool's base is now the quote side
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    from core import market_data

    [dataset_id] = [d["id"] for d in _datasets()]
    kinds = sorted(r["kind"] for r in market_data._load_rows(dataset_id) if r["source"] == "trades")
    assert kinds == ["buy", "buy", "sell", "sell", "sell", "sell"]  # the source's 4 buys flipped to sells


def test_rate_limit_mid_paging_stops_and_labels_the_dataset_partial(market, vault_home, enable_web):
    market.trade_status[2] = 429
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text  # page 1's trades and the candles are kept
    sources = out.details["coverage"]["sources"]
    assert sources["trades"]["rows"] == 6
    assert sources["trades"]["stopped_reason"] == "stopped at page 2 of ?: the public price source is limiting " \
                                                  "requests right now; try again in a minute"
    assert sources["trades"]["status"] == "partial"
    assert "rate limited" not in out.response_text and "limiting requests" in out.response_text
    assert "Partial dataset" in out.response_text
    assert len([r for r in market.requests if "/trades" in r["path"]]) == 2  # it backed off, never looped


def test_etherscan_transfers_when_the_public_source_has_no_pool(market, vault_home, enable_web, monkeypatch):
    from core import chain_providers as cp

    _store("chain.data.etherscan", ETHERSCAN_KEY)
    transfer = {"blockNumber": "21000000", "timeStamp": str(NOW - 600), "hash": "0x" + "9" * 64,
                "from": "0x" + "1" * 40, "to": "0x" + "2" * 40, "value": str(5_000_000), "tokenDecimal": "6"}
    pages = [{"status": "1", "message": "OK", "result": [transfer]},
             {"status": "0", "message": "No transactions found", "result": []}]
    state = {"page": 0}

    def answer(_record):
        body = pages[min(state["page"], len(pages) - 1)]
        state["page"] += 1
        return 200, body

    with FakeProviderServer(answer) as server:
        monkeypatch.setattr(cp, "ETHERSCAN_API_URL", f"{server.url.rstrip('/')}/etherscan/v2/api")
        out = _run("market.collect", {"token": EVM_TOKEN, "chain": "ethereum"})
    assert out.ok, out.response_text
    sources = out.details["coverage"]["sources"]
    assert sources["candles"]["status"] == "failed" and "no trading pool" in sources["candles"]["detail"]
    assert sources["transfers"]["rows"] == 2 and sources["transfers"]["provider"] == "etherscan"
    query = parse_qs(server.requests[0]["query"])
    assert query["action"] == ["tokentx"] and query["contractaddress"] == [EVM_TOKEN]
    assert query["apikey"] == [ETHERSCAN_KEY] and query["sort"] == ["desc"]
    assert ETHERSCAN_KEY not in out.response_text
    from core import market_data

    [dataset_id] = [d["id"] for d in _datasets()]
    sides = sorted((r["kind"], round(r["amount"], 6)) for r in market_data._load_rows(dataset_id))
    assert sides == [("in", 5.0), ("out", 5.0)]


# --------------------------------------------------------------------------- datasets tools


def test_datasets_listing_and_delete(market, vault_home, enable_web):
    collected = _run("market.collect", {"token": SOL})
    dataset_id = collected.details["dataset"]
    listing = _run("market.datasets", {})
    assert listing.ok and dataset_id in listing.response_text
    assert "24h" in listing.response_text and "partial" in listing.response_text
    assert "candles 24" in listing.response_text and "trades 6" in listing.response_text
    gone = _run("market.dataset_delete", {"dataset_id": dataset_id})
    assert gone.ok and f"Deleted dataset {dataset_id}" in gone.response_text
    listing2 = _run("market.datasets", {})
    assert dataset_id not in listing2.response_text
    unknown = _run("market.dataset_delete", {"dataset_id": "md_nope"})
    assert not unknown.ok and unknown.status == "unknown_dataset"
    missing = _run("market.dataset_delete", {})
    assert not missing.ok and missing.status == "missing_dataset"


def test_twenty_datasets_are_kept_and_the_oldest_unpinned_is_dropped(market, vault_home, enable_web, monkeypatch):
    import itertools

    from core import market_data

    counter = itertools.count()
    monkeypatch.setattr(market_data, "_utcnow", lambda: (
        datetime(2026, 10, 14, 8, tzinfo=timezone.utc) + timedelta(seconds=next(counter))).isoformat())
    first = None
    for _ in range(21):
        out = _run("market.collect", {"token": SOL})
        assert out.ok, out.response_text
        first = first or out.details["dataset"]
    assert len(_datasets()) == 20
    assert first not in {d["id"] for d in _datasets()}
    assert "Kept 20 datasets: removed the oldest" in out.response_text and first in out.response_text
    # a dataset from the chat that asks is pinned: dropping falls to the next oldest instead
    mine = _run("market.collect", {"token": SOL}, source_context={"chat_id": "chat-1"}).details["dataset"]
    again = _run("market.collect", {"token": SOL}, source_context={"chat_id": "chat-1"})
    assert again.ok and mine in {d["id"] for d in _datasets()}


# --------------------------------------------------------------------------- project CSV copies


def test_a_project_chat_gets_csv_copies_and_a_plain_chat_writes_no_files(market, vault_home, enable_web, tmp_path):
    from core.project_store import create_project

    (tmp_path / "proj").mkdir()
    ok, project = create_project("Market lab", str(tmp_path / "proj"))
    assert ok
    out = _run("market.collect", {"token": SOL},
               source_context={"chat_id": "chat-1", "project_id": project["id"]})
    assert out.ok, out.response_text
    folder = tmp_path / "proj" / "market-data" / f"{SOL}-{datetime.fromtimestamp(NOW, tz=timezone.utc).strftime('%Y-%m-%d')}"
    assert folder.is_dir(), list((tmp_path / "proj").rglob("*"))
    assert sorted(p.name for p in folder.iterdir()) == ["candles.csv", "trades.csv"]
    lines = (folder / "trades.csv").read_text().splitlines()
    assert lines[0] == "time_utc,kind,wallet,amount,usd,price,tx" and len(lines) == 7
    assert str(folder) in out.response_text

    plain = _run("market.collect", {"token": SOL}, source_context={"chat_id": "chat-2"})
    assert plain.ok and "CSV" not in plain.response_text
    stray = [p for p in vault_home.rglob("*") if "market-data" in str(p)]
    assert stray == []  # a non-project chat writes nothing outside VOOL's database


# --------------------------------------------------------------------------- the chat offers the tool


@pytest.mark.parametrize(("text", "seated"), [
    ("collect the last week of trades for BONK", True),
    ("collect the last 7 days of trades for " + SOL, True),
    ("is BONK being bought or sold right now", True),
    ("can you pull the trading data for that token", True),
    ("grab the market data for this mint", True),
    ("list the datasets you have", True),
    ("what datasets do you have collected", True),
    ("delete dataset md_abc123", True),
    ("what patterns do you see", True),
    ("analyze the collected trades", True),
    ("check my thesis: smart wallets bought before every pump", True),
    ("test the idea that the buy share is above 60", False),  # an idea alone is not a thesis check demand
    ("collect my thoughts about the meeting", False),
    ("tell me the history of rome", False),
    ("delete my browser history", False),
    ("how do I sell my house", False),
])
def test_plain_sentences_seat_the_market_tools(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    intents = set(resolve_demand_signals(text).explicit_intents)
    assert any(intent.startswith("market.") for intent in intents) is seated
