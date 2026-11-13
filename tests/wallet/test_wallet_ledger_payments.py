"""Payments to paid sites (x402 and MPP through pay-kit) and USDC sends from a wallet that signs on a Ledger.

The same pay-kit lane, quote, caps, approval, claim and settlement as every VOOL wallet; only the signature comes from the
Ledger (tests/wallet/_soft_ledger.py: the Solana app's APDUs over the real HID framing). The site may pay the network fee,
so the Ledger signs a transaction whose first signer is someone else, as the real Solana app allows."""
from __future__ import annotations

import uuid

import pytest

from core.wallet import ledger
from tests.wallet._rig_paykit import MAINNET_CAIP2, PAID_BODY, ScriptedMppResource, ScriptedPayKitResource
from tests.wallet._simulated_solana import USDC_MAINNET_MINT
from tests.wallet._soft_ledger import SoftLedger
from tests.wallet.test_crypto_pilot_dispatch import PIN, SOLANA_DEVNET, _hold, _sol_key, node, pilot
from tests.wallet.test_wallet_paykit_pilot import (
    AMOUNT,
    SOLANA_MAINNET,
    USDC_AMOUNT,
    _approve,
    _park,
    _quote,
    _site_account,
    capped,
    mainnet,
)

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")
pytestmark = [pytest.mark.safety]


@pytest.fixture
def soft():
    device = SoftLedger("A")
    ledger.set_transport_source_for_tests(lambda: [device])
    yield device
    ledger.set_transport_source_for_tests(None)


def _ledger_wallet(network: str) -> dict:
    from core.wallet import pilot_custody

    return pilot_custody.add_ledger_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN,
                                           creation_key=f"l-{uuid.uuid4().hex}", label="Ledger")


@pytest.mark.parametrize("kind", ["x402-site-pays-fee", "mpp-wallet-pays-fee"])
def test_a_paid_site_is_paid_from_a_ledger_wallet_once_and_settled(capped, soft, kind):
    from core.wallet import paykit_x402, proposals

    chain = capped
    wallet = _ledger_wallet(SOLANA_DEVNET)
    payee = _sol_key()
    resource = ScriptedPayKitResource(chain, amount_minor=AMOUNT, pay_to=payee) if kind.startswith("x402") else \
        ScriptedMppResource(chain, amount_minor=AMOUNT, recipient=payee, sponsored=False)
    with resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        assert quote["fields"]["signs_on_ledger"] is True
        result = _approve(parked.proposal_id, quote)
        assert result["transfer"]["state"] == "confirmed" and result["paykit"]["delivered"] is True, result
        assert len(soft.shown) == 1 and len(resource.paid_requests) == 1, "one transfer shown on the Ledger, one paid request"
        assert paykit_x402.delivered_body(parked.proposal_id) == PAID_BODY
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_CONFIRMED
        assert _hold(parked.proposal_id)[2] == "settled"


def test_a_paid_site_rejected_on_the_ledger_pays_nothing_and_frees_the_hold(capped, soft):
    from core.wallet.errors import WalletFault

    chain = capped
    wallet = _ledger_wallet(SOLANA_DEVNET)
    soft.approve = False
    with ScriptedPayKitResource(chain, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        with pytest.raises(WalletFault) as refused:
            _approve(parked.proposal_id, quote)
        assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_signing_unavailable", "ledger_refused_on_ledger")
        assert resource.paid_requests == [] and resource.landed == []
        assert _hold(parked.proposal_id)[2] == "released"


@pytest.mark.parametrize("protocol", ["x402", "mpp"])
def test_a_mainnet_usdc_payment_from_a_ledger_wallet_moves_exactly_the_approved_amount(mainnet, soft, protocol):
    chain = mainnet
    wallet = _ledger_wallet(SOLANA_MAINNET)
    chain.fund_sol(wallet["address"], 20_000_000)
    chain.fund_token(wallet["address"], USDC_MAINNET_MINT, 1_000_000)
    site = _site_account(chain)
    if protocol == "x402":
        resource = ScriptedPayKitResource(chain, amount_minor=USDC_AMOUNT, asset=USDC_MAINNET_MINT, pay_to=site, network=MAINNET_CAIP2)
    else:
        resource = ScriptedMppResource(chain, amount_minor=USDC_AMOUNT, currency=USDC_MAINNET_MINT, recipient=site, network="mainnet", decimals=6)
    chain.fund_sol(str(resource.fee_payer.pubkey()), 5_000_000)
    with resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        assert (quote["fields"]["asset"], quote["fields"]["amount_minor"]) == ("USDC", USDC_AMOUNT)
        result = _approve(parked.proposal_id, quote)
        assert result["transfer"]["state"] == "confirmed" and result["paykit"]["delivered"] is True, result
        assert (chain.token_balance(site, USDC_MAINNET_MINT), chain.token_balance(wallet["address"], USDC_MAINNET_MINT)) == (USDC_AMOUNT, 1_000_000 - USDC_AMOUNT)
        assert len(soft.shown) == 1
