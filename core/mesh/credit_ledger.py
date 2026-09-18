"""
core.mesh.credit_ledger
=======================
Per-node credit accounting for the VOOL local LLM mesh.

Each node tracks credits earned (by doing work for peers) and credits spent
(by dispatching work to peers).  Every entry carries a SHA-256 proof hash so
the full history is auditable and can be exported as a portable proof bundle
for trading or selling on external markets.

This ledger is *intentionally* decoupled from the swarm-level
``core.credit_ledger`` (which manages the hive dispatch escrow).  Here we
are accounting for *this node's* local mesh economy.

Persistence
-----------
SQLite via ``storage.db.get_connection()``.  The schema is created lazily on
first use; no separate migration file is required.

NULL credit units
-----------------
1 NULL credit = 1 unit of compute contributed to the mesh.  Credits earned
by a node can be spent to request work from other nodes, transferred to other
nodes, or exported as a proof bundle and redeemed elsewhere (e.g. as DNA x402
receipts).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_LOCK = Lock()
_SCHEMA_READY: set[str] = set()  # keyed by node_id to allow per-node DBs in tests


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mesh_credit_ledger (
    entry_id        TEXT NOT NULL PRIMARY KEY,
    node_id         TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('earn', 'spend')),
    task_id         TEXT NOT NULL,
    amount          REAL NOT NULL,
    recipient       TEXT,
    proof_hash      TEXT NOT NULL,
    metadata_json   TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcl_node    ON mesh_credit_ledger(node_id);
CREATE INDEX IF NOT EXISTS idx_mcl_task    ON mesh_credit_ledger(task_id);
CREATE INDEX IF NOT EXISTS idx_mcl_proof   ON mesh_credit_ledger(proof_hash);
"""


def _ensure_schema(conn: Any, node_id: str) -> None:
    if node_id in _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if node_id in _SCHEMA_READY:
            return
        for stmt in _CREATE_TABLE_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        _SCHEMA_READY.add(node_id)


# ---------------------------------------------------------------------------
# Entry dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditEntry:
    """A single immutable ledger entry."""

    entry_id: str
    node_id: str
    direction: str          # "earn" | "spend"
    task_id: str
    amount: float
    recipient: str          # empty for "earn" entries
    proof_hash: str
    metadata: dict[str, Any]
    created_at: str


# ---------------------------------------------------------------------------
# CreditLedger
# ---------------------------------------------------------------------------


class CreditLedger:
    """
    Tracks a single node's mesh credit balance, proof hashes, and full
    transaction history.

    Parameters
    ----------
    node_id:
        This node's peer ID.  Defaults to the signer's local peer ID.
    """

    def __init__(self, node_id: str | None = None) -> None:
        self._node_id: str = str(node_id or _resolve_local_node_id())

    # ------------------------------------------------------------------
    # Earning credits (work completed for peers)
    # ------------------------------------------------------------------

    def earn(
        self,
        task_id: str,
        amount: float,
        proof_hash: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CreditEntry:
        """
        Record credits earned after completing a peer's task.

        Parameters
        ----------
        task_id:    The mesh task that was completed.
        amount:     NULL credits earned (must be > 0).
        proof_hash: SHA-256(task_id + result + node_id) as produced by
                    :func:`core.mesh.task_router._compute_proof_hash`.
        metadata:   Optional extra data stored alongside the entry.

        Returns
        -------
        CreditEntry  The persisted entry.
        """
        if amount <= 0:
            raise ValueError(f"earn amount must be positive, got {amount!r}")
        _validate_proof_hash(proof_hash)

        entry_id = str(uuid.uuid4())
        created_at = _utcnow()
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))

        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            # Idempotent on (node_id, 'earn', proof_hash): re-settling the SAME verified proof
            # (a concurrent or replayed settlement) must credit exactly once, not twice. A
            # conditional insert does this atomically under the writer lock. No UNIQUE index is
            # added on purpose — a dirty DB that already holds a duplicate earn row must not
            # brick schema bootstrap; the guard lives in the write, not the schema.
            # NOTE: this assumes one settlement per verified proof_hash; the poster-side escrow
            # is expected to hold one escrow per (poster, task) so a single verified proof maps
            # to a single reward.
            cur = conn.execute(
                """
                INSERT INTO mesh_credit_ledger
                    (entry_id, node_id, direction, task_id, amount, recipient, proof_hash, metadata_json, created_at)
                SELECT ?, ?, 'earn', ?, ?, '', ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM mesh_credit_ledger
                    WHERE node_id = ? AND direction = 'earn' AND proof_hash = ?
                )
                """,
                (entry_id, self._node_id, task_id, amount, proof_hash, metadata_json, created_at,
                 self._node_id, proof_hash),
            )
            conn.commit()
            if cur.rowcount == 0:
                # Already settled this proof -> return the existing canonical entry unchanged.
                row = conn.execute(
                    """
                    SELECT entry_id, node_id, direction, task_id, amount,
                           recipient, proof_hash, metadata_json, created_at
                    FROM mesh_credit_ledger
                    WHERE node_id = ? AND direction = 'earn' AND proof_hash = ?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (self._node_id, proof_hash),
                ).fetchone()
                if row is not None:
                    logger.info(
                        "ledger:earn(idempotent) node=%s task=%s hash=%s already settled",
                        self._node_id, task_id, proof_hash[:12],
                    )
                    return _row_to_entry(row)
                # None-guard: a vanishingly rare race deleted the row between insert and read.
                # Fall back to a plain insert so a legitimate earn is never silently dropped.
                return self._write_entry(
                    direction="earn", task_id=task_id, amount=amount,
                    recipient="", proof_hash=proof_hash, metadata=metadata or {},
                )
        finally:
            conn.close()

        logger.info(
            "ledger:earn node=%s task=%s amount=%.4f hash=%s",
            self._node_id, task_id, amount, proof_hash[:12],
        )
        return CreditEntry(
            entry_id=entry_id,
            node_id=self._node_id,
            direction="earn",
            task_id=str(task_id),
            amount=amount,
            recipient="",
            proof_hash=proof_hash,
            metadata=metadata or {},
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # Spending credits (dispatching work to peers)
    # ------------------------------------------------------------------

    def spend(
        self,
        task_id: str,
        amount: float,
        recipient: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CreditEntry:
        """
        Record credits spent when dispatching work to another node.

        Raises
        ------
        ValueError  When *amount* exceeds the current balance.

        Parameters
        ----------
        task_id:   The mesh task being dispatched.
        amount:    NULL credits to pay the recipient.
        recipient: Node ID of the peer being paid.
        metadata:  Optional extra data.

        Returns
        -------
        CreditEntry  The persisted entry.
        """
        if amount <= 0:
            raise ValueError(f"spend amount must be positive, got {amount!r}")

        # Derive a deterministic proof hash for the spend event.
        proof_hash = _spend_proof_hash(task_id, self._node_id, recipient, amount)
        entry_id = str(uuid.uuid4())
        created_at = _utcnow()
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))

        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            # Atomic check-and-spend: insert the spend row ONLY when the current balance still
            # covers it, with the balance recomputed INSIDE the single write-locked statement.
            # Two concurrent spends serialize on SQLite's writer lock, so the second sees the
            # first's debit and cannot overdraw — closing the check-then-act TOCTOU where two
            # reads of the same stale balance each authorized a full spend (balance -> negative).
            cur = conn.execute(
                """
                INSERT INTO mesh_credit_ledger
                    (entry_id, node_id, direction, task_id, amount, recipient, proof_hash, metadata_json, created_at)
                SELECT ?, ?, 'spend', ?, ?, ?, ?, ?, ?
                WHERE (
                    SELECT COALESCE(SUM(CASE WHEN direction='earn'  THEN amount ELSE 0 END), 0.0)
                         - COALESCE(SUM(CASE WHEN direction='spend' THEN amount ELSE 0 END), 0.0)
                    FROM mesh_credit_ledger WHERE node_id = ?
                ) >= ?
                """,
                (entry_id, self._node_id, task_id, amount, str(recipient or ""), proof_hash,
                 metadata_json, created_at, self._node_id, amount),
            )
            conn.commit()
            refused = cur.rowcount == 0
        finally:
            conn.close()

        if refused:
            balance = self.balance()
            raise ValueError(
                f"insufficient credits: balance={balance:.4f} requested={amount:.4f}"
            )

        logger.info(
            "ledger:spend node=%s task=%s amount=%.4f to=%s hash=%s",
            self._node_id, task_id, amount, recipient, proof_hash[:12],
        )
        return CreditEntry(
            entry_id=entry_id,
            node_id=self._node_id,
            direction="spend",
            task_id=str(task_id),
            amount=amount,
            recipient=str(recipient or ""),
            proof_hash=proof_hash,
            metadata=metadata or {},
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # Balance & history
    # ------------------------------------------------------------------

    def balance(self) -> float:
        """
        Net credit balance: ``sum(earn) - sum(spend)``.

        Returns 0.0 when no entries exist yet.
        """
        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN direction='earn'  THEN amount ELSE 0 END), 0.0) AS earned,
                    COALESCE(SUM(CASE WHEN direction='spend' THEN amount ELSE 0 END), 0.0) AS spent
                FROM mesh_credit_ledger
                WHERE node_id = ?
                """,
                (self._node_id,),
            ).fetchone()
            if row:
                return float(row[0]) - float(row[1])
            return 0.0
        finally:
            conn.close()

    @property
    def earned_credits(self) -> float:
        """Total credits earned across all tasks."""
        return self._aggregate("earn")

    @property
    def spent_credits(self) -> float:
        """Total credits spent across all tasks."""
        return self._aggregate("spend")

    @property
    def proof_hashes(self) -> list[str]:
        """Ordered list of all proof hashes (newest first)."""
        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            rows = conn.execute(
                "SELECT proof_hash FROM mesh_credit_ledger WHERE node_id = ? ORDER BY created_at DESC",
                (self._node_id,),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def history(self, *, limit: int = 50) -> list[CreditEntry]:
        """Return up to *limit* entries, newest first."""
        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            rows = conn.execute(
                """
                SELECT entry_id, node_id, direction, task_id, amount,
                       recipient, proof_hash, metadata_json, created_at
                FROM mesh_credit_ledger
                WHERE node_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (self._node_id, max(1, min(int(limit), 500))),
            ).fetchall()
            return [_row_to_entry(row) for row in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Proof bundle export
    # ------------------------------------------------------------------

    def export_proof_bundle(self) -> list[dict[str, Any]]:
        """
        Export all anchored proofs as a portable list of dicts.

        The bundle can be:
        * Submitted to a DNA x402 receipt endpoint for on-chain anchoring.
        * Shared with other nodes as a trust / reputation signal.
        * Traded or sold as a "work record" on the NULL credit market.

        Each element contains::

            {
                "entry_id":   str,
                "node_id":    str,
                "direction":  "earn" | "spend",
                "task_id":    str,
                "amount":     float,
                "recipient":  str,
                "proof_hash": str,          # SHA-256 anchor
                "metadata":   dict,
                "created_at": str,          # ISO-8601 UTC
                "bundle_hash": str,         # SHA-256 of this entry's business fields
                "signer_peer_id": str,      # hex Ed25519 pubkey that signed (or "")
                "signature": str,           # base64 Ed25519 signature over the same bytes
            }

        Verify a bundle with :func:`verify_proof_bundle`.

        Returns
        -------
        list[dict]  All entries, oldest first (deterministic ordering).
        """
        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            rows = conn.execute(
                """
                SELECT entry_id, node_id, direction, task_id, amount,
                       recipient, proof_hash, metadata_json, created_at
                FROM mesh_credit_ledger
                WHERE node_id = ?
                ORDER BY created_at ASC
                """,
                (self._node_id,),
            ).fetchall()
        finally:
            conn.close()

        bundle: list[dict[str, Any]] = []
        for row in rows:
            entry = _row_to_entry(row)
            d: dict[str, Any] = {
                "entry_id": entry.entry_id,
                "node_id": entry.node_id,
                "direction": entry.direction,
                "task_id": entry.task_id,
                "amount": entry.amount,
                "recipient": entry.recipient,
                "proof_hash": entry.proof_hash,
                "metadata": entry.metadata,
                "created_at": entry.created_at,
            }
            # Add a self-describing bundle hash for tamper detection.
            canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
            d["bundle_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            # Ed25519 settlement signature over the SAME canonical bytes: a peer can confirm
            # the entry was produced by this node's key, not merely that it is internally
            # consistent (which the hash alone gives). Defensive — an unavailable signer
            # leaves the entry unsigned rather than failing the whole export.
            signer_peer_id = ""
            signature = ""
            try:
                from network.signer import get_local_peer_id, sign

                signature = sign(canonical.encode("utf-8"))
                signer_peer_id = get_local_peer_id()
            except Exception:
                signer_peer_id = ""
                signature = ""
            d["signer_peer_id"] = signer_peer_id
            d["signature"] = signature
            bundle.append(d)

        logger.info(
            "ledger:export_proof_bundle node=%s entries=%d balance=%.4f",
            self._node_id, len(bundle), self.balance(),
        )
        return bundle

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _aggregate(self, direction: str) -> float:
        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0.0) FROM mesh_credit_ledger WHERE node_id = ? AND direction = ?",
                (self._node_id, direction),
            ).fetchone()
            return float(row[0]) if row else 0.0
        finally:
            conn.close()

    def _write_entry(
        self,
        *,
        direction: str,
        task_id: str,
        amount: float,
        recipient: str,
        proof_hash: str,
        metadata: dict[str, Any],
    ) -> CreditEntry:
        entry_id = str(uuid.uuid4())
        created_at = _utcnow()
        metadata_json = json.dumps(metadata, sort_keys=True, separators=(",", ":"))

        conn = _get_conn()
        try:
            _ensure_schema(conn, self._node_id)
            conn.execute(
                """
                INSERT INTO mesh_credit_ledger
                    (entry_id, node_id, direction, task_id, amount,
                     recipient, proof_hash, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entry_id, self._node_id, direction, task_id,
                 amount, recipient, proof_hash, metadata_json, created_at),
            )
            conn.commit()
        finally:
            conn.close()

        return CreditEntry(
            entry_id=entry_id,
            node_id=self._node_id,
            direction=direction,
            task_id=str(task_id),
            amount=amount,
            recipient=recipient,
            proof_hash=proof_hash,
            metadata=metadata,
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_proof_hash(h: str) -> None:
    if not h or len(h) != 64 or not all(c in "0123456789abcdefABCDEF" for c in h):
        raise ValueError(f"proof_hash must be a 64-char hex string, got {h!r}")


def _spend_proof_hash(task_id: str, node_id: str, recipient: str, amount: float) -> str:
    """Deterministic proof hash for a spend event (no external result needed)."""
    raw = f"{task_id}:{node_id}:{recipient}:{amount:.8f}:{time.time_ns()}".encode()
    return hashlib.sha256(raw).hexdigest()


def _row_to_entry(row: Any) -> CreditEntry:
    metadata: dict[str, Any] = {}
    try:
        raw = row[7]
        if raw:
            metadata = json.loads(raw)
    except Exception:
        pass
    return CreditEntry(
        entry_id=str(row[0]),
        node_id=str(row[1]),
        direction=str(row[2]),
        task_id=str(row[3]),
        amount=float(row[4]),
        recipient=str(row[5] or ""),
        proof_hash=str(row[6]),
        metadata=metadata,
        created_at=str(row[8]),
    )


def _get_conn() -> Any:
    from storage.db import get_connection
    return get_connection()


def _resolve_local_node_id() -> str:
    try:
        from network.signer import get_local_peer_id
        return get_local_peer_id()
    except Exception:
        pass
    import socket
    hostname = socket.gethostname()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, hostname))


def verify_proof_bundle(bundle: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify a proof bundle exported by :meth:`MeshCreditLedger.export_proof_bundle`.

    For each entry: recompute ``bundle_hash`` from its business fields and reject a
    tampered one; then, if the entry carries a ``signature``, verify it against
    ``signer_peer_id`` over the same canonical bytes so a re-hashed forgery from another
    key does not pass. Entries with no signature are reported as ``unsigned`` (not
    tampered) — the export leaves them unsigned only when no signer was available.

    Returns ``{ok, checked, tampered, unsigned}``; ``ok`` is True only when nothing is
    tampered (unsigned entries are allowed but surfaced).
    """
    tampered: list[str] = []
    unsigned: list[str] = []
    for entry in bundle:
        business = {
            k: v for k, v in entry.items()
            if k not in ("bundle_hash", "signer_peer_id", "signature")
        }
        canonical = json.dumps(business, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        entry_id = str(entry.get("entry_id") or "")
        if str(entry.get("bundle_hash") or "") != expected:
            tampered.append(entry_id)
            continue
        signature = str(entry.get("signature") or "")
        if not signature:
            unsigned.append(entry_id)
            continue
        try:
            from network.signer import verify

            if not verify(canonical.encode("utf-8"), signature, str(entry.get("signer_peer_id") or "")):
                tampered.append(entry_id)
        except Exception:
            tampered.append(entry_id)
    return {
        "ok": not tampered,
        "checked": len(bundle),
        "tampered": tampered,
        "unsigned": unsigned,
    }
