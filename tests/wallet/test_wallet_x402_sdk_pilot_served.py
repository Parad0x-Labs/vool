"""An x402 SDK payment from a Crypto Pilot wallet, end to end through the served product on Base Sepolia.

SCRIPTED MODEL, SIMULATED CHAIN, real product: the daemon is ``apps.vool_api_server`` in its own process and home; the
model is a prompt-routed stand-in that emits the product's own ``x402.propose`` tool call; the chain is the loopback
node answering as Base Sepolia with its USDC; the paid resource is a loopback stand-in built with the x402 SDK's own
encoders, whose facilitator settles on that chain; the page is the served chat in the confined chromium. Every door and
every value is the product's.

Proved here: the model's fetch meets a 402 and parks one capped payment; the pending card opens the Pilot sheet, which
shows the request, the site and that the site pays the network fee; a wrong PIN pays nothing; the right PIN pays
exactly once, the card shows the confirmed payment, and the wallet itself broadcast nothing.
"""
from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import served_browser
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon
from tests.wallet._rig_x402_sdk import BASE_SEPOLIA, ScriptedUsdcChain, ScriptedX402SdkResource

pytest.importorskip("x402", reason="the x402 SDK lane is the optional `x402` extra")

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
WRONG_PIN = "593027"
AMOUNT = 600_000  # 0.6 USDC
FUNDED = 2_000_000
PAYEE = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=30) as response:
        return json.load(response)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("x402-sdk-pilot") / "home"
    chain = ScriptedUsdcChain(network=BASE_SEPOLIA)
    provider = PromptRoutedProvider()
    chain.__enter__()
    provider.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WALLET_ENABLED": "1",
        "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
        "VOOL_WALLET_RPC_URLS": json.dumps({BASE_SEPOLIA: chain.url}),
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        "VOOL_WALLET_X402_CAP_MINOR": str(AMOUNT * 2),
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps",
        "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
    })
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        provider.reset()
        yield daemon, chain, provider
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        chain.__exit__(None, None, None)


def _ready_pilot_wallet(daemon) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {
        "network": BASE_SEPOLIA, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
        "creation_key": f"x402sdk-{uuid.uuid4().hex}", "label": "Paid requests",
    })
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    backup = revealed.get("backup") or {}
    assert status == 200 and backup.get("ack_token"), (status, sorted(backup))  # key names only, never the value
    ack_token = backup["ack_token"]
    backup = revealed = None
    status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"]


def test_a_paid_request_from_chat_is_paid_once_from_the_pilot_sheet(served):
    daemon, chain, provider = served
    wallet = _ready_pilot_wallet(daemon)
    chain.fund_token(wallet["address"], FUNDED)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        # the model's fetch meets the 402: one capped payment is parked, nothing is signed or paid
        turn = daemon.chat(f"Fetch the x402 resource at {resource.url}", session_id="x402-sdk-served-1", model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        assert "pending_approval" in text and "pay-" in text, text
        proposal_id = next(word.strip(".,:()") for word in text.split() if word.startswith("pay-"))
        pending = {row["proposal_id"]: row for row in _get(daemon, "/api/wallet/status")["status"]["pending"]}
        assert pending[proposal_id]["pilot_transfer"] is True and pending[proposal_id]["origin"] == "x402_sdk", pending[proposal_id]
        assert resource.paid_requests == [] and len(resource.requests) == 1

        manager, browser = served_browser.launch_chromium()
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
            page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
            page.wait_for_selector("#input", timeout=20_000)

            card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
            card.locator(".vw-review").wait_for(timeout=30_000)
            with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
                card.locator(".vw-review").click()
            quote = minted.value.json()["quote"]
            page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
            origin = resource.url.split("/paid/")[0]
            assert page.locator("#vwSheetTitle").text_content() == f"Pay {origin} for one request"
            sheet = page.locator(f'[role="dialog"][data-proposal="{proposal_id}"]')
            assert sheet.locator('[data-review="network_cost"]').text_content().startswith("Network fee: paid by the site")
            assert page.locator("#vwSheetApprove").text_content() == "Approve and pay"
            assert (quote["fields"]["x402sdk"]["url"], quote["fields"]["fee_max_minor"], quote["fields"]["asset"]) == (resource.url, 0, "USDC")

            # a wrong PIN: refused by the server, nothing held or paid
            page.locator("#vwSheetCredential").fill(WRONG_PIN)
            with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as refused:
                page.locator("#vwSheetApprove").click()
            assert refused.value.json().get("error") == "wallet_pin_invalid", refused.value.json()
            page.wait_for_function("!document.getElementById('vwSheetApprove').disabled")
            assert resource.paid_requests == [] and _get(daemon, "/api/wallet/transfers")["transfers"] == []

            # the right PIN: one paid request, the payment proven on chain, the wallet broadcast nothing
            page.locator("#vwSheetCredential").fill(PIN)
            with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
                page.locator("#vwSheetApprove").click()
            answer = approved.value.json()
            assert answer.get("ok") is True, answer
            assert answer["x402_sdk"] == {"protocol": "x402", "resource_status": 200, "delivered": True, "unanswered": "", "claimed_transaction_unproven": False}
            assert answer["paykit"] is None
            transfer = answer["transfer"]
            assert (transfer["state"], transfer["tx_id"], transfer["charged_fee_minor"]) == ("confirmed", resource.landed[0], "0"), transfer
            assert len(resource.paid_requests) == 1 and chain.send_count() == 0
            assert (chain.token_balance(wallet["address"]), chain.token_balance(PAYEE)) == (FUNDED - AMOUNT, AMOUNT)
            assert page.locator('.vw-sheet[role="dialog"]').count() == 0, "the sheet closes on success"
            assert "Confirmed" in card.locator(".vw-transfer-label").text_content()
            assert card.locator(".vw-transfer-id").text_content() == f"Transaction {transfer['tx_id']}"
            assert errors == [], errors
        finally:
            browser.close()
            manager.stop()

    listed = {t["proposal_id"]: t for t in _get(daemon, "/api/wallet/transfers")["transfers"]}
    assert listed[proposal_id]["state"] == "confirmed" and listed[proposal_id]["from_address"] == wallet["address"]
    prompts = " ".join(str(call.get("prompt") or "") for call in provider.calls)
    assert PIN not in prompts and WRONG_PIN not in prompts, "no prompt the model saw carried a credential"
