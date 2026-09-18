"""The Wallet home (Home -> Wallet) against the REAL application: one served journey.

The daemon is the real app in its own disposable home; the chain is the loopback Solana-dialect RPC
answering as Devnet (simulated chain evidence); the browser is the served lane's chromium, allowed to
reach the daemon only. The journey rides the product's own doors the whole way: a fresh profile has
no Home entry and the panel never opens; enabling Crypto (the prefs door the Settings switch uses)
adds exactly one entry and creates nothing; the panel's watch-only form registers an address and its
Send is honestly disabled; the panel's Create flow seals a pilot wallet, shows the one-time backup and
finishes setup through the acknowledge door; Receive shows the exact address and a locally drawn QR;
Send is ordinary units -> one proposal -> the REAL quote sheet (server values), where Decide later
leaves the request pending and reopenable and Cancel is the server reject; disabling removes the
entry and re-enabling restores it with every stored wallet intact. No mainnet, no real funds, no
extension, no secret export: the backup value is asserted by key names only, never by value.
"""
from __future__ import annotations

import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.wallet._rig import ScriptedRpc

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
LAMPORTS = 5_000_000_000  # 5 SOL of simulated devnet funds for the funded leg


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=30) as response:
        return json.load(response)


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("wallet-home") / "home"
    with ScriptedRpc() as chain, rig.CapturingProvider(default="Acknowledged.") as provider:
        chain.real_signature = True
        chain.status_keyed = True
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            # no VOOL_WALLET_ENABLED here on purpose: a fresh profile starts with Crypto OFF
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        })
        try:
            daemon.start(timeout=180)
            certification = daemon.certify(timeout=120)
            assert certification.get("state") == "verified", certification
            yield daemon, chain
        finally:
            daemon.stop()
        assert daemon.process is None or daemon.process.poll() is not None


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


def _page(browser, daemon):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    return page, errors


def test_served_wallet_home_journey(browser, served):
    daemon, chain = served

    # 1. a fresh profile: Crypto off, no Home entry, and no balance traffic from the fragment
    page, errors = _page(browser, daemon)
    page.wait_for_timeout(900)
    assert page.locator("#vwHomeBtn").count() == 0
    assert page.locator("#vwHomeOverlay").count() == 0

    # 2. enable through the door the Settings switch uses: the entry appears exactly once
    status, _ = _door(daemon, "/api/settings/prefs", {"wallet_enabled": True})
    assert status == 200
    page.wait_for_selector("#vwHomeBtn", state="attached", timeout=10_000)
    assert page.locator("#vwHomeBtn").count() == 1
    if page.locator("#homeMenu").get_attribute("open") is None:
        page.click("#homeToggle")
    page.locator("#vwHomeBtn").click()
    page.wait_for_selector("#vwHomeOverlay", state="attached")
    # enabled with no wallet: the setup choices, and nothing was created by opening the panel
    page.wait_for_selector("#vwHomeCreate", timeout=10_000)
    wallets_after_open = _get(daemon, "/api/wallet/status")["status"].get("accounts") or []
    assert wallets_after_open == [], "opening the panel must not create a wallet"

    # 3. watch-only through the panel's own form: Send is honestly disabled for it
    watch_key = _sol_key()
    chain.balances[watch_key] = 0  # this address holds nothing; the chain's default is 5 SOL
    page.locator("#vwHomeWatch").click()
    page.fill("#vwHomeWatchAddr", watch_key)
    page.fill("#vwHomeWatchLabel", "Observation post")
    page.locator("#vwHomeWatchGo").click()
    page.wait_for_selector("#vwHomeAccount", timeout=10_000)
    page.wait_for_selector("#vwHomeBalance >> text=confirmed zero", timeout=20_000)  # the chain answers 0 for the new key
    assert page.locator("#vwHomeSend").is_disabled()
    assert "watch-only" in (page.locator("#vwHomeSend").get_attribute("title") or "")

    # 4. Receive on the watch-only account: the exact address and a locally drawn QR
    page.locator("#vwHomeReceive").click()
    page.wait_for_selector("#vwHomeAddress")
    assert page.locator("#vwHomeAddress").text_content() == watch_key
    assert page.locator("#vwHomeQr canvas").count() == 1
    page.locator("#vwHomeBody .vw-toggle").first.click()  # back to the wallet home

    # 5. Create a VOOL Wallet through the panel's own setup flow: PIN, one-time backup, acknowledgement
    page.locator("#vwHomeAddWallet").click()
    page.wait_for_selector("#vwHomeCreate", timeout=10_000)
    assert page.locator("#vwHomeWatch").count() == 1 and page.locator("#vwHomeRestore").count() == 1
    page.locator("#vwHomeCreate").click()
    page.wait_for_selector("#vwHomeCMethod")
    page.select_option("#vwHomeCMethod", "pin")
    page.fill("#vwHomeCCredential", PIN)
    page.fill("#vwHomeCConfirm", PIN)
    page.fill("#vwHomeLabel", "Panel pilot")
    page.locator("#vwHomeCreateGo").click()
    page.wait_for_selector("#vwCxBackup", timeout=30_000)  # the one-time backup dialog (key names only below)
    assert page.locator("#vwCxBackupValue").count() == 1 and page.locator("#vwCxBackupValue").text_content() != ""
    page.locator("#vwCxBackupSaved").click()
    page.wait_for_selector("#vwHomeAccount", timeout=15_000)
    home_text = page.locator("#vwHomeBody").text_content()
    assert "Panel pilot" in home_text, "the created wallet is selected and named after setup finishes"

    # 6. fund the created wallet on the simulated chain; the panel's refresh sees the real balance
    created = [a for a in _get(daemon, "/api/wallet/status")["status"]["accounts"] if a["label"] == "Panel pilot"]
    assert created and created[0]["mode"] == "pocket_sealed"
    chain.balances[created[0]["public_key"]] = LAMPORTS
    page.select_option("#vwHomeAccount", created[0]["wallet_id"])
    page.wait_for_selector("#vwHomeBalance >> text=5 SOL", timeout=20_000)

    # 7. Send through the panel: ordinary units -> one proposal -> the REAL quote sheet
    destination = _sol_key()
    assert page.locator("#vwHomeSend").is_enabled()
    page.locator("#vwHomeSend").click()
    page.fill("#vwHomeSendTo", destination)
    page.fill("#vwHomeSendAmount", "0.001")  # a fresh recipient account needs at least the rent minimum
    page.locator("#vwHomeSendGo").click()
    page.wait_for_selector("#vwSheet", timeout=30_000)
    page.wait_for_selector("#vwSheetApprove:enabled", timeout=60_000)
    sheet_text = page.locator("#vwSheet").text_content()
    assert "0.001 SOL" in sheet_text  # the server's exact human string, never a page computation
    assert "Solana Devnet" in sheet_text and destination in sheet_text

    # 8. Decide later: the request stays pending, reachable again from Activity
    page.locator("#vwSheetLater").click()
    page.wait_for_selector("#vwSheet", state="detached")
    page.wait_for_selector("#vwHomeActivity >> text=Review", timeout=15_000)
    page.locator("#vwHomeActivity .vw-wh-review").first.click()
    page.wait_for_selector("#vwSheet", timeout=15_000)
    page.wait_for_selector("#vwSheetApprove:enabled", timeout=60_000)

    # 9. Cancel request: the server reject; nothing was signed or sent
    page.locator("#vwSheetCancel").click()
    page.wait_for_selector("#vwSheet", state="detached", timeout=15_000)
    page.wait_for_timeout(300)
    transfers = _get(daemon, "/api/wallet/transfers?limit=10")["transfers"]
    states = {t["state"] for t in transfers}
    assert states <= {"cancelled"}, f"the cancelled request must read cancelled, saw {states}"

    # 10. disable: the entry disappears, the open panel closes; re-enable restores the stored wallets
    status, _ = _door(daemon, "/api/settings/prefs", {"wallet_enabled": False})
    assert status == 200
    page.wait_for_timeout(2800)  # the poll the page already runs
    assert page.locator("#vwHomeBtn").count() == 0
    assert page.locator("#vwHomeOverlay").count() == 0
    assert page.locator("#vwHomeSend").count() == 0, "no executable Send control remains"
    status, _ = _door(daemon, "/api/settings/prefs", {"wallet_enabled": True})
    assert status == 200
    page.wait_for_selector("#vwHomeBtn", state="attached", timeout=10_000)
    if page.locator("#homeMenu").get_attribute("open") is None:
        page.click("#homeToggle")
    page.locator("#vwHomeBtn").click()
    page.wait_for_selector("#vwHomeAccount", timeout=10_000)
    accounts = _get(daemon, "/api/wallet/status")["status"]["accounts"]
    assert {a["label"] for a in accounts} >= {"Panel pilot", "Observation post"}, "disable/re-enable kept every stored wallet"
    assert not errors
    page.close()
