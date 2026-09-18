"""Stay safe: the tricks that empty wallets, in words anyone can act on, with a picture each.

Operator decision (2026-09-07): the wallet's Settings carry crypto knowledge for people who do not understand
what a wallet is telling them -- lock/unlock scams, fake support asking for the secret phrase "to repair the
wallet", fake airdrops and migrations, unlimited approvals, lookalike addresses. Short lines, no jargon, one
illustration per card, and available before the wallet is even switched on.
"""
from __future__ import annotations

import os

import pytest

JARGON = ("eip", "calldata", "typed data", "nonce", "hex", "rpc", "keccak", "abi", "erc-", "secp")


def test_the_cards_are_many_plain_and_illustrated():
    from core.wallet.scam_school import GOLDEN_RULE, cards

    rows = cards()
    assert len(rows) >= 10
    ids = [c["id"] for c in rows]
    assert len(ids) == len(set(ids))
    for c in rows:
        for key in ("id", "emoji", "title", "what_happens", "they_want", "you_do", "svg"):
            assert c.get(key), (c.get("id"), key)
        for key in ("title", "what_happens", "they_want", "you_do"):
            text = c[key]
            assert len(text.split()) <= 28, (c["id"], key, text)
            assert not any(j in text.lower() for j in JARGON), (c["id"], key, text)
        assert c["svg"].lstrip().startswith("<svg") and 'viewBox="0 0 64 64"' in c["svg"]
        assert "<script" not in c["svg"].lower() and "onload" not in c["svg"].lower()
    assert "Nobody real ever needs your secret phrase or private key." in GOLDEN_RULE
    titles = " ".join(c["title"].lower() for c in rows)
    for must in ("support", "airdrop", "migrat", "approv", "address", "phrase"):
        assert must in titles, must


def test_the_route_serves_the_cards_while_the_wallet_is_off(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices
    from tests.wallet.test_wallet_api import _get

    app = create_app(RuntimeServices(display_name="VOOL"))
    status, body = _get(app, "/api/wallet/status")
    assert status == 200 and body["status"]["enabled"] is False
    status, body = _get(app, "/api/wallet/safety")
    assert status == 200 and body["ok"] is True and len(body["cards"]) >= 10 and body["golden_rule"]


def test_the_settings_group_carries_the_panel_after_the_switch():
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    group = next(g for g in settings_groups() if g["id"] == "wallet")
    ids = [r["id"] for r in group["rows"]]
    assert ids[:2] == ["wallet_enabled", "wallet_safety"], ids
    row = group["rows"][1]
    assert row["kind"] == "custom" and row["widget"] == "scam_school" and row["read"] == {"url": "/api/wallet/safety"}
    assert "widgetScamSchool" in render_vool_settings_html()


def test_red_meanings_point_at_the_panel():
    from core.wallet_fragment import render_wallet_fragment

    assert "Stay safe" in render_wallet_fragment()


@pytest.mark.timeout(600)
def test_served_settings_show_the_cards_with_the_wallet_off(tmp_path):
    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "", "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
            page.wait_for_selector(".scam-card", state="visible", timeout=20000)
            count = page.evaluate("() => document.querySelectorAll('.scam-card').length")
            with_svg = page.evaluate("() => Array.from(document.querySelectorAll('.scam-card')).filter(c => c.querySelector('svg')).length")
            assert count >= 10 and with_svg == count, (count, with_svg)
            text = page.inner_text("#paneBody")
            assert "Nobody real ever needs your secret phrase or private key." in text
            assert "repair" in text.lower() and "airdrop" in text.lower()
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
