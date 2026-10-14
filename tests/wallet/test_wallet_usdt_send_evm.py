"""USDT on Ethereum, BNB Smart Chain and Arbitrum One from a chat request: ONE proposal -> a quote in USDT with the gas in ETH -> PIN
approval -> the signer builds transfer(recipient, amount) for the registry's contract itself -> one send -> paid only
when the receipt carries the contract's own Transfer event for exactly that amount.

SIMULATED CHAIN: the loopback EVM node (tests.wallet._rig_evm_native) decodes and executes what it is sent, including
an ERC-20 contract's transfer and its Transfer event; the wallet's own custody signs with its real sealed key. The
chat door is the production model tool; Contacts are real.
"""
from __future__ import annotations

import json
import uuid

import pytest

from core.contacts import store
from core.wallet.errors import WalletFault
from tests.contacts import protected_changes
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

pytestmark = [pytest.mark.safety]

ETHEREUM = "eip155:1"
ARBITRUM = "eip155:42161"
BSC = "eip155:56"
USDT_BSC = "0x55d398326f99059ff775485246999027b3197955"
BASE = "eip155:8453"
USDT_ETHEREUM = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDT_ARBITRUM = "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"
TOMAS = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
PIN = "482913"
OWNER = store.ACTOR_OWNER
GAS = 60_000


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")  # the scripted nodes listen on loopback
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    protected_changes.reset()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    environment.set_active_environment("mainnet")
    with ScriptedEvmNativeChain(chain_id=1, estimate_gas=GAS) as eth, ScriptedEvmNativeChain(chain_id=42161, fee_model="arbitrum", estimate_gas=GAS) as arb, \
            ScriptedEvmNativeChain(chain_id=56, fee_model="bsc", estimate_gas=GAS) as bsc:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({ETHEREUM: eth.url, ARBITRUM: arb.url, BSC: bsc.url}))
        eth.add_token(USDT_ETHEREUM, decimals=6)
        arb.add_token(USDT_ARBITRUM, decimals=6)
        bsc.add_token(USDT_BSC, decimals=18)  # the BNB Smart Chain contract counts in 18 decimals
        yield {"eth": eth, "arb": arb, "bsc": bsc}
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    protected_changes.reset()


def _wallet(node, network: str, contract: str, *, wei: int = 10**17, usdt: int = 50_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"u-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund(wallet["address"], wei)
    node.fund_token(contract, wallet["address"], usdt)
    return wallet


def _propose(**args):
    from core.runtime_execution_tools import _dispatch_runtime_tool

    return _dispatch_runtime_tool("wallet.propose", dict(args), source_context={"session_id": "usdt-evm"})


def _quote(proposal_id: str) -> dict:
    from core.wallet import quotes

    return quotes.mint_quote(proposal_id)


def _approve(quote: dict) -> dict:
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_pilot_transfer(quote["proposal_id"], quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))


def _holds(proposal_id: str) -> list[tuple]:
    from core.wallet.store import connection

    with connection() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT proposal_id, asset, amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) ORDER BY proposal_id",
            (proposal_id, f"{proposal_id}:fee")).fetchall()]


def test_send_tomas_10_usdt_on_ethereum_calls_the_contract_and_is_paid_on_its_transfer_event(home) -> None:
    from core.wallet import evm_tokens

    eth = home["eth"]
    wallet = _wallet(eth, ETHEREUM, USDT_ETHEREUM)
    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[{"kind": "wallet", "value": TOMAS, "network": ETHEREUM},
                                                                           {"kind": "wallet", "value": TOMAS, "network": BASE}])
    asked = _propose(destination="Tomas Berg", asset="USDT", amount="10")
    assert asked.ok and asked.status == "pending_approval", asked.response_text
    proposal = asked.details["proposal"]
    assert (proposal["asset"], int(proposal["amount_minor"]), proposal["network"]) == ("USDT", 10_000_000, ETHEREUM), "Base carries no USDT for this wallet"

    quote = _quote(proposal["proposal_id"])
    fields = quote["fields"]
    assert (fields["asset"], fields["gas_asset"], fields["token_contract"], fields["amount_human"], fields["principal_balance_minor"]) == ("USDT", "ETH", USDT_ETHEREUM, "10", 50_000_000)
    params = fields["tx_params"]
    assert (params["to"], params["value"], params["data"]) == (USDT_ETHEREUM, 0, "0x" + evm_tokens.transfer_calldata(TOMAS, 10_000_000).hex())
    assert params["gas_limit"] > GAS, "the gas of this exact call, estimated and buffered"
    assert fields["recipient_saved"] is True and fields["recipient_label"] == "Tomas Berg"

    result = _approve(quote)
    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert (eth.token_balance(USDT_ETHEREUM, TOMAS), eth.token_balance(USDT_ETHEREUM, wallet["address"])) == (10_000_000, 40_000_000)
    sent = eth._decode("0x" + eth.sent[0].hex())
    assert (sent["to"], sent["value"], len(eth.sent)) == (USDT_ETHEREUM, 0, 1), "one call of the registry's contract, no value attached"
    pid = proposal["proposal_id"]
    holds = _holds(pid)
    assert holds[0] == (pid, "USDT", 10_000_000, 0, "settled") and holds[1][:3] == (f"{pid}:fee", "ETH", 0) and holds[1][4] == "settled", holds


@pytest.mark.parametrize(("row", "mode"), [("arb", "none"), ("eth", "wrong_amount")])
def test_a_successful_call_without_the_contracts_exact_transfer_event_is_not_called_paid(home, row: str, mode: str) -> None:
    from core.wallet import settlement, transfers

    node = home[row]
    network, contract = (ARBITRUM, USDT_ARBITRUM) if row == "arb" else (ETHEREUM, USDT_ETHEREUM)
    _wallet(node, network, contract)
    node.token_log_mode = mode
    asked = _propose(destination=TOMAS, asset="USDT", amount="5", network=network)
    assert asked.ok, asked.response_text
    pid = asked.details["proposal"]["proposal_id"]
    result = _approve(_quote(pid))
    settlement.observe_once(pid)  # one more look: still no proof
    transfer = transfers.get_transfer_by_id(pid)
    assert transfer["state"] != "confirmed" and result["transfer"]["state"] != "confirmed", transfer["state"]
    assert "principal_unproven" in str(transfer.get("evidence_json")), transfer.get("evidence_json")
    assert _holds(pid)[0][4] == "reserved", "an unproven principal stays counted against the caps"


def test_usdt_on_bnb_smart_chain_is_counted_in_6_decimals_and_sent_in_the_contracts_18(home) -> None:
    from core.wallet import evm_tokens

    bsc = home["bsc"]
    dust = 5  # less than a millionth of a USDT: never counted, never sent
    wallet = _wallet(bsc, BSC, USDT_BSC, usdt=50 * 10**18 + dust)
    asked = _propose(destination=TOMAS, asset="USDT", amount="10", network=BSC)
    assert asked.ok and int(asked.details["proposal"]["amount_minor"]) == 10_000_000, asked.response_text
    quote = _quote(asked.details["proposal"]["proposal_id"])
    fields = quote["fields"]
    assert (fields["decimals"], fields["principal_balance_minor"], fields["balance_display"]) == (6, 50_000_000, "50 USDT")
    assert fields["tx_params"]["data"] == "0x" + evm_tokens.transfer_calldata(TOMAS, 10 * 10**18).hex()
    result = _approve(quote)
    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert (bsc.token_balance(USDT_BSC, TOMAS), bsc.token_balance(USDT_BSC, wallet["address"])) == (10 * 10**18, 40 * 10**18 + dust)
    pid = asked.details["proposal"]["proposal_id"]
    assert _holds(pid)[0] == (pid, "USDT", 10_000_000, 0, "settled"), "caps and holds count USDT the same on every chain"
    from core.wallet import limits

    limits.set_limits(wallet["wallet_id"], "USDT", limits.SpendLimits(10**12, 10**12, 10**12))  # the owner raised the USDT caps
    bsc.fund_token(USDT_BSC, wallet["address"], 25_000 * 10**18)
    large = _propose(destination=TOMAS, asset="USDT", amount="25000", network=BSC, idempotency_key=uuid.uuid4().hex)
    assert large.ok and int(large.details["proposal"]["amount_minor"]) == 25_000_000_000, large.response_text  # fits the stored columns
    assert _quote(large.details["proposal"]["proposal_id"])["fields"]["tx_params"]["data"].endswith(format(25_000 * 10**18, "064x"))


def test_a_token_balance_spent_after_the_quote_asks_for_a_fresh_preview_and_sends_nothing(home) -> None:
    eth = home["eth"]
    wallet = _wallet(eth, ETHEREUM, USDT_ETHEREUM, usdt=10_000_000)
    asked = _propose(destination=TOMAS, asset="USDT", amount="10", network=ETHEREUM)
    quote = _quote(asked.details["proposal"]["proposal_id"])
    eth.tokens[USDT_ETHEREUM]["balances"][wallet["address"].lower()] = 9_999_999
    with pytest.raises(WalletFault) as caught:
        _approve(quote)
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_quote_expired", "token_balance_changed")
    assert eth.sent == []


def test_usdt_sent_to_its_own_contract_or_beyond_the_balance_is_refused_at_the_quote(home) -> None:
    eth = home["eth"]
    _wallet(eth, ETHEREUM, USDT_ETHEREUM, usdt=1_000_000)
    for destination, amount, code, reason in ((USDT_ETHEREUM, "1", "wallet_recipient_refused", "recipient_is_the_token_contract"),
                                              (TOMAS, "2", "wallet_insufficient_funds", "token_balance_below_amount")):
        asked = _propose(destination=destination, asset="USDT", amount=amount, network=ETHEREUM, idempotency_key=uuid.uuid4().hex)
        assert asked.ok, asked.response_text
        with pytest.raises(WalletFault) as caught:
            _quote(asked.details["proposal"]["proposal_id"])
        assert (caught.value.code, caught.value.context.get("reason")) == (code, reason)
    assert eth.sent == []


def test_a_contract_reporting_other_decimals_is_never_quoted(home) -> None:
    eth = home["eth"]
    _wallet(eth, ETHEREUM, USDT_ETHEREUM)
    eth.tokens[USDT_ETHEREUM]["decimals"] = 18
    asked = _propose(destination=TOMAS, asset="USDT", amount="1", network=ETHEREUM)
    with pytest.raises(WalletFault) as caught:
        _quote(asked.details["proposal"]["proposal_id"])
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_quote_mismatch", "token_decimals_differ_from_registry")


def test_the_signer_builds_the_token_call_itself_and_signs_no_other_calldata(home) -> None:
    from core.wallet import evm_tokens, pilot_custody

    eth = home["eth"]
    wallet = _wallet(eth, ETHEREUM, USDT_ETHEREUM)
    base = {"chain_id": 1, "nonce": 0, "max_priority_fee_per_gas": 10**6, "max_fee_per_gas": 3 * 10**9, "gas_limit": 80_000}
    good = "0x" + evm_tokens.transfer_calldata(TOMAS, 5).hex()
    refusals = [
        ({**base, "to": "0x" + "7" * 40, "value": 0, "data": good, "token_transfer": {"recipient": TOMAS, "amount_minor": 5}}, "token_call_not_to_a_declared_contract"),
        ({**base, "to": USDT_ETHEREUM, "value": 1, "data": good, "token_transfer": {"recipient": TOMAS, "amount_minor": 5}}, "token_call_not_to_a_declared_contract"),
        ({**base, "to": USDT_ETHEREUM, "value": 0, "data": good, "token_transfer": {"recipient": TOMAS, "amount_minor": 6}}, "quoted_calldata_differs"),
        ({**base, "to": USDT_ETHEREUM, "value": 0, "data": good}, "calldata_without_a_token_transfer"),
    ]
    with pilot_custody.signing_session(wallet["wallet_id"], PIN) as signer:
        for params, reason in refusals:
            with pytest.raises(WalletFault) as caught:
                signer.sign_evm_type2(params)
            assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_signature_invalid", reason)
        raw, _tx = signer.sign_evm_type2({**base, "to": USDT_ETHEREUM, "value": 0, "data": good, "token_transfer": {"recipient": TOMAS, "amount_minor": 5}})
    decoded = eth._decode("0x" + raw.hex())
    assert (decoded["to"], decoded["value"], "0x" + decoded["data"].hex()) == (USDT_ETHEREUM, 0, good)


def test_usdt_on_two_chains_is_asked_about_not_settled_by_the_default_account(home) -> None:
    _wallet(home["eth"], ETHEREUM, USDT_ETHEREUM)
    _wallet(home["arb"], ARBITRUM, USDT_ARBITRUM)
    asked = _propose(destination=TOMAS, asset="USDT", amount="10")
    assert not asked.ok and asked.status == "wallet_request_ambiguous", asked.response_text
    assert "USDT can go out on Ethereum or Arbitrum One. Which chain should it use?" in asked.response_text, asked.response_text
    chosen = _propose(destination=TOMAS, asset="USDT", amount="10", chain="arbitrum")
    assert chosen.ok and chosen.details["proposal"]["network"] == ARBITRUM, chosen.response_text


def test_chat_balances_show_usdt_on_each_evm_chain(home) -> None:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    _wallet(home["eth"], ETHEREUM, USDT_ETHEREUM, wei=2 * 10**17, usdt=12_500_000)
    _wallet(home["arb"], ARBITRUM, USDT_ARBITRUM, usdt=0)
    text = _dispatch_runtime_tool("wallet.status", {}, source_context={"session_id": "usdt-evm-balances"}).response_text
    assert "Balances on Mainnet (each chain's own coin and USDT):" in text, text
    assert "- Ethereum: 0.2 ETH" in text and "- Ethereum: 12.5 USDT" in text, text
    assert "Arbitrum One USDT" in text.split("Nothing held on:")[1], text


def test_usdt_asked_on_a_chain_that_does_not_carry_it_says_where_it_goes(home) -> None:
    _wallet(home["eth"], ETHEREUM, USDT_ETHEREUM)
    asked = _propose(destination=TOMAS, asset="USDT", amount="1", chain="base")
    assert not asked.ok and asked.status == "wallet_not_found"
    assert "This wallet does not send USDT on Base. It goes out on Ethereum, BNB Smart Chain and Arbitrum One." in asked.response_text, asked.response_text  # (a 0x address: not Solana)
