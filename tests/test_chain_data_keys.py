"""Chain-data keys (Helius, Alchemy, Solscan, Etherscan): the saved-key flow and the chat lookup.

Providers are loopback fixtures scripted with each provider's documented request and answer shape;
the real hosts are never contacted. What a real key on a real account answers is checked separately
on the owner's machine.
"""
from __future__ import annotations

import dataclasses
import json
from urllib.parse import parse_qs

import pytest

from tests._credential_intelligence_support import FakeProviderServer, isolated_home, vault_home

HELIUS_KEY = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
ALCHEMY_KEY = "alch_fixture_key_0123456789abcd"
SOLSCAN_KEY = "eyJhbGciOiJIUzI1NiJ9.fixture.solscan-not-real"
ETHERSCAN_KEY = "ETHERSCANFIXTUREKEY0123456789ABCD"
SOL_ADDRESS = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
EVM_ADDRESS = "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"
EVM_TX = "0x" + "ab" * 32
SOL_SIG = "5" * 88


@pytest.fixture
def chain_hosts(monkeypatch):
    """Point every chain-data provider at one loopback fake, keeping each provider's own path layout."""
    from core import chain_providers as cp

    def install(server: FakeProviderServer) -> None:
        base = server.url.rstrip("/")
        monkeypatch.setattr(cp, "HELIUS_RPC_URL", f"{base}/helius/")
        monkeypatch.setattr(cp, "SOLSCAN_API_ORIGIN", f"{base}/solscan")
        monkeypatch.setattr(cp, "ETHERSCAN_API_URL", f"{base}/etherscan/v2/api")
        monkeypatch.setattr(cp, "alchemy_url", lambda chain: f"{base}/alchemy/{cp.ALCHEMY_NETWORKS[chain]}/v2")
        verify = {
            "helius": f"{base}/helius/", "alchemy": f"{base}/alchemy/eth-mainnet/v2",
            "solscan": f"{base}/solscan/v2.0/token/meta", "etherscan": f"{base}/etherscan/v2/api",
        }
        patched = {pid: dataclasses.replace(cfg, verify_url=verify[pid]) for pid, cfg in cp.CHAIN_PROVIDERS.items()}
        monkeypatch.setattr(cp, "CHAIN_PROVIDERS", patched)

    return install


def _save(provider_id: str, key: str):
    from core.credential_intelligence.intake import CredentialIntake
    from core.credential_intelligence.provider_registry import default_registry

    intake = CredentialIntake(registry=default_registry())
    intake.paste(key)
    intake.select_provider(provider_id)
    outcome = intake.verify()
    return intake, outcome


def _lookup(**arguments):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("web.chain_lookup", arguments, source_context={})


def _store(slot: str, key: str) -> None:
    from core.credential_store import store_credential

    store_credential(slot, key, label="fixture")


# --------------------------------------------------------------------------- registry facts

def test_each_provider_is_registered_with_its_documented_key_placement():
    from core.credential_intelligence.provider_registry import default_registry

    registry = default_registry()
    want = {
        "chain.helius": ("query_token", "api-key", "https://mainnet.helius-rpc.com/", "chain.data.helius"),
        "chain.alchemy": ("bearer", "", "https://eth-mainnet.g.alchemy.com/v2", "chain.data.alchemy"),
        "chain.solscan": ("header", "token", "https://pro-api.solscan.io/v2.0/token/meta", "chain.data.solscan"),
        "chain.etherscan": ("query_token", "apikey", "https://api.etherscan.io/v2/api", "chain.data.etherscan"),
    }
    for pid, (style, name, endpoint, slot) in want.items():
        d = registry.get(pid)
        assert (d.auth_style, d.auth_name, d.verify_endpoint, d.credential_slot, d.kind) == (style, name, endpoint, slot, "chain_data"), pid


# --------------------------------------------------------------------------- Save (paste, verify, store)

def test_helius_key_rides_the_query_and_is_stored_only_after_helius_accepts_it(vault_home, chain_hosts):
    from core import credential_store
    from core.secret_redaction import redact_secrets

    answer = {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 1}, "value": {"blockhash": "x", "lastValidBlockHeight": 2}}}
    with FakeProviderServer([(200, answer)]) as server:
        chain_hosts(server)
        intake, outcome = _save("chain.helius", HELIUS_KEY)
        assert outcome.status == "verified"
        receipt = intake.complete()
        assert receipt is not None
        req = server.requests[0]
        assert req["method"] == "POST" and req["path"] == "/helius/"
        assert parse_qs(req["query"]) == {"api-key": [HELIUS_KEY]}
        assert not req["auth_present"]
    assert credential_store.get_credential("chain.data.helius") == HELIUS_KEY
    # the URL that carried the key never prints it again
    assert HELIUS_KEY not in redact_secrets(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}")


def test_alchemy_key_rides_the_bearer_header_never_the_url(vault_home, chain_hosts):
    from core import credential_store

    answer = {"jsonrpc": "2.0", "id": 1, "result": {"number": "0x10", "hash": "0x" + "1" * 64}}
    with FakeProviderServer([(200, answer)]) as server:
        chain_hosts(server)
        intake, outcome = _save("chain.alchemy", ALCHEMY_KEY)
        assert outcome.status == "verified"
        intake.complete()
        req = server.requests[0]
        assert req["path"] == "/alchemy/eth-mainnet/v2" and req["query"] == ""
        assert req["auth_present"]
    assert credential_store.get_credential("chain.data.alchemy") == ALCHEMY_KEY


def test_solscan_key_rides_the_token_header(vault_home, chain_hosts):
    answer = {"success": True, "data": {"name": "Wrapped SOL", "symbol": "SOL", "decimals": 9}}
    with FakeProviderServer([(200, answer)]) as server:
        chain_hosts(server)
        intake, outcome = _save("chain.solscan", SOLSCAN_KEY)
        assert outcome.status == "verified"
        req = server.requests[0]
        assert req["path"] == "/solscan/v2.0/token/meta"
        assert "token" in req["header_names"] and SOLSCAN_KEY not in req["query"]


@pytest.mark.parametrize(("answer", "status"), [
    ((401, {"jsonrpc": "2.0", "error": {"code": -32401, "message": "invalid api key provided"}}), "invalid"),
    ((429, {"error": "rate limited"}), "rate_limited"),
    ((503, "upstream down"), "provider_unavailable"),
    ((200, {"jsonrpc": "2.0", "id": 1, "result": "ok"}), "unexpected_schema"),
    ((200, "<html>captive portal</html>"), "malformed_response"),
])
def test_a_helius_key_that_is_not_accepted_is_never_stored(vault_home, chain_hosts, answer, status):
    from core import credential_store
    from core.credential_intelligence.store import IntakeRefusedError

    with FakeProviderServer([answer]) as server:
        chain_hosts(server)
        intake, outcome = _save("chain.helius", HELIUS_KEY)
        assert outcome.status == status
        with pytest.raises(IntakeRefusedError):
            intake.complete()
        assert server.request_count == 1
    assert not credential_store.has_credential("chain.data.helius")


@pytest.mark.parametrize(("body", "status"), [
    ({"status": "0", "message": "NOTOK", "result": "Invalid API Key"}, "invalid"),
    ({"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}, "rate_limited"),
    ({"status": "0", "message": "NOTOK", "result": "Something new"}, "unexpected_schema"),
    ({"status": "1", "message": "OK", "result": {"ethbtc": "0.03", "ethusd": "3000"}}, "verified"),
])
def test_etherscan_reads_its_http_200_error_envelope(vault_home, chain_hosts, body, status):
    with FakeProviderServer([(200, body)]) as server:
        chain_hosts(server)
        _intake, outcome = _save("chain.etherscan", ETHERSCAN_KEY)
        assert outcome.status == status
        assert parse_qs(server.requests[0]["query"])["apikey"] == [ETHERSCAN_KEY]


def test_a_paste_that_is_not_url_safe_is_never_sent_for_a_url_carried_key(vault_home, chain_hosts):
    with FakeProviderServer([(200, {})]) as server:
        chain_hosts(server)
        _intake, outcome = _save("chain.helius", "abc&api-key=other 123456789012")
        assert outcome.status == "refused"
        assert server.request_count == 0


# --------------------------------------------------------------------------- the chat lookup

def test_the_lookup_tool_is_offered_read_only_in_every_chat():
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contract = runtime_tool_contract_map()["web.chain_lookup"]
    assert contract.supported and contract.read_only and contract.renders_final_answer
    assert contract.approval_requirement == "none"
    assert "use_network_access" in contract.permission_actions


def test_no_key_says_which_key_to_add_and_goes_nowhere(vault_home, chain_hosts):
    with FakeProviderServer([(200, {})]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=SOL_ADDRESS)
        assert not result.ok and result.status == "key_missing"
        assert "Helius, Alchemy or Solscan" in result.response_text and "Settings" in result.response_text
        assert server.request_count == 0


def test_solana_balance_through_helius(vault_home, chain_hosts):
    _store("chain.data.helius", HELIUS_KEY)
    with FakeProviderServer([(200, {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 9}, "value": 1_500_000_000}})]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=SOL_ADDRESS)
        assert result.ok, result.response_text
        assert f"{SOL_ADDRESS} holds 1.5 SOL." in result.response_text
        assert "Source: Helius, Solana." in result.response_text
        assert parse_qs(server.requests[0]["query"]) == {"api-key": [HELIUS_KEY]}
    assert HELIUS_KEY not in result.response_text


def test_a_named_provider_without_a_key_is_refused_even_when_another_key_exists(vault_home, chain_hosts):
    _store("chain.data.helius", HELIUS_KEY)
    with FakeProviderServer([(200, {})]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=SOL_ADDRESS, provider="solscan")
        assert result.status == "key_missing" and "Solscan" in result.response_text
        assert server.request_count == 0


@pytest.mark.parametrize(("answer", "code"), [
    ((401, {"error": "unauthorized"}), "key_rejected"),
    ((429, {"error": "slow down"}), "rate_limited"),
    ((503, "down"), "provider_down"),
    ((200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "rate limit exceeded"}}), "rate_limited"),
    ((200, {"jsonrpc": "2.0", "id": 1, "result": {"value": "lots"}}), "bad_answer"),
    ((200, "<html>sign in to the wifi</html>"), "bad_answer"),
    ((200, {"unexpected": True}), "bad_answer"),
])
def test_every_failed_answer_fails_closed_with_no_figures(vault_home, chain_hosts, answer, code):
    _store("chain.data.helius", HELIUS_KEY)
    with FakeProviderServer([answer]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=SOL_ADDRESS)
        assert not result.ok and result.status == code, result.response_text
        assert "SOL." not in result.response_text and "holds" not in result.response_text
        assert HELIUS_KEY not in result.response_text


def test_a_provider_that_does_not_answer_in_time_is_a_timeout(vault_home, chain_hosts, monkeypatch):
    from core import chain_data

    monkeypatch.setattr(chain_data, "REQUEST_TIMEOUT_S", 0.3)
    _store("chain.data.helius", HELIUS_KEY)
    with FakeProviderServer([(200, {"jsonrpc": "2.0", "id": 1, "result": {"value": 1}}, {}, 1.5)]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=SOL_ADDRESS)
        assert result.status == "timeout", result.response_text


def test_an_unreachable_provider_is_named_as_unreachable(vault_home, chain_hosts):
    _store("chain.data.helius", HELIUS_KEY)
    with FakeProviderServer([(200, {})]) as server:
        chain_hosts(server)
    # the server is closed now: nothing listens on its port
    result = _lookup(query="balance", address=SOL_ADDRESS)
    assert result.status == "unreachable", result.response_text


def test_local_only_stops_the_lookup_before_it_leaves_the_machine(vault_home, monkeypatch):
    from core import policy_engine

    _store("chain.data.helius", HELIUS_KEY)
    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: True)
    result = _lookup(query="balance", address=SOL_ADDRESS)  # real Helius host: refused before any I/O
    assert result.status == "refused", result.response_text


@pytest.mark.parametrize(("arguments", "code"), [
    ({"query": "balance", "address": "not-an-address"}, "bad_subject"),
    ({"query": "balance", "address": EVM_ADDRESS}, "chain_needed"),
    ({"query": "balance", "address": EVM_ADDRESS, "chain": "dogechain"}, "unknown_chain"),
    ({"query": "holders", "token": EVM_ADDRESS, "chain": "base"}, "not_offered"),
    ({"query": "sing", "address": SOL_ADDRESS}, "unknown_query"),
    ({"query": "transaction", "tx": "0x1234", "chain": "ethereum"}, "bad_subject"),
])
def test_bad_requests_are_refused_before_any_request(vault_home, chain_hosts, arguments, code):
    for slot, key in (("chain.data.helius", HELIUS_KEY), ("chain.data.alchemy", ALCHEMY_KEY), ("chain.data.etherscan", ETHERSCAN_KEY)):
        _store(slot, key)
    with FakeProviderServer([(200, {})]) as server:
        chain_hosts(server)
        result = _lookup(**arguments)
        assert result.status == code, result.response_text
        assert server.request_count == 0


def test_alchemy_base_balance_and_a_chain_the_app_has_not_turned_on(vault_home, chain_hosts):
    _store("chain.data.alchemy", ALCHEMY_KEY)
    answers = [(200, {"jsonrpc": "2.0", "id": 1, "result": hex(2 * 10**18)}), (403, {"error": "BNB Smart Chain is not enabled for this app"})]
    with FakeProviderServer(answers) as server:
        chain_hosts(server)
        ok = _lookup(query="balance", address=EVM_ADDRESS, chain="base")
        assert ok.ok and f"{EVM_ADDRESS} holds 2 ETH on Base." in ok.response_text
        assert server.requests[0]["path"] == "/alchemy/base-mainnet/v2" and server.requests[0]["auth_present"]
        refused = _lookup(query="balance", address=EVM_ADDRESS, chain="bnb")
        assert refused.status == "chain_not_enabled"
        assert "Turn on BNB Smart Chain" in refused.response_text


def test_alchemy_tokens_read_metadata_in_one_batch(vault_home, chain_hosts):
    _store("chain.data.alchemy", ALCHEMY_KEY)
    usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

    def answer(record):
        if record["json_keys"]:
            return (200, {"jsonrpc": "2.0", "id": 1, "result": {"address": EVM_ADDRESS, "tokenBalances": [
                {"contractAddress": usdc, "tokenBalance": hex(12_500_000)},
                {"contractAddress": "0x" + "1" * 40, "tokenBalance": "0x0"}]}})
        return (200, [{"jsonrpc": "2.0", "id": 0, "result": {"name": "USD Coin", "symbol": "USDC", "decimals": 6}}])

    with FakeProviderServer(answer) as server:
        chain_hosts(server)
        result = _lookup(query="tokens", address=EVM_ADDRESS, chain="ethereum")
        assert result.ok, result.response_text
        assert "- 12.5 USDC (" + usdc + ")" in result.response_text
        assert "holds 1 ERC-20 token(s)" in result.response_text
        assert server.request_count == 2


def test_etherscan_history_with_no_transactions_is_an_empty_answer_not_an_error(vault_home, chain_hosts):
    _store("chain.data.etherscan", ETHERSCAN_KEY)
    with FakeProviderServer([(200, {"status": "0", "message": "No transactions found", "result": []})]) as server:
        chain_hosts(server)
        result = _lookup(query="history", address=EVM_ADDRESS, chain="ethereum")
        assert result.ok and "No transactions found" in result.response_text
        q = parse_qs(server.requests[0]["query"])
        assert q["chainid"] == ["1"] and q["action"] == ["txlist"] and q["apikey"] == [ETHERSCAN_KEY]


def test_etherscan_plan_refusal_names_the_chain(vault_home, chain_hosts):
    _store("chain.data.etherscan", ETHERSCAN_KEY)
    body = {"status": "0", "message": "NOTOK", "result": "Free API access is not supported for this chain. Please upgrade your api plan"}
    with FakeProviderServer([(200, body)]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=EVM_ADDRESS, chain="base")
        assert result.status == "plan_refused" and "Base" in result.response_text
        assert parse_qs(server.requests[0]["query"])["chainid"] == ["8453"]


def test_solscan_token_info_and_holders(vault_home, chain_hosts):
    _store("chain.data.solscan", SOLSCAN_KEY)

    def answer(record):
        if record["path"].endswith("/token/meta"):
            return (200, {"success": True, "data": {"name": "USD Coin", "symbol": "USDC", "decimals": 6, "holder": 4000000, "supply": "9000000000000000"}})
        return (200, {"success": True, "data": {"total": 2, "items": [
            {"address": "A" * 44, "owner": "B" * 44, "amount": 5_000_000, "decimals": 6, "rank": 1}]}})

    with FakeProviderServer(answer) as server:
        chain_hosts(server)
        info = _lookup(query="token_info", token=USDC_MINT)
        assert info.ok and "USD Coin (USDC)" in info.response_text and "4000000 holders." in info.response_text
        holders = _lookup(query="holders", token=USDC_MINT, provider="solscan")
        assert holders.ok and f"- 5 held by {'B' * 44}" in holders.response_text
        assert all("token" in r["header_names"] for r in server.requests)


def test_a_solana_transaction_through_helius(vault_home, chain_hosts):
    _store("chain.data.helius", HELIUS_KEY)
    tx = {"slot": 77, "blockTime": 1_760_000_000, "meta": {"err": None, "fee": 5000, "preBalances": [2_000_000_000], "postBalances": [1_499_995_000]},
          "transaction": {"message": {"accountKeys": [SOL_ADDRESS]}}}
    with FakeProviderServer([(200, {"jsonrpc": "2.0", "id": 1, "result": tx})]) as server:
        chain_hosts(server)
        result = _lookup(query="transaction", tx=SOL_SIG)
        assert result.ok, result.response_text
        assert "succeeded" in result.response_text and "Fee 0.000005 SOL" in result.response_text
        assert "changed by -0.500005" in result.response_text


def test_a_failed_provider_names_the_other_saved_key_without_trying_it(vault_home, chain_hosts):
    _store("chain.data.helius", HELIUS_KEY)
    _store("chain.data.alchemy", ALCHEMY_KEY)
    with FakeProviderServer([(503, "down")]) as server:
        chain_hosts(server)
        result = _lookup(query="balance", address=SOL_ADDRESS)
        assert result.status == "provider_down"
        assert "Your Alchemy key is also saved" in result.response_text
        assert server.request_count == 1


# --------------------------------------------------------------------------- Settings Test and Remove

def _post(path: str, body: dict):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path=path, body=body, headers={"Content-Type": "application/json"}, runtime=RuntimeServices(display_name="t"),
                        model_name="fixture", client_host="127.0.0.1", workspace_root_provider=lambda: ".")
    return res.status, json.loads(res.body)


def test_settings_test_and_remove_for_a_saved_chain_key(vault_home, chain_hosts):
    from core import credential_store

    answer = {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 1}, "value": {"blockhash": "x"}}}
    with FakeProviderServer([(200, answer), (200, answer), (401, {"error": "bad"})]) as server:
        chain_hosts(server)
        intake, outcome = _save("chain.helius", HELIUS_KEY)
        assert outcome.status == "verified"
        intake.complete()
        status, payload = _post("/api/chain/test", {"provider": "helius"})
        assert status == 200 and payload.get("state") == "ok", payload
        status, payload = _post("/api/chain/test", {"provider": "helius"})
        assert payload.get("state") == "unauthorized", payload
        assert HELIUS_KEY not in json.dumps(payload)
        status, payload = _post("/api/settings/credentials", {"name": "chain.data.helius", "delete": True})
        assert status == 200 and payload["removed"] is True, payload
    assert not credential_store.has_credential("chain.data.helius")
    status, payload = _post("/api/chain/test", {"provider": "helius"})
    assert payload.get("state") == "no_key"


def test_the_credentials_door_never_stores_an_unverified_chain_key(vault_home):
    from core import credential_store

    status, payload = _post("/api/settings/credentials", {"name": "chain.data.alchemy", "value": ALCHEMY_KEY})
    assert status == 400
    assert not credential_store.has_credential("chain.data.alchemy")


def test_guide_lists_the_chain_providers_with_their_setup_steps():
    from core.provider_key_guide import provider_key_guide

    chain = {g["id"]: g for g in provider_key_guide()}["chain"]
    rows = {r["provider"]: r for r in chain["providers"]}
    assert list(rows) == ["helius", "alchemy", "solscan", "etherscan"]
    assert any("Networks tab" in step for step in rows["alchemy"]["steps"])
    assert any("LaserStream" in note for note in rows["helius"]["notes"])
    assert rows["alchemy"]["chains"] == ["Solana", "Ethereum", "Base", "BNB Smart Chain", "Robinhood Chain"]


@pytest.mark.parametrize(("text", "seated"), [
    (f"what is the SOL balance of {SOL_ADDRESS}", True),
    (f"show the token holders of {USDC_MINT}", True),
    (f"look up transaction {EVM_TX} on base", True),
    ("use my helius key to check that wallet", True),
    ("what is my wallet balance", False),  # VOOL's own wallet answers that, not a chain lookup
    ("tell me the history of rome", False),
])
def test_chain_questions_seat_the_lookup_and_others_do_not(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    assert ("web.chain_lookup" in resolve_demand_signals(text).explicit_intents) is seated
