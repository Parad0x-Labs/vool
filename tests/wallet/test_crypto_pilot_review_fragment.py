"""Product follow-up, Phase A, the presentation branches a served chain does not reach: SYNTHETIC API FACTS in a real
browser (the actual wallet fragment composed into the actual chat page; every wallet answer intercepted and answered
with typed facts; no daemon, no chain, no funds). Labelled as such. Proven: a fee paid in another asset is labelled
apart from the amount; a service purpose names provider, resource, charge scope and the attributed description; a
credit purpose says payment accepted is not service delivered; an unknown purpose stays unknown; a dust fee is never
shown as zero; the sheet renders the server's strings verbatim and never computes money.
"""
from __future__ import annotations

import json
import time

import pytest

from tests import served_browser

pytestmark = [pytest.mark.safety]

RECIPIENT = "0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a"


def _fields(*, origin="user", **over):
    from types import SimpleNamespace

    from core.wallet import amounts, payment_review
    base = {
        "proposal_id": "pay-synthetic", "network": "eip155:84532", "chain_key": "base", "chain_label": "Base", "display_name": "Base Sepolia", "environment": "testnet",
        "badge": "TESTNET", "environment_label": "Testnet", "value_note": "Test funds have no monetary value", "chain_identity": "84532", "from_label": "Test wallet",
        "from_address": "0x1111111111111111111111111111111111111111", "to_address": RECIPIENT, "asset": "ETH", "display_symbol": "ETH", "gas_asset": "ETH", "decimals": 18,
        "amount_minor": 1_000_000_000_000_000, "amount_human": "0.001", "amount_display": "0.001 ETH", "fee_model": "evm_op_stack", "balance_observed_at": time.time(), "balance_stale": False,
        "held_elsewhere_minor": 0, "balance_minor": 10**18, "balance_ref": "block:100", "balance_human": "1", "balance_display": "1 ETH",
        "fee_estimate_minor": 61_021_210_000_007, "fee_estimate_human": "0.000061021210000007", "fee_estimate_display": "≈0.000061 ETH",
        "fee_max_minor": 92_594_227_130_007, "fee_max_human": "0.000092594227130007", "fee_max_display": "at most 0.0000926 ETH",
        "fee_parts": {"execution_estimate_minor": 21_000, "l1_estimate_minor": 7, "operator_minor": 7, "l1_is_estimate": True},
        "max_total_minor": 1_000_092_594_227_130_007, "max_total_human": "0.001092594227130007", "max_total_display": "at most 0.0011 ETH",
        "estimated_after_minor": 1, "estimated_after_human": "0.998938978789999993", "estimated_after_display": "≈0.999 ETH",
        "minimum_after_minor": 1, "minimum_after_human": "0.998907405772869993", "minimum_after_display": "at least 0.998 ETH",
        "fee_asset_differs": False, "cues": [], "quoted_at": time.time(), "expires_at": time.time() + 60, "approval_method": "pin",
        "purpose": {"kind": "direct", "headline": f"Send 0.001 ETH to {RECIPIENT}", "mechanism_label": "Direct transfer", "beneficiary": RECIPIENT, "charge_scope": "this transfer only",
                    "provider": "", "provider_source": "", "resource": "", "resource_method": "", "description": "", "description_source": "", "recipient_label": "", "recipient_label_source": "", "note": ""},
    }
    base.update(over)
    token = bool(base.get("token_transfer"))
    base["principal_balance_minor"] = base["balance_minor"]
    base["fee_balance_minor"] = base["balance_minor"]
    base["fee_decimals"] = 18
    base["max_total_minor"] = base["amount_minor"] + (0 if token else base["fee_max_minor"])
    base["estimated_after_minor"] = base["balance_minor"] - base["amount_minor"] - (0 if token else base["fee_estimate_minor"])
    base["minimum_after_minor"] = base["balance_minor"] - base["max_total_minor"]
    base["fee_balance_after_minimum_minor"] = base["fee_balance_minor"] - base["fee_max_minor"]
    for name in ("max_total", "estimated_after", "minimum_after", "principal_balance", "fee_balance", "fee_balance_after_minimum"):
        base[name + "_human"] = amounts.format_minor(base[name + "_minor"], base["decimals"])
    base["review"] = payment_review.compose(base, SimpleNamespace(origin=origin))
    return base


SERVICE = {"kind": "service", "headline": "Pay api.example.test for GET https://api.example.test/v1/summaries/42", "mechanism_label": "Service payment · x402 v2",
           "beneficiary": RECIPIENT, "charge_scope": "one paid response for this resource", "provider": "api.example.test",
           "provider_source": "the resource's own origin (not a verified merchant identity)", "resource": "https://api.example.test/v1/summaries/42", "resource_method": "GET",
           "description": "Premium summary of document 42", "description_source": "the provider's own description (untrusted)", "recipient_label": "", "recipient_label_source": "", "note": ""}
CREDIT = {"kind": "credit", "headline": "Prepay usepod.example credit for https://usepod.example/models/summarize", "mechanism_label": "Provider credit · UsePod top-up",
          "beneficiary": RECIPIENT, "charge_scope": "prepayment: credit held by the provider, spent by later requests", "provider": "usepod.example",
          "provider_source": "the UsePod requirement's provider (not a verified merchant identity)", "resource": "https://usepod.example/models/summarize", "resource_method": "",
          "description": "", "description_source": "", "recipient_label": "", "recipient_label_source": "", "note": "Payment accepted is not service delivered: this buys credit the provider spends on later requests."}
UNKNOWN = {"kind": "unknown", "headline": f"Pay 0.001 ETH to {RECIPIENT} — purpose unknown", "mechanism_label": "Purpose unknown", "beneficiary": RECIPIENT, "charge_scope": "",
           "provider": "", "provider_source": "", "resource": "", "resource_method": "", "description": "", "description_source": "", "recipient_label": "", "recipient_label_source": "", "note": "No record explains this request (origin 'mystery')."}


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


def _page_with_facts(browser, pending: list[dict], quotes: dict[str, dict]):
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html(build_commit="review-fragment-proof")
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def reply(route, data, status=200):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    def route(route):
        url = route.request.url
        path = url.split("://", 1)[1].split("/", 1)[1] if "://" in url else url
        path = "/" + path.split("?")[0]
        body = route.request.post_data_json if route.request.method == "POST" else None
        if path == "/":
            route.fulfill(status=200, content_type="text/html", body=html)
        elif path == "/api/wallet/status":
            reply(route, {"status": {"enabled": True, "pending": pending, "generated_at_epoch": time.time(), "transfers": [], "in_flight": []}})
        elif path == "/api/wallet/quote":
            reply(route, {"ok": True, "quote": {"quote_id": "quote-" + body["proposal_id"], "digest": "d-" + body["proposal_id"], "state": "open", "fields": quotes[body["proposal_id"]]}})
        else:
            reply(route, {"ok": True, "sessions": [], "items": [], "plugins": [], "skills": [], "files": [], "mode": "manual", "state": "ready", "notifications": [], "models": [], "projects": []})

    page.route("**/*", route)
    page.goto("http://vool-review.test/", wait_until="load")
    return page, errors


def _pending(pid: str, purpose: dict, **over) -> dict:
    row = {"proposal_id": pid, "pilot_transfer": True, "network": "eip155:84532", "origin": "user", "destination": RECIPIENT, "amount_minor": 1_000_000_000_000_000, "asset": "ETH", "purpose": purpose, "open_quote_id": ""}
    row.update(over)
    return row


def test_purposes_fee_asset_and_dust_render_from_typed_facts(browser):
    pending = [
        _pending("pay-direct", _fields()["purpose"]),
        _pending("pay-service", SERVICE, pilot_transfer=False, origin="x402", asset="USDC", amount_minor=10_000),
        _pending("pay-credit", CREDIT, origin="usepod"),
        _pending("pay-unknown", UNKNOWN, origin="mystery"),
        _pending("pay-dust", _fields()["purpose"]),
    ]
    quotes = {
        "pay-direct": _fields(),
        "pay-credit": _fields(origin="usepod", proposal_id="pay-credit", purpose=CREDIT),
        "pay-unknown": _fields(origin="mystery", proposal_id="pay-unknown", purpose=UNKNOWN),
        # a fee paid in another asset than the one sent, and a dust fee shown exactly, never as zero
        "pay-dust": _fields(proposal_id="pay-dust", display_symbol="TOK", asset="TOK", amount_display="0.001 TOK", fee_asset_differs=True, token_transfer=True,
                            fee_estimate_minor=1, fee_estimate_human="0.000000000000000001", fee_estimate_display="0.000000000000000001 ETH",
                            fee_max_minor=7, fee_max_human="0.000000000000000007", fee_max_display="0.000000000000000007 ETH"),
    }
    page, errors = _page_with_facts(browser, pending, quotes)
    try:
        page.locator('.vw-card[data-proposal="pay-service"]').wait_for(timeout=15_000)
        service = page.locator('.vw-card[data-proposal="pay-service"]')
        assert service.locator('[data-field="purpose"]').get_attribute("data-kind") == "service"
        assert service.locator(".vw-purpose-headline").text_content() == SERVICE["headline"]
        assert service.locator(".vw-mechanism").text_content() == "Service payment · x402 v2"
        text = service.locator('[data-field="purpose"]').text_content()
        assert "Provider: api.example.test — the resource's own origin (not a verified merchant identity)" in text
        assert "Resource: GET https://api.example.test/v1/summaries/42" in text
        assert "Charge: one paid response for this resource" in text
        assert "Says: “Premium summary of document 42” (the provider's own description (untrusted))" in text
        credit = page.locator('.vw-card[data-proposal="pay-credit"]')
        assert credit.locator(".vw-title").text_content() == "Provider credit request"
        assert credit.locator(".vw-mechanism").text_content() == "Provider credit · UsePod top-up"
        assert "Payment accepted is not service delivered" in credit.locator(".vw-purpose-note").text_content()
        unknown = page.locator('.vw-card[data-proposal="pay-unknown"]')
        assert unknown.locator(".vw-mechanism").text_content() == "Purpose unknown" and "purpose unknown" in unknown.locator(".vw-purpose-headline").text_content()
        # the credit sheet: title names the mechanism, the purpose block repeats the typed facts
        credit.locator(".vw-review").click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        assert page.locator("#vwSheetTitle").text_content() == "Prepay provider credit"
        assert "provider credit for later requests" in page.locator('#vwSheet [data-review="amount"]').text_content()
        assert "Payment accepted is not service delivered" in page.locator('#vwSheet [data-warning="prepaid_credit"]').text_content()
        page.locator("#vwSheetLater").click()
        page.wait_for_selector("#vwSheet", state="detached")
        # the dust sheet: the fee asset differs from the sent asset and is labelled so; dust is exact, never 0
        page.locator('.vw-card[data-proposal="pay-dust"] .vw-review').click()
        page.wait_for_function("!document.getElementById('vwSheetApprove').disabled", timeout=10_000)
        page.locator("#vwSheetDetailsToggle").click()
        assert page.locator('#vwSheet [data-field="amount"] .vw-sheet-value').text_content() == "0.001 TOK"
        assert "0.001 TOK" in page.locator('#vwSheet [data-review="max_debit_0"]').text_content()
        assert "0.000000000000000007 ETH" in page.locator('#vwSheet [data-review="max_debit_1"]').text_content()
        fee = page.locator('#vwSheet [data-field="fee"] .vw-sheet-value').text_content()
        assert fee == "estimated 0.000000000000000001 ETH · at most 0.000000000000000007 ETH" and " 0 ETH" not in fee
        # the sheet never computes money: every rendered figure is one of the server's strings
        rendered = page.locator("#vwSheetFields").text_content()
        for value in ("0.001 TOK", "0.000000000000000001 ETH", "0.000000000000000007 ETH", "0.999 TOK", "0.999999999999999993 ETH"):
            assert value in rendered
        assert not errors, errors
    finally:
        page.close()
