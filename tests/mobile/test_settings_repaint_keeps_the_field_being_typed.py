"""A Settings repaint (the window regained focus and a re-read came back changed) must not take the
caret out of the field the owner is typing in. The phone panel is put back as the same nodes, so
its text survives, but the field lost focus: keys typed next went nowhere (CI on 0680a8678 sent an
empty per-payment limit after a re-read landed between focusing the field and the typed text)."""
from __future__ import annotations

import os

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_payments import call
from tests.mobile.test_phone_payments import money as money


def test_a_repaint_while_typing_keeps_the_caret_in_the_same_field(money):
    from playwright.sync_api import sync_playwright

    base, phone = money[0][0], money[1]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("VOOL_TEST_CHROMIUM"))
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.goto(base + "/settings#phone")
            page.locator('[data-group="phone"]').click()
            row = page.locator('[data-device="' + phone[3]["device_id"] + '"]')
            row.locator('.phone-advanced summary').click()
            field = row.locator('.phone-advanced .phone-cap input').nth(0)
            field.click()
            page.keyboard.type("0.0")
            # The phone's own call (refused or not) moves its last-seen time, so the re-read repaints the pane.
            call(*phone, "wallet.status")
            page.evaluate("document.querySelector('#paneBody > *').dataset.drawnBefore = '1'")
            page.evaluate("window.__voolSettings.reconcile()")
            assert page.evaluate("!document.querySelector('#paneBody [data-drawn-before]')"), "the re-read did not repaint"
            page.keyboard.type("1")
            assert field.input_value() == "0.01"
            assert page.evaluate("document.activeElement === document.querySelector('.phone-advanced .phone-cap input')")
        finally:
            browser.close()
