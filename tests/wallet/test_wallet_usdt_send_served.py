"""USDT from the Crypto page's Send form against the REAL application: pick "What to send", type an amount in USDT, and
the request that reaches the wallet is ONE USDT proposal in USDT's 6 decimals, previewed on the real quote sheet.

The daemon is the real app in its own disposable home; the chain is the loopback Solana node answering as Mainnet
(simulated chain evidence, no real funds); the browser is the served lane's chromium, allowed to reach the daemon only.
The Crypto Pilot account is created through the panel's own setup flow. Nothing is approved here: the money path past
the sheet is pinned by tests/wallet/test_wallet_usdt_send.py.
"""
from __future__ import annotations

import json
from urllib.request import urlopen

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.wallet._simulated_solana import MAINNET_GENESIS, SimulatedSolanaNode

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
PIN = "482913"


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=30) as response:
        return json.load(response)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("wallet-usdt") / "home"
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as node, rig.CapturingProvider(default="Acknowledged.") as provider:
        node.create_mint(USDT_MINT, decimals=6)
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            "VOOL_WALLET_ENABLED": "1",
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "mainnet",
            "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_MAINNET: node.url}),
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        })
        try:
            daemon.start(timeout=180)
            certification = daemon.certify(timeout=120)
            assert certification.get("state") == "verified", certification
            yield daemon, node
        finally:
            daemon.stop()


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


def test_choosing_usdt_on_the_send_form_asks_for_usdt_in_its_own_units(browser, served):
    from solders.keypair import Keypair

    daemon, node = served
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    proposed: list[dict] = []
    page.on("request", lambda request: proposed.append(json.loads(request.post_data or "{}")) if request.url.endswith("/api/wallet/propose") else None)
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")

    # a Crypto Pilot account on Solana, created through the panel's own setup flow
    page.wait_for_selector("#vwHomeBtn", state="attached", timeout=15_000)
    if page.locator("#homeMenu").get_attribute("open") is None:
        page.click("#homeToggle")
    page.locator("#vwHomeBtn").click()
    page.wait_for_selector("#vwHomeCreate", timeout=10_000)
    page.locator("#vwHomeCreate").click()
    page.wait_for_selector("#vwHomeCMethod")
    page.select_option("#vwHomeCMethod", "pin")
    page.fill("#vwHomeCCredential", PIN)
    page.fill("#vwHomeCConfirm", PIN)
    page.fill("#vwHomeLabel", "Everyday")
    page.locator("#vwHomeCreateGo").click()
    page.wait_for_selector("#vwCxBackup", timeout=30_000)
    page.locator("#vwCxBackupSaved").click()
    page.wait_for_selector("#vwHomeAccount", timeout=15_000)
    account = next(a for a in _get(daemon, "/api/wallet/status")["status"]["accounts"] if a["label"] == "Everyday")
    assert (account["mode"], account["network"], account["can_send_tokens"]) == ("pocket_sealed", SOLANA_MAINNET, True), account
    node.fund_sol(account["public_key"], 20_000_000)
    node.fund_token(account["public_key"], USDT_MINT, 25_000_000)

    page.locator("#vwHomeSend").click()
    page.wait_for_selector("#vwHomeSendAsset")
    options = page.locator("#vwHomeSendAsset option")
    assert [options.nth(i).get_attribute("value") for i in range(options.count())] == ["SOL", "USDT"]
    assert page.locator("label[for=vwHomeSendAmount]").text_content() == "Amount (SOL)"
    page.select_option("#vwHomeSendAsset", "USDT")
    assert page.locator("label[for=vwHomeSendAmount]").text_content() == "Amount (USDT)"
    note = page.locator("#vwHomeBody [aria-live=polite]").text_content() or ""
    assert "network fee is paid in SOL" in note and "balance line above" not in note, note
    page.select_option("#vwHomeSendAsset", "SOL")
    assert "balance line above" in (page.locator("#vwHomeBody [aria-live=polite]").text_content() or "")
    page.select_option("#vwHomeSendAsset", "USDT")

    tomas = Keypair().pubkey()
    node.fund_sol(str(tomas), 5_000_000)
    node.open_token_account(str(tomas), USDT_MINT)
    page.fill("#vwHomeSendTo", str(tomas))
    page.fill("#vwHomeSendAmount", "5.25")  # within the default USDT cap for one send
    page.locator("#vwHomeSendGo").click()
    page.wait_for_selector("#vwSheet", timeout=30_000)
    page.wait_for_selector("#vwSheetApprove:enabled", timeout=60_000)

    assert len(proposed) == 1, proposed
    assert (proposed[0]["asset"], str(proposed[0]["amount_minor"]), proposed[0]["network"]) == ("USDT", "5250000", SOLANA_MAINNET), proposed[0]
    sheet = page.locator("#vwSheet").text_content() or ""
    assert "5.25 USDT" in sheet, sheet
    assert node.distinct_sends() == 0, "a preview signs and sends nothing"
    assert errors == [], errors
