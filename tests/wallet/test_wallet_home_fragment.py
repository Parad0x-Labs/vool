"""The Wallet home (Home -> Wallet): SYNTHETIC API FACTS in a real browser.

The actual wallet fragment composed into the actual chat page; every wallet answer intercepted and
answered with typed facts (no daemon, no chain, no funds). Labelled as such. Proven: the Home entry
follows the backend's enabled state alone (off = no entry and no wallet traffic; on = exactly one
entry and no account created by opening the panel); the panel's account/network selection repaints
only the selection's own answers (a slower stale balance cannot land on a newer selection); a
confirmed zero is said as zero while a failed read is unavailable, never $0; Receive shows the exact
address and a locally drawn QR; Send is an ordinary-units proposal (exact decimal parsing, one
idempotent submit) whose approval is the EXISTING review sheet — Decide later leaves the request
pending and reopenable, an expired preview disables Approve, and a disable mid-flight closes the
panel and removes the entry; watch-only cannot send; a contact saved for another network is never
silently resolved; a landed payment and a rising balance each say so once, from the poll the page
already runs.
"""
from __future__ import annotations

import json
import time

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

pytestmark = [pytest.mark.safety]

SOL = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE = "eip155:84532"
POCKET_KEY = "0x1111111111111111111111111111111111111111"
WATCH_KEY = "WatchOnlySolanaAddress1111111111111111111"
RECIPIENT = "0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a"


def _account(wallet_id, network, mode, key, setup_state="ready", label=""):
    chain = "solana" if network == SOL else "base"
    return {
        "wallet_id": wallet_id, "mode": mode, "network": network, "chain": network, "family": "svm" if network == SOL else "evm",
        "testnet": True, "public_key": key, "label": label, "approval_method": "pin" if mode == "pocket_sealed" else "",
        "setup_state": setup_state, "seal_policy": "pilot" if mode == "pocket_sealed" else "",
    }


def _row(network, *, accounts, create_ok=True, balance_ok=True, quote_ok=True, send_ok=True):
    evm = network.startswith("eip155:")
    caps_reason = "" if create_ok else "storage_class_refused"
    return {
        "network": network, "chain_key": "solana" if not evm else "base", "chain_label": "Solana" if not evm else "Base",
        "display_name": "Solana Devnet" if not evm else "Base Sepolia", "environment": "testnet", "family": "evm" if evm else "svm", "active": True,
        "badge": "TESTNET", "environment_label": "Testnet", "value_note": "Test funds have no monetary value",
        "native_symbol": "SOL" if not evm else "ETH", "native_display_symbol": "SOL" if not evm else "ETH",
        "native_decimals": 9 if not evm else 18,
        "explorer_label": "Solscan" if not evm else "Basescan", "explorer_link_text": "View on Solscan",
        "accounts": accounts, "capabilities": {
            "list": {"available": True, "reason": ""}, "receipt": {"available": True, "reason": ""},
            "create": {"available": create_ok, "reason": caps_reason},
            "backup": {"available": False, "reason": "no_setup_awaiting_backup"},
            "balance": {"available": balance_ok, "reason": "" if balance_ok else "no_account_on_this_network"},
            "quote": {"available": quote_ok, "reason": "" if quote_ok else "no_ready_signing_account"},
            "sign": {"available": send_ok, "reason": "" if send_ok else "no_ready_signing_account"},
            "send": {"available": send_ok, "reason": "" if send_ok else "no_ready_signing_account"},
        },
    }


def _status(enabled=True, accounts=(), rows=None, pending=(), transfers=(), in_flight=()):
    return {
        "enabled": enabled, "custody_mode": "none", "network": SOL, "mainnet_enabled": False, "signing_preference": "",
        "public_key": "", "label": "", "pending_approvals": len(pending), "pending": pending, "last_receipt": None, "limits": {},
        "x402_cap_minor": 0, "generated_at_epoch": time.time(), "soft_warnings": [], "accounts": list(accounts),
        "declared_networks": [SOL, BASE], "transfers": transfers, "in_flight": in_flight,
        "crypto_pilot": {
            "active_environment": "testnet", "environment_source": "fresh_install", "environment_source_label": "the fresh-install default",
            "environment_label": "Test networks", "environment_choices": [], "environment_note": "", "caller_binding": "unbound", "frozen": False,
            "chain_order": ["solana", "robinhood", "base", "ethereum", "bnb"],
            "networks": rows if rows is not None else [_row(SOL, accounts=[a for a in accounts if a["network"] == SOL]), _row(BASE, accounts=[a for a in accounts if a["network"] == BASE])],
            "credential_policy": {"pin": {"min": 4, "max": 8, "text": "4–8 digits"}, "password": {"min": 10, "max": 128, "text": "10–128 characters"}, "device": {"available": False, "text": "not available on this machine"}},
            "asset_support": {"native_coin_transfers": True, "token_transfers": False, "note": "native coin only"},
            "pilot_notice": "A pilot feature: keep small test amounts.",
        },
    }


def _balance(wallet_id, *, minor="1500000000", human="1.5", asset="SOL", state="read", reason=""):
    return {"wallet_id": wallet_id, "network": SOL, "asset": asset, "state": state, "reason": reason,
            "balance_minor": minor if state == "read" else None, "balance_human": human if state == "read" else "",
            "balance_display": "", "ref": "slot:71", "observed_at": time.time(), "note": ""}


def _quote_fields(**over):
    base = {
        "proposal_id": "pay-panel", "network": BASE, "chain_key": "base", "chain_label": "Base", "display_name": "Base Sepolia", "environment": "testnet",
        "badge": "TESTNET", "environment_label": "Testnet", "value_note": "Test funds have no monetary value", "from_label": "Panel wallet",
        "from_address": POCKET_KEY, "to_address": RECIPIENT, "asset": "ETH", "display_symbol": "ETH", "gas_asset": "ETH", "decimals": 18,
        "amount_minor": 1_000_000_000_000_000, "amount_human": "0.001", "balance_minor": 10**18, "balance_ref": "block:100",
        "balance_human": "1", "fee_estimate_human": "0.000061", "fee_max_human": "0.000093", "max_total_human": "0.001093",
        "estimated_after_human": "0.9989", "minimum_after_human": "0.9989", "token_transfer": False, "cues": [], "quoted_at": time.time(),
        "expires_at": time.time() + 600, "approval_method": "pin",
        "purpose": {"kind": "direct", "headline": f"Send 0.001 ETH to {RECIPIENT}", "mechanism_label": "Direct transfer", "beneficiary": RECIPIENT},
        "review": {"headline": "Send 0.001 ETH", "amount_line": "0.001 ETH", "recipient_line": "To " + RECIPIENT, "source_line": "from Panel wallet",
                   "network_line": "network fee at most 0.000093 ETH", "max_debits": [{"human": "0.001093", "asset": "ETH", "what": "payment + fees"}],
                   "remaining": [], "warnings": [], "details": []},
    }
    base.update(over)
    return base


class Facts:
    """Mutable typed facts the intercepted routes answer with."""

    def __init__(self, status):
        self.status = status
        self.balances: dict[str, dict] = {}
        self.balance_delay: dict[str, float] = {}
        self.transfers_answer: dict = {"transfers": []}
        self.contacts_answer: dict = {"contacts": []}
        self.quote_fields: dict | None = None
        self.requests: list[str] = []
        self.proposals: list[dict] = []


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


def _page(browser, facts: Facts):
    html = render_vool_chat_html(build_commit="wallet-home-proof")
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def reply(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def route(route):
        url = route.request.url
        path = url.split("://", 1)[1].split("/", 1)[1] if "://" in url else url
        path = "/" + path.split("?")[0]
        facts.requests.append(route.request.method + " " + path)
        body = route.request.post_data_json if route.request.method == "POST" else None
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=html)
        elif path == "/api/wallet/status":
            reply(route, {"status": facts.status})
        elif path == "/api/wallet/balance":
            wallet_id = str(url.split("wallet_id=", 1)[1].split("&", 1)[0] if "wallet_id=" in url else "")
            delay = facts.balance_delay.get(wallet_id, 0)
            if delay:
                time.sleep(delay)
            answer = facts.balances.get(wallet_id) or {"state": "unavailable", "reason": "not_read"}
            reply(route, {"balance": answer})
        elif path == "/api/wallet/transfers":
            reply(route, facts.transfers_answer)
        elif path == "/api/contacts":
            reply(route, facts.contacts_answer)
        elif path == "/api/wallet/quote":
            # the real quote owner mints fresh fields (quoted_at = now) and records the open quote on
            # the pending row the status poll serves; an expires_at override stays as overridden
            fields = dict(facts.quote_fields or _quote_fields())
            fields["quoted_at"] = time.time()
            for row in facts.status.get("pending") or ():
                if row.get("proposal_id") == body.get("proposal_id"):
                    row["open_quote_id"] = "quote-panel"
            reply(route, {"ok": True, "quote": {"quote_id": "quote-panel", "digest": "d-panel", "state": "open", "fields": fields}})
        elif path == "/api/wallet/propose":
            facts.proposals.append(dict(body))
            # the real door leaves the proposal pending in the status the page polls; so do the facts
            facts.status["pending"] = [*list(facts.status.get("pending") or []), {"proposal_id": "pay-panel", "amount_minor": body.get("amount_minor"), "asset": body.get("asset"), "destination": body.get("destination"), "origin": body.get("origin"), "memo": body.get("memo", ""), "created_at": time.time(), "network": body.get("network"), "state": "pending_approval", "meaning": None, "pilot_transfer": True, "purpose": None, "open_quote_id": "", "quote_expires_at": 0, "recipient": {}}]
            reply(route, {"ok": True, "proposal": {"proposal_id": "pay-panel", "wallet_id": body.get("wallet_id"), "state": "pending_approval", **{k: body.get(k) for k in ("destination", "amount_minor", "asset", "network", "origin", "memo")}}, "duplicate": False})
        else:
            reply(route, {"ok": True, "sessions": [], "items": [], "plugins": [], "skills": [], "files": [], "mode": "manual", "state": "ready", "notifications": [], "models": [], "projects": []})

    page.route("**/*", route)
    page.goto("http://vool-wallet-home.test/", wait_until="load")
    return page, errors


def _entry(page):
    return page.locator("#vwHomeBtn")


def _open_home_menu(page):
    if page.locator("#homeMenu").get_attribute("open") is None:
        page.click("#homeToggle")


def _open_panel(page):
    _open_home_menu(page)
    _entry(page).click()
    page.wait_for_selector("#vwHomeOverlay", state="attached")


def test_off_means_no_entry_and_no_wallet_traffic(browser):
    facts = Facts(_status(enabled=False, accounts=(), rows=[]))
    page, errors = _page(browser, facts)
    page.wait_for_timeout(900)  # the fragment's boot poll (400ms) plus a poll cycle
    assert _entry(page).count() == 0
    assert page.locator("#vwHomeOverlay").count() == 0
    assert not [r for r in facts.requests if r.startswith("GET /api/wallet/balance")], "crypto off must not read balances"
    assert not [r for r in facts.requests if r.startswith("POST /api/wallet")], "crypto off must not touch any wallet door"
    assert not errors


def test_enable_adds_exactly_one_entry_and_opening_creates_nothing(browser):
    facts = Facts(_status(enabled=False, accounts=(), rows=[]))
    page, errors = _page(browser, facts)
    page.wait_for_timeout(700)
    _open_home_menu(page)
    facts.status = _status(enabled=True, accounts=(), rows=[])
    page.evaluate("window.VoolWallet.refresh()")
    page.wait_for_selector("#vwHomeBtn", state="visible")
    assert _entry(page).count() == 1
    assert _entry(page).text_content().strip() == "👛 Wallet"
    posts_before = [r for r in facts.requests if r.startswith("POST ")]
    assert not posts_before, "enabling and showing the entry must not create anything"
    _open_panel(page)
    page.wait_for_selector("#vwHomeCreate")
    assert page.locator("#vwHomeOverlay").count() == 1
    posts = [r for r in facts.requests if r.startswith("POST ")]
    assert not posts, "opening the panel must not create, import or connect anything"
    page.locator("#vwHomeClose").click()
    assert page.locator("#vwHomeOverlay").count() == 0
    assert _entry(page).count() == 1, "closing the panel keeps the Home entry"
    # enable/disable cycles must not duplicate the entry
    facts.status = _status(enabled=False, accounts=(), rows=[])
    page.evaluate("window.VoolWallet.refresh()")
    page.wait_for_timeout(300)
    assert _entry(page).count() == 0
    facts.status = _status(enabled=True, accounts=(), rows=[])
    page.evaluate("window.VoolWallet.refresh()")
    page.wait_for_selector("#vwHomeBtn", state="attached")
    _open_home_menu(page)  # the page closes Home after an entry click; a fresh cycle needs it reopened
    assert _entry(page).is_visible()
    assert _entry(page).count() == 1
    assert not errors


def test_populated_home_switching_and_stale_answers_cannot_repaint(browser):
    watch = _account("w-watch", SOL, "watch_only", WATCH_KEY)
    pocket = _account("w-pocket", BASE, "pocket_sealed", POCKET_KEY, label="Panel wallet")
    facts = Facts(_status(enabled=True, accounts=[watch, pocket]))
    # the FIRST account (watch-only on Solana) answers slowly; the switch to the pocket account must not repaint it back
    facts.balances["w-watch"] = _balance("w-watch", minor="0", human="0")
    facts.balances["w-pocket"] = _balance("w-pocket", minor=str(10**18), human="1", asset="ETH")
    facts.balance_delay["w-watch"] = 1.2
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.wait_for_selector("#vwHomeAccount")
    assert page.locator("#vwHomeAccount option").count() == 2
    # switch to the pocket account before the slow watch-only balance lands
    page.select_option("#vwHomeAccount", "w-pocket")
    page.wait_for_selector("#vwHomeBalance >> text=1 ETH")
    page.wait_for_timeout(1800)  # the stale watch-only answer arrives now
    balance_text = page.locator("#vwHomeBalance").inner_text()
    assert "1 ETH" in balance_text
    assert "0 SOL" not in balance_text, "a stale answer for another account must never repaint the current selection"
    # the network switcher exists and shows both active rows; switching never creates anything
    assert page.locator("#vwHomeNetwork option").count() == 2
    posts = [r for r in facts.requests if r.startswith("POST ")]
    assert not posts
    assert not errors


def test_zero_is_zero_and_a_failed_read_is_not_zero(browser):
    watch = _account("w-zero", SOL, "watch_only", WATCH_KEY)
    broken = _account("w-broken", BASE, "watch_only", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[watch, broken]))
    facts.balances["w-zero"] = _balance("w-zero", minor="0", human="0")
    facts.balances["w-broken"] = {"state": "unavailable", "reason": "environment_inactive", "balance_minor": None}
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.wait_for_selector("#vwHomeBalance >> text=confirmed zero")
    page.select_option("#vwHomeAccount", "w-broken")
    page.wait_for_selector("#vwHomeBalance >> text=Balance unavailable")
    text = page.locator("#vwHomeBalance").inner_text()
    assert "not on the active network environment" in text
    assert "it is not zero" in text
    assert "0 ETH" not in text.split("Balance unavailable")[0], "a failed read must never render as a zero amount"
    assert not errors


def test_receive_shows_exact_address_and_local_qr(browser):
    pocket = _account("w-pocket", SOL, "pocket_sealed", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.locator("#vwHomeReceive").click()
    page.wait_for_selector("#vwHomeAddress")
    assert page.locator("#vwHomeAddress").inner_text() == POCKET_KEY
    assert page.locator("#vwHomeQr canvas").count() == 1, "the offline vendored generator must draw the QR locally"
    qr_bits = page.evaluate("() => { const c = document.querySelector('#vwHomeQr canvas'); const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data; let dark = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 128 && d[i-1] < 128 && d[i-2] < 128) dark++; return dark; }")
    assert qr_bits > 100, "the QR must actually encode modules, not be blank"
    assert page.locator("#vwHomeCopyAddress").count() == 1
    assert not errors


def test_send_proposes_in_token_units_and_decide_later_reopens(browser):
    pocket = _account("w-pocket", BASE, "pocket_sealed", POCKET_KEY, label="Panel wallet")
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    facts.quote_fields = _quote_fields()
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.locator("#vwHomeSend").click()
    page.wait_for_selector("#vwHomeSendTo")
    page.fill("#vwHomeSendTo", RECIPIENT)
    page.fill("#vwHomeSendAmount", "0.000001")
    page.locator("#vwHomeSendGo").click()
    page.wait_for_selector("#vwSheet")
    assert len(facts.proposals) == 1, "one submit, one proposal"
    body = facts.proposals[0]
    assert body["amount_minor"] == 10**12, "0.000001 ETH must convert exactly to 1e12 wei, never through a float"
    assert body["destination"] == RECIPIENT and body["wallet_id"] == "w-pocket" and body["origin"] == "user"
    assert page.locator("#vwSheetReview").inner_text().find("0.001 ETH") >= 0  # the sheet shows the SERVER's quote values
    page.locator("#vwSheetLater").click()
    page.wait_for_timeout(150)
    assert page.locator("#vwSheet").count() == 0, "Decide later dismisses without approving or cancelling"
    assert page.locator("#vwHomeOverlay").count() == 1, "the panel survives; the chat, draft and activity are untouched"
    # the pending request is reachable again from Activity
    facts.status = _status(enabled=True, accounts=[pocket], pending=[{
        "proposal_id": "pay-panel", "amount_minor": 10**12, "asset": "ETH", "destination": RECIPIENT, "origin": "user", "memo": "",
        "created_at": time.time(), "network": BASE, "state": "pending_approval", "meaning": None, "pilot_transfer": True, "purpose": None,
        "open_quote_id": "", "quote_expires_at": 0, "recipient": {},
    }])
    page.evaluate("window.VoolWallet.refresh()")
    page.wait_for_selector("#vwHomeActivity >> text=Review")
    page.locator("#vwHomeActivity .vw-wh-review").first.click()
    page.wait_for_selector("#vwSheet")
    page.wait_for_selector("#vwSheetApprove:enabled", timeout=10000)  # the reopened preview mints a fresh quote and enables
    page.locator("#vwSheetLater").click()
    assert not errors


def test_expired_quote_does_not_retain_approval(browser):
    pocket = _account("w-pocket", BASE, "pocket_sealed", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    facts.quote_fields = _quote_fields(expires_at=time.time() - 5)
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.locator("#vwHomeSend").click()
    page.fill("#vwHomeSendTo", RECIPIENT)
    page.fill("#vwHomeSendAmount", "0.001")
    page.locator("#vwHomeSendGo").click()
    page.wait_for_selector("#vwSheet")
    page.wait_for_selector("#vwSheetState >> text=expired")
    assert page.locator("#vwSheetApprove").is_disabled(), "an expired preview must not stay approvable"
    facts.quote_fields = _quote_fields()  # the operator refreshes into a live quote BEFORE the mint reads it
    page.locator("#vwSheetRefresh").click()
    page.wait_for_selector("#vwSheetApprove:enabled")
    assert not errors


def test_disable_while_open_closes_panel_and_removes_entry(browser):
    pocket = _account("w-pocket", SOL, "pocket_sealed", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.wait_for_selector("#vwHomeSend")
    facts.status = _status(enabled=False, accounts=[])
    page.evaluate("window.VoolWallet.refresh()")
    page.wait_for_timeout(200)
    assert page.locator("#vwHomeOverlay").count() == 0, "a disable closes the open panel: no executable Send controls remain"
    assert _entry(page).count() == 0
    assert page.locator("#nToast").count() in (0, 1)
    assert not errors


def test_watch_only_cannot_send(browser):
    watch = _account("w-watch", SOL, "watch_only", WATCH_KEY)
    facts = Facts(_status(enabled=True, accounts=[watch]))
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.wait_for_selector("#vwHomeSend")
    assert page.locator("#vwHomeSend").is_disabled()
    assert "watch-only" in (page.locator("#vwHomeSend").get_attribute("title") or "")
    page.locator("#vwHomeReceive").click()
    page.wait_for_selector("#vwHomeAddress")
    assert "atch-only" in page.locator("#vwHomeBody").inner_text()
    assert not errors


def test_contact_from_another_network_is_never_silently_resolved(browser):
    pocket = _account("w-pocket", BASE, "pocket_sealed", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    facts.contacts_answer = {"contacts": [{
        "contact_id": "c1", "display_name": "Ada",
        "endpoints": [
            {"endpoint_id": "e1", "kind": "wallet", "value": "AdaSolanaAddress1111111111111111111111111111", "chain_network": SOL, "network_display": "Solana Devnet"},
            {"endpoint_id": "e2", "kind": "wallet", "value": "0xada1111111111111111111111111111111111111", "chain_network": BASE, "network_display": "Base Sepolia"},
        ],
    }]}
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.locator("#vwHomeSend").click()
    page.locator("#vwHomePickContact").click()
    page.wait_for_selector("#vwHomeContactList .vw-wh-contact")
    rows = page.locator("#vwHomeContactList .vw-wh-contact")
    rows.nth(0).click()  # Ada's Solana address while sending on Base Sepolia
    note = page.locator("#vwHomeSendForm .vw-wh-sub").first.inner_text()
    assert "saved for" in note and "not Base" in note
    assert page.input_value("#vwHomeSendTo") == "", "an incompatible contact must not fill the destination"
    rows_count = page.locator("#vwHomeContactList .vw-wh-contact").count()
    # the list stays open after the refused pick; the matching entry fills the exact destination
    page.locator("#vwHomeContactList .vw-wh-contact").nth(1).click()
    assert page.input_value("#vwHomeSendTo") == "0xada1111111111111111111111111111111111111"
    note = page.locator("#vwHomeSendForm .vw-wh-sub").first.inner_text()
    assert "Ada" in note, "the name shows beside the destination, never instead of it"
    assert rows_count == 2
    assert not errors


def test_amount_parsing_is_exact_and_refuses_what_it_cannot_represent(browser):
    pocket = _account("w-pocket", SOL, "pocket_sealed", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.locator("#vwHomeSend").click()
    page.fill("#vwHomeSendTo", WATCH_KEY)
    # one nanoSOL: 0.000000001 SOL -> 1 lamport exactly
    page.fill("#vwHomeSendAmount", "0.000000001")
    page.locator("#vwHomeSendGo").click()
    page.wait_for_selector("#vwSheet")
    assert facts.proposals[-1]["amount_minor"] == 1, "a tiny nonzero amount must parse to 1, never round to 0"
    page.locator("#vwSheetLater").click()
    page.wait_for_timeout(100)
    # more decimals than the asset has: refused, not truncated (a fresh Send form is a fresh intent)
    page.locator("#vwHomeSend").click()
    page.fill("#vwHomeSendTo", WATCH_KEY)
    page.fill("#vwHomeSendAmount", "0.0000000001")
    page.locator("#vwHomeSendGo").click()
    page.wait_for_selector("#vwHomeSendForm .vw-wh-msg.err")
    msg = page.locator("#vwHomeSendForm .vw-wh-msg").inner_text()
    assert "at most 9 decimals" in msg
    before = len(facts.proposals)
    page.fill("#vwHomeSendAmount", "-1")
    page.locator("#vwHomeSendGo").click()
    page.wait_for_timeout(150)
    assert len(facts.proposals) == before, "a negative amount must not reach the door"
    assert not errors


def test_double_submit_is_one_proposal(browser):
    pocket = _account("w-pocket", BASE, "pocket_sealed", POCKET_KEY)
    facts = Facts(_status(enabled=True, accounts=[pocket]))
    page, errors = _page(browser, facts)
    _open_panel(page)
    page.locator("#vwHomeSend").click()
    page.fill("#vwHomeSendTo", RECIPIENT)
    page.fill("#vwHomeSendAmount", "0.01")
    page.locator("#vwHomeSendGo").click()
    page.evaluate("document.getElementById('vwHomeSendGo').click()")  # a second impatient click in the same breath
    page.wait_for_selector("#vwSheet")
    assert len(facts.proposals) == 1, "double clicks must not create duplicate operations"
    assert not errors


def test_landed_payment_and_rising_balance_each_say_so_once(browser):
    pocket = _account("w-pocket", SOL, "pocket_sealed", POCKET_KEY)
    transfer = {"proposal_id": "pay-old", "wallet_id": "w-pocket", "network": SOL, "display_name": "Solana Devnet", "state": "submitted",
                "state_label": "Submitted", "amount_human": "0.25", "display_symbol": "SOL", "amount_minor": "250000000",
                "to_address": WATCH_KEY, "from_address": POCKET_KEY, "created_at": time.time() - 60, "updated_at": time.time() - 60,
                "explorer_url": "", "charged_fee_display": "", "in_flight": True, "purpose": None}
    facts = Facts(_status(enabled=True, accounts=[pocket], transfers=[transfer], in_flight=[transfer]))
    facts.balances["w-pocket"] = _balance("w-pocket", minor="500000000", human="0.5")
    page, errors = _page(browser, facts)
    page.wait_for_timeout(700)  # the first snapshot baselines history: no toast yet
    assert page.locator("#nToast").count() == 0
    landed = dict(transfer, state="confirmed", state_label="Confirmed", in_flight=False)
    facts.status = _status(enabled=True, accounts=[pocket], transfers=[landed])
    page.wait_for_timeout(2800)  # the poll the page already runs notices the change
    toast = page.locator("#nToast")
    assert toast.count() == 1 and "payment confirmed" in toast.inner_text() and "0.25 SOL" in toast.inner_text()
    page.wait_for_timeout(2600)  # it is said once
    assert toast.inner_text().count("payment confirmed") == 1
    # a rising balance between two looks is noticed as received — honestly, as an observation
    _open_panel(page)  # the first look baselines 0.5 SOL
    page.wait_for_selector("#vwHomeBalance >> text=0.5 SOL")
    page.locator("#vwHomeClose").click()
    page.wait_for_timeout(200)
    facts.balances["w-pocket"] = _balance("w-pocket", minor="900000000", human="0.9")
    _open_panel(page)  # the second look sees the rise
    page.wait_for_selector("#vwHomeBalance >> text=0.9 SOL")
    toast2 = page.locator("#nToast").inner_text()
    assert "Received" in toast2 and "0.5" in toast2 and "0.9" in toast2 and "does not watch the chain in the background" in toast2
    assert not errors
