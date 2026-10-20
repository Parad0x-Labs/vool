"""On a Mainnet row a connected or watch-only account receives and shows its balance only: the UI offers it no send.

SYNTHETIC API FACTS in a real browser (the harness of test_wallet_home_fragment.py: the actual wallet fragment in the
actual chat page, every wallet answer intercepted; no daemon, no chain, no funds). Proven: Wallet home's Send is disabled
for a connected account on a Mainnet row and says why, and Activity offers to review, not approve, its waiting payment, while the same account kind on a test-network row keeps Send; a
payment card for a connected account on a Mainnet row asks for no credential, offers no signer, says why and keeps Reject,
and says real money, not test funds; the same card on a test-network row keeps its signer.
"""
from __future__ import annotations

import time

import pytest

from tests.wallet.test_wallet_home_fragment import (
    SOL,
    Facts,
    _account,
    _balance,
    _open_panel,
    _page,
    _row,
    _status,
    browser,
)

pytestmark = [pytest.mark.safety]

CONNECTED_KEY = "ConnectedSolanaAddress111111111111111111111"
DESTINATION = "DestinationSolanaAddress11111111111111111111"
VIEW_ONLY = {"code": "wallet_send_needs_vool_wallet",
             "message": "This account cannot send from VOOL on this network: only a VOOL Wallet created on this device sends here."}


def _mainnet(row):
    row.update({"environment": "mainnet", "badge": "MAINNET", "environment_label": "Mainnet", "value_note": "Real funds", "display_name": "Solana"})
    return row


def _facts(*, mainnet: bool, pending=()):
    account = _account("w-connected", SOL, "external_signer", CONNECTED_KEY)
    account["testnet"] = not mainnet
    row = _row(SOL, accounts=[account])
    status = _status(enabled=True, accounts=[account], rows=[_mainnet(row) if mainnet else row], pending=list(pending))
    status.update({"custody_mode": "external_signer", "public_key": CONNECTED_KEY, "mainnet_enabled": mainnet})
    facts = Facts(status)
    facts.balances["w-connected"] = _balance("w-connected")
    return facts


def _pending(*, mainnet: bool):
    return {"proposal_id": "pay-usepod", "amount_minor": 1000, "asset": "USDC", "destination": DESTINATION, "origin": "usepod", "memo": "",
            "created_at": time.time(), "network": SOL, "state": "pending_approval", "meaning": None, "pilot_transfer": False, "purpose": None,
            "open_quote_id": "", "quote_expires_at": 0, "recipient": {}, "payment_channel": None,
            "mainnet": mainnet, "send_unavailable": dict(VIEW_ONLY) if mainnet else None}


@pytest.mark.parametrize("mainnet", [True, False], ids=["mainnet", "testnet"])
def test_wallet_home_offers_a_connected_account_send_only_off_mainnet(browser, mainnet):
    facts = _facts(mainnet=mainnet, pending=[_pending(mainnet=mainnet)])
    page, errors = _page(browser, facts)
    try:
        _open_panel(page)
        page.wait_for_selector("#vwHomeSend")
        page.wait_for_function("document.getElementById('vwHomeBalance') && document.getElementById('vwHomeBalance').textContent.indexOf('wallet') >= 0")
        send = page.locator("#vwHomeSend")
        balance = page.locator("#vwHomeBalance").inner_text()
        page.wait_for_selector("#vwHomeBody .vw-wh-review")
        review = page.locator("#vwHomeBody .vw-wh-review").inner_text()
        if mainnet:
            assert review == "Review in chat", "Activity never offers to approve a payment this account cannot send"
            assert send.is_disabled()
            assert "connected wallet receives and shows its balance" in (send.get_attribute("title") or "")
            assert "receive and view only" in balance and "signing stays in your wallet" not in balance
        else:
            assert review == "Approve in chat"
            assert send.is_enabled()
            assert "signing stays in your wallet" in balance
        assert not [r for r in facts.requests if r.startswith("POST /api/wallet")], "nothing was proposed or signed"
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize("mainnet", [True, False], ids=["mainnet", "testnet"])
def test_a_payment_card_for_a_connected_account_on_mainnet_offers_no_signer(browser, mainnet):
    facts = _facts(mainnet=mainnet, pending=[_pending(mainnet=mainnet)])
    page, errors = _page(browser, facts)
    try:
        card = page.locator('.vw-card[data-proposal="pay-usepod"]')
        card.wait_for(timeout=15_000)
        text = card.inner_text()
        assert card.locator(".vw-reject").count() == 1, "the owner can always reject"
        if mainnet:
            assert card.locator(".vw-pin").count() == 0 and card.locator(".vw-approve").count() == 0
            assert card.locator(".vw-sign-external").count() == 0 and card.locator(".vw-sign-evm").count() == 0
            assert card.locator("[data-send-unavailable=wallet_send_needs_vool_wallet]").count() == 1
            assert "only a VOOL Wallet created on this device sends here" in text
            assert "real money" in text and "testnet funds" not in text
        else:
            assert card.locator(".vw-sign-external").count() == 1
            assert "testnet funds, not real money" in text
        assert not [r for r in facts.requests if r.startswith("POST /api/wallet")], "nothing was approved or signed"
        assert not errors, errors
    finally:
        page.close()
