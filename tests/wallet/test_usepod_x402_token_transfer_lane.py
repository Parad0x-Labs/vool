"""A UsePod x402 payment in USDC through the Crypto Pilot lane itself: requirement → ONE proposal → typed quote →
PIN approval → claim, real signature and one send → confirmed principal → proof-eligible operation.

SIMULATION chain: the loopback Solana node answers as Mainnet (its genesis hash) and executes what it is sent; the
wallet's own custody signs with its real sealed key. The provider is not involved here -- this is the wallet half of
the x402 payment, driven through the lane's production functions (no HTTP double, no signing double).
"""
from __future__ import annotations

import time
import uuid

import pytest

from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PIN = "482913"


@pytest.fixture
def lane(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle
    from core.web.api import wallet_api

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    wallet_api.reset_caller_binding_for_tests()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: node.url}))
        environment.set_active_environment("mainnet")
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        yield node
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    wallet_api.reset_caller_binding_for_tests()


def _payer(node, *, lamports: int = 20_000_000, usdc: int = 1_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"t-{uuid.uuid4().hex}", label="x402 payer")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund_sol(wallet["address"], lamports)
    if usdc:
        node.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def _recipient(node, *, open_account: bool = True) -> str:
    from solders.keypair import Keypair

    owner = str(Keypair().pubkey())
    node.fund_sol(owner, 5_000_000)
    if open_account:
        node.open_token_account(owner, USDC_MAINNET_MINT)
    return owner


def _requirement(wallet: dict, pay_to: str, *, amount_minor: int = 150_000, correlation_id: str = "") -> object:
    from core.wallet import usepod

    return usepod.parse_requirement({
        "correlation_id": correlation_id or f"upo_{uuid.uuid4().hex}", "provider": "usepod", "network": SOLANA_MAINNET, "asset": "USDC",
        "pay_to": pay_to, "amount_minor": amount_minor, "expires_at": time.time() + 300, "resource": "http://127.0.0.1:9/proxy/x402/v1/chat/completions",
        "payer_wallet": wallet["wallet_id"],
    })


def test_a_usdc_requirement_is_one_proposal_quote_approval_and_confirmed_principal(lane) -> None:
    from core.wallet import approval, lifecycle, quotes, usepod
    from core.wallet.store import connection

    node = lane
    wallet, pay_to = _payer(lane), _recipient(lane)
    requirement = _requirement(wallet, pay_to)
    minted = usepod.validate_x402_payment(requirement)
    assert (minted["state"], minted["asset"], minted["amount_minor"], minted["network"]) == ("pending_approval", "USDC", "150000", SOLANA_MAINNET), minted
    again = usepod.validate_x402_payment(requirement)
    assert (again["proposal_id"], again["duplicate"]) == (minted["proposal_id"], True), "a replay is the same operation"

    quote = quotes.mint_quote(minted["proposal_id"])
    fields = quote["fields"]
    assert (fields["asset"], fields["display_symbol"], fields["decimals"], fields["amount_human"]) == ("USDC", "USDC", 6, "0.15")
    assert (fields["token_transfer"], fields["gas_asset"], fields["fee_max_minor"]) == (True, "SOL", 5_000)
    assert (fields["principal_balance_minor"], fields["fee_balance_minor"]) == (1_000_000, 20_000_000)
    assert fields["recipient_token_account"] == node.token_account_address(pay_to, USDC_MAINNET_MINT)

    result = lifecycle.default_lifecycle().approve_pilot_transfer(minted["proposal_id"], quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert (node.token_balance(pay_to, USDC_MAINNET_MINT), node.token_balance(wallet["address"], USDC_MAINNET_MINT)) == (150_000, 850_000)
    assert node.sol_balance(wallet["address"]) == 20_000_000 - 5_000 and node.distinct_sends() == 1

    status = usepod.status_for(requirement.correlation_id, provider="usepod")
    assert status["operation"]["paid"] is True and status["operation"]["proof_eligible"] is True, status["operation"]
    assert status["transfer"]["tx_id"] == result["transfer"]["tx_id"]
    with connection() as conn:
        holds = [tuple(row) for row in conn.execute("SELECT proposal_id, asset, amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) ORDER BY proposal_id",
                                                     (minted["proposal_id"], f"{minted['proposal_id']}:fee")).fetchall()]
    assert holds == [(minted["proposal_id"], "USDC", 150_000, 0, "settled"), (f"{minted['proposal_id']}:fee", "SOL", 0, 5_000, "settled")], holds


def test_a_missing_recipient_token_account_is_refused_before_anything_is_signed(lane) -> None:
    from core.wallet import quotes, usepod
    from core.wallet.errors import WalletFault

    wallet, pay_to = _payer(lane), _recipient(lane, open_account=False)
    with pytest.raises(WalletFault) as caught:
        # refused typed at the proposal (the lane's own pre-simulation facts) or at the quote -- before any signature
        minted = usepod.validate_x402_payment(_requirement(wallet, pay_to))
        quotes.mint_quote(minted["proposal_id"])
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_recipient_refused", "recipient_token_account_missing")
    assert lane.distinct_sends() == 0


@pytest.mark.parametrize(("lamports", "usdc", "reason"), [
    (20_000_000, 149_999, "token_balance_below_amount"),
    (5_000 + 890_879, 1_000_000, "fee_balance_below_fee_and_rent"),
])
def test_a_short_token_or_fee_balance_is_refused_at_the_quote(lane, lamports: int, usdc: int, reason: str) -> None:
    from core.wallet import quotes, usepod
    from core.wallet.errors import WalletFault

    wallet, pay_to = _payer(lane, lamports=lamports, usdc=usdc), _recipient(lane)
    with pytest.raises(WalletFault) as caught:
        minted = usepod.validate_x402_payment(_requirement(wallet, pay_to))
        quotes.mint_quote(minted["proposal_id"])
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_insufficient_funds", reason), caught.value.context
    assert lane.distinct_sends() == 0


def test_a_mint_the_chain_describes_differently_is_refused(lane) -> None:
    from core.wallet import quotes, usepod
    from core.wallet.errors import WalletFault

    wallet, pay_to = _payer(lane), _recipient(lane)
    minted = usepod.validate_x402_payment(_requirement(wallet, pay_to))
    lane.mints[USDC_MAINNET_MINT]["decimals"] = 9
    with pytest.raises(WalletFault) as caught:
        quotes.mint_quote(minted["proposal_id"])
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_quote_mismatch", "mint_decimals_differ_from_registry")
