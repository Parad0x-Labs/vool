"""Token research from normal chat: facts, holders, creator, creator's other tokens, links, X.

The chain-data providers (Helius, Etherscan) and X are loopback fixtures answering their documented
shapes; no real host is contacted. Every test goes through the model's door
(``execute_runtime_tool``), so the contract, dispatch, the outbound door and each part's failure
path run for real.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_chain_data_keys import ETHERSCAN_KEY, HELIUS_KEY, chain_hosts
from tests.test_x_api_reads import SOL, _connect, fake_x

MINT = SOL
DEPLOYER = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
OTHER_MINT = "So11111111111111111111111111111111111111112"
POOL = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
FIRST_SIG = "4" * 88
TOKEN_EVM = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
CREATOR_EVM = "0x4b3e0a8e5b2a1a0c2b0f0b3f2b6d9e1a2b3c4d5e"


class FakeChain:
    """Helius JSON-RPC by method, Etherscan by action; records every request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.rpc: dict[str, object] = {
            "getTokenSupply": {"context": {}, "value": {"amount": "1000000000", "decimals": 6, "uiAmountString": "1000"}},
            "getAsset": {"id": MINT, "interface": "FungibleToken",
                         "content": {"metadata": {"name": "Fixture Dog", "symbol": "FDOG"},
                                     "links": {"external_url": "https://fixture-dog.example"},
                                     "json_uri": "http://insecure.example/meta.json"}},
            "getTokenLargestAccounts": {"context": {}, "value": [
                {"address": POOL, "amount": "400000000"}, {"address": DEPLOYER, "amount": "100000000"}]},
            "getAccountInfo": {"context": {}, "value": {
                "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "data": {"parsed": {"info": {"mintAuthority": None, "freezeAuthority": DEPLOYER, "decimals": 6}}}}},
            "getSignaturesForAddress": [{"signature": "3" * 88, "blockTime": 1760300000},
                                        {"signature": FIRST_SIG, "blockTime": 1760200000}],
            "getTransaction": {"slot": 1, "blockTime": 1760200000, "meta": {"fee": 5000},
                               "transaction": {"message": {"accountKeys": [DEPLOYER, MINT]}}},
            "getAssetsByAuthority": {"items": [
                {"id": OTHER_MINT, "interface": "FungibleToken", "content": {"metadata": {"name": "Older Cat", "symbol": "OCAT"}}},
                {"id": MINT, "interface": "FungibleToken", "content": {"metadata": {"name": "Fixture Dog"}}},
                {"id": POOL, "interface": "V1_NFT", "content": {"metadata": {"name": "an nft"}}}]},
            "getAssetsByCreator": {"items": []},
        }
        self.etherscan: dict[str, object] = {
            "getcontractcreation": {"status": "1", "message": "OK", "result": [
                {"contractAddress": TOKEN_EVM, "contractCreator": CREATOR_EVM, "txHash": "0x" + "c" * 64, "timestamp": "1700000000"}]},
            "txlist": {"status": "1", "message": "OK", "result": [
                {"hash": "0x" + "d" * 64, "to": "", "contractAddress": "0x" + "e" * 40, "timeStamp": "1690000000"},
                {"hash": "0x" + "f" * 64, "to": "0x" + "1" * 40, "contractAddress": "", "timeStamp": "1690000100"},
                {"hash": "0x" + "c" * 64, "to": "", "contractAddress": TOKEN_EVM, "timeStamp": "1700000000"}]},
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                parts = urlsplit(self.path)
                outer.requests.append({"path": parts.path, "method": body.get("method"), "params": body.get("params")})
                answer = outer.rpc.get(body.get("method"))
                if isinstance(answer, tuple):
                    return self._send(*answer)
                return self._send(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": answer})

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.requests.append({"path": parts.path, "action": query.get("action"), "query": query})
                answer = outer.etherscan.get(query.get("action"), {"status": "0", "message": "NOTOK", "result": "no route"})
                return self._send(200, answer)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def methods(self):
        return [r.get("method") or r.get("action") for r in self.requests]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def chain(vault_home, chain_hosts):
    with FakeChain() as server:
        chain_hosts(server)
        yield server


def _store(slot, key):
    from core.credential_store import store_credential

    store_credential(slot, key, label="fixture")


def _research(**args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("token.research", args, source_context={})


def test_no_key_means_nothing_goes_online_and_says_which_key(chain):
    out = _research(token=MINT)
    assert out.handled and not out.ok and out.status == "nothing_read"
    assert "Helius" in out.response_text and "did not go online" in out.response_text
    assert chain.requests == []


def test_solana_token_research_end_to_end(chain):
    from core.secret_redaction import redact_secrets

    _store("chain.data.helius", HELIUS_KEY)
    out = _research(token=MINT)
    assert out.ok, out.response_text
    parts = out.details["parts"]
    assert parts["facts"]["name"] == "Fixture Dog" and parts["facts"]["supply"] == "1000"
    assert parts["holders"]["top1_percent"] == "40.00" and parts["holders"]["top10_percent"] == "50.00"
    creator = parts["creator"]
    assert creator["address"] == DEPLOYER and creator["first_tx"] == FIRST_SIG
    assert creator["mint_authority"] is None and creator["freeze_authority"] == DEPLOYER
    assert "no one can mint more" in out.response_text and f"{DEPLOYER} can still freeze holders" in out.response_text
    # what else the creator launched: fungible tokens only, never the token itself
    assert [t["address"] for t in parts["creator_tokens"]["tokens"]] == [OTHER_MINT]
    assert "launch program may not show up" in out.response_text
    # links: only https links, the http metadata file is never fetched
    assert parts["links"]["links"] == {"website": "https://fixture-dog.example"}
    assert "not verified" in out.response_text
    assert parts["x"]["status"] == "x_not_connected"
    first_page = next(r for r in chain.requests if r.get("method") == "getAssetsByAuthority")
    assert first_page["params"]["authorityAddress"] == DEPLOYER
    assert all(r["path"].startswith("/helius") for r in chain.requests)
    assert HELIUS_KEY not in json.dumps(out.details, default=str) and HELIUS_KEY not in redact_secrets(out.response_text)


@pytest.mark.parametrize("uri,web,why", [
    ("https://meta.example/fdog.json", False, "web access is off"),
    ("https://127.0.0.1/fdog.json", True, "private address"),
])
def test_the_creators_metadata_host_is_only_contacted_with_web_on_and_never_privately(chain, monkeypatch, request, uri, web, why):
    """The metadata file sits wherever the token's creator chose: reading it with web access off
    would leak the user's address to that host, and a private address would reach their network."""
    from core import token_research

    if web:
        request.getfixturevalue("enable_web")
    _store("chain.data.helius", HELIUS_KEY)
    chain.rpc["getAsset"]["content"]["json_uri"] = uri
    fetched = []
    monkeypatch.setattr(token_research, "_metadata_json", lambda u: fetched.append(u) or {})
    out = _research(token=MINT, parts=["links"])
    assert fetched == [] and why in out.response_text
    assert out.details["parts"]["links"]["links"] == {"website": "https://fixture-dog.example"}


def test_a_token_too_busy_to_find_its_first_transaction_says_so(chain):
    _store("chain.data.helius", HELIUS_KEY)
    chain.rpc["getSignaturesForAddress"] = [{"signature": "6" * 88, "blockTime": 1760300000}] * 1000
    out = _research(token=MINT, parts=["creator", "creator_tokens"])
    assert out.ok
    assert out.details["parts"]["creator"]["address"] is None
    assert "more than 3,000 transactions" in out.response_text
    assert out.details["parts"]["creator_tokens"]["status"] == "unavailable"
    assert chain.methods().count("getSignaturesForAddress") == 3 and "getTransaction" not in chain.methods()


def test_a_wallet_is_not_researched_as_a_token(chain):
    _store("chain.data.helius", HELIUS_KEY)
    chain.rpc["getAccountInfo"] = {"context": {}, "value": {"owner": "11111111111111111111111111111111", "data": ["", "base64"]}}
    out = _research(token=MINT, parts="creator")
    assert not out.ok and out.details["parts"]["creator"]["status"] == "not_a_token"
    assert "not a token mint" in out.response_text


def test_one_failing_part_never_stops_the_others_or_invents_numbers(chain):
    _store("chain.data.helius", HELIUS_KEY)
    chain.rpc["getTokenLargestAccounts"] = (429, {"jsonrpc": "2.0", "error": {"code": 429, "message": "rate limited"}})
    out = _research(token=MINT, parts=["facts", "holders"])
    assert out.ok
    assert out.details["parts"]["facts"]["symbol"] == "FDOG"
    assert out.details["parts"]["holders"]["status"] == "rate_limited"
    assert "%" not in out.response_text.split("Holders:")[1]


def test_evm_creator_and_their_other_contracts_need_etherscan(chain):
    out = _research(token=TOKEN_EVM, chain="base", parts=["creator"])
    assert not out.ok and "Etherscan key" in out.response_text and chain.requests == []
    _store("chain.data.etherscan", ETHERSCAN_KEY)
    out = _research(token=TOKEN_EVM, chain="base", parts=["creator", "creator_tokens", "links"])
    assert out.ok, out.response_text
    parts = out.details["parts"]
    assert parts["creator"]["address"] == CREATOR_EVM
    assert [t["address"] for t in parts["creator_tokens"]["tokens"]] == ["0x" + "e" * 40]
    assert "not every contract is a token" in out.response_text
    assert parts["links"]["status"] == "not_offered"
    creation = next(r for r in chain.requests if r.get("action") == "getcontractcreation")
    assert creation["query"]["chainid"] == "8453" and creation["query"]["contractaddresses"] == TOKEN_EVM


def test_creator_only_lists_what_an_address_launched(chain):
    _store("chain.data.helius", HELIUS_KEY)
    out = _research(creator=DEPLOYER)
    assert out.ok and [t["address"] for t in out.details["parts"]["creator_tokens"]["tokens"]] == [OTHER_MINT, MINT]


def test_a_ticker_without_x_asks_for_the_address(chain):
    _store("chain.data.helius", HELIUS_KEY)
    out = _research(token="$FDOG")
    assert not out.ok and out.status == "address_needed" and chain.requests == []


def test_a_ticker_with_x_finds_the_addresses_people_post(chain, fake_x):
    _connect()
    out = _research(token="$FDOG")
    assert out.ok and out.status == "address_candidates"
    assert out.details["parts"]["x"]["addresses"][0] == {"address": SOL, "posts": 3}
    assert "Several tokens can share a ticker" in out.response_text
    assert chain.requests == []


def test_x_part_names_the_first_callers_when_x_is_connected(chain, fake_x):
    _store("chain.data.helius", HELIUS_KEY)
    _connect()
    out = _research(token=MINT, parts=["x"])
    assert out.ok, out.response_text
    callers = out.details["parts"]["x"]["callers"]
    assert callers[0]["username"] == "early_ape" and "First: @early_ape" in out.response_text


def test_research_is_offered_as_a_read_only_tool():
    from core.runtime_tool_contracts import runtime_tool_contracts

    [contract] = [c for c in runtime_tool_contracts() if c.intent == "token.research"]
    assert contract.side_effect_class == "read_only" and contract.supported
    assert "access_external_providers" not in contract.permission_actions  # X not connected


@pytest.mark.parametrize("text,seated", [
    (f"research this token {MINT}", True),
    ("what else did the dev launch?", True),
    ("does anyone still have mint authority on this?", True),
    ("dig into $FDOG for me", True),
    ("can you check out my contract for the apartment", False),
    (f"send 0.1 sol to {MINT}", False),
])
def test_research_requests_seat_the_tool(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    assert ("token.research" in resolve_demand_signals(text).explicit_intents) is seated


def test_new_launches_lists_the_newest_tokens_newest_first(chain):
    from core.runtime_execution_tools import execute_runtime_tool

    out = execute_runtime_tool("token.launches", {"limit": 5}, source_context={})
    assert not out.ok and "Helius key" in out.response_text and chain.requests == []
    _store("chain.data.helius", HELIUS_KEY)
    chain.rpc["searchAssets"] = {"items": [
        {"id": MINT, "interface": "FungibleToken", "content": {"metadata": {"name": "Fixture Dog", "symbol": "FDOG"}},
         "authorities": [{"address": DEPLOYER, "scopes": ["full"]}]},
        {"id": "not an address", "interface": "FungibleToken"}]}
    out = execute_runtime_tool("token.launches", {"limit": 500, "authority": DEPLOYER}, source_context={})
    assert out.ok, out.response_text
    assert out.details["tokens"] == [{"address": MINT, "name": "Fixture Dog", "symbol": "FDOG", "authority": DEPLOYER}]
    [req] = [r for r in chain.requests if r.get("method") == "searchAssets"]
    assert req["params"]["limit"] == 50 and req["params"]["authorityAddress"] == DEPLOYER
    assert req["params"]["sortBy"] == {"sortBy": "created", "sortDirection": "desc"}
    assert "anyone can launch a token" in out.response_text


def test_new_launches_refuses_a_bad_filter_before_going_online(chain):
    from core.runtime_execution_tools import execute_runtime_tool

    _store("chain.data.helius", HELIUS_KEY)
    out = execute_runtime_tool("token.launches", {"creator": "0xnotsolana"}, source_context={})
    assert not out.ok and out.status == "bad_subject" and chain.requests == []
