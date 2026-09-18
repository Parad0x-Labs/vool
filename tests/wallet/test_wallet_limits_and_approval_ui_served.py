"""Served proof: spending caps and the approval method are settable from the wallet section, and the doors store what the page sent.

Two doors existed with no control in front of them: POST /api/wallet/limits (the spend caps a
proposal is checked against) and POST /api/wallet/approval-method (PIN / password / device). A
cap nobody can set is not a guardrail. This drives a real Chromium against the served page and
asserts on what the daemon STORES afterwards, not on the page's own words.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

PIN = "482913"
PASSWORD = "orbit-lantern-42"


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


@pytest.mark.timeout(600)
def test_caps_and_approval_method_are_set_from_the_page_and_stored_by_the_doors(tmp_path):
    import tests._reader_served_rig as rig
    from core.wallet import custody
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            code, created = _post(daemon.base_url, "/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
            assert code == 200, created
            before = _status(daemon.base_url)
            assert before["approval_method"] == "pin" and before["wallet_id"]
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")

            # --- spending caps: three whole numbers, one Save; the door stores them and the status line re-reads them
            page.wait_for_selector("#vwLimitsSave", state="visible", timeout=20000)
            page.fill("#vwLimitPerTx", "250000")
            page.fill("#vwLimitDaily", "900000")
            page.fill("#vwLimitPerDest", "400000")
            page.click("#vwLimitsSave")
            page.wait_for_function(
                "() => { const l = document.querySelector('#vwLimitsLine'); return !!l && l.textContent.includes('250000') && l.textContent.includes('900000') && l.textContent.includes('400000'); }",
                timeout=20000,
            )
            stored = _status(daemon.base_url)["limits"]
            assert (stored["per_tx_minor"], stored["daily_minor"], stored["per_destination_daily_minor"]) == (250000, 900000, 400000), stored

            # --- approval method: PIN -> password with the current PIN; the door re-seals and status reads back the new method
            page.wait_for_selector("#vwApprovalSave", state="visible", timeout=20000)
            page.select_option("#vwApprovalMethod", "password")
            page.fill("#vwApprovalSecret", PIN)
            page.fill("#vwApprovalNew", PASSWORD)
            page.click("#vwApprovalSave")
            page.wait_for_function(
                "() => { const h = document.querySelector('#vwApprovalCurrent'); return !!h && h.textContent.trim().endsWith(': password'); }",
                timeout=20000,
            )
            after = _status(daemon.base_url)
            assert after["approval_method"] == "password"
            # the switch is real, not a label: the OLD pin no longer unlocks the wallet, the NEW password does
            code_old, _ = _post(daemon.base_url, "/api/wallet/approval-method", {"wallet_id": after["wallet_id"], "method": "pin", "new_pin": "111111", "pin": PIN})
            assert code_old != 200, "the old PIN still unlocked the wallet after the switch"
            code_new, _ = _post(daemon.base_url, "/api/wallet/approval-method", {"wallet_id": after["wallet_id"], "method": "pin", "new_pin": "654321", "password": PASSWORD})
            assert code_new == 200
            assert _status(daemon.base_url)["approval_method"] == "pin"

            # --- a wrong current secret typed on the page is refused and SAID, never swallowed
            page.reload(wait_until="networkidle")
            page.wait_for_selector("#vwApprovalSave", state="visible", timeout=20000)
            page.select_option("#vwApprovalMethod", "password")
            page.fill("#vwApprovalSecret", "000000")
            page.fill("#vwApprovalNew", PASSWORD)
            page.click("#vwApprovalSave")
            page.wait_for_function("() => { const m = document.querySelector('#vwMsg'); return !!m && m.textContent.trim().length > 0; }", timeout=20000)
            assert _status(daemon.base_url)["approval_method"] == "pin", "a refused switch must not change the stored method"
            assert page.query_selector("#vwApprovalSecret").input_value() == "", "secrets are cleared from the boxes after every attempt"
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
