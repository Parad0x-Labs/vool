"""Served: the Crypto page keeps its place, and the safety cards start short (sls, Nov 13).

sls on the Crypto page: "when i create or add wallet screen flickers and goes up", and the safety cards are "too much
to slide down". Measured on the served page: whenever Settings painted its pane again (coming back to the window,
for instance after a Keychain prompt or after pasting the backup somewhere else), the crypto surface was looked up by
id after the old pane had already been emptied, so it was missed, redrawn from a fresh read, and the page shrank
while it waited: the scroll fell from 1200px to 361px. Now a re-read that brings back the same shows no repaint at
all, a real repaint keeps the scroll and moves the same surface (its open folds and forms) across, and creating or
adding a wallet redraws the surface in place without moving the page. The safety cards show two, with Read more.
Real daemon, real Chromium. No chain is needed: creating a wallet moves no money and reads no balance.
"""
from __future__ import annotations

import json
import os
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
BASE_SEPOLIA = "eip155:84532"

# the pane's scroll, the surface node and how many pane nodes were torn out, while the window regains focus
_FOCUS = """async (target) => {
  const pane = document.getElementById('pane');
  pane.scrollTop = target; await new Promise(r => setTimeout(r, 300));
  const before = pane.scrollTop, surface = document.getElementById('vwCx');
  const tops = []; let run = true, removed = 0;
  const mo = new MutationObserver(ms => ms.forEach(m => m.removedNodes.forEach(n => { if (n.nodeType === 1) removed += 1; })));
  mo.observe(document.getElementById('paneBody'), { childList: true, subtree: true });
  const tick = () => { if (!run) return; tops.push(pane.scrollTop); requestAnimationFrame(tick); }; requestAnimationFrame(tick);
  window.dispatchEvent(new Event('focus'));
  await new Promise(r => setTimeout(r, 2500));
  run = false; mo.disconnect();
  return { before, after: pane.scrollTop, lowest: Math.min(...tops), removed, sameSurface: document.getElementById('vwCx') === surface,
           factsOpen: !!(document.getElementById('vwCxFacts') || {}).open };
}"""


def _door(base: str, path: str, body: dict) -> tuple[int, dict]:
    request = Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": base, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _owner_wallet(base: str) -> None:
    status, created = _door(base, "/api/wallet/setup/create", {"network": BASE_SEPOLIA, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
                                                               "creation_key": f"owner-{uuid.uuid4().hex}", "label": "Main"})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(base, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200, revealed
    status, ready = _door(base, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": revealed["backup"]["ack_token"]})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready


@pytest.mark.timeout(600)
def test_the_crypto_page_keeps_its_place_and_the_safety_cards_start_short(tmp_path):
    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={
            "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        })
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        try:
            manager, browser = launch_chromium()
        except BaseException:
            daemon.stop()
            raise
        try:
            base = daemon.base_url
            _owner_wallet(base)
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{base}/settings#wallet", wait_until="networkidle")
            page.wait_for_selector("#vwCx .vw-cx-account", timeout=30_000)
            page.wait_for_selector(".scam-card", timeout=30_000)

            # --- the safety cards: two show, Read more opens the rest in place and Show less folds them again
            total = page.locator(".scam-card").count()
            assert total > 2, total
            assert page.locator(".scam-card:visible").count() == 2
            more = page.locator("#scamMore")
            assert more.inner_text().strip() == f"Read more ({total - 2} more)" and more.get_attribute("aria-expanded") == "false"
            more.click()
            assert page.locator(".scam-card:visible").count() == total
            assert more.inner_text().strip() == "Show less" and more.get_attribute("aria-expanded") == "true"
            more.click()
            assert page.locator(".scam-card:visible").count() == 2

            # --- coming back to the window with nothing changed: no repaint, no move, the open fold stays open
            page.evaluate("() => { document.getElementById('vwCxFacts').open = true; }")
            for target in (1200, 99_999):
                calm = page.evaluate(_FOCUS, target)
                assert calm["removed"] == 0 and calm["after"] == calm["before"] == calm["lowest"], calm
                assert calm["sameSurface"] and calm["factsOpen"], calm

            # --- something on the page really changed meanwhile: it repaints, keeps the scroll and the same surface
            # (before: the surface was missed, redrawn from a fresh read, and the scroll fell from 1200px to 361px)
            bump = {"n": 0}

            def _changed_safety(route):
                response = route.fetch()
                answer = response.json()
                bump["n"] += 1
                answer["golden_rule"] = (answer.get("golden_rule") or "") + " " * bump["n"]
                route.fulfill(response=response, json=answer)

            page.route("**/api/wallet/safety*", _changed_safety)
            for target in (1200, 99_999):
                moved = page.evaluate(_FOCUS, target)
                assert moved["removed"] > 0, "the changed source did not repaint the pane"
                assert moved["after"] == moved["before"] == moved["lowest"], moved
                assert moved["sameSurface"] and moved["factsOpen"], moved
            page.unroute("**/api/wallet/safety*")

            # --- Add another wallet, pressed where it sits mid-page: the page does not move and the fold stays open
            add = f'.vw-cx-row[data-network="{BASE_SEPOLIA}"] .vw-cx-add-wallet'
            page.wait_for_selector(add, timeout=20_000)
            start = page.evaluate("(s) => { document.querySelector(s).scrollIntoView({block: 'center'}); return document.getElementById('pane').scrollTop; }", add)
            assert start > 100, start
            page.evaluate("""() => { const pane = document.getElementById('pane'); window.__tops = [];
                const tick = () => { window.__tops.push(pane.scrollTop); if (!window.__stop) requestAnimationFrame(tick); }; requestAnimationFrame(tick); }""")
            # the owner's clicks and typing go through the page itself, so the browser never scrolls on its own
            page.evaluate("(s) => document.querySelector(s).click()", add)
            page.wait_for_selector("#vwCxContinue", timeout=20_000)
            page.evaluate("() => document.getElementById('vwCxContinue').click()")
            for selector, value in (("#vwCxMethod", "pin"), ("#vwCxCredential", PIN), ("#vwCxConfirm", PIN)):
                page.wait_for_selector(selector, state="attached", timeout=20_000)
                page.evaluate("([s, v]) => { const e = document.querySelector(s); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); }", [selector, value])
            page.evaluate("() => document.getElementById('vwCxCreate').click()")
            page.wait_for_selector("#vwCxBackup", timeout=30_000)
            page.evaluate("() => document.getElementById('vwCxBackupSaved').click()")
            page.wait_for_selector("#vwCxBackup", state="detached", timeout=20_000)
            page.wait_for_function(f"() => document.querySelectorAll('.vw-cx-row[data-network=\"{BASE_SEPOLIA}\"] .vw-cx-account').length >= 2", timeout=20_000)
            page.wait_for_timeout(1500)
            tops = page.evaluate("() => { window.__stop = true; return window.__tops; }")
            assert min(tops) == start and tops[-1] == start, (start, min(tops), tops[-1])
            assert page.evaluate("() => !!document.getElementById('vwCxFacts').open"), "the redraw closed the open fold"
            assert errors == [], errors
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
