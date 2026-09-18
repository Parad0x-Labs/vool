"""VOOL wallet: default-disabled, watch-only by default, testnet-only, owner-approved payments.

Public surface for proposers (skills, plugins, models): :mod:`core.wallet.proposals` and
:mod:`core.wallet.status`. Custody and signing are owner-local doors and are deliberately not
re-exported here.
"""
from __future__ import annotations

from core.wallet.config import ALLOWED_NETWORKS, NETWORK_SOLANA_DEVNET, mainnet_enabled, network_allowed, wallet_enabled
from core.wallet.errors import WalletFault
from core.wallet.proposals import TransactionProposal, get_proposal, proposal_events, propose_transaction
from core.wallet.status import wallet_status
from core.wallet.x402 import X402Request, detect_x402

__all__ = [
    "ALLOWED_NETWORKS", "NETWORK_SOLANA_DEVNET", "TransactionProposal", "WalletFault", "X402Request", "detect_x402", "get_proposal",
    "mainnet_enabled", "network_allowed", "proposal_events", "propose_transaction", "wallet_enabled", "wallet_status",
]
