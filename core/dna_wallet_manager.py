from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection
from storage.migrations import run_migrations


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_secret(secret: str, salt_hex: str) -> str:
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        str(secret).encode("utf-8"),
        bytes.fromhex(salt_hex),
        200_000,
    )
    return raw.hex()


@dataclass(frozen=True)
class WalletStatus:
    profile_id: str
    hot_wallet_address: str | None
    cold_wallet_address: str | None
    hot_balance_usdc: float
    cold_balance_usdc: float
    hot_auto_spend_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "hot_wallet_address": self.hot_wallet_address,
            "cold_wallet_address": self.cold_wallet_address,
            "hot_balance_usdc": round(self.hot_balance_usdc, 6),
            "cold_balance_usdc": round(self.cold_balance_usdc, 6),
            "hot_auto_spend_enabled": self.hot_auto_spend_enabled,
        }


class DNAWalletManager:
    def __init__(self, *, profile_id: str = "default") -> None:
        self.profile_id = str(profile_id or "default").strip() or "default"

    def _ensure_schema(self) -> None:
        run_migrations()

    def configure_wallets(
        self,
        *,
        hot_wallet_address: str,
        cold_wallet_address: str,
        cold_secret: str,
        initial_hot_usdc: float = 0.0,
        initial_cold_usdc: float = 0.0,
        hot_auto_spend_enabled: bool = True,
    ) -> WalletStatus:
        """RETIRED simulated custody mutation: the only wallet is core.wallet; typed, receipt-backed refusal."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_wallet_manager.configure_wallets")

    def get_status(self) -> WalletStatus | None:
        self._ensure_schema()
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT profile_id, hot_wallet_address, cold_wallet_address,
                       hot_balance_usdc, cold_balance_usdc, hot_auto_spend_enabled
                FROM dna_wallet_profiles
                WHERE profile_id = ?
                LIMIT 1
                """,
                (self.profile_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return WalletStatus(
            profile_id=str(row["profile_id"]),
            hot_wallet_address=str(row["hot_wallet_address"] or "").strip() or None,
            cold_wallet_address=str(row["cold_wallet_address"] or "").strip() or None,
            hot_balance_usdc=float(row["hot_balance_usdc"] or 0.0),
            cold_balance_usdc=float(row["cold_balance_usdc"] or 0.0),
            hot_auto_spend_enabled=bool(int(row["hot_auto_spend_enabled"] or 0)),
        )

    def hot_wallet_ready(self) -> bool:
        status = self.get_status()
        return bool(status and status.hot_wallet_address)

    def top_up_hot_from_cold(self, amount_usdc: float, *, cold_secret: str, initiated_by: str = "user") -> WalletStatus:
        """RETIRED simulated custody mutation: the only wallet is core.wallet; typed, receipt-backed refusal."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_wallet_manager.top_up_hot_from_cold")

    def move_hot_to_cold(self, amount_usdc: float, *, cold_secret: str, initiated_by: str = "user") -> WalletStatus:
        """RETIRED simulated custody mutation: the only wallet is core.wallet; typed, receipt-backed refusal."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_wallet_manager.move_hot_to_cold")

    def deposit_hot(self, amount_usdc: float, *, initiated_by: str = "user", reference_id: str | None = None) -> WalletStatus:
        """RETIRED simulated custody mutation: the only wallet is core.wallet; typed, receipt-backed refusal."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_wallet_manager.deposit_hot")

    def consume_hot_for_credit_purchase(
        self,
        amount_usdc: float,
        *,
        local_peer_id: str,
        reference_id: str,
        initiated_by: str = "agent",
    ) -> WalletStatus:
        """RETIRED simulated custody mutation: the only wallet is core.wallet; typed, receipt-backed refusal."""
        from core.wallet.authority import refuse_legacy

        raise refuse_legacy("dna_wallet_manager.consume_hot_for_credit_purchase")

    def _require_cold_approval(self, cold_secret: str) -> None:
        self._ensure_schema()
        secret = str(cold_secret or "")
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT cold_secret_salt, cold_secret_hash
                FROM dna_wallet_security
                WHERE profile_id = ?
                LIMIT 1
                """,
                (self.profile_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise PermissionError("Cold-wallet approval is not configured.")
        expected = str(row["cold_secret_hash"] or "")
        salt = str(row["cold_secret_salt"] or "")
        if not expected or not salt:
            raise PermissionError("Cold-wallet approval data is incomplete.")
        actual = _hash_secret(secret, salt)
        if not secrets.compare_digest(actual, expected):
            raise PermissionError("Cold-wallet approval failed.")

    def _insert_ledger_entry(
        self,
        conn,
        *,
        direction: str,
        amount: float,
        initiated_by: str,
        approval_mode: str,
        reference_id: str | None,
        metadata: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO dna_wallet_ledger (
                entry_id, profile_id, direction, asset_symbol, amount,
                initiated_by, approval_mode, reference_id, metadata_json, created_at
            ) VALUES (?, ?, ?, 'USDC', ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.profile_id,
                direction,
                float(amount),
                str(initiated_by or "agent"),
                str(approval_mode or "manual"),
                str(reference_id or ""),
                json.dumps(metadata, sort_keys=True),
                _utcnow(),
            ),
        )
