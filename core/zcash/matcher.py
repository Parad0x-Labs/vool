"""Which invoices are paid: decided only from a fresh, successful watch-only sync.

An invoice is PAID only when received shielded notes whose memo is exactly the invoice's memo add up to at least
the requested amount, each with at least N confirmations. Every other outcome is an explicit state, and any doubt
about the data (no sync yet, a failed or stale sync) is UNKNOWN -- never paid, never "still unpaid".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.zcash.invoices import Invoice, format_zec
from core.zcash.watch import ReceivedNote

PAID = "paid"
OVERPAID = "overpaid"
UNDERPAID = "underpaid"
UNCONFIRMED = "unconfirmed"
UNPAID = "unpaid"
UNKNOWN = "unknown"
SETTLED = frozenset({PAID, OVERPAID})


@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reason: str  # "" when fresh; never_synced | sync_failed | stale_sync
    tip: int | None
    last_sync_at: float | None


@dataclass(frozen=True)
class InvoiceStatus:
    invoice: Invoice
    state: str
    reason: str
    confirmed_zat: int
    pending_zat: int
    notes: tuple[ReceivedNote, ...] = field(default_factory=tuple)

    @property
    def settled(self) -> bool:
        return self.state in SETTLED

    def to_dict(self) -> dict[str, Any]:
        return {**self.invoice.to_dict(), "state": self.state, "reason": self.reason, "confirmed": format_zec(self.confirmed_zat),
                "pending": format_zec(self.pending_zat), "txids": sorted({n.txid for n in self.notes})}


def freshness(state: dict[str, Any], *, stale_after: int, now: float | None = None) -> Freshness:
    now = time.time() if now is None else now
    last_sync = state.get("last_sync_at")
    last_attempt = state.get("last_attempt_at")
    tip = state.get("tip")
    if not isinstance(last_sync, int | float) or not isinstance(tip, int) or tip <= 0:
        return Freshness(False, "never_synced", None, None)
    if state.get("last_error") and isinstance(last_attempt, int | float) and last_attempt >= last_sync:
        return Freshness(False, "sync_failed", tip, float(last_sync))
    if now - float(last_sync) > stale_after or float(last_sync) > now + 60:
        return Freshness(False, "stale_sync", tip, float(last_sync))
    return Freshness(True, "", tip, float(last_sync))


def confirmations(note: ReceivedNote, tip: int) -> int:
    if note.mined_height is None or note.mined_height > tip:
        return 0
    return tip - note.mined_height + 1


def match_invoices(invoices: list[Invoice], notes: list[ReceivedNote], fresh: Freshness, *, required: int) -> list[InvoiceStatus]:
    by_memo: dict[str, list[ReceivedNote]] = {}
    for note in notes:
        if note.pool in ("sapling", "orchard") and note.memo is not None:
            by_memo.setdefault(note.memo.strip(), []).append(note)
    out: list[InvoiceStatus] = []
    for invoice in invoices:
        matched = tuple(by_memo.get(invoice.memo, ()))
        if invoice.paid_txid:
            # Already receipted: the receipt is the record. It never reverts to unknown because a later sync failed.
            total = max(invoice.paid_zat, sum(n.value_zat for n in matched))
            state = OVERPAID if total > invoice.amount_zat else PAID
            out.append(InvoiceStatus(invoice, state, "receipted", total, 0, matched))
            continue
        if not fresh.fresh or fresh.tip is None:
            out.append(InvoiceStatus(invoice, UNKNOWN, fresh.reason or "never_synced", 0, 0, matched))
            continue
        confirmed = sum(n.value_zat for n in matched if confirmations(n, fresh.tip) >= required)
        pending = sum(n.value_zat for n in matched if confirmations(n, fresh.tip) < required)
        if not matched:
            state, reason = UNPAID, "no_matching_payment"
        elif confirmed > invoice.amount_zat:
            state, reason = OVERPAID, "more_than_requested"
        elif confirmed == invoice.amount_zat:
            state, reason = PAID, "exact"
        elif confirmed + pending >= invoice.amount_zat:
            state, reason = UNCONFIRMED, f"waiting_for_{required}_confirmations"
        else:
            state, reason = UNDERPAID, "less_than_requested"
        out.append(InvoiceStatus(invoice, state, reason, confirmed, pending, matched))
    return out


def unmatched_payments(invoices: list[Invoice], notes: list[ReceivedNote]) -> list[ReceivedNote]:
    """Received payments that name no invoice of this store (no memo, another memo, or a transparent output)."""
    memos = {inv.memo for inv in invoices}
    return [n for n in notes if (n.memo or "").strip() not in memos]
