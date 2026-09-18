"""Crypto Pilot, stage 4: the approval sheet on the served chat page, against a real daemon and a scripted chain.

The daemon is the real application in its own process and home; the chain is a loopback Solana-dialect RPC answering
as Devnet (simulated chain evidence); the browser is the served lane's confined chromium, allowed to reach the daemon
only. The wallet and the proposals are made through the product's own doors. Every value on the sheet is the typed
quote's value; an expired preview disables Approve; a preview replaced elsewhere, or a request that is no longer
pending, invalidates the open sheet; a valid current preview stays usable; one sheet is open at a time; Escape closes
without deciding; Cancel is the server reject. A refused credential sends nothing; the correct credential on a valid
preview sends exactly once and the card shows the confirmed transfer with its explorer link (stage 5).

Judging the sheet against a status snapshot needs proof that the PAGE APPLIED that snapshot, not only that a response
arrived. The page applies a snapshot in one synchronous step (cards for new pending rows, then the open sheet), so a
marker proposal created after the preview is an application barrier: once its card is attached, a snapshot generated
after the marker -- and so after the preview -- has been applied to the sheet. A stale snapshot is a real server
answer generated before the preview was minted, held in transit and delivered after the mint.
"""
from __future__ import annotations

import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.wallet._rig import ScriptedRpc

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
WRONG_PIN = "593027"


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _status(daemon) -> dict:
    with urlopen(daemon.base_url + "/api/wallet/status", timeout=30) as response:
        return json.load(response)["status"]


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("sheet") / "home"
    with ScriptedRpc() as chain, rig.CapturingProvider(default="Acknowledged.") as provider:
        chain.real_signature = True  # the node returns the signature the signed bytes carry, as a node does
        chain.status_keyed = True  # statuses only for signatures the node received
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={
            "VOOL_INSTALL_PROFILE": "hybrid-fallback",
            "VOOL_WALLET_ENABLED": "1",
            "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
            "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        })
        try:
            daemon.start(timeout=180)
            # the chat page is fully usable only with a certified model; the scripted stub is certified like the startup proof's
            certification = daemon.certify(timeout=120)
            assert certification.get("state") == "verified", certification
            yield daemon, chain
        finally:
            daemon.stop()
        assert daemon.process is None or daemon.process.poll() is not None


def _ready_pilot_wallet(daemon, label: str) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {
        "network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
        "creation_key": f"sheet-{uuid.uuid4().hex}", "label": label,
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


def _pending_transfer(daemon, wallet: dict, amount_minor: int, destination: str = "") -> str:
    status, proposed = _door(daemon, "/api/wallet/propose", {
        "wallet_id": wallet["wallet_id"], "destination": destination or _sol_key(), "amount_minor": amount_minor, "asset": "SOL",
        "network": SOLANA_DEVNET, "origin": "user",
    })
    assert status == 200 and proposed["proposal"]["state"] == "pending_approval", proposed
    return proposed["proposal"]["proposal_id"]


def _applied_fresh_snapshot(page, daemon, wallet: dict, amount_minor: int) -> str:
    """Create a marker proposal and wait until its card is attached: the snapshot that carried it -- generated after
    the marker, so after any preview minted before this call -- has been applied to the open sheet."""
    marker = _pending_transfer(daemon, wallet, amount_minor)
    page.locator(f'.vw-card[data-proposal="{marker}"]').wait_for(state="attached", timeout=20_000)
    return marker


class _StatusHold:
    """While armed, fetch each status request from the daemon immediately (the server generates that snapshot now) and
    hold the answer in transit; `release` delivers the held answers in order. Instrumentation failures are recorded
    and asserted, never read as success."""

    def __init__(self, page, daemon) -> None:
        self.page = page
        self.armed = False
        self.held: list = []
        self.failures: list[str] = []
        page.route(daemon.base_url + "/api/wallet/status", self._handle)

    def _handle(self, route) -> None:
        if not self.armed:
            route.continue_()
            return
        try:
            self.held.append((route, route.fetch()))
        except Exception as exc:  # recorded; the test asserts the list is empty
            self.failures.append(f"fetch_failed:{type(exc).__name__}:{exc}")
            route.abort()

    def arm_until_held(self, timeout: float = 15.0) -> None:
        self.armed = True
        deadline = time.monotonic() + timeout
        while not self.held:
            assert not self.failures, self.failures
            assert time.monotonic() < deadline, "no status request was issued while the hold was armed"
            self.page.wait_for_timeout(100)

    def release(self) -> list[float]:
        self.armed = False
        generated: list[float] = []
        for route, response in self.held:
            body = response.json()
            generated.append(float(body["status"]["generated_at_epoch"]))
            route.fulfill(response=response)
        self.held.clear()
        return generated


def _sheet_state(page) -> tuple[bool, str]:
    return page.locator("#vwSheetApprove").is_disabled(), page.locator("#vwSheetState").text_content() or ""


def _value(sheet, name: str) -> str:
    return sheet.locator(f'[data-field="{name}"] .vw-sheet-value').text_content() or ""


def _open_page(browser, daemon, *, install_clock: bool):
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    hold = _StatusHold(page, daemon)
    if install_clock:
        page.clock.install()
    page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=20_000)
    return page, errors, hold


def _mint_through_the_sheet_with_a_stale_snapshot_in_transit(page, daemon, hold: _StatusHold, proposal_id: str) -> dict:
    """Open the sheet while a status answer generated BEFORE the mint is in transit; deliver it after the mint."""
    review = page.locator(f'.vw-card[data-proposal="{proposal_id}"] .vw-review')
    review.wait_for(timeout=20_000)
    hold.arm_until_held()
    with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
        review.click()
    assert "no-store" in (minted.value.headers.get("cache-control") or "")
    quote = minted.value.json()["quote"]
    page.wait_for_function("(document.getElementById('vwSheetTitle') || {}).textContent && document.getElementById('vwSheetTitle').textContent.indexOf('Send ') === 0")
    generated = hold.release()
    assert any(stamp < quote["fields"]["quoted_at"] for stamp in generated), ("no pre-mint snapshot was delivered after the mint", generated, quote["fields"]["quoted_at"])
    return quote


def test_the_sheet_shows_exactly_the_quote_and_refuses_replaced_or_expired_previews(served):
    daemon, chain = served
    wallet = _ready_pilot_wallet(daemon, "Sheet proof")
    # inside the default spending caps (per transaction 1 000 000 lamports): a cap refusal is the limits authority's, not the sheet's
    first = _pending_transfer(daemon, wallet, 500_000)
    assert {row["proposal_id"]: row for row in _status(daemon)["pending"]}[first]["pilot_transfer"] is True

    manager, browser = served_browser.launch_chromium()
    try:
        page, errors, hold = _open_page(browser, daemon, install_clock=True)
        assert page.locator(f'.vw-card[data-proposal="{first}"] input').count() == 0, "no credential field outside the sheet"

        # a stale snapshot (generated before the mint) is ignored; a fresh one keeps the valid preview usable
        quote = _mint_through_the_sheet_with_a_stale_snapshot_in_transit(page, daemon, hold, first)
        fields = quote["fields"]
        second = _applied_fresh_snapshot(page, daemon, wallet, 400_000)
        disabled, state = _sheet_state(page)
        assert not disabled and "replaced" not in state and "no longer" not in state, state

        # every value on the sheet is the quote's value
        sheet = page.locator(f'[role="dialog"][data-proposal="{first}"]')
        assert page.locator("#vwSheetTitle").text_content() == f"Send {fields['amount_human']} {fields['display_symbol']}"
        assert (fields["network"], fields["environment"]) == (SOLANA_DEVNET, "testnet")
        assert _value(sheet, "network").startswith(fields["display_name"]) and sheet.locator('[data-field="network"] .vw-badge').text_content() == fields["badge"]
        assert _value(sheet, "value_note") == fields["value_note"]
        assert _value(sheet, "from").endswith(fields["from_address"]) and fields["from_address"] == wallet["address"]
        assert _value(sheet, "to") == fields["to_address"]
        assert _value(sheet, "balance") == f"{fields['balance_human']} {fields['gas_asset']} · observed at {fields['balance_ref']}"
        assert _value(sheet, "amount") == f"{fields['amount_human']} {fields['display_symbol']}"
        # the fee, the maximum and the balance after read as the quote's shortened forms (an estimate marked, a maximum
        # never understated, a minimum never overstated); the exact figures stay on the sheet, in Details
        assert _value(sheet, "fee") == f"{fields['fee_estimate_display']} · {fields['fee_max_display']}"
        assert _value(sheet, "max_total") == fields["max_total_display"]
        after = _value(sheet, "after")
        assert after.startswith(f"{fields['estimated_after_display']} (estimate, not guaranteed)") and f"{fields['minimum_after_display']} if the fee reaches its maximum" in after
        assert _value(sheet, "fee_exact") == f"{fields['fee_estimate_human']} {fields['gas_asset']}"
        assert _value(sheet, "fee_max_exact") == f"{fields['fee_max_human']} {fields['gas_asset']}"
        assert _value(sheet, "max_total_exact") == f"{fields['max_total_human']} {fields['gas_asset']}"
        assert _value(sheet, "after_exact") == f"{fields['estimated_after_human']} {fields['gas_asset']} estimated · {fields['minimum_after_human']} {fields['gas_asset']} minimum"
        assert page.evaluate("document.getElementById('vwSheetCredential').closest('[role=dialog]') !== null")

        # one sheet at a time: a second request queues behind the open one
        page.evaluate("(id) => document.querySelector('.vw-card[data-proposal=\"' + id + '\"] .vw-review').click()", second)
        # the chat page carries dialogs of its own: the invariant is about wallet approval sheets
        assert page.locator('.vw-sheet[role="dialog"]').count() == 1 and sheet.count() == 1
        assert page.locator(f'.vw-sheet[data-proposal="{second}"]').count() == 0

        # a wrong PIN on a valid preview: the server's refusal, the field cleared, nothing sent, the preview still usable
        page.locator("#vwSheetCredential").fill(WRONG_PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as refused:
            page.locator("#vwSheetApprove").click()
        assert refused.value.json().get("error") == "wallet_pin_invalid", refused.value.json()
        assert page.locator("#vwSheetCredential").input_value() == ""
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled")
        assert chain.send_count() == 0

        # a preview replaced elsewhere: the fresh snapshot that names another open quote invalidates the sheet
        status, fresh = _door(daemon, "/api/wallet/quote", {"proposal_id": first})
        assert status == 200 and fresh["quote"]["quote_id"] != quote["quote_id"], fresh
        _applied_fresh_snapshot(page, daemon, wallet, 300_000)
        disabled, state = _sheet_state(page)
        assert disabled and "replaced" in state, state

        # Refresh re-mints; a fresh snapshot keeps the renewed preview usable; then the page clock passes its expiry
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as refreshed:
            page.locator("#vwSheetRefresh").click()
        renewed = refreshed.value.json()["quote"]
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        assert _value(sheet, "to") == renewed["fields"]["to_address"]
        _applied_fresh_snapshot(page, daemon, wallet, 200_000)
        disabled, state = _sheet_state(page)
        assert not disabled and "replaced" not in state, state
        page.clock.fast_forward(int((renewed["fields"]["expires_at"] - time.time() + 5) * 1000))
        page.wait_for_function("document.getElementById('vwSheetApprove').disabled === true", timeout=10_000)
        assert "expired" in (page.locator("#vwSheetState").text_content() or "")

        # Escape closes without deciding
        page.keyboard.press("Escape")
        assert page.locator('.vw-sheet[role="dialog"]').count() == 0
        states = {row["proposal_id"]: row["state"] for row in _status(daemon)["pending"]}
        assert states.get(first) == "pending_approval"

        # Cancel is the server reject
        page.locator(f'.vw-card[data-proposal="{first}"] .vw-review').click()
        page.locator(f'[role="dialog"][data-proposal="{first}"]').wait_for()
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/reject") as rejected:
            page.locator("#vwSheetCancel").click()
        assert rejected.value.status == 200
        assert page.locator(f'.vw-card[data-proposal="{first}"] .vw-result').text_content() == "Cancelled. Nothing was signed or sent."
        assert first not in {row["proposal_id"] for row in _status(daemon)["pending"]}
        assert chain.send_count() == 0 and "sendTransaction" not in [call.get("method") for call in chain.calls]
        assert hold.failures == [] and errors == [], (hold.failures, errors)
        page.close()

        # the correct PIN on a valid preview, on a fresh page: exactly one send, the sheet closes, the card shows the
        # confirmed transfer with the row's explorer link, and the request leaves the pending list
        page, errors, hold = _open_page(browser, daemon, install_clock=False)
        review = page.locator(f'.vw-card[data-proposal="{second}"] .vw-review')
        review.wait_for(timeout=20_000)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/quote") as minted:
            review.click()
        sent_quote = minted.value.json()["quote"]
        page.locator(f'[role="dialog"][data-proposal="{second}"]').wait_for()
        page.locator("#vwSheetCredential").fill(PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as approved:
            page.locator("#vwSheetApprove").click()
        answer = approved.value.json()
        assert approved.value.status == 200, answer
        transfer = answer["transfer"]
        assert (transfer["state"], transfer["state_label"], transfer["amount_human"]) == ("confirmed", "Confirmed", sent_quote["fields"]["amount_human"]), transfer
        assert transfer["explorer_url"] == f"https://solscan.io/tx/{transfer['tx_id']}?cluster=devnet"
        assert page.locator('.vw-sheet[role="dialog"]').count() == 0, "the sheet closes on success"
        card = page.locator(f'.vw-card[data-proposal="{second}"]')
        assert card.locator(".vw-transfer-label").text_content() == "Transfer Confirmed on Solana Devnet"
        assert card.locator("a.vw-explorer").get_attribute("href") == transfer["explorer_url"]
        assert card.locator(".vw-transfer-id").text_content() == f"Transaction {transfer['tx_id']}"
        assert second not in {row["proposal_id"] for row in _status(daemon)["pending"]}
        assert chain.send_count() == 1 and [call.get("method") for call in chain.calls].count("sendTransaction") == 1
        assert hold.failures == [] and errors == [], (hold.failures, errors)
    finally:
        browser.close()
        manager.stop()


def test_a_new_recipient_preview_survives_a_refused_pin_and_closes_when_the_request_leaves(served):
    """Different data from the first proof: another wallet, 0.00075 SOL to an account that does not exist yet (the
    new-recipient cue), a refused credential that leaves the valid preview usable, and a fresh snapshot in which the
    request is no longer pending."""
    daemon, chain = served
    sends_before = chain.send_count()
    wallet = _ready_pilot_wallet(daemon, "Novel sheet proof")
    destination = _sol_key()
    chain.balances = {wallet["address"]: 2_000_000_000, destination: 0}
    manager, browser = served_browser.launch_chromium()
    try:
        novel = _pending_transfer(daemon, wallet, 750_000, destination=destination)
        page, errors, hold = _open_page(browser, daemon, install_clock=False)

        quote = _mint_through_the_sheet_with_a_stale_snapshot_in_transit(page, daemon, hold, novel)
        fields = quote["fields"]
        assert (fields["amount_human"], fields["to_address"]) == ("0.00075", destination)
        assert "new_recipient_account" in fields["cues"]
        sheet = page.locator(f'[role="dialog"][data-proposal="{novel}"]')
        assert page.locator("#vwSheetTitle").text_content() == f"Send {fields['amount_human']} {fields['display_symbol']}"
        assert sheet.locator('[data-cue="new_recipient_account"]').count() == 1
        _applied_fresh_snapshot(page, daemon, wallet, 800_000)
        disabled, state = _sheet_state(page)
        assert not disabled and "replaced" not in state and "no longer" not in state, state

        # a refused credential is the server's answer; the valid preview stays usable and the field is cleared
        page.locator("#vwSheetCredential").fill(WRONG_PIN)
        with page.expect_response(lambda r: r.url == daemon.base_url + "/api/wallet/approve") as refused:
            page.locator("#vwSheetApprove").click()
        assert refused.value.json().get("error") == "wallet_pin_invalid", refused.value.json()
        assert page.locator("#vwSheetCredential").input_value() == ""
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled")

        # the request leaves the pending list elsewhere: the fresh snapshot without its row invalidates the sheet
        status, rejected = _door(daemon, "/api/wallet/reject", {"proposal_id": novel})
        assert status == 200, rejected
        _applied_fresh_snapshot(page, daemon, wallet, 850_000)
        disabled, state = _sheet_state(page)
        assert disabled and "no longer" in state, state

        page.keyboard.press("Escape")
        assert page.locator('.vw-sheet[role="dialog"]').count() == 0
        assert chain.send_count() == sends_before
        assert hold.failures == [] and errors == [], (hold.failures, errors)
    finally:
        chain.balances = {}
        browser.close()
        manager.stop()
