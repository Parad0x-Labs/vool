import os
from typing import Any

from core.dna_wallet_manager import DNAWalletManager

# Rate for purchasing credits (1 USDC = 1000 Compute Credits)
USDC_TO_CREDIT_RATE = 1000.0

class DNAPaymentBridge:
    """
    Simulates the DNA x402 payment bridge.
    Instead of staking massive deposits, new users can spin up a node,
    authorize their Solana wallet, and buy compute credits on-demand.
    """
    def __init__(self):
        self.wallet_address = os.environ.get("VOOL_SOLANA_WALLET", None)
        self.bridge_active = bool(self.wallet_address)
        self.wallets = DNAWalletManager()

    def link_wallet(self, solana_address: str) -> bool:
        """Links a Solana address to this local mesh node."""
        # Hardcoded validation mock for v1 shoestring
        if len(solana_address) < 32:
            return False

        self.wallet_address = solana_address
        self.bridge_active = True
        return True

    def purchase_credits(self, usdc_amount: float, local_peer_id: str) -> dict[str, Any]:
        """RETIRED simulated purchase: typed, receipt-backed refusal; the wallet lane owns every payment."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_payment_bridge.purchase_credits")

    def purchase_credits_from_dex(self, compute_credits_needed: int, local_peer_id: str) -> dict[str, Any]:
        """RETIRED simulated purchase: typed, receipt-backed refusal; the wallet lane owns every payment."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_payment_bridge.purchase_credits_from_dex")


#: Module singleton kept for importers; every purchase path on it is a typed refusal.
dna_bridge = DNAPaymentBridge()
