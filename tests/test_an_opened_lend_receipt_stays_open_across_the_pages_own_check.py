"""A receipt, terms or SHA-256 panel opened on the Lend page stays open when the page checks on its own.

The Lend page asks for the list every 5 seconds and rebuilds its cards. Each rebuild closed every panel the person had
opened, so a receipt snapped shut a few seconds after it was opened (macOS CI on 5a8cd0abf, 2026-11-26: the real-servers
test's scroll hit a node the rebuild had replaced). The real fragment is driven here against a stubbed list, so the
proof runs on any machine with chromium. All names and values are invented.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from core.lend_fragment import render_lend_fragment
from tests.served_browser import launch_chromium

_ORIGIN = "http://lend-page.test"
_EXPIRES = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
_TERMS = {"capability": "clean_pdf", "jobs": 1, "max_bytes": 2_000_000, "max_length": 50000, "max_seconds": 60,
          "expires": _EXPIRES, "auto": False}
_POLL = {
    "ok": True,
    "invites": [{"id": "inv-otter", "role": "host", "state": "ready", "terms": _TERMS, "guest": "G" * 43, "used": 1}],
    "jobs": [{"id": "job-otter", "role": "host", "invite": "inv-otter", "name": "seed-catalogue.pdf", "size": 48213,
              "state": "complete",
              "receipt": {"state": "complete", "seconds": 3, "finished": datetime.now(timezone.utc).isoformat(),
                          "terms_digest": "a" * 64, "input_digest": "b" * 64, "result_digest": "c" * 64}}],
}


def test_an_opened_receipt_and_its_digests_stay_open_across_two_checks():
    polls: list[int] = []

    def answer(route):
        url = route.request.url
        if url.endswith("/page"):
            route.fulfill(content_type="text/html",
                          body="<!doctype html><html lang=en><body><div id=host></div>" + render_lend_fragment()
                          + "<script>VoolLend.mountInto(document.getElementById('host'))</script></body></html>")
            return
        action = json.loads(route.request.post_data or "{}").get("action")
        if action == "status":
            body = {"ok": True, "enabled": True, "drop_enabled": True, "capabilities": {"clean_pdf": True},
                    "defaults": {"expires": _EXPIRES}}
        elif action == "identity":
            body = {"ok": True, "fingerprint": "H" * 43}
        elif action == "poll":
            polls.append(1)
            body = _POLL
        else:
            body = {"ok": True}
        route.fulfill(content_type="application/json", body=json.dumps(body))

    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.route(_ORIGIN + "/**", answer)
        page.goto(_ORIGIN + "/page")
        receipt = page.locator("[data-job] .vl-receipt").first
        receipt.wait_for()
        terms = page.locator("[data-invite] details").first
        receipt.locator(":scope > summary").click()
        receipt.locator(".vl-receipt-digests > summary").click()
        terms.locator(":scope > summary").click()
        seen = len(polls)
        page.wait_for_timeout(11000)
        assert len(polls) >= seen + 2, "the page did not check on its own twice"
        assert receipt.evaluate("(node) => node.open")
        assert receipt.locator(".vl-receipt-digests").evaluate("(node) => node.open")
        assert terms.evaluate("(node) => node.open")
        # A panel that was never opened stays closed.
        assert not page.locator("[data-job] > details").first.evaluate("(node) => node.open")
    finally:
        browser.close()
        manager.stop()
