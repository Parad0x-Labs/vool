"""The Zcash private-invoice lane end to end: set up watch-only, bill, refresh, receipt, remember, export.

Nothing here can move money. The flow is: the owner's viewing key -> a view-only devtool wallet -> a shielded
receiving address -> ZIP 321 invoices -> sync -> received notes -> :mod:`core.zcash.matcher` -> for each newly
settled invoice exactly one ``zec_received`` receipt in the Blackbox journal (staged, journaled, then committed as
paid) and one memory fact carrying the evidence.

The viewing key in VOOL's credential store is the source of truth: every refresh checks that the wallet watches
exactly that key, view-only, before anything it reports is believed. Network work obeys Local Only.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from core.zcash import config, keys, matcher
from core.zcash.invoices import Invoice, InvoiceStore, format_zec, parse_amount
from core.zcash.watch import Devtool, ReceivedNote, ZcashWatchError, write_private

RECEIPT_KIND = "zec_received"
REORG_KIND = "zec_payment_reorged"
_ADDRESS_FILE = "receiving_address.txt"
#: The wallet may trail the server's tip by this many blocks (new blocks between the sync and the tip read).
SCAN_LAG_BLOCKS = 2


def local_only(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.remote_fetch_policy import local_only_active

        return bool(local_only_active(source_context))
    except Exception:
        return False


class ZcashLane:
    def __init__(self, *, devtool: Devtool | None = None, store: InvoiceStore | None = None) -> None:
        self.devtool = devtool or Devtool()
        self.store = store or InvoiceStore()

    # --- setup ------------------------------------------------------------------------------------

    def set_viewing_key(self, raw: str, *, birthday: int | None = None, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Parse (refusing any spending key or seed), create the view-only wallet, then save the key.

        An existing wallet is never silently re-pointed: the same key again is accepted (and saved, if an earlier
        save failed), a different key is refused with ``already_initialized``.
        """
        key = keys.parse_viewing_key(raw, expected_network=self.devtool.network)
        if self.devtool.initialized():
            try:
                self.devtool.check_account(key)
            except ZcashWatchError as exc:
                if exc.code != "wallet_key_mismatch":
                    raise
                raise ZcashWatchError("already_initialized", "A Zcash watch wallet already exists for another viewing key; nothing was changed.") from None
        else:
            if local_only(source_context):
                raise ZcashWatchError("local_only", "Local Only is on, so VOOL did not contact the Zcash network to set up the wallet.")
            self.devtool.initialize(key, birthday=birthday)
        keys.store_viewing_key(key)
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
        with contextlib.suppress(OSError):  # only the cache failed; the derived address stands and is derived again next time
            write_private(path, address)
        return address

    # --- invoices ---------------------------------------------------------------------------------

    def create_invoice(self, amount: Any, *, label: str = "", payer: str = "") -> Invoice:
        amount_zat = parse_amount(amount)
        return self.store.create(address=self.receiving_address(), amount_zat=amount_zat, label=label, payer=payer, network=self.devtool.network)

    def _verified_freshness(self, source_context: dict[str, Any] | None, sync: bool) -> tuple[matcher.Freshness, str]:
        """Sync (unless Local Only), then believe the wallet only if it watches the saved key and has scanned to the tip."""
        if local_only(source_context):
            return matcher.Freshness(False, "local_only", None, None), "Local Only is on, so VOOL did not contact the Zcash network."
        sync_error = ""
        if sync:
            try:
                self.devtool.sync()
            except ZcashWatchError as exc:
                sync_error = exc.message
        fresh = matcher.freshness(self.devtool.read_state(), stale_after=config.stale_after_seconds())
        if not fresh.fresh:
            return fresh, sync_error
        try:
            key = keys.load_viewing_key(self.devtool.network)
            if key is None:
                return matcher.Freshness(False, "viewing_key_missing", fresh.tip, fresh.last_sync_at), sync_error
            self.devtool.check_account(key)
            scanned = self.devtool.scanned_height()
        except keys.ZcashKeyRefused as exc:
            return matcher.Freshness(False, exc.code, fresh.tip, fresh.last_sync_at), str(exc)
        except ZcashWatchError as exc:
            return matcher.Freshness(False, exc.code, fresh.tip, fresh.last_sync_at), exc.message
        if scanned is None or scanned < int(fresh.tip or 0) - SCAN_LAG_BLOCKS:
            return matcher.Freshness(False, "wallet_behind", fresh.tip, fresh.last_sync_at), sync_error
        # Confirmations count only blocks the wallet itself has scanned.
        return matcher.Freshness(True, "", min(int(fresh.tip or 0), scanned), fresh.last_sync_at), sync_error

    def refresh(self, *, source_context: dict[str, Any] | None = None, sync: bool = True) -> dict[str, Any]:
        """Sync, match, and receipt every newly settled invoice. Any doubt leaves open invoices UNKNOWN."""
        receipted = self._commit_pending(source_context)
        fresh, sync_error = self._verified_freshness(source_context, sync)
        notes: list[ReceivedNote] = []
        read_error = ""
        if fresh.fresh:
            try:
                notes = self.devtool.received_notes()
            except ZcashWatchError as exc:
                read_error = exc.message
                fresh = matcher.Freshness(False, exc.code, fresh.tip, fresh.last_sync_at)
        required = config.confirmations_required()
        statuses = matcher.match_invoices(self.store.all(network=self.devtool.network), notes, fresh, required=required)
        if fresh.fresh:
            for status in statuses:
                if status.settled and not status.invoice.paid_txid:
                    receipt = self._receipt(status, tip=int(fresh.tip or 0), source_context=source_context)
                    if receipt is not None:
                        receipted.append(receipt)
                elif status.invoice.paid_txid:
                    self._note_chain_state(status, source_context=source_context)
        self._remember_pending(source_context)
        if receipted or fresh.fresh:
            statuses = matcher.match_invoices(self.store.all(network=self.devtool.network), notes, fresh, required=required)
        # "Paid" is reported only with its receipt in the journal; a receipt still waiting to be written is not settled yet.
        statuses = [replace(s, state=matcher.UNKNOWN, reason="receipt_pending") if s.settled and not s.invoice.paid_txid else s
                    for s in statuses]
        return {
            "fresh": fresh.fresh, "reason": fresh.reason, "tip": fresh.tip, "sync_error": sync_error, "read_error": read_error,
            "statuses": statuses, "unmatched": matcher.unmatched_payments(self.store.all(network=self.devtool.network), notes) if fresh.fresh else [],
            "receipted": receipted,
        }

    # --- receipts: staged -> journaled -> committed, exactly once ----------------------------------

    def _receipt(self, status: matcher.InvoiceStatus, *, tip: int, source_context: dict[str, Any] | None) -> dict[str, Any] | None:
        invoice = status.invoice
        required = config.confirmations_required()
        confirmed = [n for n in status.notes if matcher.confirmations(n, tip) >= required]
        txids = sorted({n.txid for n in confirmed})
        digest = hashlib.sha256(f"{invoice.network}:{invoice.invoice_id}:{','.join(txids)}".encode()).hexdigest()
        payload = {
            "receipt_id": f"zrcpt-{digest[:16]}", "invoice_id": invoice.invoice_id, "network": invoice.network, "state": status.state,
            "requested_zat": invoice.amount_zat, "received_zat": status.confirmed_zat, "memo": invoice.memo,
            "label": invoice.label, "payer": invoice.payer, "txids": txids,
            "heights": sorted({int(n.mined_height) for n in confirmed if n.mined_height is not None}),
            "pools": sorted({n.pool for n in confirmed}), "confirmations_required": required, "tip": tip,
            "confirmed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        staged = self.store.stage_receipt(invoice.invoice_id, payload)
        if staged is None:
            return None  # already paid by another refresh
        return self._journal_and_commit(staged, source_context)

    def _journal_and_commit(self, receipt: dict[str, Any], source_context: dict[str, Any] | None) -> dict[str, Any] | None:
        try:
            journal_once(RECEIPT_KIND, receipt, source_context=source_context)
        except Exception:
            return None  # stays staged; the next refresh journals and commits it
        return receipt if self.store.commit_paid(receipt) else None

    def _commit_pending(self, source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
        done = []
        for receipt in self.store.pending_receipts(network=self.devtool.network):
            committed = self._journal_and_commit(receipt, source_context)
            if committed is not None:
                done.append(committed)
        return done

    def _remember_pending(self, source_context: dict[str, Any] | None) -> None:
        """Memory facts need a chat session; receipts made without one (the owner console) are remembered on the
        next refresh that has one. The fact key is stable, so a repeat overwrites rather than duplicates."""
        if not _session_id(source_context):
            return
        for receipt in self.store.unremembered(network=self.devtool.network):
            if remember_payment(receipt, source_context=source_context):
                self.store.mark_remembered(receipt["invoice_id"])

    def _note_chain_state(self, status: matcher.InvoiceStatus, *, source_context: dict[str, Any] | None) -> None:
        reorged = status.state == matcher.REORGED
        if self.store.set_reorged(status.invoice.invoice_id, reorged) and reorged:
            evidence = {"invoice_id": status.invoice.invoice_id, "receipt_id": status.invoice.paid_receipt_id,
                        "txids": status.invoice.paid_txid.split(","), "network": status.invoice.network,
                        "on_chain_zat": status.confirmed_zat, "requested_zat": status.invoice.amount_zat}
            try:
                journal_once(REORG_KIND, {**evidence, "receipt_id": f"{evidence['receipt_id']}-reorg-{status.invoice.paid_txid[:8]}"},
                             source_context=source_context)
            except Exception:
                self.store.set_reorged(status.invoice.invoice_id, False)  # retried on the next fresh refresh

    # --- reads ------------------------------------------------------------------------------------

    def history(self, *, since: str = "", until: str = "") -> list[Invoice]:
        rows = [inv for inv in self.store.all(network=self.devtool.network) if inv.paid_txid]
        return [inv for inv in rows if (not since or inv.created_at[:10] >= since) and (not until or inv.created_at[:10] <= until)]

    def export_csv(self, *, since: str = "", until: str = "") -> str:
        """Receipted invoices in a period, for selective disclosure: what was billed, what arrived, which transaction."""
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["invoice_id", "created_at", "label", "payer", "requested", "received", "currency", "memo", "txids", "receipt_id", "chain_status"])
        for inv in self.history(since=since, until=until):
            writer.writerow([inv.invoice_id, inv.created_at, _csv_safe(inv.label), _csv_safe(inv.payer), format_zec(inv.amount_zat),
                             format_zec(inv.paid_zat), config.ticker(inv.network), inv.memo, inv.paid_txid, inv.paid_receipt_id,
                             "reorged" if inv.reorged else "on_chain"])
        return buffer.getvalue()


def _csv_safe(text: str) -> str:
    """Spreadsheet formula injection guard for owner-typed text."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _session_id(source_context: dict[str, Any] | None) -> str:
    context = source_context if isinstance(source_context, dict) else {}
    return str(context.get("session_id") or context.get("runtime_session_id") or "").strip()


def journal_once(kind: str, payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """Append to the Blackbox journal unless an entry of this kind with this receipt id is already there.

    The look and the append happen under the journal's exclusive lock (thread and process), so two refreshes running
    at once (the turn planner runs independent tool calls in parallel) cannot both append the same receipt.
    Raises when the journal cannot be read or written: the caller keeps the receipt staged and retries.
    """
    from core.blackbox.identity import identity_from_context
    from core.blackbox.store import default_store

    store = default_store()
    receipt_id = payload["receipt_id"]
    entry = {"schema": "blackbox_effect_v1", "kind": kind, **identity_from_context(source_context).to_dict(), **payload}
    with store.exclusive():
        for seen in store.entries():
            if seen.get("kind") == kind and seen.get("receipt_id") == receipt_id:
                return seen
        written = store.append(entry)
    if not written:
        raise RuntimeError("journal append returned nothing")
    return written


def journal_receipt(payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    return journal_once(RECEIPT_KIND, payload, source_context=source_context)


def remember_payment(payload: dict[str, Any], *, source_context: dict[str, Any] | None) -> bool:
    """One memory fact per settled invoice, with the chain evidence in the text so an answer can cite it."""
    session_id = _session_id(source_context)
    if not session_id or not payload.get("receipt_id"):
        return False
    who = f" from {payload['payer']}" if payload.get("payer") else ""
    what = f" for {payload['label']}" if payload.get("label") else ""
    ticker = config.ticker(payload.get("network"))
    day = str(payload.get("confirmed_at") or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
