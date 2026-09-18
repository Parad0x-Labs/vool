from __future__ import annotations

import json
from typing import Any

from storage.db import get_connection


def list_wallet_ledger(profile_id: str = "default", *, limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT entry_id, profile_id, direction, asset_symbol, amount,
                   initiated_by, approval_mode, reference_id, metadata_json, created_at
            FROM dna_wallet_ledger
            WHERE profile_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(profile_id or "default"), max(1, min(int(limit), 500))),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        metadata_raw = str(row["metadata_json"] or "{}")
        try:
            metadata = json.loads(metadata_raw)
        except Exception:
            metadata = {"raw": metadata_raw}
        out.append(
            {
                "entry_id": str(row["entry_id"]),
                "profile_id": str(row["profile_id"]),
                "direction": str(row["direction"]),
                "asset_symbol": str(row["asset_symbol"]),
                "amount": float(row["amount"] or 0.0),
                "initiated_by": str(row["initiated_by"]),
                "approval_mode": str(row["approval_mode"]),
                "reference_id": str(row["reference_id"] or ""),
                "metadata": metadata,
                "created_at": str(row["created_at"]),
            }
        )
    return out
