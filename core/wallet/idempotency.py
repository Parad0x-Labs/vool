"""Duplicate-payment protection: one idempotency key = one proposal = at most one broadcast."""
from __future__ import annotations

import hashlib
from typing import Any

from core.wallet.store import connection


def content_digest(*, wallet_id: str, destination: str, amount_minor: int, asset: str, memo: str, network: str) -> str:
    canonical = "|".join([str(wallet_id), str(destination), str(int(amount_minor)), str(asset).upper(), str(memo or ""), str(network)])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def existing_for_key(wallet_id: str, idempotency_key: str) -> dict[str, Any] | None:
    """The proposal already minted under this key, or None. The guard a sabotage disables."""
    key = str(idempotency_key or "").strip()
    if not key:
        return None
    with connection() as conn:
        row = conn.execute("SELECT proposal_id, content_digest, state FROM wallet_proposals WHERE wallet_id = ? AND idempotency_key = ?", (str(wallet_id), key)).fetchone()
    return {"proposal_id": row[0], "content_digest": row[1], "state": row[2]} if row else None
