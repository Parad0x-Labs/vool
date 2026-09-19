"""Crypto Pilot, stage 5: the served transfer path on Solana Devnet, end to end.

SCRIPTED MODEL, SIMULATED CHAIN, real product: the daemon is ``apps.vool_api_server`` in its own process and home; the
model is a prompt-routed stand-in that emits the product's own ``wallet.propose`` / ``wallet.payment_status`` tool calls;
the chain is the loopback Solana-dialect node answering as Devnet and returning each transaction's own signature; the
page is the served chat in the confined chromium. Every door and every value is the product's.

Proved here: a chat request becomes a pending transfer; the approval sheet shows the exact quote; a wrong PIN and a
cancel send nothing; the right PIN sends exactly once and the card shows the confirmed receipt with the Devnet Solscan
link; the model reports the status from the transfer record; a genuinely different request settles independently; a
transfer whose send answer was lost survives a daemon restart as an unresolved liability and is settled by the
restarted daemon's observer from the chain alone.
"""
from __future__ import annotations

import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import served_browser
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
WRONG_PIN = "593027"


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


def _status(daemon) -> dict:
    return _get(daemon, "/api/wallet/status")["status"]


def _transfer(daemon, proposal_id: str) -> dict:
    return _get(daemon, f"/api/wallet/transfers?proposal_id={proposal_id}")["transfer"]


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _wait_until(predicate, *, timeout: float, step: float = 0.5, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(step)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("transfer") / "home"
    chain = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    chain.real_signature = True
    provider = PromptRoutedProvider()
    chain.__enter__()
    provider.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WALLET_ENABLED": "1",
        "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
        "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
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


def _ready_pilot_wallet(daemon, label: str) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {
        "network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
        "creation_key": f"transfer-{uuid.uuid4().hex}", "label": label,
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


def _chat_proposal(daemon, message: str, *, session: str) -> str:
    turn = daemon.chat(message, session_id=session, model=MODEL, mode="auto")
    text = str((turn.get("message") or {}).get("content") or "")
    assert "pending_approval" in text and "pay-" in text, text
    return next(word.strip(".,:") for word in text.split() if word.startswith("pay-"))


def _open_page(browser, daemon):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=20_000)
    return page, errors


def _open_sheet(page, daemon, proposal_id: str) -> dict:
    review = page.locator(f'.vw-card[data-proposal="{proposal_id}"] .vw-review')
    review.wait_for(timeout=30_000)
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
        review.click()
    quote = minted.value.json()["quote"]
    page.wait_for_function("(document.getElementById('vwSheetTitle') || {}).textContent && document.getElementById('vwSheetTitle').textContent.indexOf('Send ') === 0")
    page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
    return quote


def _approve_on_sheet(page, daemon, pin: str) -> dict:
    page.locator("#vwSheetCredential").fill(pin)
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
        page.locator("#vwSheetApprove").click()
    return approved.value.json()


def test_chat_request_sheet_refusals_one_send_and_the_receipt_on_solana_devnet(served):
    daemon, chain, provider = served
    wallet = _ready_pilot_wallet(daemon, "Served transfer")
    session = "transfer-served-1"

    # the original request: the model proposes, nothing is signed
    first = _chat_proposal(daemon, f"Please pay 500000 lamports to {_sol_key()} for the September invoice", session=session)
    pending = {row["proposal_id"]: row for row in _status(daemon)["pending"]}
    assert pending[first]["pilot_transfer"] is True and pending[first]["origin"] == "model"
    assert chain.send_count() == 0

    manager, browser = served_browser.launch_chromium()
    try:
        page, errors = _open_page(browser, daemon)
        quote = _open_sheet(page, daemon, first)
        fields = quote["fields"]
        sheet = page.locator(f'[role="dialog"][data-proposal="{first}"]')
        assert page.locator("#vwSheetTitle").text_content() == f"Send {fields['amount_human']} {fields['display_symbol']}"
        assert sheet.locator('[data-field="network"] .vw-badge').text_content() == "DEVNET"
        assert sheet.locator('[data-field="from"] .vw-sheet-value').text_content().endswith(wallet["address"])
        assert sheet.locator('[data-field="to"] .vw-sheet-value').text_content() == fields["to_address"]
        assert page.locator(f'.vw-card[data-proposal="{first}"] input').count() == 0, "the credential lives on the sheet only"

        # a wrong PIN: refused by the server, counted, nothing claimed or sent, the preview stays usable
        refused = _approve_on_sheet(page, daemon, WRONG_PIN)
        assert refused.get("error") == "wallet_pin_invalid", refused
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled")
        assert chain.send_count() == 0 and _get(daemon, "/api/wallet/transfers")["transfers"] == []

        # cancel: the server reject, the card says what was true, nothing sent
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/reject") as rejected:
            page.locator("#vwSheetCancel").click()
        assert rejected.value.json().get("cancelled") is True, rejected.value.json()
        assert page.locator(f'.vw-card[data-proposal="{first}"] .vw-result').text_content() == "Cancelled. Nothing was signed or sent."
        assert first not in {row["proposal_id"] for row in _status(daemon)["pending"]}
        assert chain.send_count() == 0

        # the genuinely different request: another recipient, another amount, approved with the right PIN
        recipient = _sol_key()
        second = _chat_proposal(daemon, f"Please pay 731250 lamports to {recipient} for the domain renewal", session=session)
        quote = _open_sheet(page, daemon, second)
        assert (quote["fields"]["amount_human"], quote["fields"]["to_address"]) == ("0.00073125", recipient)
        answer = _approve_on_sheet(page, daemon, PIN)
        assert answer.get("ok") is True, answer
        transfer = answer["transfer"]
        assert (transfer["state"], transfer["state_label"], transfer["evidence_kind"]) == ("confirmed", "Confirmed", "submitted"), transfer
        assert chain.send_count() == 1 and chain.recorded_signatures() == {transfer["tx_id"]}
        assert transfer["explorer_url"] == f"https://solscan.io/tx/{transfer['tx_id']}?cluster=devnet"
        assert page.locator('.vw-sheet[role="dialog"]').count() == 0, "the sheet closes on success"
        card = page.locator(f'.vw-card[data-proposal="{second}"]')
        assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        link = card.locator("a.vw-explorer")
        assert link.text_content() == "View on Solscan" and link.get_attribute("href") == transfer["explorer_url"]
        assert card.locator(".vw-transfer-id").text_content() == f"Transaction {transfer['tx_id']}"
        assert page.locator(f'.vw-card[data-proposal="{second}"] input').count() == 0

        # the record: Activity, the status read model, and the model's own status answer
        listed = {t["proposal_id"]: t for t in _get(daemon, "/api/wallet/transfers")["transfers"]}
        assert listed[second]["state"] == "confirmed" and listed[second]["charged_fee_minor"] == str(chain.transaction_fee)
        assert _status(daemon)["in_flight"] == []
        turn = daemon.chat(f"What is the status of payment {second}?", session_id=session, model=MODEL, mode="auto")
        text = str((turn.get("message") or {}).get("content") or "")
        assert "Confirmed" in text and transfer["tx_id"] in text and "View on Solscan" in text, text
        assert chain.send_count() == 1, "a status question never sends"
        assert errors == [], errors
        prompts = " ".join(str(call.get("prompt") or "") for call in provider.calls)
        assert PIN not in prompts and WRONG_PIN not in prompts, "no prompt the model saw carried a credential"
    finally:
        browser.close()
        manager.stop()


def test_a_transfer_whose_send_answer_was_lost_survives_a_daemon_restart_and_is_settled_by_the_observer(served):
    """The node records the bytes and answers 500; the daemon dies before the network answers. The restarted daemon
    shows the transfer in flight, sends nothing, and settles it from the chain once the chain answers."""
    daemon, chain, _provider = served
    wallet = _ready_pilot_wallet(daemon, "Restart proof")
    session = "transfer-served-2"
    chain.send_mode = "accept_then_500"
    chain.status_mode = "none"
    try:
        proposal_id = _chat_proposal(daemon, f"Please pay 640000 lamports to {_sol_key()} for the restart proof", session=session)
        status, quoted = _door(daemon, "/api/wallet/quote", {"proposal_id": proposal_id})
        assert status == 200, quoted
        quote = quoted["quote"]
        sends_before = chain.send_count()
        status, answer = _door(daemon, "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": PIN})
        assert status == 200 and answer["transfer"]["state"] == "unknown", answer
        assert answer["transfer"]["state_label"] == "Status unknown" and answer["transfer"]["in_flight"] is True
        assert answer["transfer"]["from_address"] == wallet["address"]
        assert chain.send_count() == sends_before + 1 and answer["transfer"]["tx_id"] in chain.recorded_signatures()
        assert [t["proposal_id"] for t in _status(daemon)["in_flight"]] == [proposal_id]
        sends_after_send = chain.send_count()

        daemon.stop()
        daemon.start(timeout=240)

        # the restarted daemon: the liability is still there, still unknown, still counted; nothing is resent
        after_restart = _transfer(daemon, proposal_id)
        assert (after_restart["state"], after_restart["in_flight"]) == ("unknown", True), after_restart
        assert [t["proposal_id"] for t in _status(daemon)["in_flight"]] == [proposal_id]
        assert chain.send_count() == sends_after_send, "a restart never resends"

        manager, browser = served_browser.launch_chromium()
        try:
            page, errors = _open_page(browser, daemon)
            card = page.locator(f'.vw-card[data-proposal="{proposal_id}"][data-transfer="1"]')
            card.wait_for(timeout=30_000)
            assert card.locator(".vw-transfer-label").text_content() == "Transfer Status unknown on Solana Devnet"
            assert card.locator("a.vw-explorer").get_attribute("href") == after_restart["explorer_url"]

            # the network answers now: the observer settles from the chain alone, and the card follows the record
            chain.status_mode = "confirmed"
            status, woken = _door(daemon, "/api/wallet/transfers/refresh", {})
            assert status == 200 and woken["observer"]["alive"] is True, woken
            settled = _wait_until(lambda: (_transfer(daemon, proposal_id)["state"] == "confirmed") and _transfer(daemon, proposal_id), timeout=90, what="the observer settling the transfer")
            assert (settled["state_label"], settled["charged_fee_minor"], settled["explorer_link_text"]) == ("Confirmed", str(chain.transaction_fee), "View on Solscan")
            assert chain.send_count() == sends_after_send, "observation never transmits, before or after a restart"
            assert _status(daemon)["in_flight"] == []
            page.wait_for_function(
                "(id) => { var r = document.querySelector('.vw-card[data-proposal=\"' + id + '\"] .vw-result'); return !!r && r.getAttribute('data-transfer-state') === 'confirmed'; }",
                arg=proposal_id, timeout=30_000,
            )
            assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
            assert errors == [], errors
        finally:
            browser.close()
            manager.stop()
        turn = daemon.chat(f"status of payment {proposal_id}", session_id=session, model=MODEL, mode="auto")
        assert "Confirmed" in str((turn.get("message") or {}).get("content") or "")
    finally:
        chain.send_mode = "ok"
        chain.status_mode = "confirmed"
