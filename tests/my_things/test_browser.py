from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.my_things.test_match import label_photo
from tests.my_things.test_served import call, daemon

_BROWSER_CHILDREN = {}


def record_browser_children(*, ended=False):
    import psutil
    path = os.environ.get("VOOL_THINGS_PROCESS_LOG")
    if not path:
        return
    with Path(path).open("a") as log:
        if not ended:
            for process in psutil.Process().children(recursive=True):
                identity = (process.pid, process.create_time())
                if identity not in _BROWSER_CHILDREN:
                    _BROWSER_CHILDREN[identity] = process
                    log.write(json.dumps({"pid": process.pid, "created": identity[1], "kind": "browser-child", "state": "started"}) + "\n")
        else:
            for (pid, created), process in _BROWSER_CHILDREN.items():
                try:
                    running = process.is_running() and process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE
                except psutil.Error:
                    running = False
                log.write(json.dumps({"pid": pid, "created": created, "kind": "browser-child", "state": "running" if running else "stopped"}) + "\n")


def shot(page, name):
    record_browser_children()
    target = os.environ.get("VOOL_THINGS_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / (name + ".png")), full_page=True)


def test_browser_receipt_photo_return_packet_red_and_polish(daemon, tmp_path):
    base, home = daemon
    receipt = tmp_path / "receipt.txt"
    receipt.write_text("Item: Synthetic headphones\nMaker: Example maker\nModel: WH-1000\nBought on: 2026-03-02\nBought from: Example shop\nPrice: 49.00 EUR\nWarranty end: 2027-03-02")
    photo = tmp_path / "label.png"
    photo.write_bytes(label_photo())
    bad = tmp_path / "unreadable.png"
    bad.write_bytes(b"\x89PNG\x00broken")
    requests, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("VOOL_BROWSER_BINARY") or None)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 920})
            page.on("request", lambda req: requests.append(req.url))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/my-things?ui_locale=en")
            expect(page.get_by_role("heading", name="My things", exact=True)).to_be_visible()
            shot(page, "01-empty")
            page.get_by_label("Review a receipt · Receipt", exact=True).set_input_files(str(receipt))
            page.get_by_role("button", name="Review a receipt", exact=True).click()
            expect(page.locator("#mt-model")).to_have_value("WH-1000")
            expect(page.locator("#mt-proposal-evidence")).to_contain_text("Model: WH-1000")
            assert call(base)[1]["things"] == []
            page.locator("#mt-specs").fill("Voltage: 5 V\nConnector: USB-C")
            page.locator("#mt-order_items").fill("Headphones\nCable")
            shot(page, "02-receipt-review")
            page.get_by_role("button", name="Save", exact=True).click()
            expect(page.get_by_role("heading", name="Synthetic headphones", exact=True)).to_be_visible()
            page.get_by_label("Match a photo · Photo", exact=True).set_input_files(str(photo))
            page.get_by_role("button", name="Match a photo", exact=True).click()
            expect(page.get_by_role("heading", name="A saved thing matches this label", exact=True)).to_be_visible(timeout=120000)
            expect(page.locator("#myThingsRoot pre")).to_contain_text("MODEL WH-1000")
            shot(page, "03-photo-evidence")
            page.get_by_role("button", name="Attach a file", exact=True).click()
            expect(page.get_by_role("heading", name="Synthetic headphones", exact=True)).to_be_visible()
            page.get_by_label("Describe what happened", exact=True).fill("The left speaker stopped working")
            page.get_by_role("button", name="Prepare a return", exact=True).click()
            expect(page.locator("#mt-result")).to_contain_text("Draft request for return")
            shot(page, "04-return-draft")
            with page.expect_download() as download:
                page.get_by_role("button", name="Download a draft packet", exact=True).click()
            path = tmp_path / "draft.zip"
            download.value.save_as(path)
            import zipfile
            with zipfile.ZipFile(path) as archive:
                assert "return" in archive.read("request-draft.txt").decode()
                assert any(n.endswith("label.png") for n in archive.namelist())
                assert any(n.endswith("receipt.txt") for n in archive.namelist())
            box = tmp_path / "box.png"
            box.write_bytes(label_photo("Voltage: 12 V"))
            page.get_by_label("Read words from a photo · Photo", exact=True).set_input_files(str(box))
            page.get_by_role("button", name="Read words from a photo", exact=True).click()
            expect(page.get_by_label("Words on the box or delivery note", exact=True)).to_have_value("Voltage: 12 V", timeout=30000)
            page.get_by_role("button", name="Compare details", exact=True).click()
            expect(page.locator("#mt-result")).to_contain_text("5 V / 12 V")
            page.get_by_role("button", name="Check delivery", exact=True).click()
            expect(page.locator("#mt-result")).to_contain_text("Cable")
            page.get_by_label("Manual web address", exact=True).fill("https://example.invalid/manual")
            page.get_by_role("button", name="Open the manual online", exact=True).click()
            expect(page.get_by_role("status")).to_contain_text("Online access is off")
            page.get_by_label("Match a photo · Photo", exact=True).set_input_files(str(bad))
            page.get_by_role("button", name="Match a photo", exact=True).click()
            expect(page.get_by_role("heading", name="These words could not be read reliably. Try a clearer photo or enter the details yourself.", exact=True)).to_be_visible()
            shot(page, "05-red-unreadable")
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(base + "/my-things?ui_locale=lt")
            expect(page.get_by_role("heading", name="Mano daiktai", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            shot(page, "06-narrow-lithuanian")
            page.goto(base + "/my-things?ui_locale=ar")
            assert page.locator("html").get_attribute("dir") == "rtl"
            shot(page, "07-rtl")
            page.goto(base + "/settings?ui_locale=en")
            expect(page.locator("#myThingsSetting input")).to_be_checked()
            page.locator("#myThingsSetting input").uncheck()
            expect(page.locator("#myThingsSetting input")).to_be_enabled()
            assert call(base, "preferences")[1]["enabled"] is False
            page.locator("#myThingsSetting input").check()
            expect(page.locator("#myThingsSetting input")).to_be_enabled()
            assert call(base, "preferences")[1]["enabled"] is True
            assert errors == []
            assert all(url.startswith(base + "/") or url.startswith("blob:") or url.startswith("data:") for url in requests)
        finally:
            browser.close()
    record_browser_children(ended=True)
    for item in call(base)[1]["things"]:
        assert call(base, "delete", {"id": item["id"], "revision": item["revision"], "confirm": True})[0] == 200
