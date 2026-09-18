"""Served proof: the DNA fee collection threshold is set from the wallet section of Settings and the door stores what the
page sent; the page holds the OWNER's saved value and says when an operator override outranks it; the owner's explicit
there is no "collect now": accrued fees ride the next native payment's approval, and the page says so.

Real Chromium against a real ``apps.vool_api_server``. Assertions are on what the daemon STORES and on the page's
rendered words for the two facts a user must not confuse: what they saved and what is in force. The 0.1% rate and the
treasury have no control here, by design (``core.wallet.dna_fees`` constants; a page cannot reach them).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from tests.usepod.test_usepod_x402_composed_served import PIN  # the served wallet suites' disposable fixture PIN


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


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return json.loads(r.read().decode())


def _text(page, selector: str) -> str:
    node = page.query_selector(selector)
    return (node.text_content() or "").strip() if node else ""


def _served(tmp_path, *, collect_min_override: str):
    """A booted daemon with the wallet section enabled; ``collect_min_override`` "" leaves the operator override unset."""
    import tests._reader_served_rig as rig
    from core.wallet import custody

    provider = rig.CapturingProvider(default="ok")
    provider.__enter__()
    daemon = rig.ServedDaemon(
        tmp_path / "home", provider=provider,
        env_extra={
            "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_DNA_FEE_COLLECT_MIN_ATOMIC": collect_min_override,
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        },
    )
    daemon.register_provider()
    try:
        daemon.start()
    except RuntimeError as exc:
        daemon.stop()
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    code, created = _post(daemon.base_url, "/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
    assert code == 200, created
    return provider, daemon


@pytest.mark.timeout(600)
def test_the_threshold_is_saved_from_the_page_and_the_door_stores_it(tmp_path):
    from tests.served_browser import launch_chromium

    provider, daemon = _served(tmp_path, collect_min_override="")
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
        page.wait_for_selector("#vwDnaFeeSave", state="visible", timeout=20000)

        # --- before any save: the documented default is in the box and SAID to be the default
        assert page.input_value("#vwDnaFeeMin") == "100000"
        assert _text(page, "#vwDnaFeeEffective") == "Effective threshold now: 100000 atomic units of USDC (the documented default; save a value to change it)."
        assert page.query_selector("#vwDnaFeeRate") is None and "rate" not in page.locator("#vwDnaFees input").evaluate_all("els => els.map(e => e.id).join(',')")
        assert page.query_selector("#vwDnaFeeCollect") is None, "no separate fee-payment control"

        # --- one whole number, one Save: the door stores it, the page says exactly what was saved, and re-reads it as the owner's value
        page.fill("#vwDnaFeeMin", "7")
        page.click("#vwDnaFeeSave")
        page.wait_for_function(
            "() => { const m = document.querySelector('#vwMsg'); return !!m && m.textContent.trim() === 'DNA fee threshold saved: 7 atomic units of USDC.'; }",
            timeout=20000,
        )
        stored = _get(daemon.base_url, "/api/wallet/dna-fees")["dna_fees"]["collect_min"]
        assert stored["USDC"] == {"asset": "USDC", "atomic": 7, "source": "settings", "settings_atomic": 7, "default_atomic": 100_000}, stored
        assert stored["SOL"] == {"asset": "SOL", "atomic": 1_000_000, "source": "default", "settings_atomic": None, "default_atomic": 1_000_000}, stored
        page.wait_for_function(
            "() => { const e = document.querySelector('#vwDnaFeeEffective'); return !!e && e.textContent.trim() === 'Effective threshold now: 7 atomic units of USDC (your saved value).'; }",
            timeout=20000,
        )
        assert page.input_value("#vwDnaFeeMin") == "7"

        # --- the other asset keeps its own threshold: selecting SOL shows SOL's default, untouched by the USDC save
        page.select_option("#vwDnaFeeAsset", "SOL")
        assert page.input_value("#vwDnaFeeMin") == "1000000"
        assert _text(page, "#vwDnaFeeEffective") == "Effective threshold now: 1000000 atomic units of SOL (the documented default; save a value to change it)."

        # --- a non-threshold is refused on the page and nothing is stored
        page.select_option("#vwDnaFeeAsset", "USDC")
        page.fill("#vwDnaFeeMin", "0")
        page.click("#vwDnaFeeSave")
        page.wait_for_function(
            "() => { const m = document.querySelector('#vwMsg'); return !!m && m.textContent.trim() === 'The threshold is a whole number of atomic units, at least 1.'; }",
            timeout=20000,
        )
        assert _get(daemon.base_url, "/api/wallet/dna-fees")["dna_fees"]["collect_min"]["USDC"]["settings_atomic"] == 7

        # --- nobody volunteers to pay fees through a separate card: no collect control exists; the page says how
        # accrued fees are collected (with the next native payment, under its approval, when economical)
        assert page.query_selector("#vwDnaFeeCollect") is None
        mode = _text(page, "#vwDnaFeeMode")
        assert mode.startswith("Accrued fees are collected with your next native payment, under that payment's approval") and "1%" in mode, mode
        status = _get(daemon.base_url, "/api/wallet/status")["status"]
        assert status["pending"] == [] and status["pending_approvals"] == 0, status
        assert _get(daemon.base_url, "/api/wallet/dna-fees")["dna_fees"]["collection"]["cost_bound_bps"] == 100
    finally:
        browser.close()
        manager.stop()
        daemon.stop()
        provider.__exit__(None, None, None)


@pytest.mark.timeout(600)
def test_an_operator_override_is_said_beside_the_saved_value_and_never_shown_as_it(tmp_path):
    from tests.served_browser import launch_chromium

    provider, daemon = _served(tmp_path, collect_min_override="3")
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
        page.wait_for_selector("#vwDnaFeeSave", state="visible", timeout=20000)

        # --- nothing saved yet: the override is in force and SAID to be one, not called the owner's value
        assert page.input_value("#vwDnaFeeMin") == "3"
        assert _text(page, "#vwDnaFeeEffective") == "Effective threshold now: 3 atomic units of USDC (an operator override is in force; a value you save applies once it is removed)."

        # --- the owner saves 9: stored as the owner's value, SAID as saved, and the effective threshold stays the override's
        page.fill("#vwDnaFeeMin", "9")
        page.click("#vwDnaFeeSave")
        page.wait_for_function(
            "() => { const m = document.querySelector('#vwMsg'); return !!m && m.textContent.trim() === 'DNA fee threshold saved: 9 atomic units of USDC. An operator override keeps the effective threshold at 3 atomic units until it is removed.'; }",
            timeout=20000,
        )
        stored = _get(daemon.base_url, "/api/wallet/dna-fees")["dna_fees"]["collect_min"]["USDC"]
        assert stored == {"asset": "USDC", "atomic": 3, "source": "operator_override", "settings_atomic": 9, "default_atomic": 100_000}, stored
        page.wait_for_function(
            "() => { const e = document.querySelector('#vwDnaFeeEffective'); return !!e && e.textContent.trim() === 'Effective threshold now: 3 atomic units of USDC (an operator override is in force; your saved value of 9 applies once it is removed).'; }",
            timeout=20000,
        )
        assert page.input_value("#vwDnaFeeMin") == "9", "the box holds the owner's value, not the override"
    finally:
        browser.close()
        manager.stop()
        daemon.stop()
        provider.__exit__(None, None, None)
