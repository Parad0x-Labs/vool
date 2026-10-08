"""The model-facing Zcash invoice tools: bill, list what is open, show what was paid.

Present only while the lane is switched on (:func:`zcash_contracts` returns nothing otherwise, so the tools are
absent, not merely refused). None of them can move money; setting the viewing key is NOT a chat tool, because a
key typed into a chat would travel with the conversation -- it goes through ``python -m core.zcash set-key``.
Each tool's own text is the answer (amounts, memos and txids are never paraphrased by a model).
"""
from __future__ import annotations

from typing import Any

from core.zcash import config

INTENT_CREATE = "zcash.invoice.create"
INTENT_UNPAID = "zcash.invoice.unpaid"
INTENT_HISTORY = "zcash.payment.history"
INTENTS = (INTENT_CREATE, INTENT_UNPAID, INTENT_HISTORY)
SURFACE = "zcash"
PAGE_PATH = "/api/zcash/invoice/page"


def zcash_contracts() -> list[Any]:
    if not config.zcash_enabled():
        return []
    from core.runtime_tool_contracts import RuntimeToolContract

    common = {
        "tool_surface": SURFACE, "supported": True, "unsupported_reason": "", "permission_actions": ("read_files",),
        "approval_requirement": "none", "timeout_policy": "network_default", "retry_policy": "none", "artifact_emission": "none",
        "error_contract": "returns_structured_error_result", "renders_final_answer": True,
    }
    return [
        RuntimeToolContract(
            intent=INTENT_CREATE,
            description="Create a private (shielded) Zcash payment request: an amount in ZEC, what it is for, and optionally who pays. Returns a ZIP 321 payment link with the invoice memo and a QR page. Nothing is paid or sent; VOOL only watches for the payment.",
            capability_id="zcash.invoice", capability_claim="create a shielded ZEC payment request",
            input_schema={"amount": "str decimal ZEC, e.g. 0.05", "label": "str (optional): what it is for", "payer": "str (optional): who pays"},
            output_schema={"invoice_id": "str", "uri": "str", "memo": "str"},
            side_effect_class="creative_state", **common,
        ),
        RuntimeToolContract(
            intent=INTENT_UNPAID,
            description="Check the shielded Zcash invoices that are not settled yet: syncs the view-only wallet and reports each one as unpaid, unconfirmed, underpaid or unknown, plus payments that name no invoice.",
            capability_id="zcash.read", capability_claim="read the state of ZEC invoices",
            input_schema={}, output_schema={"invoices": "list"}, side_effect_class="read_only", **common,
        ),
        RuntimeToolContract(
            intent=INTENT_HISTORY,
            description="List the shielded Zcash invoices that were paid, with amount, memo, transaction id and receipt, optionally between two dates (YYYY-MM-DD).",
            capability_id="zcash.read", capability_claim="read paid ZEC invoices",
            input_schema={"since": "str (optional) YYYY-MM-DD", "until": "str (optional) YYYY-MM-DD", "payer": "str (optional)"},
            output_schema={"payments": "list"}, side_effect_class="read_only", **common,
        ),
    ]


def _status_line(status: Any, ticker: str) -> str:
    inv = status.invoice
    who = f" from {inv.payer}" if inv.payer else ""
    what = f" for {inv.label}" if inv.label else ""
    base = f"- {inv.invoice_id}: {status.to_dict()['amount']} {ticker}{what}{who}, memo {inv.memo}: {status.state}"
    if status.state == "unconfirmed":
        return base + f" ({status.to_dict()['pending']} {ticker} seen, waiting for {config.confirmations_required()} confirmations)"
    if status.state == "underpaid":
        return base + f" (only {status.to_dict()['confirmed']} {ticker} confirmed)"
    if status.state == "unknown":
        return base + " (cannot confirm right now)"
    return base


_REASONS = {
    "never_synced": "the watch wallet has not synced yet",
    "sync_failed": "the last sync failed",
    "stale_sync": "the last successful sync is too old",
    "tx_crosscheck_failed": "the wallet data did not cross-check",
}


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult
    from core.zcash.invoices import InvoiceError, format_zec
    from core.zcash.keys import ZcashKeyRefused
    from core.zcash.service import ZcashLane
    from core.zcash.watch import ZcashWatchError

    args = dict(arguments or {})
    ticker = config.ticker()

    def result(ok: bool, status: str, text: str, **details: Any):
        return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=text,
                                      details={**details, "observation": _tool_observation(intent=intent, tool_surface=SURFACE, ok=ok, status=status)})

    if intent not in INTENTS:
        return result(False, "unsupported_intent", "That is not a Zcash invoice tool.")
    if not config.zcash_enabled():
        return result(False, "zcash_disabled", "Zcash invoices are switched off. Nothing was created and nothing was sent.")
    try:
        lane = ZcashLane()
        if intent == INTENT_CREATE:
            invoice = lane.create_invoice(args.get("amount"), label=str(args.get("label") or ""), payer=str(args.get("payer") or ""))
            what = f" for {invoice.label}" if invoice.label else ""
            text = (
                f"Invoice {invoice.invoice_id}: {format_zec(invoice.amount_zat)} {ticker}{what}. It pays to your shielded address with memo "
                f"{invoice.memo}, which is how VOOL recognises the payment.\nPayment link: {invoice.to_dict()['uri']}\n"
                f"QR code: {PAGE_PATH}?invoice_id={invoice.invoice_id}\nNothing was sent; VOOL only watches for this payment."
            )
            return result(True, "ok", text, invoice=invoice.to_dict())
        report = lane.refresh(source_context=source_context)
        statuses = report["statuses"]
        freshness_note = "" if report["fresh"] else f"I can't confirm payments right now: {_REASONS.get(report['reason'], report['reason'])}."
        if intent == INTENT_UNPAID:
            open_ = [s for s in statuses if not s.settled]
            lines = [_status_line(s, ticker) for s in open_]
            new_paid = [r for r in report["receipted"]]
            head = f"{len(open_)} Zcash invoice(s) not settled." if open_ else "No open Zcash invoices."
            parts = [p for p in (freshness_note, head, *lines) if p]
            if new_paid:
                parts.append("Newly paid: " + ", ".join(f"{r['invoice_id']} ({format_zec(r['received_zat'])} {ticker}, txid {', '.join(r['txids'])})" for r in new_paid))
            if report["unmatched"]:
                parts.append(f"{len(report['unmatched'])} received payment(s) name no invoice.")
            return result(True, "ok" if report["fresh"] else "unknown", "\n".join(parts), invoices=[s.to_dict() for s in open_])
        payer = str(args.get("payer") or "").strip().lower()
        paid = [s for s in statuses if s.settled and (not payer or payer in s.invoice.payer.lower())]
        since, until = str(args.get("since") or "").strip(), str(args.get("until") or "").strip()
        paid = [s for s in paid if (not since or s.invoice.created_at[:10] >= since) and (not until or s.invoice.created_at[:10] <= until)]
        lines = [f"- {s.invoice.created_at[:10]} {s.invoice.invoice_id}: {format_zec(s.confirmed_zat)} {ticker} received"
                 f"{' for ' + s.invoice.label if s.invoice.label else ''}{' from ' + s.invoice.payer if s.invoice.payer else ''}"
                 f" ({s.state}), memo {s.invoice.memo}, txid {s.invoice.paid_txid}, receipt {s.invoice.paid_receipt_id}" for s in paid]
        head = f"{len(paid)} paid Zcash invoice(s)." if paid else "No paid Zcash invoices match."
        parts = [p for p in (head, *lines, freshness_note and f"Note: {freshness_note} Payments after the last good sync are not shown.") if p]
        return result(True, "ok", "\n".join(parts), payments=[s.to_dict() for s in paid])
    except (InvoiceError, ZcashWatchError, ZcashKeyRefused) as exc:
        return result(False, exc.code, f"{exc} Nothing was sent.")
