"""Converting coins inside the owner's own Solana wallet (core.wallet.swaps): "we only have SOL, convert some to USDC".

ONE proposal holding at most the shown maximum of the input coin -> a sheet with the provider's fresh price (pay at
most, get at least, slippage, price impact, route, network cost) -> PIN -> the provider's transaction is built for
exactly that price and PROVEN before signing (one signer, allowed programs only, the simulated outcome inside the shown
bounds, no token delegate) -> one send -> the chain's own balances prove it paid no more and received no less.

A provider that misbehaves (delivers less, takes more, adds a transfer to someone else, asks for a second signer, calls
an unknown program, hands out a delegate, overpays priority) is refused with nothing signed or sent.

SIMULATION chain and provider: the loopback Solana node answers as Mainnet and executes what it is sent, with a
stand-in venue at Jupiter's program id (tests/wallet/_swap_rig.py); the provider API is a loopback stand-in in
Jupiter's shapes. The wallet's own custody signs with its real sealed key.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.contacts import protected_changes
from tests.wallet._simulated_solana import MAINNET_GENESIS, TOKEN_ACCOUNT_SIZE, USDC_MAINNET_MINT, rent_minimum
from tests.wallet._swap_rig import WSOL_MINT, FakeJupiter, SwapNode

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
PIN = "482913"
FEE = 5_000
ACCOUNT_RENT = rent_minimum(TOKEN_ACCOUNT_SIZE)
#: 1 SOL = 80 USDC
SOL_TO_USDC = (2, 25)
USDC_TO_SOL = (25, 2)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle, swaps

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    protected_changes.reset()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with SwapNode(genesis_hash=MAINNET_GENESIS) as node, FakeJupiter(node, rates={(WSOL_MINT, USDC_MAINNET_MINT): SOL_TO_USDC, (USDC_MAINNET_MINT, WSOL_MINT): USDC_TO_SOL}) as jup:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: node.url}))
        monkeypatch.setattr(swaps, "API_BASE", jup.base)
        environment.set_active_environment("mainnet")
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        yield node, jup
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    protected_changes.reset()


def _wallet(node, *, lamports: int = 400_000_000, usdc: int = 0) -> dict:
    from core.wallet import limits, pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"t-{uuid.uuid4().hex}", label="Main")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    # the owner's own caps, high enough for these amounts (the default pilot caps are 0.1 SOL a payment)
    limits.set_limits(wallet["wallet_id"], "SOL", limits.SpendLimits(per_tx_minor=1_000_000_000, daily_minor=5_000_000_000, per_destination_daily_minor=5_000_000_000))
    limits.set_limits(wallet["wallet_id"], "USDC", limits.SpendLimits(per_tx_minor=1_000_000_000, daily_minor=5_000_000_000, per_destination_daily_minor=5_000_000_000))
    node.fund_sol(wallet["address"], lamports)
    if usdc:
        node.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def _propose(**kwargs):
    from core.wallet import lifecycle, swaps

    parked = swaps.propose_swap(**kwargs)
    prepared = lifecycle.default_lifecycle().prepare(parked["proposal"].proposal_id)
    return prepared


def _quote(proposal_id: str) -> dict:
    from core.wallet import quotes

    return quotes.mint_quote(proposal_id)


def _approve(proposal_id: str, quote: dict, pin: str = PIN) -> dict:
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(pin))


def _holds(proposal_id: str) -> list[tuple]:
    from core.wallet.store import connection

    with connection() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT asset, amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) ORDER BY proposal_id",
            (proposal_id, f"{proposal_id}:fee")).fetchall()]


def test_get_10_usdc_from_sol_shows_the_bounds_signs_once_and_the_chain_proves_it(rig) -> None:
    node, jup = rig
    wallet = _wallet(node)
    proposal = _propose(from_asset="SOL", to_asset="USDC", amount="10", side="out")
    assert proposal.state == "pending_approval" and proposal.asset == "SOL" and proposal.destination == wallet["address"]
    assert node.distinct_sends() == 0, "preparing a swap signs and sends nothing"

    quote = _quote(proposal.proposal_id)
    fields, swap = quote["fields"], quote["fields"]["swap"]
    # 10 USDC at 80 USDC/SOL = 0.125 SOL expected; at most 0.125 * 1.005 with 0.5% slippage
    assert (swap["from_symbol"], swap["to_symbol"], swap["min_out_human"], swap["expected_in_human"], swap["max_in_human"]) == ("SOL", "USDC", "10", "0.125", "0.125625")
    assert (swap["slippage_text"], swap["route"], swap["provider"]) == ("0.5%", ["SimPool"], "Jupiter")
    assert fields["swap_opens_output_account"] is True and fields["fee_parts"]["account_rent_minor"] == ACCOUNT_RENT
    review = fields["review"]
    assert review["headline"] == "Convert SOL to 10 USDC", review["headline"]
    assert "at most 0.125625 SOL" in review["amount_line"] and "at least 10 USDC" in review["amount_line"], review["amount_line"]
    assert review["primary_action"] == "Approve and convert"
    assert any("Jupiter" in line[1] for line in review["details"]), review["details"]

    result = _approve(proposal.proposal_id, quote)
    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert node.token_balance(wallet["address"], USDC_MAINNET_MINT) == 10_000_000
    assert node.sol_balance(wallet["address"]) == 400_000_000 - 125_000_000 - FEE - ACCOUNT_RENT
    assert node.distinct_sends() == 1 and ("POST", "/swap/v1/swap") in jup.calls
    facts = result["swap"]
    assert (facts["paid_human"], facts["received_human"]) == ("0.125", "10"), facts
    pid = proposal.proposal_id
    held = _holds(pid)
    assert all(state == "settled" for *_rest, state in held), held


def test_convert_usdc_to_sol_exact_in(rig) -> None:
    node, _jup = rig
    wallet = _wallet(node, lamports=20_000_000, usdc=50_000_000)
    proposal = _propose(from_asset="USDC", to_asset="SOL", amount="8", side="in")
    assert (proposal.asset, int(proposal.amount_minor)) == ("USDC", 8_000_000), "converting an exact 8 USDC holds exactly 8 USDC"
    quote = _quote(proposal.proposal_id)
    assert quote["fields"]["swap"]["min_out_human"] == "0.0995", quote["fields"]["swap"]
    result = _approve(proposal.proposal_id, quote)
    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert node.token_balance(wallet["address"], USDC_MAINNET_MINT) == 42_000_000
    assert node.sol_balance(wallet["address"]) == 20_000_000 + 100_000_000 - FEE


@pytest.mark.parametrize("knob, value, reason", [
    ("deliver_less", 1, "swap_check:receives_less_than_shown"),
    ("take_more", 5_000_000, "swap_check:pays_more_than_shown"),
    ("extra_transfer", 1_000_000, "swap_check:system_instruction"),
    ("unknown_program", True, "swap_check:program_not_allowed"),
    ("second_signer", True, "swap_check:signers_or_fee_payer"),
    ("unit_price", 10_000_000, "swap_check:priority_fee_above_cap"),
])
def test_a_provider_that_turns_hostile_at_approval_is_refused_with_nothing_signed_or_sent(rig, knob, value, reason) -> None:
    from core.wallet.errors import WalletFault

    node, jup = rig
    wallet = _wallet(node)
    proposal = _propose(from_asset="SOL", to_asset="USDC", amount="10", side="out")
    quote = _quote(proposal.proposal_id)
    jup.knobs[knob] = value
    with pytest.raises(WalletFault) as refused:
        _approve(proposal.proposal_id, quote)
    assert refused.value.context.get("reason") == reason, refused.value.context
    assert "did not sign" in (refused.value.user_message or "") or "Nothing was converted" in (refused.value.user_message or ""), refused.value.user_message
    assert node.distinct_sends() == 0
    assert node.sol_balance(wallet["address"]) == 400_000_000 and node.token_balance(wallet["address"], USDC_MAINNET_MINT) == 0
    assert all(state != "reserved" for *_rest, state in _holds(proposal.proposal_id)), "a refusal before the claim holds nothing"


def test_a_token_delegate_handed_out_by_the_provider_is_refused(rig) -> None:
    from core.wallet.errors import WalletFault

    node, jup = rig
    _wallet(node, lamports=20_000_000, usdc=50_000_000)
    proposal = _propose(from_asset="USDC", to_asset="SOL", amount="8", side="in")
    quote = _quote(proposal.proposal_id)
    jup.knobs["approve_delegate"] = True
    with pytest.raises(WalletFault) as refused:
        _approve(proposal.proposal_id, quote)
    assert refused.value.context.get("reason") == "swap_check:token_instruction"
    assert node.distinct_sends() == 0


def test_a_hostile_provider_is_caught_already_while_preparing(rig) -> None:
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    node, jup = rig
    _wallet(node)
    jup.knobs["deliver_less"] = 1
    with pytest.raises(WalletFault) as refused:
        _propose(from_asset="SOL", to_asset="USDC", amount="10", side="out")
    assert refused.value.context.get("reason") == "swap_check:receives_less_than_shown"
    assert all(p.state == proposals.STATE_FAILED for p in proposals.list_proposals()), "the refused swap is ended, never left approvable"


def test_a_quote_that_names_another_coin_or_moves_the_price_too_much_is_refused(rig) -> None:
    from core.wallet.errors import WalletFault

    node, jup = rig
    _wallet(node)
    jup.knobs["quote_lies_about_mint"] = True
    with pytest.raises(WalletFault) as lied:
        _propose(from_asset="SOL", to_asset="USDC", amount="10", side="out")
    assert str(lied.value.context.get("reason")).startswith("swap_quote_malformed")
    jup.knobs.clear()
    jup.price_impact_pct = "0.05"  # 5 %
    with pytest.raises(WalletFault) as impact:
        _propose(from_asset="SOL", to_asset="USDC", amount="10", side="out")
    assert impact.value.context.get("reason") == "swap_price_impact_too_high" and "5.00%" in impact.value.user_message


def test_a_wrong_pin_converts_nothing(rig) -> None:
    from core.wallet.errors import WalletFault

    node, _jup = rig
    wallet = _wallet(node)
    proposal = _propose(from_asset="SOL", to_asset="USDC", amount="10", side="out")
    quote = _quote(proposal.proposal_id)
    with pytest.raises(WalletFault):
        _approve(proposal.proposal_id, quote, pin="000000")
    assert node.distinct_sends() == 0 and node.sol_balance(wallet["address"]) == 400_000_000


def test_swaps_are_mainnet_solana_only_and_between_listed_coins(rig) -> None:
    from core.wallet import swaps
    from core.wallet.errors import WalletFault

    node, _jup = rig
    _wallet(node)
    with pytest.raises(WalletFault) as unknown:
        swaps.propose_swap(from_asset="SOL", to_asset="BONK", amount="1", side="in")
    assert unknown.value.context.get("reason") == "coin_not_swappable" and "SOL, USDC, USDT" in unknown.value.user_message
    with pytest.raises(WalletFault) as same:
        swaps.propose_swap(from_asset="SOL", to_asset="SOL", amount="1", side="in")
    assert same.value.context.get("reason") == "swap_same_coin"
