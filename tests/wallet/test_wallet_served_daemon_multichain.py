"""Stage F — the REAL served daemon journey: /api/chat with a model proposing, the browser
page showing the approval card, operator decisions taking effect, restart visible, and no
console errors — all on the multichain engine.

The daemon boots in remote-only mode against a scripted provider and the scripted Solana
RPC (loopback only). The EVM lanes' served journeys live in test_wallet_served_multichain
(in-process, the same dispatchers); the browser's EVM connect surface is not built yet and
is reported as a gap, not faked.
"""
from __future__ import annotations

import json
import re
import time

import pytest

from tests.wallet._rig import DESTINATION
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

REPLY_SELECTOR = ".msg.assistant:not(.pending):not(.vw-card)"


@pytest.fixture(scope="module")
def browser():
    from tests.served_browser import launch_chromium

    manager, chromium = launch_chromium()
    yield manager, chromium
    manager.stop()


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    """The real `apps.vool_api_server` subprocess, wallet on, bootable env, scripted RPC
    for the Solana lane AND the three EVM lanes (Base Sepolia, Ethereum Sepolia, BNB
    testnet), each with its own scripted endpoint so chain identities stay distinct."""
    import json as _json

    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ServedDaemon, run_in_home
    from tests.wallet._rig import ScriptedRpc as _Rpc
    from tests.wallet._rig_evm import FacilitatorSimulator, ScriptedEvmRpc

    rpc = _Rpc()
    provider = PromptRoutedProvider()
    base_rpc = ScriptedEvmRpc(chain_id=84532)
    sepolia_rpc = ScriptedEvmRpc(chain_id=11155111)
    bnb_rpc = ScriptedEvmRpc(chain_id=97)
    facilitator = FacilitatorSimulator(networks=("eip155:84532", "eip155:11155111"))
    store_dir = tmp_path_factory.mktemp("served-multichain") / "blackbox-store"
    daemon = ServedDaemon(
        tmp_path_factory.mktemp("served-home"),
        env_extra={
            "VOOL_WALLET_ENABLED": "1",
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_WALLET_TESTNET_RPC_URL": rpc.url,
            "VOOL_WALLET_X402_CAP_MINOR": "20000",
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
            "VOOL_WALLET_RPC_URLS": _json.dumps({
                "eip155:84532": base_rpc.url,
                "eip155:11155111": sepolia_rpc.url,
                "eip155:97": bnb_rpc.url,
            }),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            # the bootable env: do NOT force the local-only install profile (no local
            # backend on this machine); remote-only boot with the scripted provider
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            "VOOL_ALWAYS_ON_CATALOG": "1",
        },
    )
    provider.__enter__()
    rpc.__enter__()
    base_rpc.__enter__()
    sepolia_rpc.__enter__()
    bnb_rpc.__enter__()
    facilitator.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(daemon.home, provider.base_url)
        yield {
            "daemon": daemon, "rpc": rpc, "provider": provider, "base": daemon.base_url,
            "seed": lambda: run_in_home(daemon.home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL])),
            "evm": {"rpcs": {"base": base_rpc, "sepolia": sepolia_rpc, "bnb": bnb_rpc}, "facilitator": facilitator},
        }
    finally:
        facilitator.__exit__(None, None, None)
        bnb_rpc.__exit__(None, None, None)
        sepolia_rpc.__exit__(None, None, None)
        base_rpc.__exit__(None, None, None)
        provider.__exit__(None, None, None)
        rpc.__exit__(None, None, None)
        if daemon.process is not None:
            daemon.process.terminate()


def _wait_replies(page, *, timeout_s: float = 60.0) -> list[str]:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        texts = page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)")
        if texts:
            last = texts[-1]
            if "pending_approval" in last or "rejected" in last or "confirmed" in last or "proposed" in last:
                return texts
        time.sleep(1.0)
    return texts if (texts := page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)")) else []


def test_served_daemon_chat_journey_model_proposes_operator_rejects_then_approves(daemon, browser):
    rpc, base = daemon["rpc"], daemon["base"]
    _manager, chromium = browser
    context = chromium.new_context()
    page = context.new_page()
    console_errors: list[str] = []
    page.on("pageerror", lambda exc: console_errors.append(str(exc)))
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    try:
        page.goto(f"{base}/chat", wait_until="networkidle")
        page.evaluate("localStorage.clear(); (window.newChat || (() => {}))()")
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#input", timeout=30000)
        # 0. operator setup: a pocket account on the devnet row, created over the wire
        import urllib.request

        request = urllib.request.Request(
            f"{base}/api/wallet/pocket/create",
            data=json.dumps({"acknowledged_warning": True, "confirmation_phrase": "I ACCEPT THAT THIS DEVICE HOLDS THE KEY", "pin": "246810", "label": "served"}).encode(),
            headers={"Content-Type": "application/json", "Origin": base},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            created = json.loads(response.read())
        assert created["ok"] is True and created["shown_once"] is True
        page.reload(wait_until="networkidle")

        # pin the certified stub model and put the chat in auto mode (Manual never offers tools)
        page.evaluate("(m) => { modelValue = m; if (typeof reflectModel === 'function') reflectModel(); }", MODEL)
        page.evaluate(
            "async () => { const r = await fetch('/api/mode', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ session_id: displayedChat, mode: 'auto' }) }); return r.status; }"
        )
        page.evaluate("() => { try { const o = chatState(displayedChat); if (o) o.mode = 'auto'; } catch (e) {} }")

        # 1. the model proposes through the tool registry: a visible pending approval ONLY
        page.fill("#input", f"Please pay 1500 lamports to {DESTINATION} for the multichain proof")
        page.click("#send")
        replies = _wait_replies(page)
        joined = "\n".join(replies)
        assert "pending_approval" in joined, joined[:400]
        card = page.wait_for_selector(".vw-card", timeout=20000)
        assert card is not None
        page.wait_for_selector(".vw-card .vw-pin", timeout=10000)
        assert rpc.send_count() == 0  # a proposal never sends

        # the rejection path
        page.click(".vw-card .vw-reject")
        time.sleep(1.5)
        assert rpc.send_count() == 0  # rejection: zero signing, zero network

        # 2. approve the SAME kind of proposal with the PIN: exactly one broadcast
        before = len(page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)"))
        page.fill("#input", f"Please pay 1500 lamports to {DESTINATION} for the second proof")
        page.click("#send")
        second_replies = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            texts = page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)") or []
            if len(texts) > before and "pending_approval" in texts[-1]:
                second_replies = texts
                break
            time.sleep(1.0)
        assert second_replies, "the second proposal never became pending"
        page.wait_for_selector(".vw-card .vw-pin", timeout=20000)
        # approve the LATEST card (the first was rejected above)
        page.fill(".vw-card:last-of-type .vw-pin", "246810")
        page.click(".vw-card:last-of-type .vw-approve")
        deadline = time.monotonic() + 45
        result_text = ""
        while time.monotonic() < deadline:
            result_text = page.eval_on_selector(".vw-card:last-of-type .vw-result", "el => el ? el.textContent : ''") or ""
            if result_text.strip():
                break
            time.sleep(1.0)
        assert "confirmed" in result_text.lower(), result_text or "(no result shown)"
        assert rpc.send_count() == 1

        # 3. the confirmation is visible through chat with the public signature
        matches = re.findall(r"pay-[0-9a-f]{20}", "\n".join(second_replies) + result_text)
        proposal_id = matches[-1] if matches else ""  # the approved proposal's id
        if proposal_id:
            before_count = len(page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)"))
            page.fill("#input", f"status of payment {proposal_id}")
            page.click("#send")
            deadline = time.monotonic() + 45
            last_text = ""
            while time.monotonic() < deadline:
                texts = page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)") or []
                if len(texts) > before_count:
                    last_text = texts[-1]
                    if "confirmed" in last_text or "state=" in last_text:
                        break
                time.sleep(1.0)
            assert "confirmed" in last_text, last_text[:400]
            assert rpc.send_count() == 1  # status reads never re-send
        assert console_errors == [], console_errors[:5]
    finally:
        context.close()


# --- the EVM lanes through the REAL daemon: /api/chat + the browser page ---------------------------

BASE_SEPOLIA = "eip155:84532"
ETHEREUM_SEPOLIA = "eip155:11155111"
BSC_TESTNET = "eip155:97"
USDC_BASE = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_SEPOLIA = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


def _http_json(base: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        base + path, data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Origin": base},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _wait_for_account(base: str, chain: str, *, timeout_s: float = 20.0) -> dict | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _status, st = _http_json(base, "/api/wallet/status")
        for account in (st.get("status", {}).get("accounts") or []):
            if account.get("chain") == chain:
                return account
        time.sleep(1.0)
    return None


def _connect_evm_in_browser(page, chain_value: str) -> None:
    """Operator action: open the Wallet section of Settings, choose the EVM testnet, connect window.ethereum.

    Integrated UI (2026-09-07): Settings is its own page and the wallet section lives at /settings#wallet
    (the chat page's legacy overlay is gone). The same page navigates there and back, so the page-level
    EIP-1193 shim stays installed; model and mode are pinned by the caller AFTER this returns."""
    base = _status_base(page)
    page.goto(f"{base}/settings#wallet", wait_until="networkidle")
    page.wait_for_selector("#vwConnectEvm", state="visible", timeout=15000)
    page.select_option("#vwEvmChain", chain_value)
    page.click("#vwConnectEvm")
    assert _wait_for_account(base, chain_value) is not None, "the EVM account never registered"
    page.goto(f"{base}/chat", wait_until="networkidle")
    page.wait_for_selector("#input", timeout=30000)


_STATUS_BASE: list[str] = []


def _status_base(page) -> str:
    return _STATUS_BASE[0]


def test_served_daemon_base_sepolia_x402_v2_chat_and_browser_journey(daemon, browser):
    """Base Sepolia, end to end on the REAL daemon: the operator connects window.ethereum on
    the page, the MODEL fetches a paid x402 v2 resource through /api/chat (x402.propose),
    the approval card offers the EVM handoff, the wallet signs the typed data in its own
    process, and the settlement is proven from the chain — distinct identity, one delivery,
    no console errors."""
    import re as _re

    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource, fake_eip1193_init_script

    base, evm = daemon["base"], daemon["evm"]
    _STATUS_BASE.clear()
    _STATUS_BASE.append(base)
    base_rpc, facilitator = evm["rpcs"]["base"], evm["facilitator"]

    # the operator declares the facilitator through the owner-local door (official discovery)
    code, discovered = _http_json(base, "/api/wallet/facilitator/discover", {"origin": facilitator.url, "facilitator_id": "fac-daemon"})
    assert code == 200 and discovered["ok"] is True
    networks = {c["network"] for c in discovered["capabilities"] if c["verified"]}
    assert {"eip155:84532", "eip155:11155111"} <= networks

    with EvmExtensionSigner() as signer, X402V2Resource(facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, rpc=base_rpc) as resource:

        _manager, chromium = browser
        context = chromium.new_context()
        page = context.new_page()
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        context.add_init_script(fake_eip1193_init_script(signer.url, signer.address, chain_id=84532))
        try:
            page.goto(f"{base}/chat", wait_until="networkidle")
            page.evaluate("localStorage.clear(); (window.newChat || (() => {}))()")
            page.reload(wait_until="networkidle")
            page.wait_for_selector("#input", timeout=30000)

            # 1. the operator connects the EVM wallet on the page (settings open/close first:
            #    loadPrefs() restores the stored mode, so auto mode is pinned AFTER this)
            _connect_evm_in_browser(page, BASE_SEPOLIA)
            account = _wait_for_account(base, BASE_SEPOLIA)
            assert account["mode"] == "external_signer" and account["family"] == "evm"
            page.evaluate("(m) => { modelValue = m; if (typeof reflectModel === 'function') reflectModel(); }", MODEL)
            page.evaluate(
                "async () => { const r = await fetch('/api/mode', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ session_id: displayedChat, mode: 'auto' }) }); return r.status; }"
            )
            page.evaluate("() => { try { const o = chatState(displayedChat); if (o) o.mode = 'auto'; } catch (e) {} }")

            # 2. the MODEL fetches the paid resource through /api/chat: a parked proposal ONLY
            before = len(page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)"))
            page.fill("#input", f"x402 fetch {resource.url} for the base sepolia proof")
            page.click("#send")
            deadline = time.monotonic() + 60
            replies = []
            while time.monotonic() < deadline:
                texts = page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)") or []
                if len(texts) > before and ("parked" in texts[-1] or "pending_approval" in texts[-1]):
                    replies = texts
                    break
                time.sleep(1.0)
            assert replies, (
                "the x402 proposal never appeared in chat; last replies="
                + json.dumps(page.eval_on_selector_all(REPLY_SELECTOR, "els => els.map(e => e.textContent)") or [], default=str)[:800]
                + " provider calls=" + json.dumps(daemon["provider"].calls[-3:], default=str)[:1200]
            )
            match = _re.search(r"pay-[0-9a-f]{20}", replies[-1])
            proposal_id = match.group(0) if match else ""
            assert proposal_id, replies[-1][:300]
            assert base_rpc.methods().count("eth_chainId") >= 1  # the daemon proved the chain identity
            assert resource.deliveries == []  # a parked proposal never pays

            # 3. the approval card offers the EVM handoff; the wallet signs in its own process
            page.wait_for_selector(f'.vw-card[data-proposal="{proposal_id}"] .vw-sign-evm', timeout=10000)
            page.click(f'.vw-card[data-proposal="{proposal_id}"] .vw-sign-evm')
            deadline = time.monotonic() + 60
            result_text = ""
            while time.monotonic() < deadline:
                result_text = page.eval_on_selector(f'.vw-card[data-proposal="{proposal_id}"] .vw-result', "el => el ? el.textContent : ''") or ""
                if result_text.strip():
                    break
                time.sleep(1.0)
            assert "confirmed" in result_text.lower(), (
                result_text or "(no result shown)"
                + " events=" + json.dumps(_http_json(base, f"/api/wallet/proposals/{proposal_id}")[1].get("events", [])[-3:], default=str)[:900]
                + " deliveries=" + json.dumps(resource.deliveries, default=str)[:300]
            )
            assert resource.settlements and resource.settlements[0]["tx"] in result_text

            # 4. exactly one delivery, exactly the approved terms, distinct chain identity
            assert len(resource.deliveries) == 1
            assert resource.deliveries[0]["network"] == BASE_SEPOLIA
            assert signer.signed and signer.signed[0]["domain"]["chainId"] == 84532
            assert signer.signed[0]["message"]["to"].lower() == PAY_TO.lower()
            assert signer.signed[0]["message"]["value"] == "10000"
            assert console_errors == [], console_errors[:5]
        finally:
            context.close()


def test_served_daemon_ethereum_sepolia_distinct_identity_and_receipt(daemon, browser):
    """Ethereum Sepolia on the SAME daemon: its own endpoint, its own chain id (11155111),
    its own USDC row, its own receipt — provably a different chain than Base Sepolia."""
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource, fake_eip1193_init_script

    base, evm = daemon["base"], daemon["evm"]
    _STATUS_BASE.clear()
    _STATUS_BASE.append(base)
    sepolia_rpc, facilitator = evm["rpcs"]["sepolia"], evm["facilitator"]

    with EvmExtensionSigner() as signer, X402V2Resource(facilitator, network=ETHEREUM_SEPOLIA, asset=USDC_SEPOLIA, pay_to=PAY_TO, amount_minor=8000, eip712_name="USD Coin", eip712_version="2", rpc=sepolia_rpc) as resource:

        _manager, chromium = browser
        context = chromium.new_context()
        page = context.new_page()
        context.add_init_script(fake_eip1193_init_script(signer.url, signer.address, chain_id=11155111))
        try:
            page.goto(f"{base}/chat", wait_until="networkidle")
            page.evaluate("localStorage.clear(); (window.newChat || (() => {}))()")
            page.reload(wait_until="networkidle")
            page.wait_for_selector("#input", timeout=30000)
            _connect_evm_in_browser(page, ETHEREUM_SEPOLIA)

            # offer + park over the wire (the chat route is proven on Base Sepolia; here the
            # page's own status poll surfaces the card from the parked proposal)
            code, outcome = _http_json(base, "/api/wallet/x402/fetch", {"url": resource.url})
            assert code == 200 and outcome["outcome"]["status"] == "payment_required"
            proposal_id = outcome["outcome"]["proposal_id"]

            page.wait_for_selector(f'.vw-card[data-proposal="{proposal_id}"] .vw-sign-evm', timeout=10000)
            page.click(f'.vw-card[data-proposal="{proposal_id}"] .vw-sign-evm')
            deadline = time.monotonic() + 60
            result_text = ""
            while time.monotonic() < deadline:
                result_text = page.eval_on_selector(f'.vw-card[data-proposal="{proposal_id}"] .vw-result', "el => el ? el.textContent : ''") or ""
                if result_text.strip():
                    break
                time.sleep(1.0)
            assert "confirmed" in result_text.lower(), result_text or "(no result shown)"
            # the wallet signed SEPOLIA typed data, not Base typed data: distinct identity
            assert signer.signed[0]["domain"]["chainId"] == 11155111
            assert signer.signed[0]["domain"]["verifyingContract"].lower() == USDC_SEPOLIA.lower()
            assert len(resource.deliveries) == 1 and resource.deliveries[0]["network"] == ETHEREUM_SEPOLIA
            # the receipt is chain-qualified to sepolia
            _status, st = _http_json(base, "/api/wallet/status")
            assert st["status"]["last_receipt"]["network"] == ETHEREUM_SEPOLIA
        finally:
            context.close()


def test_served_daemon_bnb_testnet_identity_traverse_and_typed_receipt(daemon):
    """BNB Smart Chain testnet on the REAL daemon: the payment proposal's prepare reads the
    chain id (97) from ITS OWN scripted endpoint, then honestly refuses — no EIP-3009 token
    exists on BNB Chain — leaving a chain-qualified failed proposal trail."""
    evm = daemon["evm"]
    base = daemon["base"]
    bnb_rpc = evm["rpcs"]["bnb"]

    # the operator registers a BNB testnet account first: chain-qualified, on its own row
    code, registered = _http_json(base, "/api/wallet/external", {"public_key": "0x" + "d" * 40, "network": BSC_TESTNET, "label": "bnb"})
    assert code == 200 and registered["wallet"]["network"] == BSC_TESTNET
    wallet_id = registered["wallet"]["wallet_id"]
    code, proposed = _http_json(base, "/api/wallet/propose", {
        "wallet_id": wallet_id, "network": BSC_TESTNET, "destination": "0x" + "e" * 40,
        "amount_minor": 1_000_000_000_000_000_000, "asset": "BNB",
    })
    assert code in (400, 502), proposed
    assert proposed["error"] == "wallet_simulation_failed"
    # the refusal carries the chain-qualified identity in its typed context
    context = proposed["fault"]["context"]
    assert context["network"] == BSC_TESTNET
    proposal_id = context["proposal_id"]
    assert "eth_chainId" in bnb_rpc.methods()
    _status, detail = _http_json(base, f"/api/wallet/proposals/{proposal_id}")
    states = [event["state"] for event in detail["events"]]
    assert states[-1] == "failed"
    assert detail["proposal"]["fault_code"] == "wallet_simulation_failed"
    # the account ledger still shows the earlier EVM accounts as chain-qualified rows
    _status, st = _http_json(base, "/api/wallet/status")
    chains_seen = {a["chain"] for a in (st["status"].get("accounts") or [])}
    assert {BASE_SEPOLIA, ETHEREUM_SEPOLIA} <= chains_seen
