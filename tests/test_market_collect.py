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


WSOL = "So11111111111111111111111111111111111111112"
EVM_POOL = "0x" + "a" * 40


def _trade(ts: int, side: str, usd: float, token_amount: float, wallet: str, tx: str, *,
           token_is_base: bool = True, addresses: bool = True) -> dict:
    """One trade in GeckoTerminal v2's documented shape (GET /networks/{network}/pools/{pool}/trades).

    ``side`` is what happened to the asked token (SOL): "buy" means the trader got it. The source's
    own ``kind`` names the POOL'S BASE token, so it is the opposite when the token is the quote side.
    The trader gave ``from_token_amount`` of ``from_token_address`` and got ``to_token_amount`` of
    ``to_token_address``; prices are per token in USD."""
    other_amount = usd / 150.0  # wSOL at $150
    token_leg = {"address": SOL, "amount": token_amount, "price": usd / token_amount}
    other_leg = {"address": WSOL, "amount": other_amount, "price": 150.0}
    got, gave = (token_leg, other_leg) if side == "buy" else (other_leg, token_leg)
    kind = side if token_is_base else ("sell" if side == "buy" else "buy")
    attrs = {
        "block_number": 123, "tx_hash": tx, "tx_from_address": wallet,
        "from_token_amount": repr(gave["amount"]), "to_token_amount": repr(got["amount"]),
        "price_from_in_currency_token": "1", "price_to_in_currency_token": "1",
        "price_from_in_usd": repr(gave["price"]), "price_to_in_usd": repr(got["price"]),
        "block_timestamp": _iso(ts), "kind": kind, "volume_in_usd": repr(usd),
    }
    if addresses:
        attrs["from_token_address"] = gave["address"]
        attrs["to_token_address"] = got["address"]
    return {"id": f"solana_123_{tx}_0_{ts}", "type": "trade", "attributes": attrs}


def _pool(address: str, name: str, base: str, quote: str, network: str = "solana") -> dict:
    """A pool in the documented shape: relationships.base_token / quote_token, ids "<network>_<address>"."""
    return {"id": f"{network}_{address}", "type": "pool",
            "attributes": {"address": address, "name": name, "reserve_in_usd": "50000",
                           "pool_created_at": "2026-10-01T07:00:00Z"},
            "relationships": {"base_token": {"data": {"id": f"{network}_{base}", "type": "token"}},
                              "quote_token": {"data": {"id": f"{network}_{quote}", "type": "token"}},
                              "dex": {"data": {"id": "raydium", "type": "dex"}}}}


#: (seconds before NOW, side for SOL, USD, SOL amount, wallet, tx)
TRADES = [
    (600, "buy", 500.0, 250.0, WALLET_A, "t" * 60),
    (1200, "buy", 300.0, 150.0, "Buy2BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "u" * 60),
    (1800, "sell", 200.0, 90.0, "Se11333333333333333333333333333333333333333", "v" * 60),
    (3600, "buy", 100.0, 50.0, WALLET_A, "w" * 60),
    (7200, "sell", 50.0, 25.0, "Se11333333333333333333333333333333333333333", "x" * 60),
    (9000, "buy", 25.0, 12.5, "Buy4DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD", "y" * 60),
]


class FakeMarket:
    """Loopback GeckoTerminal v2: token pools (with base/quote relationships), candles, and the pool's
    trades listing, which has no paging -- one answer, the last 300 trades of the past 24h."""

    def __init__(self):
        self.requests: list[dict] = []
        self.token_is_base = True
        self.addresses = True
        self.pools = {SOL: [_pool(POOL, "FDOG / SOL", SOL, WSOL)]}
        self.evm_pools = {EVM_TOKEN: [_pool(EVM_POOL, "TKN / WETH", EVM_TOKEN, "0x" + "c" * 40, network="eth")]}
        self.ohlcv = [_candle(T0 + i * 3600, 1.0 + i * 0.05, 1000.0) for i in range(24)]
        self.trades: list[dict] | None = None  # None: built from TRADES with the flags above
        self.trade_status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.requests.append({"path": parts.path, "query": query, "auth": self.headers.get("Authorization")})
                if "/trades" in parts.path:
                    status = outer.trade_status
                    body = ({"errors": [{"status": str(status), "title": "Too Many Requests"}]} if status != 200
                            else {"data": outer.trade_rows()})
                elif parts.path.endswith("/pools") and "/tokens/" in parts.path:
                    token = parts.path.split("/tokens/")[1].split("/")[0]
                    pools = outer.evm_pools if "/networks/eth/" in parts.path else outer.pools
                    body, status = {"data": pools.get(token, [])}, 200
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

    def trade_rows(self) -> list[dict]:
        if self.trades is not None:
            return self.trades
        return [_trade(NOW - ago, side, usd, amount, wallet, tx, token_is_base=self.token_is_base,
                       addresses=self.addresses) for ago, side, usd, amount, wallet, tx in TRADES]

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
    assert "Turn on web access" in out.response_text and out.response_text.count("Nothing was collected") == 1
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
    # the public source DID go online here, so the chat lookup's "VOOL did not go online" is not borrowed
    assert "did not go online" not in out.response_text
    assert "trades: 6 row(s)" in out.response_text and "(last 300 trades (24h))" in out.response_text
    trades_requested = [r for r in market.requests if "/trades" in r["path"]]
    assert len(trades_requested) == 1 and "page" not in trades_requested[0]["query"]  # the listing has no paging
    assert all(r["auth"] is None for r in market.requests)  # keyless: nothing of the user's sent
    from core import market_data

    stored = sorted((r["ts"], r["kind"], r["amount"], r["usd"], r["price"], r["wallet"], r["tx"])
                    for r in market_data._load_rows(out.details["dataset"]) if r["source"] == "trades")
    expected = sorted((NOW - ago, side, amount, usd, pytest.approx(usd / amount), wallet, tx)
                      for ago, side, usd, amount, wallet, tx in TRADES)
    assert stored == expected  # tx_hash, tx_from_address, the token's own amount and USD price, read as documented


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


def _trade_kinds(dataset_id: str) -> list[tuple[int, str, object]]:
    from core import market_data

    return sorted((r["ts"], r["kind"], r["amount"]) for r in market_data._load_rows(dataset_id) if r["source"] == "trades")


_EXPECTED_SIDES = sorted((NOW - ago, side, amount) for ago, side, _usd, amount, _w, _tx in TRADES)


def test_a_token_on_the_quote_side_is_oriented_from_the_trade_legs(market, vault_home, enable_web):
    # the pool is wSOL / SOL-token: the source's kind names wSOL, so every kind is the opposite of the token's side
    market.pools[SOL] = [_pool(POOL, "SOL / FDOG", WSOL, SOL)]
    market.token_is_base = False
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    assert _trade_kinds(out.details["dataset"]) == _EXPECTED_SIDES


def test_without_leg_addresses_the_kind_is_swapped_for_a_quote_side_token(market, vault_home, enable_web):
    market.pools[SOL] = [_pool(POOL, "SOL / FDOG", WSOL, SOL)]
    market.token_is_base = False
    market.addresses = False
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    assert _trade_kinds(out.details["dataset"]) == _EXPECTED_SIDES  # oriented by relationships.quote_token


def test_a_trade_whose_side_cannot_be_told_counts_as_neither(market, vault_home, enable_web):
    pool = _pool(POOL, "FDOG / SOL", SOL, WSOL)
    del pool["relationships"]["base_token"], pool["relationships"]["quote_token"]
    market.pools[SOL] = [pool]
    market.addresses = False
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    assert {kind for _ts, kind, _amount in _trade_kinds(out.details["dataset"])} == {"trade"}
    assert "6 trade(s) did not say which side the token was on" in out.response_text
    analysis = _run("market.analyze", {"dataset_id": out.details["dataset"]})
    values = analysis.details["metrics"]["values"]
    assert "buy_count" not in values and "sell_count" not in values  # never a confident 0 buys
    assert "none of the 6 trade row(s) says whether the token was bought or sold" in analysis.response_text


def test_one_request_dedupes_by_tx_hash_and_labels_the_source_limit(market, vault_home, enable_web):
    rows = market.trade_rows()
    market.trades = [*rows, rows[0]] * 50  # 350 entries, the same 6 transactions over and over
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text
    trades = out.details["coverage"]["sources"]["trades"]
    assert trades["rows"] == 6 and trades["status"] == "partial"
    assert "returned its limit of 300 trades" in trades["stopped_reason"]
    assert len([r for r in market.requests if "/trades" in r["path"]]) == 1


def test_a_window_longer_than_a_day_says_the_trades_cover_only_24h(market, vault_home, enable_web):
    out = _run("market.collect", {"token": SOL, "window": "7d"})
    assert out.ok, out.response_text
    trades = out.details["coverage"]["sources"]["trades"]
    assert trades["status"] == "partial" and "not the whole 7d window" in trades["stopped_reason"]
    assert "only its last 300 trades (24h)" in out.response_text


def test_rate_limited_trades_keep_the_candles_and_label_the_dataset_partial(market, vault_home, enable_web):
    market.trade_status = 429
    out = _run("market.collect", {"token": SOL})
    assert out.ok, out.response_text  # the candles are kept
    sources = out.details["coverage"]["sources"]
    assert sources["candles"]["rows"] == 24
    assert sources["trades"]["rows"] == 0 and sources["trades"]["status"] == "failed"
    assert "limiting requests" in sources["trades"]["detail"]
    assert "Partial dataset" in out.response_text
    assert len([r for r in market.requests if "/trades" in r["path"]]) == 1  # it backed off, never looped


def test_etherscan_transfers_when_the_public_source_has_no_pool(market, vault_home, enable_web, monkeypatch):
    from core import chain_providers as cp

    market.evm_pools = {}
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


# --------------------------------------------------------------------------- transfer paging


def _etherscan_page(page_no: int, size: int = 1000) -> list[dict]:
    """``size`` ERC-20 transfers in Etherscan's documented tokentx shape, all inside the 24h window."""
    first = NOW - 60 - (page_no - 1) * size
    return [{"blockNumber": "21000000", "timeStamp": str(first - i), "hash": f"0x{page_no:02x}{i:062x}",
             "from": "0x" + "1" * 40, "to": "0x" + f"{i % 7 + 2}" * 40, "value": "1000000", "tokenDecimal": "6",
             "contractAddress": EVM_TOKEN} for i in range(size)]


def _etherscan_collect(monkeypatch, answer):
    from core import chain_providers as cp

    _store("chain.data.etherscan", ETHERSCAN_KEY)
    with FakeProviderServer(answer) as server:
        monkeypatch.setattr(cp, "ETHERSCAN_API_URL", f"{server.url.rstrip('/')}/etherscan/v2/api")
        out = _run("market.collect", {"token": EVM_TOKEN, "chain": "ethereum"})
    return out, server


def _page_of(record) -> int:
    return int(parse_qs(record["query"])["page"][0])


def test_etherscan_paging_says_it_stopped_at_the_page_budget(market, vault_home, monkeypatch):
    out, server = _etherscan_collect(monkeypatch, lambda record: (
        200, {"status": "1", "message": "OK", "result": _etherscan_page(_page_of(record))}))
    assert out.ok, out.response_text
    transfers = out.details["coverage"]["sources"]["transfers"]
    assert len(server.requests) == 5 and transfers["rows"] == 10_000  # 5 pages x 1,000 transfers x 2 sides
    assert transfers["stopped_reason"].startswith("stopped at page 5: page budget")
    assert "stopped at page 5: page budget" in out.response_text and "Partial dataset" in out.response_text


def test_a_rate_limit_on_page_3_keeps_pages_1_and_2_labelled_partial(market, vault_home, monkeypatch):
    def answer(record):
        if _page_of(record) == 3:
            return 429, {"status": "0", "message": "NOTOK", "result": "Max calls per sec rate limit reached"}
        return 200, {"status": "1", "message": "OK", "result": _etherscan_page(_page_of(record))}

    out, server = _etherscan_collect(monkeypatch, answer)
    assert out.ok, out.response_text
    transfers = out.details["coverage"]["sources"]["transfers"]
    assert len(server.requests) == 3  # it stopped at the 429, never retried in a loop
    assert transfers["status"] == "partial" and transfers["rows"] == 4_000
    assert transfers["stopped_reason"].startswith("stopped at page 3: Etherscan is limiting requests")
    assert "Partial dataset" in out.response_text


def test_a_bad_transfer_row_keeps_the_candles_and_trades(market, vault_home, enable_web, monkeypatch):
    bad = _etherscan_page(1, size=2)
    bad[1]["timeStamp"] = "yesterday"  # not the documented unix seconds
    out, _server = _etherscan_collect(monkeypatch, lambda record: (
        200, {"status": "1", "message": "OK", "result": bad}))
    assert out.ok, out.response_text  # the public sources' rows are kept and stored
    sources = out.details["coverage"]["sources"]
    assert sources["candles"]["rows"] == 24 and sources["trades"]["rows"] == 6
    assert sources["transfers"]["status"] == "failed"
    assert "shape VOOL does not recognise" in sources["transfers"]["detail"]


def test_alchemy_paging_says_it_stopped_at_the_page_budget(market, vault_home, monkeypatch):
    from core import chain_providers as cp

    _store("chain.data.alchemy", "alch_fixture_key_0123456789abcd")
    stamp = _iso(NOW - 600).replace("Z", ".000Z")
    page = {"transfers": [{"blockNum": "0x1", "hash": f"0x{i:064x}", "from": "0x" + "1" * 40, "to": "0x" + "2" * 40,
                           "value": 1.5, "asset": "TKN", "category": "erc20",
                           "metadata": {"blockTimestamp": stamp}} for i in range(1000)], "pageKey": "next-page"}
    with FakeProviderServer(lambda record: (200, {"jsonrpc": "2.0", "id": 1, "result": page})) as server:
        monkeypatch.setattr(cp, "alchemy_url", lambda chain: f"{server.url.rstrip('/')}/alchemy/v2")
        out = _run("market.collect", {"token": EVM_TOKEN, "chain": "ethereum"})
    assert out.ok, out.response_text
    transfers = out.details["coverage"]["sources"]["transfers"]
    assert len(server.requests) == 5 and transfers["provider"] == "alchemy"
    assert transfers["stopped_reason"].startswith("stopped at page 5: page budget")


def test_the_helius_setup_notes_say_the_key_also_collects_trade_data():
    from core import chain_providers as cp

    notes = " ".join(cp.config_for("helius").setup_notes)
    assert "collect trade data" in notes and "transfer history" in notes


# --------------------------------------------------------------------------- private chat and projects


def _no_datasets_stored() -> bool:
    return _datasets() == []


def test_a_private_chat_collects_nothing_and_goes_nowhere(market, helius, vault_home, enable_web, tmp_path):
    from core.private_chat import set_private
    from core.project_store import create_project

    _store("chain.data.helius", HELIUS_KEY)
    (tmp_path / "proj").mkdir()
    ok, project = create_project("Private lab", str(tmp_path / "proj"))
    assert ok
    set_private("chat-private", True)
    out = _run("market.collect", {"token": SOL}, source_context={"chat_id": "chat-private", "project_id": project["id"]})
    assert not out.ok and out.status == "private_chat"
    assert "private chat" in out.response_text and "Nothing was collected" in out.response_text
    assert market.requests == [] and helius.requests == []  # nothing went online
    assert _no_datasets_stored() and not (tmp_path / "proj" / "market-data").exists()
    # a turn stamped private by the chat door is refused the same way
    stamped = _run("market.collect", {"token": SOL}, source_context={"chat_id": "chat-other", "private_chat": True})
    assert stamped.status == "private_chat" and _no_datasets_stored()


def test_analyzing_in_a_private_chat_saves_no_result(market, vault_home, enable_web):
    from core import market_data
    from core.private_chat import set_private

    dataset_id = _run("market.collect", {"token": SOL}, source_context={"chat_id": "chat-1"}).details["dataset"]
    set_private("chat-private", True)
    out = _run("market.analyze", {"dataset_id": dataset_id}, source_context={"chat_id": "chat-private"})
    assert out.ok and "private chat, so the result was not saved" in out.response_text
    conn = market_data._conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM market_analyses").fetchone()[0] == 0
    finally:
        conn.close()


def test_datasets_are_seen_only_from_the_project_that_collected_them(market, vault_home, enable_web, tmp_path):
    from core.project_store import create_project

    projects = []
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        ok, project = create_project(name, str(tmp_path / name))
        assert ok
        projects.append(project["id"])
    alpha, beta = ({"chat_id": "chat-a", "project_id": projects[0]}, {"chat_id": "chat-b", "project_id": projects[1]})
    plain = {"chat_id": "chat-plain"}
    dataset_id = _run("market.collect", {"token": SOL}, source_context=alpha).details["dataset"]
    assert dataset_id in _run("market.datasets", {}, source_context=alpha).response_text
    for other in (beta, plain):
        assert dataset_id not in _run("market.datasets", {}, source_context=other).response_text
        assert _run("market.analyze", {"dataset_id": dataset_id}, source_context=other).status == "unknown_dataset"
        check = _run("market.thesis_check", {"dataset_id": dataset_id, "claims": [
            {"metric": "rows_trades", "comparator": ">", "value": 0}]}, source_context=other)
        assert check.status == "unknown_dataset"
        assert _run("market.dataset_delete", {"dataset_id": dataset_id}, source_context=other).status == "unknown_dataset"
    assert [d["id"] for d in _datasets()] == [dataset_id]  # nobody else could delete it
    assert _run("market.analyze", {"dataset_id": dataset_id}, source_context=alpha).ok
    assert _run("market.dataset_delete", {"dataset_id": dataset_id}, source_context=alpha).ok
    assert _no_datasets_stored()


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


# Every market rule needs a market anchor in the same sentence: an address, a $TICKER, "this/that
# token|coin|mint", or a dataset id (md_…) / "market dataset". The ordinary sentences below were all
# seated before that anchor existed; trade words alone are everyday English.
@pytest.mark.parametrize(("text", "seated"), [
    ("collect the last week of trades for $BONK", True),
    ("collect the last 7 days of trades for " + SOL, True),
    ("is $BONK being bought or sold right now", True),
    ("can you pull the trading data for that token", True),
    ("grab the market data for this mint", True),
    ("list the market datasets you have", True),
    ("what market datasets do you have collected", True),
    ("delete dataset md_abc123", True),
    ("what patterns do you see in md_abc123", True),
    ("analyze the collected trades for $BONK", True),
    ("check my thesis on $BONK: smart wallets bought before every pump", True),
    ("test the idea that the buy share is above 60", False),  # an idea alone is not a thesis check demand
    ("collect my thoughts about the meeting", False),
    ("tell me the history of rome", False),
    ("delete my browser history", False),
    ("how do I sell my house", False),
    # ordinary sentences with trade words and no market anchor
    ("Grab the candles from the store", False),
    ("Did the house get bought or sold", False),
    ("Is there much sell pressure this quarter", False),
    ("Fetch the market data for apples", False),
    ("fetch the trading history from my broker statement", False),
    ("My thesis is due tomorrow", False),
    ("My idea is that we go to Rome", False),
    ("Check my thesis draft for typos", False),
    ("What patterns do you see in this poem?", False),
    ("Delete the dataset I uploaded for the ML class", False),
    ("list the CSV datasets in the folder", False),
    ("Grab the candles from the store. Then check $BONK's price", False),  # the anchor is in another sentence
])
def test_plain_sentences_seat_the_market_tools(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    intents = set(resolve_demand_signals(text).explicit_intents)
    assert any(intent.startswith("market.") for intent in intents) is seated


@pytest.mark.parametrize(("text", "intent"), [
    ("collect the last 7 days of trades for " + SOL, "market.collect"),
    ("check my thesis on $BONK: smart wallets bought before every pump", "market.thesis_check"),
    ("delete dataset md_abc123", "market.dataset_delete"),
])
def test_a_seated_sentence_puts_the_tool_in_the_models_offer(text, intent):
    from core.capability_graph import model_visible_specs

    offered = [spec["intent"] for spec in model_visible_specs(user_text=text)]
    assert intent in offered, offered


def test_ordinary_trade_words_do_not_put_a_market_tool_in_the_offer():
    from core.capability_graph import model_visible_specs

    offered = [spec["intent"] for spec in model_visible_specs(user_text="Grab the candles from the store")]
    assert not [intent for intent in offered if intent.startswith("market.")], offered
