"""
core/x402/client.py
===================
Minimal x402 payment client for the VOOL compute rental mesh.

Implements the HTTP 402 payment-required flow for agent-to-agent USDC
settlements on Solana using the canonical x402 "exact" scheme against the
PayAI facilitator (https://facilitator.payai.network).

Modes
-----
stub    — default; deterministic fake receipt, no Solana calls. Safe for CI
          and offline development.
devnet / mainnet — RETIRED as payers in this module. Every signing or
          settling door here (`pay`, `pay_requirements`, `_live_pay`,
          `_load_payer_keypair`, `wallet_signer`, `build_solana_x402_payment`)
          answers with the typed, receipt-backed refusal
          `wallet_legacy_surface_retired`. The one money authority is
          `core.wallet` (proposal -> simulation -> reservation -> operator
          approval -> signing -> broadcast -> receipt); its x402 flow is
          `core.wallet.x402.fetch_paid_resource` / `retry_paid_resource`.
          What remains here is read-only: requirement building, receipt
          hashing, the `_get_latest_blockhash` door read, and config.

Protocol (canonical x402 "exact" on Solana)
-------------------------------------------
1. Payment requirements (scheme/network/maxAmountRequired/payTo/asset/feePayer)
   come from the resource server's HTTP 402 (or are built directly here).
2. The client builds a v0 transaction — ComputeBudget limit+price, then an SPL
   TransferChecked of `asset` from the payer's ATA to payTo's ATA — with the
   facilitator's sponsored `feePayer` as the fee payer, and PARTIALLY signs it
   (the payer slot only; the facilitator fills the feePayer signature at settle).
3. The base64 transaction is wrapped as the x402 payment payload and POSTed to
   the facilitator /verify, then /settle.
4. /settle returns the on-chain Solana transaction signature → X402Receipt.
5. receipt.receipt_hash is included in WorkProof.signature for anchoring.

Usage
-----
    from core.x402.client import X402Client, X402Config, X402Mode

    cfg = X402Config(mode=X402Mode.STUB)
    client = X402Client(cfg)
    receipt = client.pay(amount_usdc=0.001, recipient_wallet="<pubkey>",
                         session_id="sess-abc123")
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# USDC constants
# ---------------------------------------------------------------------------

USDC_DECIMALS = 6                                     # USDC has 6 decimal places
USDC_MINT_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_MINT_DEVNET  = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

def usdc_to_atomic(amount_usdc: float) -> int:
    """Convert a USDC amount to atomic units (6 decimals), rounding to nearest.

    Truncating (``int(amount_usdc * 10**6)``) undercounts: 0.0000019 USDC would
    floor to 1 atomic unit instead of 2, and float artefacts like
    1.999999... * 10**6 would drop a whole unit. Rounding keeps the on-chain
    transfer amount consistent with the rounded values sent to the facilitator
    /quote and /receipt endpoints (both ``round(amount_usdc, 6)``).
    """
    return round(amount_usdc * (10 ** USDC_DECIMALS))


# PayAI facilitator — canonical x402 facilitator. ONE host for every network
# (the network is a field in the payment, not a subdomain). The old
# devnet.facilitator.payai.network subdomain does NOT resolve.
PAYAI_FACILITATOR = "https://facilitator.payai.network"

# Sponsored Solana fee payer the facilitator advertises at GET /supported. It is
# fetched live at runtime; this is only the fallback if that fetch fails.
PAYAI_SOLANA_FEEPAYER = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"

# Canonical Solana program ids (universal — safe as literals, not "our" ids).
TOKEN_PROGRAM_ID            = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
MEMO_PROGRAM_ID             = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"  # SPL Memo v2

# A memo on the settlement tx makes it self-describing on-chain (explorers render
# it). The SPL Memo program costs ~445 CU/byte, and the facilitator caps the
# sponsored compute limit (60k accepted, 80k rejected — see compute_unit_limit
# below), so the memo length is bounded: 120 bytes (~53k CU) fits under a 60k cap
# alongside the transfer. Longer memos are truncated rather than risk a sim failure.
_MEMO_MAX_BYTES = 120

# Solana RPC endpoints. Mainnet uses the keyless publicnode endpoint — the
# api.mainnet-beta endpoint 403s on requests carrying an Origin header and is
# banned for this stack; this constant is what effective_rpc broadcasts against.
SOLANA_RPC_MAINNET = "https://solana-rpc.publicnode.com"
SOLANA_RPC_DEVNET  = "https://api.devnet.solana.com"

# ---------------------------------------------------------------------------
# Parad0x / dna-x402 on-chain program IDs (mainnet-beta)
# Multisig upgrade authority: 9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5
# Source: configs/mainnet.commercial.json
# ---------------------------------------------------------------------------

# Core receipt / ZK programs (2026-05-29 batch, under Squads multisig)
RECEIPT_ANCHOR_PROGRAM_MAINNET   = "6HSRGivdYR5D7yTDy1TFMCM8h3LzXxRtKU1RA3RnCMRN"
DARK_PROOF_GATE_LITE_MAINNET     = "PmSCTuehX1MYxf8GNsGsUZySYTtqWAtuTt3N2xZLpw2"
DARK_BN254_GATE_MAINNET          = "GCptvBYF8S6eVYoh15B7WAESc54FUHCpN1Ui6aHeQYZd"
DARK_SEMAPHORE_MAINNET           = "Ev7HEFhhKTXk6kS2Y6ssbUcK9C7E6yZ589jJNjUrQV5p"
DARK_SECP256R1_VAULT_MAINNET     = "3hbbtjeSrTVYXq6eRwjeofDe2DCPh3n8cfN6kZcQfewi"
DARK_SECP256K1_AUTH_MAINNET      = "AqwBbV13AoczhoELwP8oxT3nDqB6MsLWXauNzHkssZ9B"
NULL_TOKEN_HOOK_MAINNET          = "14ivonrNRmaMbJMQkGdHVVTcqZYhNvchULWxveazhW2g"
NULL_LOTTERY_MAINNET             = "3t5c2Trk4SFK7hvKVjsmmC2xQtasFnK9pJQRdwPHqxbG"
NULL_MINT_GATE_MAINNET           = "5jduvBZggszFeE7uxxNrvZAp8pJxzqtgzBGqg12fKhC1"

# NULL ecosystem
NULL_REGISTRAR_MAINNET           = "NXgQhepFpDCu935H1D4g34g59ZYbo1jR4tBCZWhV8Np"
DNA_X402_MAIN_MAINNET            = "6HSRGivdYR5D7yTDy1TFMCM8h3LzXxRtKU1RA3RnCMRN"

# $NULL token mint (Token-2022)
NULL_TOKEN_MINT_MAINNET          = "8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump"

# Squads multisig that controls the 2026-05-29 batch
PARAD0X_UPGRADE_AUTHORITY        = "9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class X402Mode(str, Enum):
    STUB    = "stub"    # no real Solana calls; deterministic fake receipt
    DEVNET  = "devnet"  # real devnet USDC payment
    MAINNET = "mainnet" # real mainnet USDC payment


@dataclass
class X402Config:
    """
    Configuration for the x402 payment client.

    Parameters
    ----------
    mode : X402Mode
        STUB (default) | DEVNET | MAINNET
    keypair_path : str | None
        Path to a Solana JSON keypair file (required for DEVNET / MAINNET).
    facilitator_url : str | None
        Override the default PayAI facilitator URL.
    rpc_url : str | None
        Override the default Solana RPC URL.
    max_fee_usdc : float
        Refuse payments above this amount (safety guard). Default 1.0 USDC.
    """
    mode: X402Mode = X402Mode.STUB
    keypair_path: Optional[str] = None
    facilitator_url: Optional[str] = None
    rpc_url: Optional[str] = None
    asset_mint: Optional[str] = None       # override the asset (default: cluster USDC)
    asset_decimals: int = USDC_DECIMALS    # decimals of the asset being transferred
    memo: str = ""                         # optional on-chain memo for client-built payments
    max_fee_usdc: float = 1.0

    @property
    def effective_rpc(self) -> str:
        if self.rpc_url:
            return self.rpc_url
        return SOLANA_RPC_DEVNET if self.mode == X402Mode.DEVNET else SOLANA_RPC_MAINNET

    @property
    def effective_facilitator(self) -> str:
        # Canonical x402 uses one facilitator host for every network.
        return self.facilitator_url or PAYAI_FACILITATOR

    @property
    def network_name(self) -> str:
        """x402 network id for this mode ("solana-devnet" / "solana")."""
        return "solana-devnet" if self.mode == X402Mode.DEVNET else "solana"

    @property
    def effective_usdc_mint(self) -> str:
        return USDC_MINT_DEVNET if self.mode == X402Mode.DEVNET else USDC_MINT_MAINNET

    @property
    def effective_asset(self) -> str:
        """The SPL mint to transfer (asset_mint override, else cluster USDC)."""
        return self.asset_mint or self.effective_usdc_mint


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class X402Quote:
    """Payment details returned by the x402 endpoint / derived from listing."""
    amount_usdc: float
    recipient_wallet: str       # node operator's Solana wallet (base58)
    facilitator_url: str
    usdc_mint: str
    quote_hash: str             # sha256 of canonical quote fields
    expires_at: float           # unix timestamp


@dataclass
class X402Receipt:
    """
    Signed proof that a payment was made.

    In stub mode the `payment_tx` and `facilitator_sig` are placeholders.
    In devnet/mainnet mode they are real Solana tx signatures and ECDSA sigs
    from the facilitator.
    """
    session_id: str
    payment_tx: str             # Solana tx signature (or "stub-{uuid}")
    amount_usdc: float
    recipient_wallet: str
    facilitator_sig: str        # facilitator's signature over the receipt
    timestamp: float
    mode: str                   # "stub" | "devnet" | "mainnet"
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.receipt_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """SHA-256 over canonical receipt fields (deterministic, order-fixed)."""
        canonical = json.dumps({
            "session_id":       self.session_id,
            "payment_tx":       self.payment_tx,
            "amount_usdc":      round(self.amount_usdc, 8),
            "recipient_wallet": self.recipient_wallet,
            "timestamp":        round(self.timestamp, 3),
            "mode":             self.mode,
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "session_id":       self.session_id,
            "payment_tx":       self.payment_tx,
            "amount_usdc":      self.amount_usdc,
            "recipient_wallet": self.recipient_wallet,
            "facilitator_sig":  self.facilitator_sig,
            "timestamp":        self.timestamp,
            "mode":             self.mode,
            "receipt_hash":     self.receipt_hash,
        }


# ---------------------------------------------------------------------------
# X402Client
# ---------------------------------------------------------------------------

class X402Client:
    """
    Minimal x402 payment client.

    The public API is a single method: `pay()`. Internally it dispatches
    to either the stub path or the live Solana path depending on config.mode.
    """

    def __init__(self, config: Optional[X402Config] = None, *, signer=None) -> None:
        self.config: X402Config = config or X402Config(mode=X402Mode.STUB)
        # Optional signer (anything exposing .pubkey() -> solders Pubkey and
        # .sign_message(bytes) -> solders Signature, e.g. a wrapped VoolWallet).
        # When None, the live path loads a solders Keypair from config.keypair_path.
        self._signer = signer

    def _resolve_signer(self):
        """RETIRED: no payer signer is ever resolved here (the file-keypair path and the injected-signer path are gone)."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("x402.client._load_payer_keypair")

    def pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: Optional[str] = None,
    ) -> X402Receipt:
        """
        Execute an x402 USDC payment.

        Parameters
        ----------
        amount_usdc : float
            Amount to pay in USDC (e.g. 0.001 for 1 milli-USDC).
        recipient_wallet : str
            Solana wallet address (base58) of the node operator being paid.
        session_id : str | None
            Rental session ID to bind to this receipt. Auto-generated if None.

        Returns
        -------
        X402Receipt
            A receipt with a canonical receipt_hash ready for WorkProof.

        Raises
        ------
        ValueError
            If amount_usdc exceeds config.max_fee_usdc (safety guard).
        X402PaymentError
            If the live payment fails (devnet/mainnet modes only).
        """
        if amount_usdc <= 0:
            raise ValueError(f"amount_usdc must be > 0, got {amount_usdc}")
        if amount_usdc > self.config.max_fee_usdc:
            raise ValueError(
                f"amount_usdc={amount_usdc:.6f} exceeds max_fee_usdc="
                f"{self.config.max_fee_usdc:.6f} safety limit"
            )

        sid = session_id or f"sess-{uuid.uuid4().hex[:12]}"

        if self.config.mode == X402Mode.STUB:
            return self._stub_pay(amount_usdc, recipient_wallet, sid)
        else:
            return self._live_pay(amount_usdc, recipient_wallet, sid)

    def quote(
        self,
        amount_usdc: float,
        recipient_wallet: str,
    ) -> X402Quote:
        """Build a payment quote (no Solana call in stub mode)."""
        canonical = json.dumps({
            "amount_usdc":      round(amount_usdc, 8),
            "recipient_wallet": recipient_wallet,
            "facilitator":      self.config.effective_facilitator,
            "mint":             self.config.effective_usdc_mint,
        }, sort_keys=True)
        quote_hash = hashlib.sha256(canonical.encode()).hexdigest()

        return X402Quote(
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            facilitator_url=self.config.effective_facilitator,
            usdc_mint=self.config.effective_usdc_mint,
            quote_hash=quote_hash,
            expires_at=time.time() + 300,  # 5-minute quote TTL
        )

    # ------------------------------------------------------------------
    # Stub path — no Solana calls
    # ------------------------------------------------------------------

    def _stub_pay(
        self,
        amount_usdc: float,
        recipient_wallet: str,
        session_id: str,
    ) -> X402Receipt:
        """Return a deterministic fake receipt. Used in STUB mode."""
        fake_tx = f"stub-tx-{uuid.uuid4().hex}"
        return X402Receipt(
            session_id=session_id,
            payment_tx=fake_tx,
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            facilitator_sig=f"stub-fac-sig-{uuid.uuid4().hex[:16]}",
            timestamp=time.time(),
            mode="stub",
        )

    # ------------------------------------------------------------------
    # Live path — real Solana USDC transfer
    # ------------------------------------------------------------------

    def _live_pay(self, amount_usdc: float, recipient_wallet: str, session_id: str) -> X402Receipt:
        """RETIRED live payment: the only payment door is core.wallet (proposal -> approval -> broadcast)."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("x402.client.pay")

    def _load_payer_keypair(self):
        """RETIRED: raw keypair files are never loaded; keys live sealed in core.wallet.custody only."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("x402.client._load_payer_keypair")

    def _facilitator_fee_payer(self) -> str:
        """The sponsored Solana fee payer for this network, from GET /supported.

        Cached per client. Falls back to the advertised constant if the fetch
        fails so a transient /supported hiccup never blocks a payment.
        """
        cached = getattr(self, "_fee_payer_cache", None)
        if cached:
            return cached
        fee_payer = PAYAI_SOLANA_FEEPAYER
        try:
            from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote_url

            r = open_remote_url(
                f"{self.config.effective_facilitator}/supported",
                headers={"User-Agent": "vool-x402/1.0"}, timeout=15,
            )
            if r.status == 200:
                for kind in (json.loads(r.read().decode("utf-8")).get("kinds") or []):
                    if (kind.get("scheme") == "exact"
                            and kind.get("network") == self.config.network_name):
                        fp = (kind.get("extra") or {}).get("feePayer")
                        if fp:
                            fee_payer = fp
                            break
        except RemoteFetchRefusedError:
            # A vetoed turn must not quietly fall back to the advertised constant
            # and keep spending: refusal propagates and fails closed.
            raise
        except Exception:
            pass
        self._fee_payer_cache = fee_payer
        return fee_payer

    def _build_payment_requirements(
        self, amount_usdc: float, recipient_wallet: str, session_id: str,
    ) -> dict:
        """The x402 paymentRequirements a resource server would issue in its 402."""
        extra: dict = {"feePayer": self._facilitator_fee_payer()}
        if self.config.memo:
            extra["memo"] = self.config.memo
        return {
            "scheme":            "exact",
            "network":           self.config.network_name,
            "maxAmountRequired": str(usdc_to_atomic(amount_usdc)),  # atomic units
            "resource":          f"https://vool.local/x402/{session_id}",
            "description":       f"vool x402 settlement {session_id}",
            "mimeType":          "application/json",
            "payTo":             recipient_wallet,
            "maxTimeoutSeconds": 120,
            "asset":             self.config.effective_asset,
            "extra":             extra,
        }

    def pay_requirements(self, payment_requirements: dict, session_id: Optional[str] = None) -> X402Receipt:
        """RETIRED settlement path: a 402's requirements are paid through core.wallet.x402 (fetch -> proposal -> approval -> retry)."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("x402.client.pay_requirements")

    def _solana_pay(self, amount_usdc: float, recipient_wallet: str, session_id: str) -> X402Receipt:
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("x402.client.pay")


def _get_latest_blockhash(rpc_url: str) -> str:
    """Read-only: the latest blockhash from an RPC (kept for quote/receipt helpers)."""
    from core.remote_fetch_policy import open_remote_url

    r = open_remote_url(
        rpc_url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash",
              "params": [{"commitment": "finalized"}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "vool-x402/1.0"},
        method="POST",
        timeout=15,
    )
    if r.status >= 400:
        raise RuntimeError(f"Solana RPC blockhash fetch failed: HTTP {r.status}")
    return json.loads(r.read().decode("utf-8"))["result"]["value"]["blockhash"]


def build_solana_x402_payment(payer, payment_requirements: dict, rpc_url: str, *, decimals: int = 6, compute_unit_limit: int = 60_000, compute_unit_price: int = 1, memo: str = "") -> bytes:
    """RETIRED: transaction building + signing for payments lives in core.wallet.lifecycle only."""
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("x402.client.build_solana_x402_payment")


def wallet_signer(wallet) -> Any:
    """RETIRED signer shim: no external object can be wrapped into a payment signer."""
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("x402.client.wallet_signer")


class X402PaymentError(RuntimeError):
    """Raised when a live x402 payment fails."""
