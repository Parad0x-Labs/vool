"""Accountless x402 in the chat page, driven as a person drives it: BROWSER-level evidence against the served page.

One served daemon with the production authorities, the SIMULATION Solana node and the strict local UsePod stand-in, as in
``test_usepod_x402_composed_served.py``. The setup that is not under test goes through the same doors the Settings panel
calls (environment, Crypto Pilot wallet, lane and origin, prices, route approval, consent). The journey itself is the chat
page: pick the UsePod model, send, see the Crypto Pilot card the paused turn raised, open the payment preview and close it
with Decide later (closing a preview is not a decision: the request stays pending and the turn stays paused), review it
again and approve it with the PIN, then read the answer and the payment receipt in Activity. No claim is made about the
packaged desktop host.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_funded_journey_served import _evidence_dir, _open_receipt_in_activity, _page, _pick_in_chat, _usepod_panel
from tests.usepod.test_usepod_served_flow import MARKET, MARKET_ID, MODEL, _keep
from tests.usepod.test_usepod_settings_ui import CENTRAL
from tests.usepod.test_usepod_x402_composed_served import PIN, WalletServedDaemon, _allow_pending_consent, ready_pilot_wallet
from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode

X402_PATH = "/proxy/x402/v1/chat/completions"
_RELEASED = "(prev) => !!(view.run && view.run.turnId && view.run.turnId !== prev && view.run.released)"


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    from solders.keypair import Keypair

    from tests.served_browser import launch_chromium

    root = tmp_path_factory.mktemp("usepod-x402-chat-journey")
    node = SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS).start()
    service = daemon = manager = browser = None
    try:
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        pay_to = str(Keypair().pubkey())
        node.fund_sol(pay_to, 5_000_000)
        node.open_token_account(pay_to, USDC_MAINNET_MINT)
        service = StrictUsePodService(
            models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
            chain=node, pay_to=pay_to, usdc_mint=USDC_MAINNET_MINT,
        ).start()
        daemon = WalletServedDaemon(root / "home", node.url)
        daemon.start()
        manager, browser = launch_chromium()
        yield SimpleNamespace(service=service, node=node, daemon=daemon, browser=browser, pay_to=pay_to, state={})
    finally:
        if browser is not None:
            browser.close()
        if manager is not None:
            manager.stop()
        if daemon is not None:
            daemon.stop()
        if service is not None:
            service.stop()
        node.stop()


def test_setup_through_the_settings_doors(journey) -> None:
    daemon, service, node = journey.daemon, journey.service, journey.node
    status, switched = daemon.door("POST", "/api/wallet/environment", {"environment": "mainnet"})
    assert status == 200, switched
    wallet = ready_pilot_wallet(daemon)
    node.fund_sol(wallet["address"], 20_000_000)
    node.fund_token(wallet["address"], USDC_MAINNET_MINT, 1_000_000)
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "x402", "origin": service.origin})
    assert status == 200 and lane["origin"] == service.origin, lane
    for path, body in (("/api/cloud/usepod/refresh", {}), ("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"}), ("/api/cloud/usepod/approve-route", {"model_id": MODEL})):
        status, answer = daemon.call("POST", path, body)
        assert status == 200, (path, answer)
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
    assert status == 200 and proposed["facts"]["account"] == wallet["address"], proposed
    _allow_pending_consent(daemon)
    journey.state["wallet"] = wallet


def test_a_paid_turn_waits_on_the_crypto_pilot_card_and_answers_with_a_readable_payment_receipt(journey) -> None:
    daemon, service, node, pay_to = journey.daemon, journey.service, journey.node, journey.pay_to
    assert journey.state.get("wallet"), "requires the setup test"
    page = _page(journey, "/chat")
    try:
        page.wait_for_selector("#input", timeout=30000)
        _pick_in_chat(page, MODEL)
        sends, quotes_before = node.distinct_sends(), len(service.requests_to(X402_PATH))
        previous = page.evaluate("() => (view.run && view.run.turnId) || ''")
        page.fill("#input", "Name one reason harbours build breakwaters.")
        page.click("#send")
        confirmations: list[str] = []
        card = None
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            gate = page.locator(".vg-btn.vg-danger")
            if gate.count() and gate.first.is_visible():
                confirmations.append(gate.first.evaluate("b => (b.parentElement && b.parentElement.parentElement ? b.parentElement.parentElement : b).innerText")[:600])
                gate.first.click()
            review = page.locator(".vw-card[data-pilot='1'] .vw-review")
            if review.count() and review.first.is_visible():
                card = page.locator(".vw-card[data-pilot='1']").first
                break
            page.wait_for_timeout(250)
        if card is None:
            _keep("x402_chat_journey_no_card.json", {"confirmations": confirmations, "released": page.evaluate(_RELEASED, previous)})
            if _evidence_dir():
                page.screenshot(path=str(Path(_evidence_dir()) / "x402_chat_journey_no_card.png"), full_page=True)
            raise AssertionError("the paused turn raised no Crypto Pilot card in the chat")
        card_text = card.inner_text()
        # the turn is waiting on the owner: nothing signed, nothing sent, the turn not released
        assert node.distinct_sends() == sends and not page.evaluate(_RELEASED, previous)
        proposal_id = card.get_attribute("data-proposal")
        card.locator(".vw-review").click()
        page.wait_for_selector("#vwSheetApprove:not([disabled])", timeout=30000)
        first_preview = page.inner_text("#vwSheet")
        # closing the preview decides nothing: no approval or rejection call, the request stays pending, the turn stays paused
        page.click("#vwSheetLater")
        page.wait_for_selector("#vwSheet", state="detached", timeout=10000)
        deferred = card.locator(".vw-result").inner_text()
        status, wallet_view = daemon.door("GET", "/api/wallet/status")
        still_pending = [row for row in ((wallet_view or {}).get("status") or {}).get("pending") or [] if row.get("proposal_id") == proposal_id]
        assert status == 200 and still_pending, wallet_view
        assert node.distinct_sends() == sends and not page.evaluate(_RELEASED, previous)
        card.locator(".vw-review").click()
        page.wait_for_selector("#vwSheetApprove:not([disabled])", timeout=30000)
        sheet = page.inner_text("#vwSheet")
        if _evidence_dir():
            page.screenshot(path=str(Path(_evidence_dir()) / "x402_chat_journey_sheet.png"), full_page=True)
        page.fill("#vwSheetCredential", PIN)
        page.click("#vwSheetApprove")
        page.wait_for_selector(f".vw-card[data-proposal='{proposal_id}'] .vw-result[data-transfer-state='confirmed']", timeout=60000)
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline and not page.evaluate(_RELEASED, previous):
            page.wait_for_timeout(250)
        answer = page.locator(".msg.assistant .msg-text").last.inner_text()
        activity = _open_receipt_in_activity(page)
        paid_to_rows = page.locator(".xp-kv-row").filter(has_text=pay_to).all_inner_texts()
        if _evidence_dir():
            page.screenshot(path=str(Path(_evidence_dir()) / "x402_chat_journey_receipt.png"), full_page=True)
        _keep("x402_chat_journey.json", {"card": card_text, "first_preview": first_preview, "deferred": deferred, "sheet": sheet, "confirmations": confirmations, "answer": answer, "activity_tail": activity[-4000:]})
        assert page.evaluate(_RELEASED, previous), "the turn was released after the payment"
        assert "proposed by usepod" in card_text, card_text
        assert "Decide later" in first_preview and deferred == "Review this request again whenever you are ready.", (first_preview, deferred)
        assert "USDC" in sheet and pay_to in sheet and "Recipient token account" in sheet and "Network fee" in sheet, sheet
        assert "synthetic reply" in answer, answer
        receipt_lines = [line for line in activity.splitlines() if "UsePod receipt" in line]
        assert receipt_lines, activity[-2000:]
        receipt_line = receipt_lines[-1]
        assert "from your wallet" in receipt_line and "network fee 0.000005000 SOL" in receipt_line and "chain-confirmed" in receipt_line, receipt_line
        # three separate facts on the receipt, and the full destination in its details
        assert "transaction confirmed on chain" in receipt_line and "inference completed" in receipt_line and "provider credit" in receipt_line, receipt_line
        assert any("paid to" in row for row in paid_to_rows), paid_to_rows
        assert node.distinct_sends() == sends + 1 and len(service.requests_to(X402_PATH)) - quotes_before == 2
        journey.state["receipt_line"] = receipt_line
    finally:
        page.close()


def _the_card_the_paused_turn_raised(page, *, timeout: float = 120.0):
    """Answer the page's paid confirmation(s) and return the pending Crypto Pilot card the paused turn raised."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gate = page.locator(".vg-btn.vg-danger")
        if gate.count() and gate.first.is_visible():
            gate.first.click()
        review = page.locator(".vw-card[data-pilot='1'] .vw-review")
        if review.count() and review.last.is_visible():
            return page.locator(".vw-card[data-pilot='1']").filter(has=page.locator(".vw-review")).last
        page.wait_for_timeout(250)
    raise AssertionError("the paused turn raised no Crypto Pilot card in the chat")


def test_a_paid_turn_whose_answer_is_lost_says_paid_result_unknown_and_resumes_once_from_settings(journey) -> None:
    daemon, service, node, pay_to = journey.daemon, journey.service, journey.node, journey.pay_to
    assert journey.state.get("receipt_line"), "requires the paid journey"
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 50_000, "max_total_atomic": 50_000, "asset": "USDC"})
    assert status == 200, proposed
    _allow_pending_consent(daemon)
    page = _page(journey, "/chat")
    # SIMULATION: the paid request reaches nobody who processes it and the connection closes with no response
    service.faults["drop_paid_retry"] = True
    try:
        page.wait_for_selector("#input", timeout=30000)
        _pick_in_chat(page, MODEL)
        sends, quotes_before = node.distinct_sends(), len(service.requests_to(X402_PATH))
        previous = page.evaluate("() => (view.run && view.run.turnId) || ''")
        page.fill("#input", "Name one reason tugboats guide ships into a berth.")
        page.click("#send")
        card = _the_card_the_paused_turn_raised(page)
        proposal_id = card.get_attribute("data-proposal")
        card.locator(".vw-review").click()
        page.wait_for_selector("#vwSheetApprove:not([disabled])", timeout=30000)
        page.fill("#vwSheetCredential", PIN)
        page.click("#vwSheetApprove")
        page.wait_for_selector(f".vw-card[data-proposal='{proposal_id}'] .vw-result[data-transfer-state='confirmed']", timeout=60000)
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline and not page.evaluate(_RELEASED, previous):
            page.wait_for_timeout(250)
        released = page.evaluate(_RELEASED, previous)
        answer = page.locator(".msg.assistant .msg-text").last.inner_text()
        activity = _open_receipt_in_activity(page)
        paid_to_rows = page.locator(".xp-kv-row").filter(has_text=pay_to).all_inner_texts()
        if _evidence_dir():
            page.screenshot(path=str(Path(_evidence_dir()) / "x402_chat_lost_answer.png"), full_page=True)
    finally:
        service.faults.pop("drop_paid_retry", None)
        page.close()
    _keep("x402_chat_lost_answer.json", {"released": released, "answer": answer, "paid_to_rows": paid_to_rows, "activity_tail": activity[-4000:]})
    assert released, "the turn was released after the lost answer"
    assert "was paid from your wallet" in answer and "result is unknown" in answer and "no refund is assumed" in answer, answer
    assert "synthetic reply" not in answer, answer
    receipt_lines = [line for line in activity.splitlines() if "UsePod receipt" in line]
    assert receipt_lines and "PAID, RESULT UNKNOWN" in receipt_lines[-1] and "inference result unknown" in receipt_lines[-1], activity[-2000:]
    assert any("paid to" in row for row in paid_to_rows), paid_to_rows
    # one payment, one quote and one paid request that nobody answered
    assert node.distinct_sends() == sends + 1 and len(service.requests_to(X402_PATH)) - quotes_before == 2
    status, listed = daemon.call("GET", "/api/cloud/usepod/x402/unresolved")
    waiting = [item for item in (listed or {}).get("operations") or [] if item.get("resumable")]
    assert status == 200 and len(waiting) == 1 and waiting[0]["liability_state"] == "unknown", listed
    operation_id = waiting[0]["operation_id"]
    panel = _usepod_panel(journey)
    try:
        row = panel.locator(f".usepod-x402-operation[data-operation='{operation_id}']")
        row.wait_for(state="visible", timeout=30000)
        row.locator("button.usepod-x402-resume").click()
        said = panel.wait_for_function(
            "() => { const m = document.body.innerText.match(/(Resumed: the paid call answered|Not resumed)[^\\n]*/); return m ? m[0] : false; }",
            timeout=180000,
        ).json_value()
        if _evidence_dir():
            panel.screenshot(path=str(Path(_evidence_dir()) / "x402_settings_resumed.png"), full_page=True)
    finally:
        panel.close()
    _keep("x402_chat_lost_answer_resumed.json", {"said": said})
    assert str(said).startswith("Resumed: the paid call answered") and "synthetic reply" in str(said), said
    # the resume resent the recorded request once and paid nothing again
    assert node.distinct_sends() == sends + 1 and len(service.requests_to(X402_PATH)) - quotes_before == 3
    status, listed = daemon.call("GET", "/api/cloud/usepod/x402/unresolved")
    assert status == 200 and all(item["operation_id"] != operation_id for item in listed.get("operations") or []), listed
