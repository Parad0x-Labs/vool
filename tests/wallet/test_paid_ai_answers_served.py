"""Pay-per-answer AI over x402, end to end through the served product on Base Sepolia.

SCRIPTED MODEL, SIMULATED CHAIN, real product: the daemon is ``apps.vool_api_server`` in its own process and home; the
chat model is a prompt-routed stand-in that emits the product's own ``ai.paid_ask`` tool call; the paid AI service is
the loopback x402 resource built with the x402 SDK's own encoders, answering a chat completion once paid; the chain is
the loopback node answering as Base Sepolia with its USDC; the approval is the served chat's own wallet card and Pilot
sheet in the confined chromium.

Proved here, from the owner's side:
* the owner adds a service in Settings' door; asking it from chat sends only the question and parks ONE capped payment,
  nothing paid;
* approving with the PIN pays exactly once, and the answer appears in that chat marked as the model that was paid for;
* a service that reports another model is still shown (it was paid for) but marked, and is paused: the next question is
  refused before anything is sent;
* a service asking more than the owner's price limit is refused before anything is parked; nothing is paid.
"""
from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import served_browser
from tests.wallet import _rig_provider, _rig_x402_sdk
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon
from tests.wallet._rig_x402_sdk import BASE_SEPOLIA, ScriptedUsdcChain, ScriptedX402SdkResource

pytest.importorskip("x402", reason="the x402 SDK lane is the optional `x402` extra")

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
AMOUNT = 50_000  # 0.05 USDC
FUNDED = 2_000_000
PAYEE = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
ASKED_MODEL = "open-reasoner-70b"
#: chat ids the served page opens from its address (#openclaw:<20 hex>)
CHAT_1, CHAT_2, CHAT_3 = (f"openclaw:{n:020x}" for n in (0xA1, 0xA2, 0xA3))


def _completion(model: str | None, content: str) -> bytes:
    data: dict = {"id": "cmpl-1", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                  "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}}
    if model is not None:
        data["model"] = model
    return json.dumps(data).encode()


def _route(last_user: str):
    marker = "ask my paid model:"
    if marker in last_user.lower():
        question = last_user[last_user.lower().index(marker) + len(marker):].strip()
        return {"id": "call-ai", "type": "function", "function": {"name": "ai__paid_ask", "arguments": {"question": question}}}
    return None


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

    patch = pytest.MonkeyPatch()
    original = _rig_provider.route_tool_call
    patch.setattr(_rig_provider, "route_tool_call", lambda text: _route(text) or original(text))
    home = tmp_path_factory.mktemp("paid-ai") / "home"
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
        "VOOL_WALLET_X402_CAP_MINOR": str(AMOUNT * 20),
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
        status, created = _door(daemon, "/api/wallet/setup/create", {
            "network": BASE_SEPOLIA, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
            "creation_key": f"paid-ai-{uuid.uuid4().hex}", "label": "Paid answers",
        })
        assert status == 200, created
        wallet_id = created["setup"]["wallet_id"]
        status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
        ack_token = (revealed.get("backup") or {}).get("ack_token")
        assert status == 200 and ack_token
        status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
        assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
        chain.fund_token(ready["setup"]["address"], FUNDED)
        yield daemon, chain, provider, ready["setup"]
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        chain.__exit__(None, None, None)
        patch.undo()


def _add(daemon, resource, *, label: str, max_price: str) -> dict:
    status, added = _door(daemon, "/api/wallet/ai-endpoints", {"action": "add", "label": label, "base_url": resource.url.split("/paid/")[0] + "/v1",
                                                                "model": ASKED_MODEL, "max_price": max_price})
    assert status == 200, added
    return next(e for e in added["ai_endpoints"] if e["label"] == label)


def _approve_in_chat(daemon, proposal_id: str, *, session: str, shows: str) -> dict:
    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
        page.goto(daemon.base_url + "/chat#" + session, wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=20_000)
        card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
        card.locator(".vw-review").wait_for(timeout=30_000)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            card.locator(".vw-review").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        page.locator("#vwSheetCredential").fill(PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
            page.locator("#vwSheetApprove").click()
        answer = approved.value.json()
        # the open chat shows the answer on its own (the idle refresh), no reload
        page.wait_for_function("t => document.body.innerText.includes(t)", arg=shows, timeout=30_000)
        assert errors == [], errors
        return answer
    finally:
        browser.close()
        manager.stop()


def _transcript(daemon, session: str) -> list[dict]:
    """The chat's own transcript door, as the page reads it: the paid answers posted into this chat."""
    from urllib.parse import quote

    data = _get(daemon, f"/api/chat/history?session={quote(session)}&event_kind=paid_ai_answer")
    return [m for m in data["messages"] if m.get("role") == "assistant"]


def test_a_paid_answer_is_paid_once_and_posted_with_its_model_checked(served, monkeypatch):
    daemon, chain, provider, wallet = served
    monkeypatch.setattr(_rig_x402_sdk, "PAID_BODY", _completion(ASKED_MODEL, "Twelve, by the stated rule."))
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _add(daemon, resource, label="Reasoner", max_price="0.10")
        turn = daemon.chat("ask my paid model: what is seven plus five?", session_id=CHAT_1, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        assert "Approve the payment in your wallet" in text and "0.05 USDC" in text, text
        proposal_id = next(word.strip(".,:()") for word in text.split() if word.startswith("pay-"))
        assert resource.paid_requests == [] and len(resource.requests) == 1
        sent = json.loads(resource.requests[0]["body"])
        # only the question leaves, for the model the owner named, as one non-streamed answer
        assert sent["model"] == ASKED_MODEL and sent["messages"] == [{"role": "user", "content": "what is seven plus five?"}] and sent["stream"] is False

        answer = _approve_in_chat(daemon, proposal_id, session=CHAT_1, shows="Twelve, by the stated rule.")
        assert answer.get("ok") is True, answer
        assert answer["x402_sdk"]["delivered"] is True
        assert answer["paid_ai"]["verdict"] == "match" and answer["paid_ai"]["posted"] is True, answer["paid_ai"]
        assert len(resource.paid_requests) == 1 and json.loads(resource.paid_requests[0]["body"]) == sent, "the paid request is the asked one"
        assert chain.token_balance(PAYEE) == AMOUNT

    posted = _transcript(daemon, CHAT_1)
    assert len(posted) == 1
    assert "Twelve, by the stated rule." in posted[0]["content"] and "named the model you paid for" in posted[0]["content"]
    listed = {e["label"]: e for e in _get(daemon, "/api/wallet/status")["status"]["crypto_pilot"]["ai_endpoints"]}
    assert listed["Reasoner"]["on"] is True and listed["Reasoner"]["last_verdict"] == "match"


def _clear(daemon) -> None:
    for entry in _door(daemon, "/api/wallet/ai-endpoints", {"action": "list"})[1]["ai_endpoints"]:
        _door(daemon, "/api/wallet/ai-endpoints", {"action": "remove", "id": entry["id"]})


def test_a_service_reporting_another_model_is_marked_and_paused(served, monkeypatch):
    daemon, chain, provider, wallet = served
    _clear(daemon)
    monkeypatch.setattr(_rig_x402_sdk, "PAID_BODY", _completion("tiny-cheap-8b", "Twelve."))
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _add(daemon, resource, label="Swapper", max_price="0.10")
        turn = daemon.chat("ask my paid model: what is seven plus five?", session_id=CHAT_2, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        proposal_id = next(word.strip(".,:()") for word in text.split() if word.startswith("pay-"))
        answer = _approve_in_chat(daemon, proposal_id, session=CHAT_2, shows="Heads up: Swapper says tiny-cheap-8b answered")
        assert answer["paid_ai"]["verdict"] == "mismatch" and answer["paid_ai"]["model_reported"] == "tiny-cheap-8b", answer["paid_ai"]
        assert len(resource.paid_requests) == 1

        posted = _transcript(daemon, CHAT_2)
        assert "not open-reasoner-70b, the model you paid for" in posted[-1]["content"] and "tiny-cheap-8b" in posted[-1]["content"]
        assert "Twelve." in posted[-1]["content"], "a paid answer is still shown, marked"
        listed = {e["label"]: e for e in _door(daemon, "/api/wallet/ai-endpoints", {"action": "list"})[1]["ai_endpoints"]}
        assert listed["Swapper"]["on"] is False and "tiny-cheap-8b" in listed["Swapper"]["paused_reason"]

        # paused: the next question is refused before anything leaves, so nothing more can be paid there
        before = len(resource.requests)
        turn = daemon.chat("ask my paid model: and eight plus eight?", session_id=CHAT_2, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        assert "is paused" in text and len(resource.requests) == before, text


def test_a_price_over_the_owner_limit_is_refused_before_anything_is_parked(served, monkeypatch):
    daemon, chain, provider, wallet = served
    _clear(daemon)
    pending_before = len(_get(daemon, "/api/wallet/status")["status"]["pending"])
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _add(daemon, resource, label="Pricey", max_price="0.01")
        turn = daemon.chat("ask my paid model: what is seven plus five?", session_id=CHAT_3, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        assert "more than your limit of 0.01 USDC" in text and "Nothing was paid" in text, text
        assert resource.paid_requests == []
    assert len(_get(daemon, "/api/wallet/status")["status"]["pending"]) == pending_before


def test_a_paid_request_the_service_never_answers_is_said_plainly_in_the_chat(served, monkeypatch):
    daemon, chain, provider, wallet = served
    _clear(daemon)
    chat = f"openclaw:{0xA4:020x}"
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE, mode="drop") as resource:
        _add(daemon, resource, label="Dropper", max_price="0.10")
        turn = daemon.chat("ask my paid model: what is seven plus five?", session_id=chat, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        proposal_id = next(word.strip(".,:()") for word in text.split() if word.startswith("pay-"))
        answer = _approve_in_chat(daemon, proposal_id, session=chat, shows="No answer arrived")
        assert answer["paid_ai"]["verdict"] == "unanswered", answer
        assert answer["x402_sdk"]["delivered"] is False and len(resource.paid_requests) == 1
    posted = _transcript(daemon, chat)
    assert "No answer arrived" in posted[-1]["content"] and "Check your wallet to see whether this payment went through" in posted[-1]["content"]


def test_a_service_paused_after_the_question_is_not_paid_at_approval(served, monkeypatch):
    daemon, chain, provider, wallet = served
    _clear(daemon)
    chat = f"openclaw:{0xA5:020x}"
    monkeypatch.setattr(_rig_x402_sdk, "PAID_BODY", _completion(ASKED_MODEL, "Twelve."))
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        service = _add(daemon, resource, label="Later", max_price="0.10")
        turn = daemon.chat("ask my paid model: what is seven plus five?", session_id=chat, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        proposal_id = next(word.strip(".,:()") for word in text.split() if word.startswith("pay-"))
        status, paused = _door(daemon, "/api/wallet/ai-endpoints", {"action": "set_on", "id": service["id"], "on": False})
        assert status == 200
        status, quote = _door(daemon, "/api/wallet/quote", {"proposal_id": proposal_id})
        assert status == 200, quote
        paid_before = chain.token_balance(PAYEE)
        status, refused = _door(daemon, "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote"]["quote_id"],
                                                                 "quote_digest": quote["quote"]["digest"], "pin": PIN})
        assert status == 409 and refused["error"] == "endpoint_paused", refused
        assert resource.paid_requests == [] and chain.token_balance(PAYEE) == paid_before, "nothing reached the service"


def test_a_private_chat_gets_its_paid_answer_on_the_card_and_nothing_in_the_log(served, monkeypatch):
    daemon, chain, provider, wallet = served
    _clear(daemon)
    chat = f"openclaw:{0xA6:020x}"
    monkeypatch.setattr(_rig_x402_sdk, "PAID_BODY", _completion(ASKED_MODEL, "A private twelve."))
    daemon.chat("private chat", session_id=chat, model=MODEL, mode="auto")
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _add(daemon, resource, label="Quiet", max_price="0.10")
        turn = daemon.chat("ask my paid model: what is seven plus five?", session_id=chat, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        proposal_id = next(word.strip(".,:()") for word in text.split() if word.startswith("pay-"))
        answer = _approve_in_chat(daemon, proposal_id, session=chat, shows="A private twelve.")
        assert answer["paid_ai"]["verdict"] == "match" and answer["paid_ai"]["posted"] is False, answer["paid_ai"]
        assert len(resource.paid_requests) == 1
    assert _transcript(daemon, chat) == [], "a private chat's paid answer is never written to the chat log"
