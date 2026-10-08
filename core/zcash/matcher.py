"""Which invoices are paid: decided only from a fresh, successful watch-only sync.

An invoice is PAID only when received shielded notes whose memo is exactly the invoice's memo add up to at least
the requested amount, each with at least N confirmations. Every other outcome is an explicit state, and any doubt
about the data (no sync yet, a failed or stale sync, a wallet that has not scanned up to the tip) is UNKNOWN -- never
paid, never "still unpaid". A receipted invoice whose payment is no longer on the chain (a reorg) is REORGED, not paid.
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
REORGED = "reorged"
SETTLED = frozenset({PAID, OVERPAID})
#: A sync newer than "now" by more than this means the clock moved; the data cannot be dated, so it is not fresh.
CLOCK_SLACK_S = 60


@dataclass(frozen=True)
class Freshness:
    fresh: bool
    reason: str  # "" when fresh; never_synced | sync_failed | stale_sync | clock_moved | wallet_behind | local_only | ...
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
    """Fresh only when the LATEST attempt (by sequence, not by clock) succeeded, recently, at a sane time."""
    now = time.time() if now is None else now
    last_sync = state.get("last_sync_at")
    tip = state.get("tip")
    attempt_seq, ok_seq = state.get("attempt_seq"), state.get("ok_seq")
    if not isinstance(last_sync, (int, float)) or not isinstance(tip, int) or tip <= 0 or not isinstance(ok_seq, int) or ok_seq <= 0:
        return Freshness(False, "never_synced", None, None)
    if attempt_seq != ok_seq or state.get("last_error"):
        return Freshness(False, "sync_failed", tip, float(last_sync))
    if float(last_sync) > now + CLOCK_SLACK_S:
        return Freshness(False, "clock_moved", tip, float(last_sync))
    if now - float(last_sync) > stale_after:
        return Freshness(False, "stale_sync", tip, float(last_sync))
    return Freshness(True, "", tip, float(last_sync))


def confirmations(note: ReceivedNote, tip: int) -> int:
    if note.mined_height is None or note.mined_height > tip:
        return 0
    return tip - note.mined_height + 1


def _distinct(notes: list[ReceivedNote]) -> tuple[list[ReceivedNote], set[str]]:
    """One entry per output (txid, pool, index). Memos whose output appears with conflicting contents are returned
    as doubtful: an invoice carrying one is UNKNOWN."""
    seen: dict[tuple[str, str, int], ReceivedNote] = {}
    doubtful: set[str] = set()
    for note in notes:
        key = (note.txid, note.pool, note.output_index)
        if key in seen and seen[key] != note:
            doubtful.update(m.strip() for m in (seen[key].memo, note.memo) if m)
        seen.setdefault(key, note)
    return list(seen.values()), doubtful


def match_invoices(invoices: list[Invoice], notes: list[ReceivedNote], fresh: Freshness, *, required: int) -> list[InvoiceStatus]:
    distinct, doubtful = _distinct(notes)
    by_memo: dict[str, list[ReceivedNote]] = {}
    for note in distinct:
        if note.pool in ("sapling", "orchard") and note.memo is not None:
            by_memo.setdefault(note.memo.strip(), []).append(note)
    out: list[InvoiceStatus] = []
    for invoice in invoices:
        matched = tuple(by_memo.get(invoice.memo, ()))
        if invoice.paid_txid:
            out.append(_receipted(invoice, matched, fresh))
            continue
        if not fresh.fresh or fresh.tip is None:
            out.append(InvoiceStatus(invoice, UNKNOWN, fresh.reason or "never_synced", 0, 0, matched))
            continue
        if invoice.memo in doubtful:
            out.append(InvoiceStatus(invoice, UNKNOWN, "conflicting_wallet_rows", 0, 0, matched))
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


def _receipted(invoice: Invoice, matched: tuple[ReceivedNote, ...], fresh: Freshness) -> InvoiceStatus:
    """A receipted invoice stays paid while its payment is on the chain. A fresh read that no longer finds enough of
    it mined makes it REORGED; without a fresh read the last known chain state stands."""
    if fresh.fresh and fresh.tip is not None:
        on_chain = sum(n.value_zat for n in matched if confirmations(n, fresh.tip) >= 1)
        if on_chain < invoice.amount_zat:
            return InvoiceStatus(invoice, REORGED, "payment_left_the_chain", on_chain, 0, matched)
        return InvoiceStatus(invoice, OVERPAID if on_chain > invoice.amount_zat else PAID, "receipted", on_chain, 0, matched)
    if invoice.reorged:
        return InvoiceStatus(invoice, REORGED, "payment_left_the_chain", 0, 0, matched)
    return InvoiceStatus(invoice, OVERPAID if invoice.paid_zat > invoice.amount_zat else PAID, "receipted", invoice.paid_zat, 0, matched)


def unmatched_payments(invoices: list[Invoice], notes: list[ReceivedNote]) -> list[ReceivedNote]:
    """Received payments that name no invoice of this store (no memo, another memo, or a transparent output)."""
    memos = {inv.memo for inv in invoices}
    return [n for n in _distinct(notes)[0] if (n.memo or "").strip() not in memos]
