"""The Zcash private-invoice lane end to end: set up watch-only, bill, refresh, receipt, remember, export.

Nothing here can move money. The flow is: the owner's viewing key -> a view-only devtool wallet -> a shielded
receiving address -> ZIP 321 invoices -> sync -> received notes -> :mod:`core.zcash.matcher` -> for each newly
settled invoice one ``zec_received`` receipt in the Blackbox journal and one memory fact carrying the evidence.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any

from core.zcash import config, keys, matcher
from core.zcash.invoices import Invoice, InvoiceStore, format_zec, parse_amount
from core.zcash.watch import Devtool, ReceivedNote, ZcashWatchError

RECEIPT_KIND = "zec_received"
_ADDRESS_FILE = "receiving_address.txt"


class ZcashLane:
    def __init__(self, *, devtool: Devtool | None = None, store: InvoiceStore | None = None) -> None:
        self.devtool = devtool or Devtool()
        self.store = store or InvoiceStore()

    # --- setup ------------------------------------------------------------------------------------

    def set_viewing_key(self, raw: str, *, birthday: int | None = None) -> dict[str, Any]:
        """Parse (refusing any spending key or seed), keep in the keychain, create the view-only wallet."""
        key = keys.parse_viewing_key(raw, expected_network=self.devtool.network)
        keys.store_viewing_key(key)
        if not self.devtool.initialized():
            self.devtool.initialize(key, birthday=birthday)
        address = self.receiving_address(refresh=True)
        return {"network": key.network, "kind": key.kind, "address": address}

    def receiving_address(self, *, refresh: bool = False) -> str:
        path = self.devtool.wallet_dir / _ADDRESS_FILE
        if not refresh:
            try:
                cached = path.read_text(encoding="utf-8").strip()
                if cached:
                    return cached
            except OSError:
                pass
        if not self.devtool.initialized():
            raise ZcashWatchError("not_initialized", "Zcash invoices are not set up yet: give VOOL your viewing key first (it starts with uview1).")
        address = self.devtool.receiving_address()
        path.write_text(address, encoding="utf-8")
        return address

    # --- invoices ---------------------------------------------------------------------------------

    def create_invoice(self, amount: Any, *, label: str = "", payer: str = "") -> Invoice:
        amount_zat = parse_amount(amount)
        return self.store.create(address=self.receiving_address(), amount_zat=amount_zat, label=label, payer=payer, network=self.devtool.network)

    def refresh(self, *, source_context: dict[str, Any] | None = None, sync: bool = True) -> dict[str, Any]:
        """Sync, match, and receipt every newly settled invoice. A failed sync leaves open invoices UNKNOWN."""
        sync_error = ""
        if sync:
            try:
                self.devtool.sync()
            except ZcashWatchError as exc:
                sync_error = exc.message
        fresh = matcher.freshness(self.devtool.read_state(), stale_after=config.stale_after_seconds())
        notes: list[ReceivedNote] = []
        read_error = ""
        if fresh.fresh:
            try:
                notes = self.devtool.received_notes()
            except ZcashWatchError as exc:
                read_error = exc.message
                fresh = matcher.Freshness(False, exc.code, fresh.tip, fresh.last_sync_at)
        invoices = self.store.all(network=self.devtool.network)
        statuses = matcher.match_invoices(invoices, notes, fresh, required=config.confirmations_required())
        receipted: list[dict[str, Any]] = []
        for status in statuses:
            if status.settled and not status.invoice.paid_txid and fresh.fresh:
                receipt = self._receipt(status, tip=int(fresh.tip or 0), source_context=source_context)
                if receipt is not None:
                    receipted.append(receipt)
        if receipted:
            statuses = matcher.match_invoices(self.store.all(network=self.devtool.network), notes, fresh, required=config.confirmations_required())
        return {
            "fresh": fresh.fresh, "reason": fresh.reason, "tip": fresh.tip, "sync_error": sync_error, "read_error": read_error,
            "statuses": statuses, "unmatched": matcher.unmatched_payments(invoices, notes) if fresh.fresh else [], "receipted": receipted,
        }

    def _receipt(self, status: matcher.InvoiceStatus, *, tip: int, source_context: dict[str, Any] | None) -> dict[str, Any] | None:
        invoice = status.invoice
        confirmed = [n for n in status.notes if matcher.confirmations(n, tip) >= config.confirmations_required()]
        txids = sorted({n.txid for n in confirmed})
        receipt_id = f"zrcpt-{uuid.uuid4().hex[:16]}"
        if not self.store.mark_paid(invoice.invoice_id, txid=",".join(txids), receipt_id=receipt_id, received_zat=status.confirmed_zat):
            return None  # another refresh recorded it first
        payload = {
            "receipt_id": receipt_id, "invoice_id": invoice.invoice_id, "network": invoice.network, "state": status.state,
            "requested_zat": invoice.amount_zat, "received_zat": status.confirmed_zat, "memo": invoice.memo,
            "label": invoice.label, "payer": invoice.payer, "txids": txids,
            "heights": sorted({int(n.mined_height) for n in confirmed if n.mined_height is not None}),
            "pools": sorted({n.pool for n in confirmed}), "confirmations_required": config.confirmations_required(), "tip": tip,
        }
        journal_receipt(payload, source_context=source_context)
        remember_payment(payload, source_context=source_context)
        return payload

    # --- reads ------------------------------------------------------------------------------------

    def history(self, *, since: str = "", until: str = "") -> list[Invoice]:
        rows = [inv for inv in self.store.all(network=self.devtool.network) if inv.paid_txid]
        return [inv for inv in rows if (not since or inv.created_at[:10] >= since) and (not until or inv.created_at[:10] <= until)]

    def export_csv(self, *, since: str = "", until: str = "") -> str:
        """Settled invoices in a period, for selective disclosure: what was billed, what arrived, which transaction."""
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["invoice_id", "created_at", "label", "payer", "requested", "received", "currency", "memo", "txids", "receipt_id"])
        for inv in self.history(since=since, until=until):
            writer.writerow([inv.invoice_id, inv.created_at, _csv_safe(inv.label), _csv_safe(inv.payer), format_zec(inv.amount_zat),
                             format_zec(inv.paid_zat), config.ticker(inv.network), inv.memo, inv.paid_txid, inv.paid_receipt_id])
        return buffer.getvalue()


def _csv_safe(text: str) -> str:
    """Spreadsheet formula injection guard for owner-typed text."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def journal_receipt(payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Append the receipt to the Blackbox journal (hash-chained). Best effort: the invoice store already holds it."""
    try:
        from core.blackbox.identity import identity_from_context
        from core.blackbox.store import default_store

        entry = {"schema": "blackbox_effect_v1", "kind": RECEIPT_KIND, **identity_from_context(source_context).to_dict(), **payload}
        return default_store().append(entry)
    except Exception:
        return None


def remember_payment(payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> bool:
    """One memory fact per settled invoice, with the chain evidence in the text so an answer can cite it."""
    context = source_context if isinstance(source_context, dict) else {}
    session_id = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    if not session_id:
        return False
    who = f" from {payload['payer']}" if payload.get("payer") else ""
    what = f" for {payload['label']}" if payload.get("label") else ""
    ticker = config.ticker(payload.get("network"))
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    fact = (
        f"On {day} VOOL confirmed a private Zcash payment{who}: {format_zec(int(payload['received_zat']))} {ticker} received"
        f" against invoice {payload['invoice_id']}{what} (requested {format_zec(int(payload['requested_zat']))} {ticker}, state {payload['state']})."
        f" Evidence: memo {payload['memo']}, txid {', '.join(payload['txids'])}, block height {', '.join(str(h) for h in payload['heights'])},"
        f" receipt {payload['receipt_id']}."
    )
    try:
        from core.memory.entries import add_memory_fact

        return bool(add_memory_fact(fact, category="fact", session_id=session_id, source="zcash_watch",
                                    keywords=["zcash", "zec", "payment", "invoice", payload["invoice_id"]],
                                    fact_key=f"zcash-invoice-{payload['invoice_id']}"))
    except Exception:
        return False
