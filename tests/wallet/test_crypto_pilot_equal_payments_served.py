"""Equal payments approved close together are separate transactions, end to end through the served product.

SCRIPTED MODEL, SIMULATED CHAIN, real product: the daemon is ``apps.vool_api_server`` in its own process and home; the
model is a prompt-routed stand-in that emits the product's own transfer tool call; the chain is the loopback node
answering as Solana Devnet, whose recent blockhash moves only when this test moves it; the page is the served chat in
the confined chromium. The wallet is made through the setup doors and every payment is approved on its sheet.

A transfer the wallet builds is fixed by its payer, recipient, amount and recent blockhash, so two equal payments
approved inside one blockhash are one transaction with one signature, and the chain runs it once. The wallet used to
record both as confirmed and count both. Proven here, on the sheet a person uses:

* the first of three equal payments is confirmed;
* the second, approved before the chain moves on, waits a few seconds for it and is then refused with the sheet's own
  "refresh the preview" words, with nothing held, sent or counted; once the chain moves on, Refresh and Approve confirm
  it as its own transaction;
* the third, approved while the chain moves on during that wait, is confirmed as its own transaction with no refusal.
"""
from __future__ import annotations

import json
import os
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

PIN = "482913"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
REFRESH_WORDS = "This transfer preview expired or was replaced, so it cannot be approved and nothing was sent. Refresh the preview, then try again."


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


def _shot(page, name: str) -> None:
    folder = os.environ.get("VOOL_EQUAL_PAYMENTS_SHOTS")
    if folder:
        page.screenshot(path=os.path.join(folder, name), full_page=False)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("equal-payments") / "home"
    chain = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    chain.real_signature = True  # the node answers with the transaction's own signature, as a real one does
    chain.status_keyed = True
    provider = PromptRoutedProvider()
    for node in (chain, provider):
        node.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_DEVNET: chain.url}),
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url, "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps", "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
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
        yield daemon, chain, provider
    finally:
        daemon.stop()
        for node in (provider, chain):
            node.__exit__(None, None, None)


def _ready_wallet(daemon) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"equal-{uuid.uuid4().hex}", "label": "Shared costs"})
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


def _open_sheet(page, daemon, proposal_id: str) -> dict:
    review = page.locator(f'.vw-card[data-proposal="{proposal_id}"] .vw-review')
    review.wait_for(timeout=30_000)
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
        review.click()
    quote = minted.value.json()["quote"]
    page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=15_000)
    return quote


def _approve_on_sheet(page, daemon) -> tuple[dict, float]:
    page.locator("#vwSheetCredential").fill(PIN)
    started = time.monotonic()
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve", timeout=60_000) as approved:
        page.locator("#vwSheetApprove").click()
    return approved.value.json(), time.monotonic() - started


def _confirmed_on_card(page, proposal_id: str, tx_id: str) -> None:
    card = page.locator(f'.vw-card[data-proposal="{proposal_id}"]')
    page.wait_for_function(
        f"document.querySelector('.vw-card[data-proposal=\"{proposal_id}\"] .vw-transfer-label')?.textContent === 'Transfer Confirmed on Solana Devnet'",
        timeout=30_000,
    )
    assert card.locator(".vw-transfer-id").text_content() == f"Transaction {tx_id}"


def test_three_equal_payments_from_chat_are_three_transactions_on_the_sheet(served):
    daemon, chain, provider = served
    wallet = _ready_wallet(daemon)
    to = _sol_key()
    ids = [_chat_proposal(daemon, f"Send 0.0004 SOL on solana to {to} for shared cost {n}", session=f"equal-payments-{n}") for n in (1, 2, 3)]
    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=20_000)

        # the first payment: confirmed, its own transaction
        _open_sheet(page, daemon, ids[0])
        reads = chain.blockhash_reads
        first, _ = _approve_on_sheet(page, daemon)
        alone = chain.blockhash_reads - reads  # how often an approval with nothing equal in flight reads the chain
        assert first.get("ok") is True and first["transfer"]["state"] == "confirmed", first
        page.wait_for_selector("#vwSheet", state="detached")
        assert chain.send_count() == 1

        # the second, equal payment before the chain moves on: it waits for the chain, then asks for a fresh preview
        _open_sheet(page, daemon, ids[1])
        refused, waited = _approve_on_sheet(page, daemon)
        assert refused.get("error") == "wallet_quote_expired", refused
        assert waited >= 3.5, f"refused after {waited:.2f}s: it did not wait for the chain to move on"
        page.wait_for_function("document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        assert page.locator("#vwSheetState").text_content() == REFRESH_WORDS
        _shot(page, "equal-payments-2-second-refused.png")
        listed = _get(daemon, "/api/wallet/transfers")["transfers"]
        assert [row["proposal_id"] for row in listed] == [ids[0]], "nothing held, signed or sent for the second payment"
        assert chain.send_count() == 1
        pending = {row["proposal_id"] for row in _get(daemon, "/api/wallet/status")["status"]["pending"]}
        assert {ids[1], ids[2]} <= pending, "the refused payment still waits for its approval"

        # the chain moves on: Refresh, then Approve, confirm it as its own transaction
        chain.advance_blockhash()
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote"):
            page.locator("#vwSheetRefresh").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        second, _ = _approve_on_sheet(page, daemon)
        assert second.get("ok") is True and second["transfer"]["state"] == "confirmed", second
        page.wait_for_selector("#vwSheet", state="detached")

        # the third, equal payment while the chain moves on during the wait: the approval's first read of the chain
        # still finds the second payment's blockhash, the chain moves on right after, and the payment is confirmed as
        # its own transaction with no refusal
        _open_sheet(page, daemon, ids[2])
        reads = chain.blockhash_reads
        chain.blockhash_moves_after_reads = 1
        third, _ = _approve_on_sheet(page, daemon)
        assert third.get("ok") is True and third["transfer"]["state"] == "confirmed", third
        assert chain.blockhash_reads - reads > alone, "it read the chain again after finding the equal payment"
        page.wait_for_selector("#vwSheet", state="detached")

        # three payments, three transactions on the chain, three receipts, each on its own card
        answers = dict(zip(ids, (first, second, third), strict=True))
        tx_ids = [answer["transfer"]["tx_id"] for answer in answers.values()]
        assert len(set(tx_ids)) == 3, tx_ids
        assert chain.send_count() == 3 and len({bytes(raw) for raw in chain.sent}) == 3
        listed = {row["proposal_id"]: row for row in _get(daemon, "/api/wallet/transfers")["transfers"]}
        assert sorted(listed) == sorted(ids)
        assert {pid: (row["state"], row["tx_id"], row["from_address"]) for pid, row in listed.items()} == {
            pid: ("confirmed", answer["transfer"]["tx_id"], wallet["address"]) for pid, answer in answers.items()
        }
        for pid, answer in answers.items():
            _confirmed_on_card(page, pid, answer["transfer"]["tx_id"])
        page.locator(f'.vw-card[data-proposal="{ids[0]}"]').scroll_into_view_if_needed()
        _shot(page, "equal-payments-3-all-confirmed.png")
        assert errors == [], errors
        assert PIN not in " ".join(str(call.get("prompt") or "") for call in provider.calls)
    finally:
        browser.close()
        manager.stop()
