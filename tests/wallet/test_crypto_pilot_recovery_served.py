"""Product follow-up, Phase B, SERVED: forgotten PIN recovered from the chat page's own recovery surface.

SCRIPTED MODEL, SIMULATED CHAIN (Solana Devnet node), real daemon, the confined browser. The wallet is made through the
setup doors (its one-time backup is the test's disposable fixture key), the transfer through chat, the preview through
the quote door. Proven: the Forgot link beside the credential opens a local surface that reads the wallet's own options
(backup always; device recovery "not set up" for a PIN wallet; external-import guidance with the official references;
the no-reset statement); Close and Escape change nothing; a wrong key is refused and leaves the wallet untouched; the
right backup with a new PIN restores access; the open preview is withdrawn and, after a refresh, the OLD PIN is refused
and the NEW PIN sends exactly once; the same entry exists per account under Settings. The backup never reaches the
page after dismissal, the console, or the model.
"""
from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import served_browser
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN, NEW_PIN = "482913", "775310"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("recovery") / "home"
    sol = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    sol.real_signature = True
    sol.status_keyed = True
    provider = PromptRoutedProvider()
    for node in (sol, provider):
        node.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_DEVNET: sol.url}),
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps", "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
    })
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        provider.reset()
        status, switched = _door(daemon, "/api/wallet/environment", {"environment": "testnet"})
        assert status == 200, switched
        yield daemon, sol, provider
    finally:
        daemon.stop()
        for node in (provider, sol):
            node.__exit__(None, None, None)


def _wallet_with_backup(daemon) -> tuple[dict, str]:
    status, created = _door(daemon, "/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"recover-{uuid.uuid4().hex}", "label": "forgotten pin"})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200 and revealed["backup"]["backup_format"] == "solana_keypair_base58"
    backup, token = revealed["backup"]["backup_value"], revealed["backup"]["ack_token"]
    revealed = None
    status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"], backup


def _chat_proposal(daemon, message: str, *, session: str) -> str:
    turn = daemon.chat(message, session_id=session, model=MODEL, mode="auto")
    text = str((turn.get("message") or {}).get("content") or "")
    assert "pending_approval" in text and "pay-" in text, text
    return next(word.strip(".,:") for word in text.split() if word.startswith("pay-"))


def _open_page(browser, daemon, path: str = "/chat"):
    page = browser.new_page()
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console.append(message.text))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + path, wait_until="domcontentloaded")
    return page, errors, console


def _generation(daemon, wallet_id: str) -> int:
    status, options = _door(daemon, "/api/wallet/recovery/options", {"wallet_id": wallet_id})
    assert status == 200, options
    return int(options["recovery"]["credential_generation"])


def test_forgot_pin_from_the_preview_restores_access_and_the_new_pin_sends_once(served, browser):
    daemon, node, provider = served
    wallet, backup = _wallet_with_backup(daemon)
    proposal_id = _chat_proposal(daemon, f"Send 0.0004 SOL on solana to {_sol_key()} for the recovery proof", session="recovery-1")
    page, errors, console = _open_page(browser, daemon)
    try:
        page.wait_for_selector("#input", timeout=20_000)
        card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
        card.locator(".vw-review").wait_for(timeout=30_000)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            card.locator(".vw-review").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        forgot = page.locator("#vwSheetForgot")
        assert forgot.is_visible() and forgot.text_content() == "Forgot PIN or password?"
        # the surface: options from the wallet's own record; closing changes nothing
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/recovery/options"):
            forgot.click()
        dialog = page.locator("#vwRecovery")
        page.wait_for_selector("#vwRecoveryKey", timeout=10_000)
        assert wallet["address"] in dialog.locator("#vwRecoveryWallet").text_content()
        assert dialog.locator('[data-recovery="backup"] #vwRecoveryRestore').count() == 1
        assert "not set up" in dialog.locator('[data-recovery="device"]').text_content() and dialog.locator("#vwRecoveryDevice").count() == 0
        assert "Phantom" in dialog.locator('[data-recovery="external"]').text_content()
        assert dialog.locator('[data-recovery="external"] a[href^="https://help.phantom.com/"]').count() == 2
        assert "cannot reset access" in dialog.locator('[data-recovery="none"]').text_content()
        page.locator("#vwRecoveryClose").click()
        page.wait_for_selector("#vwRecovery", state="detached")
        assert _generation(daemon, wallet["wallet_id"]) == 1
        assert page.locator("#vwSheet").count() == 1  # the preview stayed open underneath
        # a wrong key is refused, the field is emptied, the wallet untouched; Escape closes without changing anything
        forgot.click()
        page.wait_for_selector("#vwRecoveryKey", timeout=10_000)
        page.locator("#vwRecoveryKey").fill(_sol_key() + _sol_key())
        page.locator("#vwRecoveryNew").fill(NEW_PIN)
        page.locator("#vwRecoveryConfirm").fill(NEW_PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/recovery/backup") as refused:
            page.locator("#vwRecoveryRestore").click()
        assert refused.value.status == 400 and refused.value.json()["error"] == "wallet_recovery_refused"
        page.wait_for_function("document.getElementById('vwRecoveryState').textContent.length > 0")
        assert page.locator("#vwRecoveryKey").input_value() == "" and _generation(daemon, wallet["wallet_id"]) == 1
        page.keyboard.press("Escape")
        page.wait_for_selector("#vwRecovery", state="detached")
        assert page.locator("#vwSheet").count() == 1
        # the right backup with a new PIN restores access; the open preview is withdrawn
        forgot.click()
        page.wait_for_selector("#vwRecoveryKey", timeout=10_000)
        page.locator("#vwRecoveryKey").fill(backup)
        page.locator("#vwRecoveryNew").fill(NEW_PIN)
        page.locator("#vwRecoveryConfirm").fill(NEW_PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/recovery/backup") as restored:
            page.locator("#vwRecoveryRestore").click()
        assert restored.value.status == 200 and restored.value.json()["setup"]["credential_generation"] == 2
        page.wait_for_function("document.getElementById('vwRecoveryBody').textContent.indexOf('Access restored') >= 0", timeout=10_000)
        assert backup not in page.content()
        page.locator("#vwRecoveryClose").click()
        page.wait_for_selector("#vwRecovery", state="detached")
        assert backup not in page.content()
        # the sheet's preview was withdrawn: the page notices on its next poll and asks for a refresh
        page.wait_for_function("document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        assert "replaced or withdrawn" in page.locator("#vwSheetState").text_content()
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            page.locator("#vwSheetRefresh").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        sends = node.send_count()
        page.locator("#vwSheetCredential").fill(PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as old:
            page.locator("#vwSheetApprove").click()
        assert old.value.status == 400 and old.value.json()["error"] == "wallet_pin_invalid" and node.send_count() == sends
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        page.locator("#vwSheetCredential").fill(NEW_PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as fresh:
            page.locator("#vwSheetApprove").click()
        assert fresh.value.status == 200 and fresh.value.json()["transfer"]["state"] == "confirmed" and node.send_count() == sends + 1
        page.wait_for_selector("#vwSheet", state="detached")
        assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        # nothing leaked: not the console, not the model's prompts, not the page
        assert not any(backup in line or NEW_PIN in line for line in console), console
        prompts = " ".join(str(c.get("prompt") or "") for c in provider.calls)
        assert backup not in prompts and NEW_PIN not in prompts and PIN not in prompts
        assert not errors, errors
    finally:
        page.close()


def test_settings_offers_recovery_per_account_and_options_read_changes_nothing(served, browser):
    daemon, _node, _provider = served
    wallet, _backup = _wallet_with_backup(daemon)
    page, errors, _console = _open_page(browser, daemon, "/settings#wallet")  # the integrated Settings page: the Wallet section
    try:
        page.wait_for_selector("#vwStatus", state="visible", timeout=20_000)
        link = page.locator(f'.vw-forgot-account[data-wallet="{wallet["wallet_id"]}"]')
        link.wait_for(timeout=15_000)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/recovery/options"):
            link.click()
        page.wait_for_selector("#vwRecoveryKey", timeout=10_000)
        assert wallet["address"] in page.locator("#vwRecoveryWallet").text_content()
        page.locator("#vwRecoveryClose").click()
        page.wait_for_selector("#vwRecovery", state="detached")
        assert _generation(daemon, wallet["wallet_id"]) == 1
        assert not errors, errors
    finally:
        page.close()
