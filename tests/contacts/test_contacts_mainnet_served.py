"""Served mainnet contact review: real daemon and Chromium; simulated RPC; no signing.

Only public-shaped fixture addresses are registered through the external-wallet API.
No signing request is submitted and no transaction may leave the simulation boundary.
The controlled model chooses tools; this proves transport and execution, not model quality.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import expect

from core.vool_wallet import b58encode
from core.wallet import chains
from tests import _reader_served_rig as rig
from tests import served_browser
from tests.contacts.test_contacts_served_journey import (
    MODEL,
    PROVIDER_NAME,
    ScriptedContactsModel,
    _install,
    plan_calls,
)
from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc

SENDER = b58encode(bytes([42]) * 32)
RECIPIENT = b58encode(bytes([43]) * 32)
EVM_RECIPIENT = "0x" + "b" * 40
PIN = "482913"


def _post(daemon, path, body):
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
        headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(daemon, path):
    with urlopen(daemon.base_url + path, timeout=60) as response:
        return json.load(response)


@pytest.fixture(scope="module")
def mainnet_product(tmp_path_factory):
    home = tmp_path_factory.mktemp("contacts-mainnet-served") / "home"
    model = ScriptedContactsModel()
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as chain, rig.CapturingProvider(default="Ready.") as provider:
        _install(provider, model)
        daemon = rig.ServedDaemon(home, provider=provider, model=MODEL, provider_name=PROVIDER_NAME, env_extra={
            "VOOL_CREDENTIAL_STORE": "vault", "VOOL_WALLET_SKIP_CONSENT_GATE": "yes",
            "VOOL_WALLET_RPC_URLS": json.dumps({chains.SOLANA_MAINNET: chain.url}),
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "",
        })
        original_env = daemon.env

        def fresh_env():
            env = original_env()
            env.pop("VOOL_WALLET_ENABLED", None)
            env.pop("VOOL_ALWAYS_ON_CATALOG", None)
            return env

        daemon.env = fresh_env
        try:
            daemon.start(timeout=300)
            assert daemon.certify(timeout=240).get("state") == "verified"
            yield daemon, chain, model
        finally:
            daemon.stop()
        assert chain.send_count() == 0
        assert not any(c["method"] == "sendTransaction" for c in chain.calls)


def _tab_to(page, selector):
    target = page.locator(selector)
    for _ in range(100):
        if target.evaluate("node => node === document.activeElement"):
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"Keyboard could not reach {selector}")


def _activate(page, selector):
    _tab_to(page, selector)
    page.keyboard.press("Enter")


def _home_item(page, selector):
    if page.locator("#homeMenu").get_attribute("open") is None:
        _activate(page, "#homeToggle")
    _activate(page, selector)


def _type(page, selector, value):
    _tab_to(page, selector)
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type(value)
    expect(page.locator(selector)).to_have_value(value)


def _shot(page, name):
    target = os.environ.get("VOOL_WALLET_MISSION_SHOTS")
    if target:
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / f"{name}.png"), full_page=True)


def test_mainnet_contacts_chat_wallet_receive_and_renamed_approval(mainnet_product):
    daemon, chain, model = mainnet_product
    manager, browser = served_browser.launch_chromium()
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    errors, outside, delayed_listing = [], [], []

    def local_only(route):
        if route.request.url == daemon.base_url + "/api/contacts" and not delayed_listing:
            delayed_listing.append(route)
            return
        if route.request.url.startswith(daemon.base_url + "/"):
            route.continue_()
        else:
            outside.append(route.request.url)
            route.abort()

    context.route("**/*", local_only)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
        status = _get(daemon, "/api/wallet/status")["status"]
        assert status["enabled"] is False and status["has_wallet"] is False
        assert chain.calls == []
        _shot(page, "01-fresh-crypto-off")
        code, _ = _post(daemon, "/api/settings/prefs", {"wallet_enabled": True})
        assert code == 200
        assert _get(daemon, "/api/wallet/status")["status"]["crypto_pilot"]["active_environment"] == "mainnet"
        code, account = _post(daemon, "/api/wallet/external", {"public_key": SENDER, "network": chains.SOLANA_MAINNET, "label": "Mainnet review"})
        assert code == 200, account

        _home_item(page, "#contactsBtn")
        page.wait_for_selector("#vcAdd")
        _activate(page, "#vcAdd")
        _type(page, "#vcName", "Ast")
        assert len(delayed_listing) == 1
        delayed_listing[0].continue_()
        expect(page.locator("#vcList")).to_have_text("Nothing saved yet.")
        expect(page.locator("#vcName")).to_be_focused()
        page.keyboard.type("er")
        expect(page.locator("#vcName")).to_have_value("Aster")
        _activate(page, "#vcAddWallet")
        rows = page.locator("#vcForm .vc-ep-row")
        first = rows.count()
        _type(page, f"#vcValue{first}", RECIPIENT)
        expect(page.locator(f"#vcNetwork{first}")).to_have_value(chains.SOLANA_MAINNET)
        _activate(page, "#vcAddWallet")
        second = rows.count()
        _type(page, f"#vcValue{second}", EVM_RECIPIENT)
        _tab_to(page, f"#vcNetwork{second}")
        page.keyboard.press("b")
        assert page.locator(f"#vcNetwork{second}").input_value() == "eip155:8453"
        _activate(page, "#vcSave")
        page.wait_for_selector("#vcDetail .vc-endpoint")
        _shot(page, "02-contact-two-mainnet-chains")
        contact = _get(daemon, "/api/contacts?q=Aster")["contacts"][0]
        contact = _get(daemon, "/api/contacts/detail?id=" + contact["contact_id"])["contact"]
        assert {e["chain_network"] for e in contact["endpoints"]} == {chains.SOLANA_MAINNET, "eip155:8453"}
        assert _get(daemon, "/api/contacts/operations")["operations"] == []
        page.keyboard.press("Escape")

        model.plan = plan_calls(("wallet.propose", {"destination": "Aster", "amount": "0.001", "asset": "SOL", "chain": "solana"}))
        try:
            daemon.chat("Prepare 0.001 SOL to Aster on Solana mainnet.", session_id=rig.canonical_session(uuid.uuid4().hex), timeout=300)
        finally:
            model.plan = None
        pending = _get(daemon, "/api/wallet/status")["status"]["pending"]
        assert len(pending) == 1 and pending[0]["recipient"]["display_name"] == "Aster", pending
        proposal_id = pending[0]["proposal_id"]
        code, preview = _post(daemon, "/api/wallet/quote", {"proposal_id": proposal_id})
        assert code == 200, preview
        assert preview["quote"]["fields"]["to_address"] == RECIPIENT
        assert any(call["method"] == "simulateTransaction" for call in chain.calls)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#vwHomeBtn", state="attached")
        _home_item(page, "#vwHomeBtn")
        page.wait_for_selector("#vwHomeAccount")
        _shot(page, "03-mainnet-wallet-balance-activity")
        _activate(page, "#vwHomeReceive")
        page.wait_for_selector("#vwHomeAddress")
        assert page.locator("#vwHomeAddress").inner_text() == SENDER
        assert page.locator("#vwHomeQr canvas").count() == 1
        _shot(page, "04-mainnet-receive-address-qr")
        page.keyboard.press("Escape")

        # The wallet screen uses the same name resolver as chat. A wrong-chain
        # picker choice fills nothing, a cap refusal is recoverable, and decline
        # leaves a receipt without a signature or submission.
        _home_item(page, "#vwHomeBtn")
        page.wait_for_selector("#vwHomeSend")
        _activate(page, "#vwHomeSend")
        _activate(page, "#vwHomePickContact")
        wrong_chain = page.locator("#vwHomeContactList button", has_text="Base")
        wrong_chain.wait_for()
        _activate(page, '#vwHomeContactList button:has-text("Base")')
        expect(page.locator("#vwHomeSendTo")).to_have_value("")
        assert "not Solana" in page.locator("#vwHomeBody").inner_text()
        _activate(page, "#vwHomePickContact")
        _type(page, "#vwHomeSendTo", "Aster")
        _type(page, "#vwHomeSendAmount", "0.100000001")
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/propose") and r.request.method == "POST") as cap_response:
            _activate(page, "#vwHomeSendGo")
        cap = cap_response.value.json()
        assert cap_response.value.status == 403 and cap["error"] == "wallet_limit_exceeded", cap
        _shot(page, "06-mainnet-cap-hit")
        _type(page, "#vwHomeSendAmount", "0.001")
        _activate(page, "#vwHomeSendGo")
        page.wait_for_selector("#vwSheet")
        expect(page.locator("#vwSheet")).to_contain_text(RECIPIENT)
        expect(page.locator("#vwSheet")).to_contain_text("Aster")
        _shot(page, "07-mainnet-send-review")
        _activate(page, "#vwSheetCancel")
        page.wait_for_selector("#vwSheet", state="detached")
        transfers = _get(daemon, "/api/wallet/transfers")["transfers"]
        assert any(t["state"] == "cancelled" for t in transfers), transfers
        assert chain.send_count() == 0
        _shot(page, "08-mainnet-declined-receipt")
        page.keyboard.press("Escape")

        # Keep the quote current while testing a changed identity, rather than
        # confusing that refusal with an unrelated expired-preview refusal.
        code, preview = _post(daemon, "/api/wallet/quote", {"proposal_id": proposal_id})
        assert code == 200, preview

        code, enrolled = _post(daemon, "/api/contacts/credential/enroll", {"kind": "pin", "secret": PIN})
        assert code == 200 and enrolled["status"] == "enrolled", enrolled
        code, changed = _post(daemon, "/api/contacts/update", {"contact_id": contact["contact_id"], "expected_revision": contact["revision"], "display_name": "Birch"})
        assert code == 200 and changed["status"] == "pending_authentication", changed
        operation = changed["operation"]
        code, committed = _post(daemon, "/api/contacts/operations/confirm", {"operation_id": operation["operation_id"], "digest": operation["digest"], "secret": PIN})
        assert code == 200 and committed["status"] == "committed", committed
        code, refused = _post(daemon, "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": preview["quote"]["quote_id"], "quote_digest": preview["quote"]["digest"], "pin": PIN})
        assert code >= 400 and refused["error"] == "wallet_recipient_refused", refused
        assert "contact_identity_changed" in json.dumps(refused)
        assert chain.send_count() == 0
        receipts = _get(daemon, "/api/wallet/receipts")["receipts"]
        assert receipts and not any(r["state"] in {"signed", "broadcast", "confirmed"} for r in receipts)

        page.reload(wait_until="domcontentloaded")
        _home_item(page, "#contactsBtn")
        page.wait_for_selector("#vcList .vc-row")
        _shot(page, "05-contact-renamed-old-approval-refused")
        settings = context.new_page()
        settings.goto(daemon.base_url + "/settings#companion/appearance_theme", wait_until="domcontentloaded")
        settings.wait_for_selector('select[name="appearanceTheme"]')
        settings.locator('select[name="appearanceTheme"]').select_option("light")
        expect(settings.locator('select[name="appearanceTheme"]')).to_have_value("light")
        settings.close()
        page.bring_to_front()
        page.wait_for_function('document.documentElement.getAttribute("data-theme") === "light"')
        _shot(page, "09-contacts-light")
        page.set_viewport_size({"width": 390, "height": 844})
        _shot(page, "10-contacts-narrow-window")
        assert not outside, outside
        assert not errors, errors
    except Exception:
        failure = "failure-mainnet-" + uuid.uuid4().hex[:8]
        _shot(page, failure)
        target = os.environ.get("VOOL_WALLET_MISSION_SHOTS")
        if target:
            Path(target, failure + ".txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
        raise
    finally:
        context.close()
        browser.close()
        manager.stop()
