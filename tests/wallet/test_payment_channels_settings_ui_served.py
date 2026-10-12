"""Served proof: the owner chooses the payment channels from Settings, and the choice decides what pays.

Real Chromium against a real ``apps.vool_api_server``, the scripted testnet RPC and a loopback x402 resource. The owner
turns VOOL's own x402 channel off on the Crypto section of Settings: the daemon stores it, a reload shows it, a paid
request through the x402 door parks nothing, and a payment parked before is refused on its approval card with the
PIN typed, before anything is held or sent. Turned back on from the same page, the same card pays once and the
resource is delivered once. Assertions are on what the daemon stores and the chain stub saw, and on the page's words.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from tests.wallet._rig import ScriptedRpc, ScriptedX402Resource

pytestmark = [pytest.mark.safety]

PIN = "482913"
X402_LABEL = "x402, built into VOOL"
OFF_MESSAGE = "That payment goes through a payment channel that is turned off, so nothing was signed or sent."


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}


def _status(base: str) -> dict:
    with urllib.request.urlopen(base + "/api/wallet/status", timeout=60) as r:
        return json.loads(r.read().decode())["status"]


def _channels(base: str) -> dict[str, dict]:
    return {row["channel_id"]: row for row in _status(base)["crypto_pilot"]["payment_channels"]}


def _open_channels(page, base: str) -> None:
    page.goto(f"{base}/settings#wallet", wait_until="networkidle")
    page.wait_for_selector("#vwCxChannels #vwCh_x402", state="visible", timeout=20000)


def _switch(page, channel_id: str, on: bool, label: str) -> None:
    box = f"#vwCh_{channel_id}"
    page.check(box) if on else page.uncheck(box)
    said = f"{label} is on." if on else f"{label} is off: nothing is paid through it."
    page.wait_for_function(
        "([row, said]) => { const r = document.querySelector(row); const m = r && r.parentElement.querySelector('.vw-msg');"
        " return !!m && m.textContent.trim() === said && !document.querySelector(row + ' input').disabled; }",
        arg=[f'#vwCxChannels .vw-cx-channel[data-channel="{channel_id}"][data-on="{"true" if on else "false"}"]', said],
        timeout=20000,
    )


@pytest.mark.timeout(600)
def test_the_owner_turns_a_payment_channel_off_and_on_from_settings_and_the_doors_follow(tmp_path):
    import tests._reader_served_rig as rig
    from core.wallet import custody
    from tests.served_browser import launch_chromium

    with ScriptedRpc() as chain, ScriptedX402Resource(chain) as resource, rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={
            "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1", "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
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
            code, created = _post(base, "/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
            assert code == 200, created
            wallet_id = _status(base)["wallet_id"]
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            # --- every channel is listed with who builds it and where it pays, each on until the owner says otherwise
            _open_channels(page, base)
            stored = _channels(base)
            rows = page.locator("#vwCxChannels .vw-cx-channel")
            assert rows.evaluate_all("els => els.map(e => e.getAttribute('data-channel'))") == ["x402", "paykit_x402", "paykit_mpp", "x402_sdk"]
            for channel_id, row in stored.items():
                assert page.is_checked(f"#vwCh_{channel_id}") and row["on"] is True, row
                text = page.locator(f'#vwCxChannels .vw-cx-channel[data-channel="{channel_id}"]').inner_text()
                assert row["label"] in text and f"Built by {row['built_by']} · pays on {', '.join(row['network_names'])}" in text, text
                # a channel this install cannot use says why, in words, instead of looking ready
                assert ("not usable on this install: the optional pay extra (Solana pay-kit) is not installed" in text) == (row["reason"] == "paykit_not_installed"), (row, text)
                assert ("not usable on this install: the optional x402 extra (x402 Foundation SDK) is not installed" in text) == (row["reason"] == "x402_sdk_not_installed"), (row, text)
            assert "Solana Devnet" in stored["x402"]["network_names"]

            # --- off from the page: stored by the door, kept across a reload
            _switch(page, "x402", False, X402_LABEL)
            assert {key: row["on"] for key, row in _channels(base).items()} == {"x402": False, "paykit_x402": True, "paykit_mpp": True, "x402_sdk": True}
            _open_channels(page, base)
            assert not page.is_checked("#vwCh_x402") and page.is_checked("#vwCh_paykit_x402")

            # --- a paid request while it is off: the 402 is read, nothing is parked, held or sent
            code, refused = _post(base, "/api/wallet/x402/fetch", {"url": resource.url, "wallet_id": wallet_id})
            assert (code, refused["error"], refused["fault"]["context"]["channel"]) == (403, "wallet_payment_channel_off", "x402"), refused
            assert refused["message"].startswith(OFF_MESSAGE)
            assert resource.challenges == 1 and _status(base)["pending"] == [] and chain.send_count() == 0

            # --- on again: the same request parks one payment for the owner
            _switch(page, "x402", True, X402_LABEL)
            code, fetched = _post(base, "/api/wallet/x402/fetch", {"url": resource.url, "wallet_id": wallet_id})
            assert code == 200 and fetched["outcome"]["status"] == "payment_required" and fetched["outcome"]["proposal_id"], fetched
            proposal_id = fetched["outcome"]["proposal_id"]

            # --- turned off while it waits: the card says so before the PIN, and the approval is refused with nothing sent
            _switch(page, "x402", False, X402_LABEL)
            page.goto(f"{base}/chat", wait_until="domcontentloaded")
            card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
            card.wait_for(timeout=30000)
            channel_row = card.locator('[data-channel="x402"]')
            assert channel_row.get_attribute("data-channel-on") == "false"
            assert channel_row.inner_text().strip() == f"payment channel: {X402_LABEL} (turned off: turn it on in Settings, Crypto, Payment channels to approve this payment)"
            card.locator(".vw-pin").fill(PIN)
            card.locator(".vw-approve").click()
            page.wait_for_function(
                "([pid, said]) => { const r = document.querySelector('.vw-card[data-proposal=\"' + pid + '\"] .vw-result'); return !!r && r.textContent.startsWith(said); }",
                arg=[proposal_id, OFF_MESSAGE], timeout=20000,
            )
            waiting = _status(base)
            assert [row["proposal_id"] for row in waiting["pending"]] == [proposal_id] and chain.send_count() == 0 and resource.deliveries == []

            # --- on again from Settings: the same card pays once and the resource is delivered once
            _open_channels(page, base)
            _switch(page, "x402", True, X402_LABEL)
            page.goto(f"{base}/chat", wait_until="domcontentloaded")
            card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
            card.wait_for(timeout=30000)
            assert card.locator('[data-channel="x402"]').get_attribute("data-channel-on") == "true"
            assert card.locator('[data-channel="x402"]').inner_text().strip() == f"payment channel: {X402_LABEL}"
            card.locator(".vw-pin").fill(PIN)
            card.locator(".vw-approve").click()
            page.wait_for_function(
                "(pid) => { const r = document.querySelector('.vw-card[data-proposal=\"' + pid + '\"] .vw-result'); return !!r && r.textContent.indexOf('confirmed') >= 0; }",
                arg=proposal_id, timeout=30000,
            )
            assert chain.send_count() == 1 and _status(base)["pending"] == []
            code, delivered = _post(base, "/api/wallet/x402/retry", {"proposal_id": proposal_id})
            assert code == 200 and delivered["outcome"]["status"] == "delivered" and len(resource.deliveries) == 1, delivered
            assert errors == [], errors
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
