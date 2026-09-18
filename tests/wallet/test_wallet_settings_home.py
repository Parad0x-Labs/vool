"""The wallet has a reachable home in the integrated Settings.

Measured on the combined source (2026-09-07): the wallet fragment mounted only into the chat page's legacy
settings overlay, and the integrated Settings redesign opens `/settings` instead -- so the wallet controls
(create, restore, watch-only, connect an EVM or Phantom wallet, receipts) existed in the DOM but no user could
reach them, and every crypto-lane browser journey timed out on a hidden button. The Settings model now carries
a Wallet group whose widget hosts the same fragment; the fragment mounts into that host.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from core.vool_settings_page import render_vool_settings_html, settings_groups
from core.wallet_fragment import render_wallet_fragment

ROOT = Path(__file__).resolve().parents[2]


def test_the_settings_model_has_a_wallet_group_with_the_fragment_widget() -> None:
    groups = {g["id"]: g for g in settings_groups()}
    assert "wallet" in groups, sorted(groups)
    rows = groups["wallet"]["rows"]
    assert any(r.get("kind") == "custom" and r.get("widget") == "wallet" for r in rows), rows


def test_the_settings_page_ships_the_wallet_fragment_and_its_host_widget() -> None:
    html = render_vool_settings_html()
    assert "window.VoolWallet" in html, "the wallet fragment is not included in the Settings page"
    assert "walletHost" in html and "widgetWallet" in html


def test_the_fragment_mounts_into_a_settings_host() -> None:
    fragment = render_wallet_fragment()
    assert "mountInto" in fragment and "walletHost" in fragment
    # the fragment keeps its laws: one namespace, no browser storage
    assert "window.VoolWallet" in fragment and "localStorage" not in fragment


@pytest.mark.timeout(600)
def test_served_settings_page_shows_the_wallet_section_with_its_controls(tmp_path):
    """Real daemon, real Chromium: /settings#wallet renders the status, the soft warning and the
    EVM connect control (the declared testnets include EVM networks)."""
    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            # the lane convention: an interpreter whose served daemon cannot boot here (no ML backend module
            # under a local-only profile) skips with the cause, never fails; the repo interpreter executes it
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
            page.wait_for_selector("#vwStatus", state="visible", timeout=20000)
            page.wait_for_selector("#vwConnectEvm", state="visible", timeout=20000)
            text = page.inner_text("#walletHost")
            assert "custody" in text.lower(), text[:300]
            assert "not been audited by an external party" in text, text[:400]
            assert page.is_visible("#vwConnectPhantom") or page.is_visible("#vwCreateBtn"), "wallet controls are not visible"
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
