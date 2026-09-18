from __future__ import annotations

import heapq
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import audit_logger
from network import signer


def local_peer_id() -> str:
    """Resolve the local peer id lazily so signer reloads and test patches take effect."""
    return signer.get_local_peer_id()


@dataclass(order=True)
class _PrioritizedCreditOffer:
    # Sorts automatically by cheapest price first, then by earliest timestamp
    usdc_per_credit: float
    timestamp_iso: str
    offer_id: str = field(compare=False)
    seller_peer_id: str = field(compare=False)
    credits_available: int = field(compare=False)
    seller_wallet_address: str = field(compare=False)


class CreditMarketQueue:
    """
    Phase 29: Maintains a thread-safe priority queue of all known CreditOffers on the mesh.
    Buyers query this queue to find the cheapest Compute Credits automatically.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._offers: list[_PrioritizedCreditOffer] = []
        # Store dict mapping offer_id -> _PrioritizedCreditOffer for quick lookups/updates
        self._offer_map: dict[str, _PrioritizedCreditOffer] = {}

    def push(self, offer: dict[str, Any]) -> None:
        """
        Pushes a new CREDIT_OFFER into the market. Overwrites if offer_id already exists.
        """
        offer_id = str(offer.get("offer_id", ""))
        seller_id = str(offer.get("seller_peer_id", ""))
        if not offer_id or not seller_id:
            return

        with self._lock:
            if offer_id in self._offer_map:
                # To keep it simple, remove the old one (O(N) search) and re-insert
                self._offers = [x for x in self._offers if x.offer_id != offer_id]
                heapq.heapify(self._offers)

            p_offer = _PrioritizedCreditOffer(
                usdc_per_credit=float(offer.get("usdc_per_credit", 999.0)),
                timestamp_iso=str(
                    offer.get("timestamp_iso")
                    or offer.get("timestamp")
                    or datetime.now(timezone.utc).isoformat()
                ),
                offer_id=offer_id,
                seller_peer_id=seller_id,
                credits_available=int(offer.get("credits_available", 0)),
                seller_wallet_address=str(offer.get("seller_wallet_address", "")),
            )

            heapq.heappush(self._offers, p_offer)
            self._offer_map[offer_id] = p_offer

    def remove_offer(self, offer_id: str) -> None:
        """Removes an exhausted or canceled order."""
        with self._lock:
            if offer_id in self._offer_map:
                del self._offer_map[offer_id]
                self._offers = [x for x in self._offers if x.offer_id != offer_id]
                heapq.heapify(self._offers)

    def get_cheapest_offers(self, total_credits_needed: int) -> list[dict[str, Any]]:
        """
        Finds the combination of the cheapest offers required to fulfill the amount.
        Returns a list of dicts that the DNA Payment Bridge will use to construct the payments.
        """
        matched = []
        credits_remaining = total_credits_needed

        with self._lock:
            # We don't want to actually pop them off the heap until verified purchase
            # So we iterate through the sorted list
            sorted_offers = sorted(self._offers)
            for p_offer in sorted_offers:
                if credits_remaining <= 0:
                    break

                # Exclude self from buying your own credits
                if p_offer.seller_peer_id == local_peer_id():
                    continue

                take_amount = min(credits_remaining, p_offer.credits_available)
                matched.append({
                    "offer_id": p_offer.offer_id,
                    "seller_peer_id": p_offer.seller_peer_id,
                    "credits_taking": take_amount,
                    "usdc_price": p_offer.usdc_per_credit,
                    "total_usdc_cost": take_amount * p_offer.usdc_per_credit,
                    "seller_wallet_address": p_offer.seller_wallet_address,
                })
                credits_remaining -= take_amount

        return matched


global_credit_market = CreditMarketQueue()


# ---------------------------------------------------------------------------
# Miner Sales Logic
# ---------------------------------------------------------------------------

def check_and_generate_credit_offer(auto_sell_threshold: int = 1000, usdc_ask_price: float = 0.05) -> dict[str, Any] | None:
    """
    Called periodically by the background loop.
    Checks local ledger balance; if above threshold, returns an offer dict to broadcast.
    """
    from core.credit_ledger import get_credit_balance

    balance = get_credit_balance(local_peer_id())
    if balance >= auto_sell_threshold:
        # Leave a tiny buffer so user isn't immediately 0'd out
        credits_to_sell = int(max(0, math.floor(balance - 100)))
        if credits_to_sell <= 0:
            return None

        offer_id = f"creditoffer_{local_peer_id()[:8]}_{int(datetime.now(timezone.utc).timestamp())}"

        offer = {
            "offer_id": offer_id,
            "seller_peer_id": local_peer_id(),
            "credits_available": credits_to_sell,
            "usdc_per_credit": usdc_ask_price,
            "seller_wallet_address": "simulated_solana_wallet_address", # Hardcoded until Phase 29 full wallet binding
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        audit_logger.log(
            "credit_offer_generated",
            target_id=offer_id,
            target_type="dex",
            details={"sell_amount": credits_to_sell, "ask_price": usdc_ask_price}
        )
        return offer
    return None
