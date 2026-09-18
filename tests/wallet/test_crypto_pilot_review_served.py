"""Product follow-up, Phase A, SERVED: the payment review a person reads, on the real daemon's chat page.

SCRIPTED MODEL, SIMULATED CHAINS (a Base Sepolia node answering as Jovian -- operator and L1 fee parts, long decimal
tails -- and a Solana Devnet node), real daemon in its own home, the served lane's confined chromium allowed to reach
the daemon only. The wallets are made through the setup doors, the requests through chat (the model's own tool call),
every preview through the quote door. Proven here:

* the preview says who receives what and why (typed purpose, full recipient, network badge, exact amount), shows the
  fee as a short estimate and a rounded-up maximum, and keeps every exact figure in Details;
* Decide later / Escape close without deciding: no approve or reject request, the typed PIN cleared, the chat draft and
  the Review action kept, focus returned; every reopening mints a fresh quote and the old one is superseded on the
  server; an expired preview (five simulated minutes, a controlled clock) refuses approval until Refresh;
* a late quote reply for a dismissed preview cannot replace a newer sheet; an approval that already started says Close;
* the confirmed receipt keeps purpose, recipient, network and the exact fee beside its short form.
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
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"


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

    home = tmp_path_factory.mktemp("review") / "home"
    sol = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    sol.real_signature = True
    sol.status_keyed = True
    base = ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack", l1_fee=40_000_000_000_000, fork="jovian", operator_fee_scalar=100, operator_fee=7)
    provider = PromptRoutedProvider()
    for node in (sol, base, provider):
        node.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_DEVNET: sol.url, BASE_SEPOLIA: base.url}),
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
        yield daemon, {"sol": sol, "base": base}, provider
    finally:
        daemon.stop()
        for node in (provider, base, sol):
            node.__exit__(None, None, None)


def _ready_wallet(daemon, network: str, label: str) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {"network": network, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"review-{uuid.uuid4().hex}", "label": label})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200 and (revealed.get("backup") or {}).get("ack_token"), (status, sorted(revealed.get("backup") or {}))
    token = revealed["backup"]["ack_token"]
    revealed = None
    status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"]


def _chat_proposal(daemon, message: str, *, session: str) -> str:
    turn = daemon.chat(message, session_id=session, model=MODEL, mode="auto")
    text = str((turn.get("message") or {}).get("content") or "")
    assert "pending_approval" in text and "pay-" in text, text
    return next(word.strip(".,:") for word in text.split() if word.startswith("pay-"))


def _open_page(browser, daemon):
    page = browser.new_page()
    errors: list[str] = []
    decisions: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: decisions.append(request.url) if request.method == "POST" and request.url.endswith(("/api/wallet/approve", "/api/wallet/reject")) else None)
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=20_000)
    return page, errors, decisions


def _card(page, proposal_id: str):
    return page.locator(f'.vw-card[data-proposal="{proposal_id}"]')


def _open_sheet(page, daemon, proposal_id: str) -> dict:
    review = _card(page, proposal_id).locator(".vw-review")
    review.wait_for(timeout=30_000)
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
        review.click()
    quote = minted.value.json()["quote"]
    page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
    return quote


def _approve_on_sheet(page, daemon, pin: str) -> dict:
    page.locator("#vwSheetCredential").fill(pin)
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
        page.locator("#vwSheetApprove").click()
    return approved.value.json()


def test_base_review_reads_purpose_recipient_and_short_fees_with_exact_details(served, browser):
    daemon, chains, _provider = served
    wallet = _ready_wallet(daemon, BASE_SEPOLIA, "base review")
    chains["base"].fund(wallet["address"], 10**18)
    to = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
    to = to[:-1] + "7"  # no digits+b tail: this proof is about the preview, not the routing finding
    proposal_id = _chat_proposal(daemon, f"Send 0.001 ETH on base to {to} for the review proof", session="review-base")
    page, errors, _decisions = _open_page(browser, daemon)
    try:
        card = _card(page, proposal_id)
        card.wait_for(timeout=30_000)
        assert card.locator('[data-field="purpose"]').get_attribute("data-kind") == "direct"
        assert card.locator(".vw-purpose-headline").text_content() == f"Send 0.001 ETH to {to}"
        assert card.locator(".vw-mechanism").text_content() == "Direct transfer"
        assert card.locator(".vw-row .vw-ident").first.text_content() == to
        assert "x402" not in card.text_content()
        quote = _open_sheet(page, daemon, proposal_id)
        f = quote["fields"]
        sheet = page.locator('#vwSheet [role="dialog"]')
        assert page.locator("#vwSheetTitle").text_content() == "Send 0.001 ETH"
        assert sheet.locator('[data-field="purpose"]').get_attribute("data-kind") == "direct"
        assert sheet.locator('[data-field="to"] .vw-ident').text_content() == to
        assert sheet.locator('[data-field="to"] .vw-copy').count() == 1
        assert sheet.locator('[data-field="to"]').text_content().find("unsaved address") > 0
        assert sheet.locator('[data-field="network"] .vw-badge').text_content() == "TESTNET"
        assert sheet.locator('[data-field="amount"] .vw-sheet-value').text_content() == "0.001 ETH"
        fee_text = sheet.locator('[data-field="fee"] .vw-sheet-value').text_content()
        assert fee_text == f["fee_estimate_display"] + " · " + f["fee_max_display"], fee_text
        assert "≈" in fee_text and "at most" in fee_text and len(fee_text) < 60
        assert sheet.locator('[data-field="max_total"] .vw-sheet-value').text_content() == f["max_total_display"]
        # the exact figures live in Details, closed by default, and are the exact integer decimals the quote carries
        details = page.locator("#vwSheetDetails")
        assert details.evaluate("d => d.open") is False
        assert details.locator('[data-field="fee_max_exact"] .vw-sheet-value').text_content() == f["fee_max_human"] + " ETH"
        assert details.locator('[data-field="fee_exact"] .vw-sheet-value').text_content() == f["fee_estimate_human"] + " ETH"
        assert details.locator('[data-field="after_exact"]').count() == 1
        assert len(f["fee_max_human"].split(".")[1]) > 8  # the long tail is real base-unit precision
        assert details.locator('[data-field^="fee_part_"]').count() >= 3  # execution, L1, operator parts
        # the ordinary sentence wraps by words; the recipient may break anywhere
        assert page.evaluate("getComputedStyle(document.querySelector('[data-field=\"to\"] .vw-ident')).overflowWrap") == "anywhere"
        assert page.evaluate("getComputedStyle(document.querySelector('[data-field=\"fee\"] .vw-sheet-value')).wordBreak") == "normal"
        sends = chains["base"].send_count()
        answer = _approve_on_sheet(page, daemon, PIN)
        assert answer["ok"] and answer["transfer"]["state"] == "confirmed", answer
        assert chains["base"].send_count() == sends + 1
        page.wait_for_selector('#vwSheet', state="detached")
        result = card.locator(".vw-result")
        assert result.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Base Sepolia"
        assert result.locator('[data-field="purpose"] .vw-purpose-headline').text_content() == f"Send 0.001 ETH to {to}"
        assert result.locator(".vw-transfer-detail .vw-ident").text_content() == to
        record = _get(daemon, f"/api/wallet/transfers?proposal_id={proposal_id}")["transfer"]
        fee_line = result.locator(".vw-transfer-detail", has_text="Fee ").text_content()
        assert fee_line == f"Fee {record['charged_fee_display']} (exactly {record['charged_fee_human']} ETH)", fee_line
        assert record["fee_fork"] == "jovian" and record["fee_state"] == "exact"
        assert result.locator("a.vw-explorer").text_content() == "View on Basescan"
        assert not errors, errors
    finally:
        page.close()


def test_solana_decide_later_escape_fresh_quotes_expiry_and_late_replies(served, browser):
    daemon, chains, provider = served
    _ready_wallet(daemon, SOLANA_DEVNET, "solana review")
    first_to, second_to = _sol_key(), _sol_key()
    first = _chat_proposal(daemon, f"Send 0.0004 SOL on solana to {first_to} for the first review", session="review-sol-1")
    second = _chat_proposal(daemon, f"Send 0.0007 SOL on solana to {second_to} for the second review", session="review-sol-2")
    page, errors, decisions = _open_page(browser, daemon)
    try:
        card = _card(page, first)
        card.wait_for(timeout=30_000)
        assert card.locator(".vw-purpose-headline").text_content() == f"Send 0.0004 SOL to {first_to}"
        quote_a = _open_sheet(page, daemon, first)
        sheet = page.locator('#vwSheet [role="dialog"]')
        assert sheet.locator('[data-field="network"] .vw-badge').text_content() == "DEVNET"
        assert sheet.locator('[data-field="fee"] .vw-sheet-value').text_content() == "0.000005 SOL · 0.000005 SOL"  # nothing to shorten: no marker
        assert page.locator("#vwSheetDetails").locator('[data-field="after"] .vw-sheet-value').text_content().find("at least") > 0
        # Decide later with a typed PIN: no decision request, the PIN gone, the draft and the Review action kept
        page.locator("#input").fill("my unfinished chat message")
        page.locator("#vwSheetCredential").fill(PIN)
        page.evaluate("window.__cred = document.getElementById('vwSheetCredential')")
        assert page.locator("#vwSheetLater").text_content() == "Decide later"
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        assert page.evaluate("window.__cred.value") == ""
        assert decisions == []
        assert card.locator(".vw-review").count() == 1 and page.evaluate("document.activeElement && document.activeElement.className.indexOf('vw-review') >= 0")
        assert page.locator("#input").input_value() == "my unfinished chat message"
        assert card.locator(".vw-result").text_content() == "Review this request again whenever you are ready."
        # reopening later mints a fresh preview with an empty credential; the old preview is superseded on the server
        # (the daemon's own clock rules the quote window: its expiry is proven in-process by the quote corpus)
        quote_b = _open_sheet(page, daemon, first)
        assert quote_b["quote_id"] != quote_a["quote_id"]
        assert page.locator("#vwSheetCredential").input_value() == ""
        pending = {row["proposal_id"]: row for row in _get(daemon, "/api/wallet/status")["status"]["pending"]}
        assert pending[first]["open_quote_id"] == quote_b["quote_id"]
        # the preview's own window closes under a controlled page clock (five minutes, no sleep): Approve refuses until Refresh
        page.evaluate("window.__realNow = Date.now; Date.now = () => window.__realNow() + 300000")
        page.wait_for_function("document.getElementById('vwSheetApprove').disabled", timeout=5_000)
        assert "expired" in page.locator("#vwSheetState").text_content()
        page.evaluate("Date.now = window.__realNow")
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            page.locator("#vwSheetRefresh").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        # Escape: the same non-deciding close
        page.locator("#vwSheetCredential").fill(PIN)
        page.keyboard.press("Escape")
        page.wait_for_selector("#vwSheet", state="detached")
        assert decisions == [] and page.locator("#input").input_value() == "my unfinished chat message"
        # a late quote reply for a dismissed preview cannot replace the sheet opened afterwards
        held: list = []
        arm = {"on": True}

        def hold(route):
            if arm["on"]:
                arm["on"] = False
                held.append((route, route.fetch()))
            else:
                route.continue_()

        page.route(daemon.base_url + "/api/wallet/quote", hold)
        _card(page, second).locator(".vw-review").click()
        page.wait_for_function("!!document.getElementById('vwSheetLater')")
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        _open_sheet(page, daemon, first)
        route, response = held.pop()
        route.fulfill(response=response)
        page.wait_for_timeout(300)
        assert page.locator('#vwSheet [role="dialog"]').get_attribute("data-proposal") == first
        assert page.locator("#vwSheetTitle").text_content() == "Send 0.0004 SOL"
        page.unroute(daemon.base_url + "/api/wallet/quote")
        # an approval that already started says Close, and closing does not retract it
        sends = chains["sol"].send_count()
        approve_hold: list = []
        page.route(daemon.base_url + "/api/wallet/approve", lambda route: approve_hold.append((route, route.fetch())))
        page.locator("#vwSheetCredential").fill(PIN)
        page.locator("#vwSheetApprove").click()
        page.wait_for_function("document.getElementById('vwSheetLater').textContent === 'Close'", timeout=10_000)
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        assert card.locator(".vw-result").text_content() == "Check this request for its latest status."
        for _ in range(300):  # the held approval answers when the daemon has signed, sent and observed
            if approve_hold:
                break
            page.wait_for_timeout(200)
        route, response = approve_hold.pop()
        route.fulfill(response=response)
        page.unroute(daemon.base_url + "/api/wallet/approve")
        page.wait_for_function(f"document.querySelector('.vw-card[data-proposal=\"{first}\"] .vw-transfer-label') !== null", timeout=15_000)
        assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        assert chains["sol"].send_count() == sends + 1
        assert decisions == [daemon.base_url + "/api/wallet/approve"]
        assert not errors, errors
        assert PIN not in " ".join(str(c.get("prompt") or "") for c in provider.calls)
    finally:
        page.close()
