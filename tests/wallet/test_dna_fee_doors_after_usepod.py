"""The DNA service fee after the UsePod lane left VOOL (0.8): no payment carries a fee collection any more, and the
doors that stay must still refuse what they always refused. A model cannot move Mainnet tokens or mint a fee collection,
the fee rate is a constant, the collection threshold accepts only whole positive units of a known asset, and a
collection proposal has no quote or sheet of its own.

SIMULATION chain: the loopback Solana node answers as Mainnet and executes what it is sent. The treasury is a
DISPOSABLE FIXTURE RECIPIENT set through the operator override (a fresh key with its USDC account open). Nothing is
signed or sent.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode
from tests.wallet.test_wallet_limits_and_approval_ui_served import (
    PIN,  # the wallet suites' disposable fixture PIN, defined once
)

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"


@pytest.fixture
def lane(monkeypatch, tmp_path):
    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "eb.db"))
    # ONE file for the wallet store and the money store, as in a served process: the collection's claim and
    # settlement write the fee ledger on the wallet's own connection, which is atomic only in one file
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    from core import effect_budget, runtime_paths

    # configure_runtime_home is a PROCESS-GLOBAL pin that monkeypatch cannot undo; left
    # unset at teardown it outlived this fixture and every later test in the shard resolved
    # the runtime home into this dead tmp dir (measured: the permission restart test's
    # child read the session home while its parent's grant mirror landed here -- CI runs
    # 36265150283/36268704787/36272309227, shard tests(3), three identical failures). The
    # override STATE is captured per seam, not its resolved value, so a dynamic original
    # stays dynamic.
    _previous_home_override = runtime_paths._VOOL_HOME_OVERRIDE
    _previous_db_override = active_default_db_path()
    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR", "VOOL_DNA_FEE_COLLECT_MIN_ATOMIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from solders.keypair import Keypair

    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle
    from core.web.api import wallet_api

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    wallet_api.reset_caller_binding_for_tests()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    treasury = str(Keypair().pubkey())
    monkeypatch.setenv("VOOL_DNA_FEE_TREASURY_OWNER", treasury)
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: node.url}))
        environment.set_active_environment("mainnet")
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        node.fund_sol(treasury, 5_000_000)
        node.open_token_account(treasury, USDC_MAINNET_MINT)
        try:
            yield type("Lane", (), {"node": node, "treasury": treasury, "monkeypatch": monkeypatch})()
        finally:
            chains.invalidate_chain_identity()
            store_module.reset_default_store()
            wallet_api.reset_caller_binding_for_tests()
            effect_budget.reset_effect_budget_process_state()
            runtime_paths.configure_runtime_home(_previous_home_override)
            configure_default_db_path(_previous_db_override)


def _payer(node, *, lamports: int = 20_000_000, usdc: int = 5_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"t-{uuid.uuid4().hex}", label="fee payer")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund_sol(wallet["address"], lamports)
    node.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def test_a_model_cannot_propose_a_fee_collection_the_rate_is_a_constant_and_a_collection_has_no_sheet_of_its_own(lane) -> None:
    from core.wallet import dna_fees, proposals, quotes
    from core.wallet.errors import WalletFault

    wallet = _payer(lane.node)
    # the token lane on Mainnet stays closed to a skill's origin; a fee collection is minted only by the runtime
    with pytest.raises(WalletFault) as closed:
        proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=5, asset="USDC", origin=proposals.ORIGIN_SKILL, network=SOLANA_MAINNET)
    assert closed.value.context.get("reason") == "mainnet_tokens_move_only_for_service_payments"
    # the UsePod lane left VOOL in 0.8: its origin mints nothing at all any more
    with pytest.raises(WalletFault) as removed:
        proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=5, asset="USDC", origin=proposals.ORIGIN_USEPOD, network=SOLANA_MAINNET)
    assert removed.value.context.get("reason") == "payment_lane_removed"
    assert dna_fees.RATE_BPS == 10 and dna_fees.POLICY_ID == "dna_native_x402_fee:v1" and dna_fees.COLLECTION_COST_BOUND_BPS == 100
    with pytest.raises(WalletFault):
        dna_fees.set_collect_min_atomic("USDC", 0)
    with pytest.raises(WalletFault):
        dna_fees.set_collect_min_atomic("USDC", 1.5)  # type: ignore[arg-type]
    with pytest.raises(WalletFault):
        dna_fees.set_collect_min_atomic("DOGE", 1)
    # nobody volunteers to pay fees through a separate card: a collection proposal has no quote and no sheet of its own
    standalone = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=5, asset="USDC", origin=proposals.ORIGIN_DNA_FEE, network=SOLANA_MAINNET)
    with pytest.raises(WalletFault) as no_sheet:
        quotes.mint_quote(standalone.proposal_id)
    assert (no_sheet.value.code, no_sheet.value.context.get("reason")) == ("wallet_quote_unavailable", "dna_fee_collection_rides_its_payment")
    assert lane.node.distinct_sends() == 0


def test_a_model_usdc_proposal_on_mainnet_is_only_a_card_moves_nothing_without_the_pin_and_stays_inside_the_caps(lane) -> None:
    """Chat may now ASK to send USDC on Mainnet ("send 10 USDC to Maya", the owner's own transfer). That ask is a card and
    nothing more: a model origin still cannot move funds. Nothing is signed or sent until the owner's PIN, a wrong PIN
    moves nothing, no allowance door pays it, and the wallet's own caps refuse anything above them before any card."""
    from core.wallet import agent_pay, approval, lifecycle, limits, proposals, quotes
    from core.wallet.errors import WalletFault

    wallet = _payer(lane.node)
    limits.set_limits(wallet["wallet_id"], "USDC", limits.SpendLimits(per_tx_minor=3_000_000, daily_minor=3_000_000, per_destination_daily_minor=3_000_000))
    asked = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=1_000_000, asset="USDC", origin=proposals.ORIGIN_MODEL, network=SOLANA_MAINNET)
    prepared = lifecycle.default_lifecycle().prepare(asked.proposal_id)
    # only a card: it waits for the owner, and nothing has been signed or sent
    assert prepared.state == proposals.STATE_PENDING_APPROVAL
    assert lane.node.distinct_sends() == 0
    quote = quotes.mint_quote(asked.proposal_id)
    # no allowance door pays a model's proposal on the owner's wallet: it stays with the owner
    unridden = agent_pay.pay(asked.proposal_id, agent_id=agent_pay.ASSISTANT_AGENT)
    assert unridden["paid"] is False and unridden["decision"] != "pay", unridden
    # a wrong PIN signs nothing and moves nothing
    with pytest.raises(WalletFault):
        lifecycle.default_lifecycle().approve_pilot_transfer(asked.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover("000000"))
    assert lane.node.distinct_sends() == 0
    assert lane.node.token_balance(lane.treasury, USDC_MAINNET_MINT) == 0
    # above the wallet's own cap: refused before any card, nothing sent
    over = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=4_000_000, asset="USDC", origin=proposals.ORIGIN_MODEL, network=SOLANA_MAINNET)
    with pytest.raises(WalletFault) as capped:
        lifecycle.default_lifecycle().prepare(over.proposal_id)
    assert capped.value.code == "wallet_limit_exceeded", capped.value.to_dict()
    assert proposals.get_proposal(over.proposal_id).state != proposals.STATE_PENDING_APPROVAL
    assert lane.node.distinct_sends() == 0
    # only the owner's PIN moves it, exactly the amount on the card
    sent = lifecycle.default_lifecycle().approve_pilot_transfer(asked.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    assert sent["transfer"]["state"] == "confirmed", sent
    assert lane.node.token_balance(lane.treasury, USDC_MAINNET_MINT) == 1_000_000 and lane.node.distinct_sends() == 1
