"""Agent spending, SERVED: the owner sets it up in Settings and VOOL pays a service from chat without asking.

SCRIPTED MODEL, SIMULATED CHAIN, real product: the daemon is ``apps.vool_api_server`` in its own process and home; the
model is a prompt-routed stand-in that emits the product's own ``x402.propose`` tool call; the chain is the loopback
node answering as Base Sepolia with its USDC; the paid service is a loopback stand-in built with the x402 SDK's own
encoders. Everything the owner does is done on the served Settings and chat pages in the confined chromium.

Proved here, in the order a person meets it: Agent spending is in Settings; the assistant has no wallet and pays
nothing on its own; a wrong PIN creates nothing; the right PIN creates its wallet and shows the backup key once; an
allowance is typed in plain amounts and needs the PIN; the first payment to a service still asks the owner, on a sheet
that asks for the owner's own PIN; the next request is paid from chat with nobody asked, and Settings shows what was
used and why; pausing needs no PIN and stops it; turning it back on needs the PIN; a price above the per-payment
limit asks again; the owner's own wallet never pays for any of it.
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

PIN, WRONG_PIN = "482913", "593027"
AMOUNT = 600_000  # 0.6 USDC
FUNDED = 5_000_000
PAYEE = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


class Site(ScriptedX402SdkResource):
    """One site selling several things: each address it is asked at is its own offer (the 402 names that address)."""

    asked_at = ""

    def challenge(self):
        from x402.schemas import PaymentRequired

        return PaymentRequired.model_validate({"x402Version": 2, "error": "payment required", "accepts": [self.requirement()],
                                               "resource": {"url": self.asked_at or self.url, "description": "Base numbers", "mimeType": "application/json"}})


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
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

    home = tmp_path_factory.mktemp("agent-spending") / "home"
    chain = ScriptedUsdcChain(network=BASE_SEPOLIA)
    provider = PromptRoutedProvider()
    chain.__enter__()
    provider.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
        "VOOL_WALLET_RPC_URLS": json.dumps({BASE_SEPOLIA: chain.url}), "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1", "VOOL_WALLET_X402_CAP_MINOR": str(AMOUNT * 4),
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url, "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps", "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
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


def _owner_wallet(daemon) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {"network": BASE_SEPOLIA, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
                                                                "creation_key": f"owner-{uuid.uuid4().hex}", "label": "My wallet"})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200
    status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": revealed["backup"]["ack_token"]})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"]


def _page(browser, daemon, path: str, ready: str):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(daemon.base_url + path, wait_until="domcontentloaded")
    page.wait_for_selector(ready, timeout=30_000)
    return page, errors


def _prove(page, credential: str) -> None:
    page.wait_for_selector("#vwOwnerProofSecret", timeout=10_000)
    page.fill("#vwOwnerProofSecret", credential)
    page.click("#vwOwnerProofOk")


def _said(page, text: str) -> None:
    # the section's message line, or the line under the agent's own Create button (a create answers where it was pressed)
    page.wait_for_function("(t) => Array.from(document.querySelectorAll('#vwAgentsMsg, [id^=vwAgentMsg_]')).some(m => m.textContent.indexOf(t) >= 0)", arg=text, timeout=15_000)


def _ask(daemon, url: str, session: str) -> str:
    turn = daemon.chat(f"Fetch the x402 resource at {url}", session_id=session, model=MODEL, mode="auto")
    return str((turn.get("message") or {}).get("content") or "")


def test_the_owner_sets_up_agent_spending_in_settings_and_vool_pays_from_chat_inside_it(served):
    daemon, chain, provider = served
    owner = _owner_wallet(daemon)
    chain.fund_token(owner["address"], FUNDED)
    manager, browser = served_browser.launch_chromium()
    try:
        with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as site:
            origin = site.url.split("/paid/")[0]
            page, errors = _page(browser, daemon, "/settings#wallet", "#vwCxAgents")
            box = page.locator("#vwCxAgents")
            page.wait_for_selector("#vwAgentCreate_assistant", timeout=15_000)
            assert "It has no wallet yet, so it pays nothing on its own." in box.text_content()
            assert box.locator("[data-agent-wallet]").count() == 0

            # --- a wrong PIN creates nothing; the right one creates the wallet and shows its backup key once ----------
            page.select_option("#vwAgentNet_assistant", BASE_SEPOLIA)
            page.click("#vwAgentCreate_assistant")
            _prove(page, WRONG_PIN)
            _said(page, "That PIN was not right. Nothing was changed.")
            assert _get(daemon, "/api/wallet/agents")["agent_spending"]["agents"][0]["wallets"] == []
            page.select_option("#vwAgentNet_assistant", BASE_SEPOLIA)
            page.click("#vwAgentCreate_assistant")
            _prove(page, PIN)
            page.wait_for_selector("#vwCxBackup", timeout=20_000)
            assert len(page.locator("#vwCxBackupValue").text_content() or "") >= 32
            page.click("#vwCxBackupSaved")
            card = box.locator('[data-agent-wallet][data-setup-state="ready"]')
            card.wait_for(timeout=20_000)
            assert page.locator("#vwCxBackup").count() == 0
            agent_wallet = _get(daemon, "/api/wallet/agents")["agent_spending"]["agents"][0]["wallets"][0]
            assert agent_wallet["ready"] is True and agent_wallet["address"].lower() != owner["address"].lower()
            assert card.locator(".vw-cx-address").text_content() == agent_wallet["address"]
            assert "every payment from this wallet asks you first" in card.text_content()
            chain.fund_token(agent_wallet["address"], FUNDED)
            key = agent_wallet["wallet_id"]

            # --- the allowance: typed amounts; terms that do not fit are refused in plain words; saving needs the PIN ---
            card.locator(f'[data-agent-allow="{key}"]').click()
            page.select_option(f"#vwAgCoin_{key}", "USDC")
            page.fill(f"#vwAgPer_{key}", "5")
            page.fill(f"#vwAgPeriodAmount_{key}", "2")
            page.fill(f"#vwAgTotal_{key}", "3")
            page.fill(f"#vwAgCount_{key}", "10")
            page.fill(f"#vwAgSvc_{key}_0", origin)
            # the page reading its status again (another control, its own refresh) keeps what is being typed here
            page.evaluate("window.VoolWallet.refresh()")
            page.wait_for_timeout(1500)
            assert page.input_value(f"#vwAgTotal_{key}") == "3" and page.input_value(f"#vwAgSvc_{key}_0") == origin
            assert page.locator("#vwCxAgents").count() == 1
            page.click(f"#vwAgSave_{key}")
            _said(page, "One payment has to fit inside the period amount, and the period amount inside the total. Nothing was changed.")
            page.fill(f"#vwAgPer_{key}", "1")
            page.click(f"#vwAgSave_{key}")
            _prove(page, PIN)
            allowance = box.locator('[data-allowance][data-state="active"]')
            allowance.wait_for(timeout=15_000)
            facts = allowance.locator("[data-allowance-facts]").text_content()
            assert "Up to 1 USDC for one payment" in facts and "0 of 2 USDC used this day" in facts and "0 of 3 USDC used in total" in facts and "0 of 10 payments" in facts and "30 days left" in facts
            assert "its first payment asks you" in allowance.locator(f'[data-service="{origin}"]').text_content()

            # --- the first payment to a service is still the owner's: the sheet asks for the owner's own PIN ------------
            site.asked_at = site.url + "?n=1"
            # --- Web lookups are off on a fresh install: the allowance does not open the web, and nothing is paid -------
            text = _ask(daemon, site.asked_at, "agent-spend-web-off")
            assert "Web lookups are off" in text, text
            assert site.paid_requests == [] and _get(daemon, "/api/wallet/agents")["agent_spending"]["waiting"] == []
            # the owner turns Web lookups on, through the door the Privacy & Permissions switch posts to
            status, switched = _door(daemon, "/api/onboarding/pact/boundary", {"key": "web_lookups", "value": True})
            assert status == 200 and switched["live"]["allow_web_fallback"] is True, switched
            text = _ask(daemon, site.asked_at, "agent-spend-1")
            assert "Nothing was paid" in text and "first payment to this service" in text, text
            assert site.paid_requests == []
            waiting = _get(daemon, "/api/wallet/agents")["agent_spending"]["waiting"]
            assert len(waiting) == 1 and waiting[0]["wallet_id"] == key and waiting[0]["amount"] == "0.6"
            proposal_id = waiting[0]["proposal_id"]
            chat, chat_errors = _page(browser, daemon, "/chat", "#input")
            pending = chat.locator(f'.vw-card[data-proposal="{proposal_id}"]')
            pending.locator(".vw-review").wait_for(timeout=30_000)
            pending.locator(".vw-review").click()
            chat.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
            assert chat.locator("#vwSheetCredential").get_attribute("placeholder") == "wallet PIN"
            chat.locator("#vwSheetCredential").fill(WRONG_PIN)
            with chat.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as refused:
                chat.locator("#vwSheetApprove").click()
            assert refused.value.json().get("error") == "wallet_pin_invalid" and site.paid_requests == []
            chat.wait_for_function("!document.getElementById('vwSheetApprove').disabled")
            chat.locator("#vwSheetCredential").fill(PIN)
            with chat.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
                chat.locator("#vwSheetApprove").click()
            assert approved.value.json().get("ok") is True, approved.value.json()
            assert len(site.paid_requests) == 1 and chain.token_balance(PAYEE) == AMOUNT
            chat.close()

            # --- now the service is known: the next request is paid from chat, nobody asked -------------------------------
            site.asked_at = site.url + "?n=2"
            text = _ask(daemon, site.asked_at, "agent-spend-1")
            assert "Paid 0.6 USDC from the assistant's own wallet" in text and "inside the spending allowance" in text, text
            assert len(site.paid_requests) == 2 and chain.token_balance(PAYEE) == AMOUNT * 2
            assert chain.token_balance(agent_wallet["address"]) == FUNDED - AMOUNT * 2 and chain.token_balance(owner["address"]) == FUNDED
            assert _get(daemon, "/api/wallet/status")["status"]["pending"] == []
            page.reload(wait_until="domcontentloaded")
            allowance = page.locator('#vwCxAgents [data-allowance][data-state="active"]')
            allowance.wait_for(timeout=30_000)
            facts = allowance.locator("[data-allowance-facts]").text_content()
            assert "0.6 of 2 USDC used this day" in facts and "0.6 of 3 USDC used in total" in facts and "1 of 10 payments" in facts, facts
            assert "paid at " in allowance.locator(f'[data-service="{origin}"]').text_content()
            held = page.locator('#vwCxAgents [data-agent-balance="read"]')
            held.wait_for(timeout=20_000)
            assert "In it now: 3.8 USDC" in held.text_content(), held.text_content()
            recent = page.locator("#vwAgentsRecent")
            assert recent.locator('[data-paid="true"]').count() == 1 and "paid 0.6 USDC" in recent.text_content() and "first payment to this service" in recent.text_content()

            # --- pause needs no PIN and stops it; turning it back on needs the PIN ------------------------------------------
            allowance.locator('[data-allowance-action="pause"]').click()
            page.locator('#vwCxAgents [data-allowance][data-state="paused"]').wait_for(timeout=15_000)
            site.asked_at = site.url + "?n=3"
            text = _ask(daemon, site.asked_at, "agent-spend-1")
            assert "Nothing was paid" in text and "paused" in text and len(site.paid_requests) == 2, text
            page.locator('#vwCxAgents [data-allowance-action="resume"]').click()
            _prove(page, WRONG_PIN)
            _said(page, "That PIN was not right. Nothing was changed.")
            assert _get(daemon, "/api/wallet/agents")["agent_spending"]["agents"][0]["allowances"][0]["state"] == "paused"
            page.locator('#vwCxAgents [data-allowance-action="resume"]').click()
            _prove(page, PIN)
            page.locator('#vwCxAgents [data-allowance][data-state="active"]').wait_for(timeout=15_000)

            # --- a tighter limit is saved without the PIN, and a price above it asks the owner again ------------------------
            allowance = page.locator('#vwCxAgents [data-allowance][data-state="active"]')
            allowance.locator('[data-allowance-action="change"]').click()
            allowance_id = allowance.get_attribute("data-allowance")
            page.fill(f"#vwAgPer_{allowance_id}", "0.5")
            page.click(f"#vwAgSave_{allowance_id}")
            _said(page, "Saved.")
            site.asked_at = site.url + "?n=4"
            text = _ask(daemon, site.asked_at, "agent-spend-1")
            assert "Nothing was paid" in text and "more than the allowance permits for one payment" in text and len(site.paid_requests) == 2, text
            assert chain.token_balance(owner["address"]) == FUNDED, "the owner's own wallet never paid"

            # --- helper agents get nothing until the owner types what one helper may spend, with the PIN -------------------
            allowance = page.locator('#vwCxAgents [data-allowance][data-state="active"]')
            assert allowance.locator('[data-allowance-helpers="off"]').text_content() == "Helper agents pay nothing."
            allowance.locator('[data-allowance-action="change"]').click()
            page.fill(f"#vwAgHelperPer_{allowance_id}", "0.1")
            page.fill(f"#vwAgHelperTotal_{allowance_id}", "0.3")
            page.click(f"#vwAgSave_{allowance_id}")
            _prove(page, PIN)
            page.locator('#vwCxAgents [data-allowance-helpers="on"]').wait_for(timeout=15_000)
            assert "each may spend up to 0.1 USDC for one payment and 0.3 USDC in total" in page.locator('#vwCxAgents [data-allowance-helpers="on"]').text_content()
            assert errors == [] and chat_errors == [], (errors, chat_errors)
    finally:
        browser.close()
        manager.stop()
    prompts = " ".join(str(call.get("prompt") or "") for call in provider.calls)
    assert PIN not in prompts and WRONG_PIN not in prompts, "no prompt the model saw carried a credential"
