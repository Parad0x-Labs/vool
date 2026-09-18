"""Served proof: the export controls exist for a pocket wallet, use no PIN, and reveal the Phantom key once."""
from __future__ import annotations

import json
import os
import urllib.request

import pytest

from tests.wallet._controlled_helper import install_for_runtime

PIN = "482913"


@pytest.mark.timeout(600)
def test_export_flow_in_a_real_browser_through_the_fake_device_seam(tmp_path):
    import tests._reader_served_rig as rig
    from core.wallet import custody
    from core.wallet.external_signing import b58decode
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        daemon.register_provider()
        controlled = install_for_runtime(tmp_path / "home")  # the runtime finds it where it compiles its own helper; no environment seam
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            def post(path, body):
                req = urllib.request.Request(daemon.base_url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read().decode())
            created = post("/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
            pubkey = created["wallet"]["public_key"] if "wallet" in created else created["profile"]["public_key"]
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
            page.wait_for_selector("#vwExportBtn", state="visible", timeout=20000)
            assert page.query_selector("#vwExport input[type=password]") is None, "the export flow has no PIN box"
            page.select_option("#vwExportTarget", "phantom")
            page.click("#vwExportBtn")
            page.wait_for_selector("#vwExportValue", state="visible", timeout=20000)
            value = page.inner_text("#vwExportValue").strip()
            from solders.keypair import Keypair
            raw = b58decode(value)
            assert len(raw) == 64 and str(Keypair.from_bytes(raw).pubkey()) == pubkey
            assert "Phantom" in page.inner_text("#vwExportReveal") and "owns the wallet" in page.inner_text("#vwExportReveal")
            page.click("#vwExportDone")
            page.wait_for_selector("#vwExportReveal", state="detached", timeout=10000)
            assert value not in page.content(), "the key is gone from the page after Done"
            calls = controlled.journal()
            assert [c["argv"][1] for c in calls] == ["store", "read"], "creation stored the unlock secret; the export read it through the real helper"
            stored = json.loads(calls[0]["stdin"])["secret_b64"]
            assert all(stored not in " ".join(c["argv"]) and stored not in json.dumps(c["env"]) for c in calls), "no secret in argv or environment on the served path"
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
