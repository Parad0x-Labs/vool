"""Product follow-up, Phase C1, SERVED: a pending card follows the authoritative status without a reload.

SCRIPTED MODEL, SIMULATED CHAIN (Solana Devnet node), real daemon, the confined browser. A request approved through
the doors from another surface (here: the API, as another window or a restart would) turns the page's card into its
receipt line, with no second approval from the page; a request cancelled elsewhere says so; a request that ends while
its preview is open invalidates the sheet and settles the card; a late quote reply for a dismissed preview never
resurrects a settled card. Status polling only reads.
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

PIN = "482913"
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

    home = tmp_path_factory.mktemp("cardsync") / "home"
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
        status, created = _door(daemon, "/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"sync-{uuid.uuid4().hex}", "label": "card sync"})
        assert status == 200, created
        wallet_id = created["setup"]["wallet_id"]
        status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
        token = revealed["backup"]["ack_token"]
        revealed = None
        status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": token})
        assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
        yield daemon, sol, provider
    finally:
        daemon.stop()
        for node in (provider, sol):
            node.__exit__(None, None, None)


def _chat_proposal(daemon, message: str, *, session: str) -> str:
    turn = daemon.chat(message, session_id=session, model=MODEL, mode="auto")
    text = str((turn.get("message") or {}).get("content") or "")
    assert "pending_approval" in text and "pay-" in text, text
    return next(word.strip(".,:") for word in text.split() if word.startswith("pay-"))


def _approve_elsewhere(daemon, proposal_id: str) -> dict:
    status, quoted = _door(daemon, "/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, quoted
    quote = quoted["quote"]
    status, answer = _door(daemon, "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": PIN})
    assert status == 200 and answer["transfer"]["state"] == "confirmed", answer
    return answer["transfer"]


def test_cards_follow_approval_cancellation_and_expiry_from_other_surfaces(served, browser):
    daemon, node, provider = served
    approved = _chat_proposal(daemon, f"Send 0.0004 SOL on solana to {_sol_key()} for proof one", session="sync-1")
    cancelled = _chat_proposal(daemon, f"Send 0.0005 SOL on solana to {_sol_key()} for proof two", session="sync-2")
    open_then_ended = _chat_proposal(daemon, f"Send 0.0006 SOL on solana to {_sol_key()} for proof three", session="sync-3")
    page = browser.new_page()
    errors: list[str] = []
    posts: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: posts.append(request.url.rsplit("/", 1)[-1]) if request.method == "POST" and "/api/wallet/" in request.url else None)
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    try:
        for pid in (approved, cancelled, open_then_ended):
            page.locator(f'.vw-card[data-proposal="{pid}"] .vw-review').wait_for(timeout=30_000)
        # approved from another surface: the card becomes the receipt line, the page itself never approves
        sends = node.send_count()
        transfer = _approve_elsewhere(daemon, approved)
        assert node.send_count() == sends + 1
        card = page.locator(f'.vw-card[data-proposal="{approved}"]')
        # The label is eventually consistent (see the Base review test): wait for it to
        # REACH its final text, not merely to exist.
        page.wait_for_function(
            f"document.querySelector('.vw-card[data-proposal=\"{approved}\"] .vw-transfer-label')?.textContent === 'Transfer Confirmed on Solana Devnet'",
            timeout=15_000,
        )
        assert card.locator(".vw-review").count() == 0
        assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        assert card.locator(".vw-transfer-id").text_content() == f"Transaction {transfer['tx_id']}"
        assert card.locator("a.vw-explorer").count() == 1
        assert node.send_count() == sends + 1 and "approve" not in posts and "quote" not in posts
        # cancelled from another surface: the card says so, the Review action is gone
        status, rejected = _door(daemon, "/api/wallet/reject", {"proposal_id": cancelled})
        assert status == 200 and rejected.get("cancelled") is True, rejected
        card2 = page.locator(f'.vw-card[data-proposal="{cancelled}"]')
        page.wait_for_function(f"document.querySelector('.vw-card[data-proposal=\"{cancelled}\"] .vw-result').textContent.length > 0", timeout=15_000)
        assert card2.locator(".vw-review").count() == 0
        assert card2.locator(".vw-result").text_content() == "Cancelled elsewhere. Nothing was signed or sent."
        assert card2.locator(".vw-result").get_attribute("data-proposal-state") == "rejected"
        # a request that ends while its preview is open: the sheet invalidates, the card settles, nothing is sent twice
        card3 = page.locator(f'.vw-card[data-proposal="{open_then_ended}"]')
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            card3.locator(".vw-review").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        sends = node.send_count()
        _approve_elsewhere(daemon, open_then_ended)
        page.wait_for_function("document.getElementById('vwSheetApprove').disabled", timeout=15_000)
        assert "no longer waiting" in page.locator("#vwSheetState").text_content()
        page.wait_for_function(f"document.querySelector('.vw-card[data-proposal=\"{open_then_ended}\"] .vw-transfer-label') !== null", timeout=15_000)
        assert card3.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        assert node.send_count() == sends + 1 and "approve" not in posts and posts.count("quote") == 1
        # reopening the page: a settled request offers no stale Review action; its truth is the transfers read model
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=20_000)
        page.wait_for_timeout(3_500)  # one status poll
        for pid in (approved, cancelled, open_then_ended):
            assert page.locator(f'.vw-card[data-proposal="{pid}"] .vw-review').count() == 0, pid
        with urlopen(daemon.base_url + f"/api/wallet/transfers?proposal_id={approved}", timeout=30) as response:
            assert json.load(response)["transfer"]["state"] == "confirmed"
        with urlopen(daemon.base_url + f"/api/wallet/proposals/{cancelled}", timeout=30) as response:
            assert json.load(response)["proposal"]["state"] == "rejected"
        assert not errors, errors
        assert PIN not in " ".join(str(c.get("prompt") or "") for c in provider.calls)
    finally:
        page.close()


def test_a_late_quote_reply_for_a_dismissed_preview_never_resurrects_a_settled_card(served, browser):
    daemon, node, _provider = served
    pid = _chat_proposal(daemon, f"Send 0.0007 SOL on solana to {_sol_key()} for proof four", session="sync-4")
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    try:
        card = page.locator(f'.vw-card[data-proposal="{pid}"]')
        card.locator(".vw-review").wait_for(timeout=30_000)
        held: list = []
        page.route(daemon.base_url + "/api/wallet/quote", lambda route: held.append((route, route.fetch())))
        card.locator(".vw-review").click()
        page.wait_for_function("!!document.getElementById('vwSheetLater')")
        page.locator("#vwSheetLater").click()  # dismissed while its quote is still in transit
        page.wait_for_selector("#vwSheet", state="detached")
        sends = node.send_count()
        _approve_elsewhere(daemon, pid)  # settled elsewhere meanwhile
        page.wait_for_function(f"document.querySelector('.vw-card[data-proposal=\"{pid}\"] .vw-transfer-label') !== null", timeout=15_000)
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(100)
        route, response = held.pop()
        route.fulfill(response=response)  # the late reply lands on a closed preview
        page.wait_for_timeout(500)
        assert page.locator("#vwSheet").count() == 0
        assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet" and card.locator(".vw-review").count() == 0
        assert node.send_count() == sends + 1
        assert not errors, errors
    finally:
        page.unroute(daemon.base_url + "/api/wallet/quote")
        page.close()
