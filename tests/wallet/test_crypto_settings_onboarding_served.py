"""Settings onboarding, SERVED: a fresh disposable profile, the visible Settings controls only.

SCRIPTED MODEL, SIMULATED CHAINS (Solana Devnet, Base Sepolia and Ethereum Sepolia loopback nodes), real daemon in its
own home with Crypto OFF (no environment override), the served lane's confined chromium. Proven through what a person
sees and clicks: Settings → Crypto (Pilot), off by default with the section discoverable; Enable Crypto creates, signs
and pays nothing; the network rows come from the runtime's registry in its order with typed capabilities; Developer
options select Test networks and the authoritative environment and its origin are shown; Create wallet → warning →
credential confirmed before any key exists → one-time backup in the issued format → explicit saved-backup confirmation
→ the wallet displayed with its address, a real balance read and its actions; a genuinely different EVM setup by
password with the backup closed unconfirmed, a wrong credential refused, then confirmed; a cancelled setup resumed
after a reload without a second reveal; duplicate clicks create one wallet; the device option is absent where the
machine has none; a switch back to Mainnet keeps the test-network wallet visible and marks it not active; creation on
an inactive row is refused by the backend law. The backup value is read from the dialog inside the test only and is
never printed, stored or asserted by content beyond its format and derived address.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import served_browser
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet._rig_provider import PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN, WRONG_PIN = "482913", "111111"
PASSWORD, WRONG_PASSWORD = "onboarding proof phrase", "not the phrase at all"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
ETH_SEPOLIA = "eip155:11155111"
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{80,100}$")


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=30) as response:
        return json.load(response)


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

    home = tmp_path_factory.mktemp("onboarding") / "home"
    sol = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    sol.real_signature = True
    sol.status_keyed = True
    base = ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack", l1_fee=40_000_000_000_000, fork="jovian", operator_fee_scalar=100, operator_fee=7)
    eth = ScriptedEvmNativeChain(chain_id=11155111, fee_model="eip1559")
    provider = PromptRoutedProvider()
    for node in (sol, base, eth, provider):
        node.__enter__()
    # Crypto is OFF: no VOOL_WALLET_ENABLED override; the switch in Settings is the only way on
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_DEVNET: sol.url, BASE_SEPOLIA: base.url, ETH_SEPOLIA: eth.url}),
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
        yield daemon, {"sol": sol, "base": base, "eth": eth}, provider
    finally:
        daemon.stop()
        for node in (provider, eth, base, sol):
            node.__exit__(None, None, None)


def _open_settings(browser, daemon):
    page = browser.new_page()
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console.append(message.text))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(daemon.base_url + "/settings#wallet", wait_until="networkidle")
    page.wait_for_selector("#cryptoHost", timeout=20_000)
    return page, errors, console


def _card(page, network: str):
    return page.locator(f'.vw-cx-row[data-network="{network}"]')


def _account(page, network: str):
    return _card(page, network).locator(".vw-cx-account")


def _fill_credential(page, method: str, value: str, confirm: str) -> None:
    page.select_option("#vwCxMethod", method)
    page.fill("#vwCxCredential", value)
    page.fill("#vwCxConfirm", confirm)


def _read_backup_from_dialog(page) -> str:
    page.wait_for_selector("#vwCxBackup", timeout=20_000)
    return page.locator("#vwCxBackupValue").text_content() or ""


def _visible_box(page, selector: str) -> None:
    """The element and every ancestor are displayed with a non-empty box: what a person sees. Playwright's own
    actionability wait mis-reads a form toggled a moment ago (measured: a 132×19 select inside visible blocks), so
    the credential steps assert this layout themselves and then drive the control."""
    chain = page.evaluate(
        "(sel) => { const n = document.querySelector(sel); if (!n) return null; const out = []; let e = n;"
        " while (e && e !== document.body) { const cs = getComputedStyle(e); const r = e.getBoundingClientRect();"
        " out.push([cs.display, cs.visibility, !!e.hidden, Math.round(r.width), Math.round(r.height)]); e = e.parentElement; }"
        " return out; }",
        selector,
    )
    assert chain, selector
    assert all(d != "none" and v != "hidden" and not h and w > 0 and hgt > 0 for d, v, h, w, hgt in chain), (selector, chain)


def _credential_step(page, prefix: str, method: str, value: str) -> None:
    """Drive one credential form (`<prefix>Method`, `<prefix>Credential`) that an account action opened."""
    page.wait_for_selector(f"#{prefix}Method", state="visible", timeout=5_000)
    _visible_box(page, f"#{prefix}Method")
    page.locator(f"#{prefix}Method").select_option(method, force=True)
    _visible_box(page, f"#{prefix}Credential")
    page.locator(f"#{prefix}Credential").fill(value, force=True)


def test_fresh_profile_settings_onboarding_solana_then_evm_with_every_control(served, browser):
    daemon, chains, provider = served
    page, errors, console = _open_settings(browser, daemon)
    try:
        # --- the entry: Crypto (Pilot), off by default, discoverable; nothing created by turning it on --------------
        assert page.locator("#paneTitle").text_content() == "Crypto"
        row = page.locator('[data-row="wallet_enabled"]')
        assert row.locator(".badge").text_content() == "Pilot"
        switch = row.locator('button.sw[role="switch"]')
        assert switch.get_attribute("aria-checked") == "false"
        assert page.locator("#vwCxOff").count() == 1 and "Crypto is off" in page.locator("#vwCxOff").text_content()
        assert page.locator("#paneBody").text_content().find("Testnets only") < 0
        status_before = _get(daemon, "/api/wallet/status")["status"]
        assert status_before["enabled"] is False and status_before.get("accounts", []) == []
        with page.expect_response(lambda r: r.url.endswith("/api/settings/prefs") and r.request.method == "POST"):
            switch.click()
        page.wait_for_selector("#vwCxNotice", timeout=15_000)
        status_on = _get(daemon, "/api/wallet/status")["status"]
        assert status_on["enabled"] is True and status_on.get("accounts", []) == [] and status_on["pending"] == []
        assert chains["sol"].send_count() == 0 and chains["base"].send_count() == 0
        # --- the rows come from the registry, in its order, with typed capabilities -----------------------------------
        cp = status_on["crypto_pilot"]
        shown = [c.get_attribute("data-chain") for c in page.locator('.vw-cx-row[data-active="true"]').all()]
        assert shown == [r["chain_key"] for r in sorted((r for r in cp["networks"] if r["active"]), key=lambda r: cp["chain_order"].index(r["chain_key"]))]
        assert shown == cp["chain_order"] == ["solana", "robinhood", "base", "ethereum", "bnb"]
        robinhood = next(r for r in cp["networks"] if r["active"] and r["chain_key"] == "robinhood")
        assert "Robinhood Chain is a public network, not the Robinhood brokerage" in _card(page, robinhood["network"]).text_content()
        assert "native coin" in page.locator("#vwCxAssets").text_content() and "USDC" in page.locator("#vwCxAssets").text_content()
        env_line = page.locator("#vwCxEnv").text_content()
        assert cp["environment_label"] in env_line and cp["environment_source_label"] in env_line
        # --- Developer options: Test networks, shown with its origin; no funds move, no wallet changes ------------------
        page.locator("#vwCxDev summary").click()
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/environment")):
            page.locator('input[name="vwCxEnv"][value="testnet"]').check()
        page.wait_for_function("document.getElementById('vwCxEnvState') && document.getElementById('vwCxEnvState').textContent.indexOf('Test networks') >= 0", timeout=10_000)
        cp = _get(daemon, "/api/wallet/status")["status"]["crypto_pilot"]
        assert cp["active_environment"] == "testnet" and cp["environment_source"] == "stored" and "chosen in Settings" in page.locator("#vwCxEnvState").text_content()
        for chip in _card(page, SOLANA_DEVNET).locator(".vw-cx-cap").all():
            assert chip.get_attribute("data-available") == ("true" if chip.get_attribute("data-cap") == "create" else "false"), chip.text_content()
        assert "no account on this network yet" in _card(page, SOLANA_DEVNET).locator('[data-cap="balance"]').text_content()
        # --- create on Solana Devnet: warning, credential confirmed before any key, duplicate clicks, one reveal ---------
        card = _card(page, SOLANA_DEVNET)
        card.locator(".vw-cx-create").click()
        assert "Pilot feature" in page.locator("#vwCxWarning").text_content() and "shown once" in page.locator("#vwCxWarning").text_content()
        page.locator("#vwCxNotNow").click()  # closing here changes nothing
        assert page.locator("#vwCxCreateForm").count() == 0 and _get(daemon, "/api/wallet/status")["status"].get("accounts", []) == []
        card.locator(".vw-cx-create").click()
        page.locator("#vwCxContinue").click()
        assert page.locator("#vwCxDeviceNote").count() == 1 and "not available" in page.locator("#vwCxDeviceNote").text_content()
        assert [o.get_attribute("value") for o in page.locator("#vwCxMethod option").all()] == ["pin", "password"]
        _fill_credential(page, "pin", PIN, "482914")
        page.locator("#vwCxCreate").click()
        assert "differ" in page.locator("#vwCxState").text_content() and _get(daemon, "/api/wallet/status")["status"].get("accounts", []) == []
        _fill_credential(page, "pin", PIN, PIN)
        page.fill("#vwCxLabel", "solana proof")
        create = page.locator("#vwCxCreate")
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/reveal")):
            create.click()
            # a duplicate click while the request runs: the button is disabled, so the DOM click does nothing; and the
            # creation key is one per attempt, so even a repeated request would return the same wallet
            page.evaluate("(() => { const b = document.getElementById('vwCxCreate'); if (b) b.click(); return !!b && b.disabled; })()")
        backup = _read_backup_from_dialog(page)
        assert BASE58.match(backup), "the issued Solana keypair backup format"
        assert page.locator("#vwCxCredential").count() == 0  # the form is gone: the credential fields left with it
        accounts = _get(daemon, "/api/wallet/status")["status"]["accounts"]
        assert len(accounts) == 1 and accounts[0]["network"] == SOLANA_DEVNET and accounts[0]["setup_state"] == "backup_revealed"
        from core.vool_wallet import b58decode, b58encode

        raw = bytes(b58decode(backup))
        assert len(raw) == 64 and b58encode(raw[32:]) == accounts[0]["public_key"]  # the backup derives this exact address
        page.locator("#vwCxBackupCopy").click()  # a deliberate copy: the clipboard, when the browser grants it, with a reminder to clear it
        page.wait_for_function("(() => { const t = document.getElementById('vwCxBackupCopy').textContent; return t.startsWith('Copied') || t.startsWith('Clipboard unavailable'); })()", timeout=5_000)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/acknowledge")):
            page.locator("#vwCxBackupSaved").click()
        page.wait_for_selector("#vwCxBackup", state="detached")
        assert backup not in page.content()
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{SOLANA_DEVNET}\"] .vw-cx-account[data-setup-state=\"ready\"]') !== null", timeout=10_000)
        account = _account(page, SOLANA_DEVNET)
        address = accounts[0]["public_key"]
        assert account.locator(".vw-cx-address").text_content() == address
        assert card.locator(".vw-cx-create").count() == 0
        account.locator(".vw-cx-refresh").click()
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{SOLANA_DEVNET}\"] .vw-cx-balance').getAttribute('data-balance-state') !== 'reading'", timeout=10_000)
        assert account.locator(".vw-cx-balance").text_content() == "Balance: 5 SOL · observed at slot:1"
        account.locator(".vw-cx-receive").click()
        assert address in account.locator(".vw-msg").text_content() and "DEVNET" in account.locator(".vw-msg").text_content()
        account.locator(".vw-cx-history-btn").click()
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{SOLANA_DEVNET}\"] .vw-cx-history').textContent.indexOf('Reading') < 0", timeout=10_000)
        assert "No transfers" in account.locator(".vw-cx-history").text_content()
        assert account.locator(".vw-cx-forgot").count() == 1
        status, again = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": accounts[0]["wallet_id"], "credential": PIN})
        assert status == 409 and again["error"] == "wallet_backup_unavailable"  # one reveal, ever
        # --- a genuinely different setup: Base Sepolia by password; the backup closed unconfirmed, then confirmed --------
        base_card = _card(page, BASE_SEPOLIA)
        base_card.locator(".vw-cx-create").click()
        page.locator("#vwCxContinue").click()
        _fill_credential(page, "password", PASSWORD, PASSWORD)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/reveal")):
            page.locator("#vwCxCreate").click()
        evm_backup = _read_backup_from_dialog(page)
        assert re.match(r"^0x[0-9a-f]{64}$", evm_backup), "the issued EVM private-key format"
        page.locator("#vwCxBackupClose").click()
        page.wait_for_selector("#vwCxBackup", state="detached")
        assert evm_backup not in page.content()
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{BASE_SEPOLIA}\"] .vw-cx-account[data-setup-state=\"backup_revealed\"]') !== null", timeout=10_000)
        base_account = _account(page, BASE_SEPOLIA)
        assert "shown once and is not yet confirmed" in base_account.locator(".vw-cx-state").text_content()
        assert "cannot be shown again" in base_account.text_content() and base_account.locator("#vwCxReveal").count() == 0
        base_account.locator("#vwCxAck").click()
        assert page.evaluate("document.querySelectorAll('#vwCxAckMethod').length + document.querySelectorAll('#vwCx').length + document.querySelectorAll('#cryptoHost').length") == 3  # one surface, one host, one form
        _credential_step(page, "vwCxAck", "password", WRONG_PASSWORD)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/acknowledge")) as refused:
            base_account.locator("#vwCxAckGo").click(force=True)
        assert refused.value.status == 400 and refused.value.json()["error"] == "wallet_password_invalid"
        assert page.locator("#vwCxAckCredential").input_value() == ""
        _credential_step(page, "vwCxAck", "password", PASSWORD)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/acknowledge")) as ok:
            base_account.locator("#vwCxAckGo").click(force=True)
        assert ok.value.status == 200
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{BASE_SEPOLIA}\"] .vw-cx-account[data-setup-state=\"ready\"]') !== null", timeout=10_000)
        _account(page, BASE_SEPOLIA).locator(".vw-cx-refresh").click()
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{BASE_SEPOLIA}\"] .vw-cx-balance').getAttribute('data-balance-state') === 'read'", timeout=10_000)
        assert _account(page, BASE_SEPOLIA).locator(".vw-cx-balance").text_content().startswith("Balance: 0 ETH · observed at block:")  # a real read of an unfunded account
        # --- cancel and resume across a reload: Ethereum Sepolia ---------------------------------------------------------
        eth_card = _card(page, ETH_SEPOLIA)
        eth_card.locator(".vw-cx-create").click()
        page.locator("#vwCxContinue").click()
        _fill_credential(page, "pin", PIN, PIN)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/reveal")):
            page.locator("#vwCxCreate").click()
        _read_backup_from_dialog(page)
        page.keyboard.press("Escape")  # closes the dialog without confirming; the value node is cleared
        page.wait_for_selector("#vwCxBackup", state="detached")
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{ETH_SEPOLIA}\"] .vw-cx-account[data-setup-state=\"backup_revealed\"]') !== null", timeout=10_000)
        eth_account = _account(page, ETH_SEPOLIA)
        eth_account.locator("#vwCxCancelSetup").click()
        _credential_step(page, "vwCxCancelSetup", "pin", PIN)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/cancel")):
            eth_account.locator("#vwCxCancelSetupGo").click(force=True)
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{ETH_SEPOLIA}\"] .vw-cx-account[data-setup-state=\"cancelled\"]') !== null", timeout=10_000)
        page.reload(wait_until="networkidle")
        page.wait_for_selector(f'.vw-cx-row[data-network="{ETH_SEPOLIA}"] .vw-cx-account[data-setup-state="cancelled"]', timeout=20_000)
        eth_account = _account(page, ETH_SEPOLIA)
        assert "cancelled" in eth_account.locator(".vw-cx-state").text_content() and eth_account.locator("#vwCxResume").count() == 1
        eth_account.locator("#vwCxResume").click()
        _credential_step(page, "vwCxResume", "pin", PIN)
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/setup/resume")):
            eth_account.locator("#vwCxResumeGo").click(force=True)
        page.wait_for_function(f"document.querySelector('.vw-cx-row[data-network=\"{ETH_SEPOLIA}\"] .vw-cx-account[data-setup-state=\"backup_revealed\"]') !== null", timeout=10_000)
        assert _account(page, ETH_SEPOLIA).locator("#vwCxReveal").count() == 0  # never a second reveal
        accounts = {a["network"]: a for a in _get(daemon, "/api/wallet/status")["status"]["accounts"]}
        assert accounts[SOLANA_DEVNET]["setup_state"] == "ready" and accounts[BASE_SEPOLIA]["setup_state"] == "ready" and accounts[ETH_SEPOLIA]["setup_state"] == "backup_revealed"
        assert len(accounts) == 3
        # --- back to Mainnet: the test-network wallets stay visible and inactive; creation on an inactive row is refused ---
        page.locator("#vwCxDev summary").click()
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/environment")):
            page.locator('input[name="vwCxEnv"][value="mainnet"]').check()
        page.wait_for_function("document.getElementById('vwCxEnvState') && document.getElementById('vwCxEnvState').textContent.indexOf('Mainnet') >= 0", timeout=10_000)
        sol_card = _card(page, SOLANA_DEVNET)
        assert sol_card.get_attribute("data-active") == "false" and "not active" in sol_card.locator(".vw-cx-title").text_content()
        assert sol_card.locator(".vw-cx-account .vw-cx-address").text_content() == address
        assert "not on the active network environment" in sol_card.locator('[data-cap="quote"]').text_content()
        assert sol_card.locator(".vw-cx-refresh").is_disabled()
        assert sol_card.locator(".vw-cx-balance").text_content() == "Balance not read yet."  # no cross-network number, no vanished balance
        status, refused = _door(daemon, "/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": "inactive-row"})
        assert status == 409 and refused["error"] == "wallet_environment_inactive"
        assert len(_get(daemon, "/api/wallet/status")["status"]["accounts"]) == 3
        # --- off again: the section stays discoverable, the rows go, nothing is deleted ----------------------------------
        with page.expect_response(lambda r: r.url.endswith("/api/settings/prefs") and r.request.method == "POST"):
            page.locator('[data-row="wallet_enabled"] button.sw').click()
        page.wait_for_selector("#vwCxOff", timeout=15_000)
        assert page.locator("#paneTitle").text_content() == "Crypto"
        assert not any(backup in line or evm_backup in line or PIN in line or PASSWORD in line for line in console), "no secret in the console"
        assert not errors, errors
        assert PIN not in " ".join(str(c.get("prompt") or "") for c in provider.calls)
    finally:
        page.close()


def _chat_proposal(daemon, message: str, *, session: str) -> str:
    from tests.wallet._rig_provider import MODEL

    turn = daemon.chat(message, session_id=session, model=MODEL, mode="auto")
    text = str((turn.get("message") or {}).get("content") or "")
    assert "pending_approval" in text and "pay-" in text, text
    return next(word.strip(".,:") for word in text.split() if word.startswith("pay-"))


def test_settings_created_wallet_pays_through_chat_with_decide_later_and_a_fresh_preview(served, browser):
    """The combined product flow on the wallet the previous test created through the visible Settings controls:
    Crypto on and Test networks chosen again through Settings, a chat request (the model's own tool call), the
    readable preview, Decide later with a typed PIN, a reopened preview with a fresh quote and an empty credential,
    approval, the confirmed receipt with its purpose, recipient and exact fee. One send."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    daemon, chains, provider = served
    page, errors, _console = _open_settings(browser, daemon)
    try:
        switch = page.locator('[data-row="wallet_enabled"] button.sw')
        if switch.get_attribute("aria-checked") != "true":  # the previous proof left Crypto off; turn it on through the switch
            switch.click()
        page.wait_for_selector("#vwCxNotice", timeout=15_000)
        page.locator("#vwCxDev summary").click()
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/environment")):
            page.locator('input[name="vwCxEnv"][value="testnet"]').check()
        page.wait_for_function("document.getElementById('vwCxEnvState') && document.getElementById('vwCxEnvState').textContent.indexOf('Test networks') >= 0", timeout=10_000)
        wallet = next(a for a in _get(daemon, "/api/wallet/status")["status"]["accounts"] if a["network"] == SOLANA_DEVNET)
        assert wallet["setup_state"] == "ready"
        assert _account(page, SOLANA_DEVNET).locator(".vw-cx-address").text_content() == wallet["public_key"]
    finally:
        page.close()
    to = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    proposal_id = _chat_proposal(daemon, f"Send 0.0004 SOL on solana to {to} for proof five", session="onboarding-pay")
    page = browser.new_page()
    errors = []
    decisions: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: decisions.append(request.url.rsplit("/", 1)[-1]) if request.method == "POST" and request.url.endswith(("/api/wallet/approve", "/api/wallet/reject")) else None)
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("#input", timeout=20_000)
        card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
        card.locator(".vw-review").wait_for(timeout=30_000)
        assert card.locator(".vw-purpose-headline").text_content() == f"Send 0.0004 SOL to {to}"
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as first:
            card.locator(".vw-review").click()
        quote_a = first.value.json()["quote"]
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        sheet = page.locator('#vwSheet [role="dialog"]')
        assert sheet.locator('[data-field="to"] .vw-sheet-value').text_content() == to
        assert sheet.locator('[data-field="from"] .vw-sheet-value').text_content().endswith(wallet["public_key"])
        assert sheet.locator('[data-field="network"] .vw-badge').text_content() == "DEVNET"
        assert sheet.locator('[data-field="amount"] .vw-sheet-value').text_content() == "0.0004 SOL"
        assert page.locator("#vwSheetForgot").is_visible()
        page.locator("#vwSheetCredential").fill(PIN)
        page.evaluate("window.__cred = document.getElementById('vwSheetCredential')")
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        assert page.evaluate("window.__cred.value") == "" and decisions == []
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as second:
            card.locator(".vw-review").click()
        quote_b = second.value.json()["quote"]
        assert quote_b["quote_id"] != quote_a["quote_id"]
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        assert page.locator("#vwSheetCredential").input_value() == ""
        page.locator("#vwSheetCredential").fill(PIN)
        page.keyboard.press("Escape")
        page.wait_for_selector("#vwSheet", state="detached")
        assert decisions == []
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            card.locator(".vw-review").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        sends = chains["sol"].send_count()
        page.locator("#vwSheetCredential").fill(PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
            page.locator("#vwSheetApprove").click()
        answer = approved.value.json()
        assert answer["ok"] and answer["transfer"]["state"] == "confirmed" and chains["sol"].send_count() == sends + 1
        page.wait_for_selector("#vwSheet", state="detached")
        result = card.locator(".vw-result")
        assert result.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        assert result.locator('[data-field="purpose"] .vw-purpose-headline').text_content() == f"Send 0.0004 SOL to {to}"
        fee_line = result.locator(".vw-transfer-detail", has_text="Fee ").text_content()
        assert fee_line.startswith("Fee 0.000005 SOL") and "exactly 0.000005 SOL" in fee_line, fee_line  # the exact charged fee
        assert result.locator("a.vw-explorer").text_content() == "View on Solscan"
        assert decisions == ["approve"]
        assert not errors, errors
        assert PIN not in " ".join(str(c.get("prompt") or "") for c in provider.calls)
    finally:
        page.close()
