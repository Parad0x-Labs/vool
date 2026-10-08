"""Owner-local HTTP surface for Zcash invoices: the QR page and the selective-disclosure CSV.

Both are desktop-only (loopback), and absent (404) while the lane is switched off. The QR is drawn in the page
by the already-vendored offline generator (core/web/api/mobile_vendor/qrcode.js); nothing is fetched from a CDN.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

PATH_PREFIX = "/api/zcash/"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zcash invoice __ID__</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#111;--muted:#666;--card:#f4f4f5}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#aaa;--card:#1d1d20}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:520px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}
#qr{background:#fff;padding:12px;display:inline-block;margin:16px 0;border-radius:8px}
code{word-break:break-all;background:var(--card);padding:2px 4px;border-radius:4px}
.row{margin:8px 0}
</style></head><body><main>
<h1>__AMOUNT__ __TICKER__</h1>
<div class="muted">__LABEL__</div>
<div id="qr" aria-label="Payment QR code"></div>
<div class="row">Memo (keep it, it identifies this invoice): <code>__MEMO__</code></div>
<div class="row">Shielded address: <code>__ADDRESS__</code></div>
<div class="row"><a href="__URI_ATTR__">Open in a Zcash wallet</a></div>
<p class="muted">Pay from any shielded Zcash wallet. VOOL only watches for this payment; it holds no spending key.</p>
</main>
<script>
(function () {
  var payload = __PAYLOAD__;
  var qr = qrcode(0, "M");
  qr.addData(payload.uri);
  qr.make();
  document.getElementById("qr").innerHTML = qr.createImgTag(5, 8, "Payment QR code");
})();
</script></body></html>
"""


def _response(status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None):
    from core.web.api.service import ApiResponse

    return ApiResponse(status, content_type=content_type, body=body, headers=dict(headers or {}))


def _err(status: int, code: str, message: str):
    return _response(status, json.dumps({"ok": False, "code": code, "error": message}).encode("utf-8"), "application/json")


def render_invoice_page(invoice: Any, ticker: str) -> str:
    from core.zcash.invoices import format_zec, payment_uri

    uri = payment_uri(invoice)
    page = _PAGE
    for marker, value in (("__ID__", invoice.invoice_id), ("__AMOUNT__", format_zec(invoice.amount_zat)), ("__TICKER__", ticker),
                          ("__LABEL__", " · ".join(p for p in (invoice.label, invoice.payer) if p)), ("__MEMO__", invoice.memo),
                          ("__ADDRESS__", invoice.address), ("__URI_ATTR__", uri)):
        page = page.replace(marker, html.escape(str(value), quote=True))
    payload = json.dumps({"uri": uri}).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = page.replace("__PAYLOAD__", payload)
    vendor = (Path(__file__).resolve().parent.parent / "web" / "api" / "mobile_vendor" / "qrcode.js").read_text(encoding="utf-8")
    return page.replace("<script>", "<script>\n" + vendor + "\n", 1)


def handle_zcash_get(path: str, query: dict[str, list[str]], *, client_host: str = ""):
    """GET routes under /api/zcash/. Returns None when the path is not ours."""
    if not path.startswith(PATH_PREFIX):
        return None
    from core.request_trust import is_loopback_host
    from core.zcash import config

    if not config.zcash_enabled():
        return _err(404, "not_found", "Zcash invoices are switched off.")
    if not is_loopback_host(client_host):
        return _err(403, "owner_local_required", "Zcash invoices are desktop-only.")

    def _q(name: str) -> str:
        return str((query.get(name) or [""])[0] or "").strip()

    from core.zcash.service import ZcashLane

    if path == PATH_PREFIX + "invoice/page":
        invoice = ZcashLane().store.get(_q("invoice_id")) if _q("invoice_id") else None
        if invoice is None or invoice.network != config.network():
            return _err(404, "invoice_unknown", "No such invoice.")
        return _response(200, render_invoice_page(invoice, config.ticker(invoice.network)).encode("utf-8"), "text/html; charset=utf-8",
                         {"Cache-Control": "no-store"})
    if path == PATH_PREFIX + "export.csv":
        since, until = _q("since"), _q("until")
        if (since and not _DATE.match(since)) or (until and not _DATE.match(until)):
            return _err(400, "date_invalid", "Dates are YYYY-MM-DD.")
        body = ZcashLane().export_csv(since=since, until=until).encode("utf-8")
        name = f"zcash-payments-{since or 'start'}-{until or 'now'}.csv"
        return _response(200, body, "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
    return _err(404, "not_found", "No such Zcash route.")
