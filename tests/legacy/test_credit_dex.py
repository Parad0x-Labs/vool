"""The simulated credit DEX purchase is a retired money surface.

The seller-side order book (``core.credit_dex``: offer generation, the cheapest-fill matcher) is
untouched and still pinned here. The buyer-side purchase on ``core.dna_payment_bridge`` -- which
used to award local credits against a simulated USDC settlement -- is retired into a typed,
receipt-backed :class:`WalletFault`: no credits move, no offer is consumed, and the feature flag
that used to open the lane opens nothing.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.credit_dex import check_and_generate_credit_offer, global_credit_market
from core.credit_ledger import award_credits, get_credit_balance
from core.wallet.authority import LEGACY_RETIRED
from core.wallet.errors import WalletFault
from storage.db import get_connection
from storage.migrations import run_migrations

PEER = "abcd000011112222"
BUYER_WALLET = "simulated_solana_wallet_buyer_address_long_enough"


def _zero_ledger_for_testing():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM compute_credit_ledger")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _seed_market():
    global_credit_market._offers = []  # clear queue
    global_credit_market._offer_map = {}
    global_credit_market.push({
        "offer_id": "offer_whale", "seller_peer_id": "deadbeef11112222",
        "credits_available": 10000, "usdc_per_credit": 0.10, "seller_wallet_address": "wallet_whale",
    })
    global_credit_market.push({
        "offer_id": "offer_cheap", "seller_peer_id": "1111222233334444",
        "credits_available": 50, "usdc_per_credit": 0.04, "seller_wallet_address": "wallet_cheap",
    })
    global_credit_market.push({
        "offer_id": "offer_mid", "seller_peer_id": "5555666677778888",
        "credits_available": 500, "usdc_per_credit": 0.06, "seller_wallet_address": "wallet_mid",
    })


def _offer_snapshot() -> list[tuple[str, int]]:
    return sorted((str(o.offer_id), int(o.credits_available)) for o in global_credit_market._offers)


def _assert_refusal(exc: WalletFault) -> None:
    from core.faults.recorder import fault_by_id
    from core.security_events.catalog import SEC_WALLET_LEGACY_SURFACE_RETIRED
    from core.security_events.store import list_security_events

    assert exc.code == LEGACY_RETIRED
    assert exc.fault_id.startswith("fault-"), exc.to_dict()
    assert exc.user_message
    assert exc.context["surface"].startswith("dna_payment_bridge.")
    record = fault_by_id(exc.fault_id)
    assert record is not None and record.code == LEGACY_RETIRED
    observed = [e for e in list_security_events(limit=50) if e.fault_id == exc.fault_id]
    assert observed and observed[0].sec_code == SEC_WALLET_LEGACY_SURFACE_RETIRED


def test_seller_side_offer_generation_still_works() -> None:
    with patch("network.signer.get_local_peer_id", return_value=PEER):
        run_migrations()
        _zero_ledger_for_testing()
        award_credits(PEER, 1500, "farmed_from_tasks")

        offer_dict = check_and_generate_credit_offer(auto_sell_threshold=1000, usdc_ask_price=0.08)

    assert offer_dict is not None
    assert offer_dict["credits_available"] == 1400
    assert offer_dict["usdc_per_credit"] == 0.08
    assert get_credit_balance(PEER) == 1500.0  # an offer is a listing, not a transfer


@pytest.mark.parametrize("purchases_enabled", [True, False])
def test_dex_purchase_refuses_typed_and_moves_no_credits(purchases_enabled: bool) -> None:
    with patch("network.signer.get_local_peer_id", return_value=PEER), patch(
        "core.credit_ledger.credit_purchases_enabled", return_value=purchases_enabled
    ):
        run_migrations()
        _zero_ledger_for_testing()
        award_credits(PEER, 1500, "farmed_from_tasks")
        _seed_market()
        offers_before = _offer_snapshot()

        from core.dna_payment_bridge import dna_bridge

        dna_bridge.link_wallet(BUYER_WALLET)
        with pytest.raises(WalletFault) as info:
            dna_bridge.purchase_credits_from_dex(300, PEER)

        _assert_refusal(info.value)
        # Nothing was minted, matched or consumed -- with the flag on OR off.
        assert get_credit_balance(PEER) == 1500.0
        assert _offer_snapshot() == offers_before
        assert len(global_credit_market._offers) == 3


def test_direct_credit_purchase_refuses_typed_and_debits_nothing() -> None:
    with patch("network.signer.get_local_peer_id", return_value=PEER):
        run_migrations()
        _zero_ledger_for_testing()
        award_credits(PEER, 10, "farmed_from_tasks")

        from core.dna_payment_bridge import dna_bridge

        dna_bridge.link_wallet(BUYER_WALLET)
        with pytest.raises(WalletFault) as info:
            dna_bridge.purchase_credits(1.0, PEER)

        _assert_refusal(info.value)
        assert info.value.context["surface"] == "dna_payment_bridge.purchase_credits"
        assert get_credit_balance(PEER) == 10.0
