"""Buy and sell activity: trade counts, traded USD and price change per window, with named gaps.

The public trading source (GeckoTerminal's documented v2 pool shapes) is a loopback fixture; no
real host is contacted and no key is used. Everything goes through the model's door
(``execute_runtime_tool``), and the plain words that seat the tool are checked too.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_api_reads import EVM, SOL

POOL_A = "PooL1111111111111111111111111111111111111111"
POOL_B = "PooL2222222222222222222222222222222222222222"


WSOL = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _pool(address: str, name: str, dex: str, reserve: str, split: bool, volume: dict | None = None, *,
          base: str = SOL, quote: str = WSOL, network: str = "solana") -> dict:
    attributes: dict = {"address": address, "name": name, "reserve_in_usd": reserve,
                        "pool_created_at": "2026-10-13T07:00:00Z",
                        "volume_usd": volume or {"m5": "4000.5", "h1": "40000", "h6": "250000", "h24": "900000.42"},
                        "price_change_percentage": {"m5": "0.5", "h1": "1.25", "h6": "-2.0", "h24": "3.2"}}
    if split:
        attributes["transactions"] = {"m5": {"buys": 12, "sells": 3, "buyers": 10, "sellers": 3},
                                      "h1": {"buys": 120, "sells": 80, "buyers": 90, "sellers": 70},
                                      "h6": {"buys": 600, "sells": 400, "buyers": 400, "sellers": 300},
                                      "h24": {"buys": 1000, "sells": 900, "buyers": 700, "sellers": 650}}
    # GeckoTerminal's documented pool shape: the counts are for the BASE token; base and quote are named by id
    return {"id": f"{network}_{address}", "type": "pool",
            "attributes": attributes,
            "relationships": {"network": {"data": {"id": network, "type": "network"}},
                              "dex": {"data": {"id": dex, "type": "dex"}},
                              "base_token": {"data": {"id": f"{network}_{base}", "type": "token"}},
                              "quote_token": {"data": {"id": f"{network}_{quote}", "type": "token"}}}}


ORCA_VOLUME = {"m5": "500", "h1": "5000", "h6": "30000", "h24": "100000"}


class FakeFlow:
    """Two pools for SOL: raydium reports a buy/sell split, orca does not."""

    def __init__(self):
        self.requests: list[dict] = []
        self.pools = {SOL: [_pool(POOL_A, "FDOG / SOL", "raydium", "50000", True),
                            _pool(POOL_B, "FDOG / USDC", "orca", "8000", False, ORCA_VOLUME, quote=USDC_MINT)]}
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
                    body, status = {"errors": [{"title": "rate limited"}]}, outer.status
                elif parts.path.endswith("/pools") and "/tokens/" in parts.path:
                    token = parts.path.split("/tokens/")[1].split("/")[0]
                    body, status = {"data": outer.pools.get(token, [])}, 200
                elif parts.path.endswith("/search/pools"):
                    query_text = query.get("query", "")
                    body, status = {"data": outer.pools.get(query_text, [])}, 200
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
def flows(monkeypatch):
    from core import token_flow

    with FakeFlow() as server:
        monkeypatch.setattr(token_flow, "API_ORIGIN", server.url)
        monkeypatch.setattr(token_flow, "_cache", {})
        yield server


def _run(args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("token.flow", args, source_context={})


def test_web_off_means_nothing_goes_online(flows):
    out = _run({"token": SOL})
    assert not out.ok and out.status == "web_off" and "Turn on web access" in out.response_text
    assert flows.requests == []


def test_no_trading_pool_is_named_not_guessed(flows, enable_web):
    out = _run({"token": "So11111111111111111111111111111111111111112"})
    assert not out.ok and out.status == "not_found" and "no trading pool" in out.response_text
    assert flows.requests, "the source was asked once"


def test_counts_volumes_and_named_pools(flows, enable_web):
    out = _run({"token": SOL, "chain": "solana"})
    assert out.ok, out.response_text
    totals = out.details["totals"]
    # the 24h totals are the SUM over the one pool reporting a split, never a 50/50 guess for the other
    assert totals["24h"]["buys"] == 1000 and totals["24h"]["sells"] == 900
    assert totals["24h"]["pools_with_split"] == 1 and totals["24h"]["pools_total"] == 2
    assert totals["24h"]["volume_usd"] == pytest.approx(1000000.42)  # both pools' volume counts
    assert totals["1h"]["buy_share_of_trades"] == pytest.approx(0.6)
    text = out.response_text
    assert "1 of 2 pool(s) reporting a split" in text
    assert "60.0% of trades were buys" in text  # a share of trades, said as that, with its window
    assert "does not split traded USD between buys and sells" in text
    # unique traders are shown where the source reports them
    assert "1,000 buys / 900 sells by 700 buyers and 650 sellers" in text
    # each pool is named with its DEX and pair, and the pool without a split says so
    assert "raydium FDOG / SOL" in text and "orca FDOG / USDC" in text
    assert "buy/sell split not reported" in text
    assert "$1,000,000" in text and "+3.2%" in text and "-2.0%" in text
    [pool_a] = [p for p in out.details["pools"] if p["dex"] == "raydium"]
    assert pool_a["name"] == "FDOG / SOL" and pool_a["liquidity_usd"] == pytest.approx(50000.0)
    assert all(r["auth"] is None for r in flows.requests)  # keyless: nothing of the user's is sent
    assert flows.requests[0]["path"].endswith(f"/networks/solana/tokens/{SOL}/pools")


def test_an_evm_token_without_a_chain_is_found_by_search(flows, enable_web):
    flows.pools[EVM] = [_pool(POOL_A, "TKN / WETH", "uniswap_v3", "50000", True, base=EVM, quote="0x" + "c" * 40, network="eth")]
    out = _run({"token": EVM})
    assert out.ok, out.response_text
    assert any(r["path"].endswith("/search/pools") for r in flows.requests)
    assert out.details["chain"] == "ethereum" and out.details["totals"]["24h"]["buys"] == 1000


def test_pools_on_different_chains_are_never_added_together(flows, enable_web):
    weth = "0x" + "c" * 40
    flows.pools[EVM] = [_pool(POOL_A, "TKN / WETH", "uniswap_v3", "50000", True, base=EVM, quote=weth, network="eth"),
                        _pool(POOL_B, "TKN / WETH", "aerodrome", "9000", True, base=EVM, quote=weth, network="base")]
    out = _run({"token": EVM})
    assert out.ok, out.response_text
    # only the network of the most liquid pool is counted, and the answer is labelled with it
    assert out.details["chain"] == "ethereum" and [p["dex"] for p in out.details["pools"]] == ["uniswap_v3"]
    assert out.details["totals"]["24h"]["buys"] == 1000 and out.details["totals"]["24h"]["pools_total"] == 1


def test_a_pool_where_the_token_is_the_second_of_the_pair_has_its_buys_and_sells_swapped(flows, enable_web):
    # "SOL / FDOG": the source counts trades of SOL (the base); buying SOL there is SELLING FDOG
    flows.pools[SOL] = [_pool(POOL_A, "SOL / FDOG", "raydium", "50000", True, base=WSOL, quote=SOL)]
    out = _run({"token": SOL, "chain": "solana"})
    assert out.ok, out.response_text
    row = out.details["totals"]["24h"]
    assert row["buys"] == 900 and row["sells"] == 1000
    assert out.details["symbol"] == "FDOG"
    text = out.response_text
    assert "900 buys / 1,000 sells by 650 buyers and 700 sellers" in text
    assert "second token of the pair" in text
    # the pool's price change is the base token's, not this token's: not shown
    assert "+3.2%" not in text and "change not reported" in text


def test_a_pool_that_does_not_say_which_side_the_token_is_on_reports_no_split(flows, enable_web):
    pool = _pool(POOL_A, "FDOG / SOL", "raydium", "50000", True)
    del pool["relationships"]["base_token"], pool["relationships"]["quote_token"]
    flows.pools[SOL] = [pool]
    out = _run({"token": SOL, "chain": "solana"})
    assert out.ok and out.details["totals"]["24h"]["buys"] is None and out.details["totals"]["24h"]["pools_with_split"] == 0
    assert "no pool reports a buy/sell split" in out.response_text


@pytest.mark.parametrize("junk", [
    [1, 2, 3],                      # a list where an object belongs
    {"data": None},                 # no list of pools
    {"data": "pools"},
])
def test_an_answer_in_an_unknown_shape_is_refused_with_no_numbers(flows, enable_web, junk, monkeypatch):
    from core import token_flow

    monkeypatch.setattr(token_flow, "_get", lambda path, params=None: junk)
    out = _run({"token": SOL, "chain": "solana"})
    assert not out.ok and out.status == "bad_answer" and "$" not in out.response_text


def test_junk_numbers_inside_a_pool_are_not_reported_never_shown(flows, enable_web):
    pool = _pool(POOL_A, "FDOG / SOL", "raydium", "50000", True, volume={"h24": "nan", "h1": "inf", "m5": "-5", "h6": None})
    pool["attributes"]["transactions"]["h24"] = ["not", "a", "window"]
    pool["attributes"]["transactions"]["h1"] = {"buys": -3, "sells": True}
    flows.pools[SOL] = [pool]
    out = _run({"token": SOL, "chain": "solana"})
    assert out.ok, out.response_text
    for window in ("5m", "1h", "6h", "24h"):
        assert out.details["totals"][window]["volume_usd"] is None
    assert out.details["totals"]["24h"]["buys"] is None and out.details["totals"]["1h"]["buys"] is None
    assert "nan" not in out.response_text.lower() and "inf" not in out.response_text.lower()


def test_common_chain_names_are_understood(flows, enable_web):
    flows.pools[EVM] = [_pool(POOL_A, "TKN / WETH", "uniswap_v3", "50000", True, base=EVM, quote="0x" + "c" * 40, network="eth")]
    for name in ("eth", "Ethereum", "mainnet"):
        out = _run({"token": EVM, "chain": name})
        assert out.ok and out.details["chain"] == "ethereum", (name, out.response_text)


def test_windows_are_respected_and_unknown_windows_refused(flows, enable_web):
    out = _run({"token": SOL, "windows": ["1h"]})
    assert out.ok and out.details["windows"] == ["1h"]
    assert "1h" in out.response_text and "24h:" not in out.response_text
    out = _run({"token": SOL, "windows": ["2h"]})
    assert not out.ok and out.status == "invalid_arguments"
    assert "windows can be" in out.response_text and flows.requests  # the earlier call went out, this one refused first


def test_a_cashtag_alone_is_refused(flows, enable_web):
    out = _run({"token": "$FDOG"})
    assert not out.ok and out.status == "no_address"
    assert "ticker alone" in out.response_text


def test_a_rate_limit_refuses_with_no_numbers(flows, enable_web):
    flows.status = 429
    out = _run({"token": SOL})
    assert not out.ok and out.status == "rate_limited"
    assert "limiting requests" in out.response_text and "$" not in out.response_text
    assert len(flows.requests) == 1


@pytest.mark.parametrize("text,seated", [
    ("is this token being bought or sold right now?", True),
    ("what's the buy volume for $FDOG today?", True),
    ("is 0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae being bought or sold?", True),
    ("how did the meeting go", False),
    ("should I sell my car", False),
    # ordinary sentences that only share a word with a market question never seat it
    ("sell activity today was boring at the office", False),
    ("The buy volume for the new office coffee machine today", False),
    ("pressure for $PSI in the tire", False),
    ("Help me buy or sell my car, it is a token of love", False),
    ("tell me the selling activity in my store today", False),
    ("I bought or sold the coin last year, write a poem", False),
    ("buy and sell pressure on $WIF right now", True),
    ("is $BONK being bought or sold?", True),
])
def test_flow_requests_seat_the_tool(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    assert ("token.flow" in resolve_demand_signals(text).explicit_intents) is seated


def test_a_seated_question_puts_the_tool_in_the_models_offer():
    """The runtime-door proof the job asks for: the plain words seat token.flow in the offer."""
    from core.capability_graph import model_visible_specs

    specs = model_visible_specs(user_text="is this token being bought or sold right now?")
    assert "token.flow" in {s["intent"] for s in specs}
