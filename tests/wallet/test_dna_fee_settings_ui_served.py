"""Served proof: the DNA fee line and its collection threshold in the wallet section of Settings.

The DNA service fee was charged only on UsePod's paid answers, and UsePod has stopped, so the panel says nothing about
a DNA fee to a person with none on record. A person with fees on record (from earlier UsePod use) sees the line and
sets the collection threshold from the page: the door stores what the page sent; the page holds the OWNER's saved
value and says when an operator override outranks it; there is no "collect now": accrued fees ride the next native
payment's approval, and the page says so.

Real Chromium against a real ``apps.vool_api_server``. A fee is put on record as an older VOOL left one: one accrual in
the fee ledger in the daemon's own store, through the owner's CLI (a child interpreter with the daemon's home; no HTTP
door writes money). Assertions are on what the daemon STORES and
on the page's rendered words for the two facts a user must not confuse: what they saved and what is in force. The
0.1% rate and the treasury have no control here, by design (``core.wallet.dna_fees`` constants; a page cannot reach
them).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.wallet.test_wallet_limits_and_approval_ui_served import PIN  # the wallet suites' disposable fixture PIN


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


REPO_ROOT = Path(__file__).resolve().parents[2]
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
FEE_BASIS_ATOMIC = 400_000  # one earlier accepted payment of 0.40 USDC: its 0.1% fee is 400 atomic units


def _shot(page, name: str) -> None:
    """The wallet panel itself (Settings scrolls inside its own pane, so a page shot would show only its top)."""
    folder = os.environ.get("VOOL_DNA_FEE_PANEL_SHOTS")
    if folder:
        page.locator("#vwStatus").screenshot(path=os.path.join(folder, name))


def _put_a_fee_on_record(daemon) -> None:
    """A DNA fee on record in the daemon's own store, as an older VOOL left one: the 0.1% fee of one accepted
    0.40 USDC payment on Solana Mainnet, accrued in the fee ledger under the production fee terms (the lane that
    accrued fees left VOOL in 0.8, so only such a record can exist). The owner's CLI writes it (a child interpreter
    with the daemon's home and environment); no HTTP door writes money. Synthetic figures and a synthetic payer;
    nothing touches any chain."""
    script = f"""
import uuid
from solders.keypair import Keypair
from core import service_fee_ledger
from core.wallet import dna_fees
network, amount = {SOLANA_MAINNET!r}, {FEE_BASIS_ATOMIC}
payer = str(Keypair().pubkey())
_key, terms = dna_fees.identity_for(payer=payer, network=network, asset="USDC")
with dna_fees._ledger_txn() as conn:
    service_fee_ledger.accrue(conn, liability_id="synthetic-" + uuid.uuid4().hex, operation_id="synthetic-" + uuid.uuid4().hex,
                              payer_account=payer, network=terms["network"], asset_key=terms["asset_key"], treasury_owner=terms["treasury_owner"],
                              policy_id=terms["policy_id"], basis_atomic=amount, rate_bps=int(terms["rate_bps"]),
                              evidence_id="synthetic", evidence_kind="synthetic", source="synthetic test funds: dna fee panel proof")
"""
    env = daemon.env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-B", "-c", script], env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    identities = _get(daemon.base_url, "/api/wallet/dna-fees")["dna_fees"]["identities"]
    assert [(row["asset"], row["owed_atomic"]) for row in identities] == [("USDC", FEE_BASIS_ATOMIC // 1000)], identities


def _prove_owner(page) -> None:
    """Changing the fee threshold needs the wallet PIN: the page asks for it once and saves again with it."""
    page.wait_for_selector("#vwOwnerProof", state="visible", timeout=20000)
    page.fill("#vwOwnerProofSecret", PIN)
    page.click("#vwOwnerProofOk")


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
        _put_a_fee_on_record(daemon)  # the threshold is shown to a person with DNA fees on record
        page = browser.new_page()
        page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
        # the other ways to add a wallet start folded; a same-page visit keeps it open, so open it only when it is closed
        if not page.locator("#walletMore").evaluate("d => d.open"):
            page.locator("#walletMore > summary").click()
        page.wait_for_selector("#vwDnaFeeSave", state="visible", timeout=20000)

        # --- before any save: the documented default is in the box and SAID to be the default
        assert page.input_value("#vwDnaFeeMin") == "100000"
        assert _text(page, "#vwDnaFeeEffective") == "Effective threshold now: 100000 atomic units of USDC (the documented default; save a value to change it)."
        assert page.query_selector("#vwDnaFeeRate") is None and "rate" not in page.locator("#vwDnaFees input").evaluate_all("els => els.map(e => e.id).join(',')")
        assert page.query_selector("#vwDnaFeeCollect") is None, "no separate fee-payment control"

        # --- one whole number, one Save: the door stores it, the page says exactly what was saved, and re-reads it as the owner's value
        page.fill("#vwDnaFeeMin", "7")
        page.click("#vwDnaFeeSave")
        _prove_owner(page)
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
        _put_a_fee_on_record(daemon)  # the threshold is shown to a person with DNA fees on record
        page = browser.new_page()
        page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
        # the other ways to add a wallet start folded; a same-page visit keeps it open, so open it only when it is closed
        if not page.locator("#walletMore").evaluate("d => d.open"):
            page.locator("#walletMore > summary").click()
        page.wait_for_selector("#vwDnaFeeSave", state="visible", timeout=20000)

        # --- nothing saved yet: the override is in force and SAID to be one, not called the owner's value
        assert page.input_value("#vwDnaFeeMin") == "3"
        assert _text(page, "#vwDnaFeeEffective") == "Effective threshold now: 3 atomic units of USDC (an operator override is in force; a value you save applies once it is removed)."

        # --- the owner saves 9: stored as the owner's value, SAID as saved, and the effective threshold stays the override's
        page.fill("#vwDnaFeeMin", "9")
        page.click("#vwDnaFeeSave")
        _prove_owner(page)
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


@pytest.mark.timeout(600)
def test_a_wallet_with_no_dna_fee_on_record_shows_none_and_one_on_record_shows_the_line(tmp_path):
    """UsePod's paid answers were the only payments that carried the DNA fee. The wallet panel of a person with no fee on
    record says nothing about one: no fee line, no threshold control. Once a fee is on record (earlier UsePod use), the
    same panel shows the line with what is owed, and the threshold control."""
    from tests.served_browser import launch_chromium

    provider, daemon = _served(tmp_path, collect_min_override="")
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(f"{daemon.base_url}/settings#wallet", wait_until="networkidle")
        # the other ways to add a wallet start folded; a same-page visit keeps it open, so open it only when it is closed
        if not page.locator("#walletMore").evaluate("d => d.open"):
            page.locator("#walletMore > summary").click()
        # spending caps sit under Developer options; a same-page visit keeps it open, so open it only when it is closed
        if not page.locator("#vwLegacyDev").evaluate("d => d.open"):
            page.locator("#vwLegacyDev > summary").click()
        page.wait_for_selector("#vwLimits", state="visible", timeout=20000)
        assert _get(daemon.base_url, "/api/wallet/dna-fees")["dna_fees"]["identities"] == []
        assert page.locator("#vwDnaFeesLine").count() == 0 and page.locator("#vwDnaFees").count() == 0
        assert "DNA" not in page.locator("#vwStatus").inner_text()
        _shot(page, "dna-fee-panel-nothing-on-record.png")

        _put_a_fee_on_record(daemon)
        page.reload(wait_until="networkidle")
        # the other ways to add a wallet start folded; a same-page visit keeps it open, so open it only when it is closed
        if not page.locator("#walletMore").evaluate("d => d.open"):
            page.locator("#walletMore > summary").click()
        page.wait_for_selector("#vwDnaFeeSave", state="visible", timeout=20000)
        line = _text(page, "#vwDnaFeesLine")
        assert line.startswith("DNA service fee 0.1% on native x402 payments · 0.0004 USDC owed"), line
        assert page.input_value("#vwDnaFeeMin") == "100000"
        _shot(page, "dna-fee-panel-fee-on-record.png")
    finally:
        browser.close()
        manager.stop()
        daemon.stop()
        provider.__exit__(None, None, None)
