"""Served proof: the spending limits card on the new chat screen.

A running VOOL that only runs local models shows no card. Once the wallet is turned on, the new chat screen asks once,
naming the built-in limits; "Keep these" puts the card away, and it stays away after a reload.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from tests import _reader_served_rig as rig
from tests import served_browser


def _post(base_url: str, path: str, body: dict) -> None:
    request = Request(base_url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                      method="POST")
    with urlopen(request, timeout=60) as response:
        assert response.status == 200


def _new_chat(browser, base_url):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base_url) else route.abort())
    page.goto(base_url + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector(".empty-greet", timeout=20000)
    page.wait_for_load_state("networkidle")
    return page, errors


def test_the_spending_card_shows_only_once_money_can_be_spent(tmp_path):
    with rig.CapturingProvider() as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=120)
            manager, browser = served_browser.launch_chromium()
            try:
                page, errors = _new_chat(browser, daemon.base_url)
                card = page.locator("[data-testid='spend-card']")
                assert card.count() == 1 and not card.is_visible()
                assert not page.locator(".today-box").is_visible()
                assert not errors, errors
                page.close()

                _post(daemon.base_url, "/api/settings/prefs", {"wallet_enabled": True})
                page, errors = _new_chat(browser, daemon.base_url)
                card = page.locator("[data-testid='spend-card']")
                card.wait_for(state="visible", timeout=15000)
                text = card.inner_text()
                assert "Spending limits for paid models" in text, text
                assert "$5.00" in text and "$25.00" in text, text
                # Nothing for today: no empty outline above the card.
                assert not page.locator(".today-box").is_visible()
                # "Set my limits" opens Settings at the limits and puts nothing in the message box.
                with page.expect_popup() as popup:
                    page.locator("[data-spend-set]").click()
                assert popup.value.url.endswith("/settings#budgets/spend"), popup.value.url
                popup.value.close()
                assert page.locator("#input").input_value() == ""
                page.locator("[data-spend-keep]").click()
                card.wait_for(state="hidden", timeout=15000)
                assert not errors, errors
                page.close()

                page, errors = _new_chat(browser, daemon.base_url)
                assert not page.locator("[data-testid='spend-card']").is_visible()
                assert not errors, errors
                page.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
