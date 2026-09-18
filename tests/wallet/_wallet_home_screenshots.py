"""Screenshot driver for the VOOL Wallet home delivery (not a pytest module).

Boots the real daemon in a disposable home with the loopback Solana-dialect chain, drives the real
panel in the served browser through the delivery's required states, and saves PNGs. No mainnet, no
real funds, no extension, no secret export. Run with the canonical interpreter from the candidate
root:  ~/Desktop/vool-local-product/.venv/bin/python tests/wallet/_wallet_home_screenshots.py OUT_DIR
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from urllib.request import Request, urlopen

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # the repo root, for tests.* and core.*

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.wallet._rig import ScriptedRpc

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
PIN = "482913"
LAMPORTS = 5_000_000_000


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def main(out: pathlib.Path) -> int:
    from urllib.error import HTTPError

    out.mkdir(parents=True, exist_ok=True)
    home = pathlib.Path(tempfile.mkdtemp(prefix="vw-shots-")) / "home"

    def door(path: str, body: dict):
        request = Request("http://127.0.0.1:{port}".format(port=daemon.port) + path, data=json.dumps(body).encode(),
                          method="POST", headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    with ScriptedRpc() as chain, rig.CapturingProvider(default="Acknowledged.") as provider:
        chain.real_signature = True
        chain.status_keyed = True
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        })
        daemon.start(timeout=180)
        assert daemon.certify(timeout=120).get("state") == "verified"
        door("/api/settings/prefs", {"wallet_enabled": True})

        ctx, browser = served_browser.launch_chromium()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: print("PAGEERROR:", error))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")

        # 1. Home with the Wallet entry
        page.wait_for_selector("#vwHomeBtn", state="attached", timeout=15_000)
        if page.locator("#homeMenu").get_attribute("open") is None:
            page.click("#homeToggle")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / "01-home-with-wallet.png"))

        # 2. empty/setup state
        page.locator("#vwHomeBtn").click()
        page.wait_for_selector("#vwHomeCreate", timeout=15_000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(out / "02-wallet-setup-empty.png"))

        # create the funded pilot wallet through the panel's own flow (PIN + one-time backup)
        page.locator("#vwHomeCreate").click()
        page.wait_for_selector("#vwHomeCMethod")
        page.select_option("#vwHomeCMethod", "pin")
        page.fill("#vwHomeCCredential", PIN)
        page.fill("#vwHomeCConfirm", PIN)
        page.fill("#vwHomeLabel", "Everyday wallet")
        page.locator("#vwHomeCreateGo").click()
        page.wait_for_selector("#vwCxBackup", timeout=30_000)
        page.screenshot(path=str(out / "03-one-time-backup.png"))
        page.locator("#vwCxBackupSaved").click()
        page.wait_for_selector("#vwHomeAccount", timeout=15_000)
        created = [a for a in json.load(urlopen(daemon.base_url + "/api/wallet/status", timeout=30))["status"]["accounts"]
                   if a["label"] == "Everyday wallet"][0]
        chain.balances[created["public_key"]] = LAMPORTS

        # a watch-only observation account beside it
        watch_key = _sol_key()
        chain.balances[watch_key] = 0
        page.locator("#vwHomeAddWallet").click()
        page.wait_for_selector("#vwHomeWatch")
        page.locator("#vwHomeWatch").click()
        page.fill("#vwHomeWatchAddr", watch_key)
        page.fill("#vwHomeWatchLabel", "Observation post")
        page.locator("#vwHomeWatchGo").click()
        page.wait_for_selector("#vwHomeAccount", timeout=15_000)
        page.select_option("#vwHomeAccount", created["wallet_id"])
        page.wait_for_selector("#vwHomeBalance >> text=5 SOL", timeout=25_000)
        page.wait_for_timeout(400)

        # 3. populated wallet home
        page.screenshot(path=str(out / "04-wallet-home-populated.png"))

        # 4. network selection: the rows the active environment really offers
        page.locator("#vwHomeNetwork").click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "05-network-selection.png"))
        page.locator("#vwHomeTitle").click()  # dismiss the dropdown WITHOUT Escape (Escape closes the panel)

        # 5. honest unavailable: an EVM watch-only row whose runtime stack is missing (no I/O attempted)
        evm_watch = "0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a"
        page.locator("#vwHomeAddWallet").click()
        page.wait_for_selector("#vwHomeWatch")
        page.locator("#vwHomeWatch").click()
        page.fill("#vwHomeWatchAddr", evm_watch)
        page.select_option("#vwHomeWatchNet", BASE_SEPOLIA)
        page.fill("#vwHomeWatchLabel", "Base lookout")
        page.locator("#vwHomeWatchGo").click()
        page.wait_for_selector("#vwHomeAccount", timeout=15_000)
        evm_id = page.evaluate("""(addr) => {
          const sel = document.getElementById('vwHomeAccount');
          for (const g of sel.querySelectorAll('optgroup')) for (const o of g.querySelectorAll('option'))
            if (o.textContent.indexOf(addr.slice(0, 8)) !== -1) return o.value;  // the option shows short(): 8 + … + 4
          return '';
        }""", evm_watch)
        if not evm_id:
            print("EVM account not listed; options:",
                  page.evaluate("() => Array.from(document.getElementById('vwHomeAccount').options).map(o => o.textContent)"),
                  "panel msg:", page.locator("#vwHomeMsg").text_content())
            raise SystemExit(3)
        page.select_option("#vwHomeAccount", evm_id)
        page.wait_for_selector("#vwHomeBalance >> text=Balance unavailable", timeout=20_000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "06-honest-unavailable-evm.png"))

        # back to the funded wallet; receive view with the locally drawn QR
        page.select_option("#vwHomeAccount", created["wallet_id"])
        page.wait_for_selector("#vwHomeBalance >> text=5 SOL", timeout=25_000)
        page.locator("#vwHomeReceive").click()
        page.wait_for_selector("#vwHomeQr canvas")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "07-receive-address-and-qr.png"))
        page.locator("#vwHomeBody .vw-toggle").first.click()

        # 6. send -> the real review sheet, details collapsed then expanded
        page.wait_for_selector("#vwHomeSend:enabled", timeout=15_000)
        page.locator("#vwHomeSend").click()
        page.fill("#vwHomeSendTo", _sol_key())
        page.fill("#vwHomeSendAmount", "0.001")
        page.locator("#vwHomeSendGo").click()
        page.wait_for_selector("#vwSheet", timeout=30_000)
        page.wait_for_selector("#vwSheetApprove:enabled", timeout=60_000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "08-payment-review-collapsed.png"))
        page.locator("#vwSheetDetailsToggle").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "09-payment-review-expanded.png"))

        # 7. Decide later -> pending activity
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        page.wait_for_selector("#vwHomeActivity >> text=Review", timeout=15_000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "10-pending-activity.png"))

        # 8. approve the payment; the poll notices the landed payment and says so once
        page.locator("#vwHomeActivity .vw-wh-review").first.click()
        page.wait_for_selector("#vwSheet", timeout=15_000)
        page.wait_for_selector("#vwSheetApprove:enabled", timeout=60_000)
        page.fill("#vwSheetCredential", PIN)
        page.locator("#vwSheetApprove").click()
        try:
            page.wait_for_selector("#nToast >> text=payment confirmed", timeout=25_000)
            page.screenshot(path=str(out / "11-payment-landed-notice.png"))
        except Exception:
            print("toast not observed in time (the fixture lane proves it); state:",
                  page.locator("#nToast").text_content() if page.locator("#nToast").count() else "none")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / "12-after-send-activity.png"))

        browser.close()
        ctx.stop()
        daemon.stop()
    print("screenshots in", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "delivery-wallet-home-screenshots")))
