"""Regressions from the 15 September 2026 live UsePod x402 drive, replayed at the transport boundary.

The two fixtures are the exact bytes the live gateway returned: the Cloudflare block page relayed on a paid
HTTP 403 (``same-payment-response-diagnostic.json``) and a real ``PAYMENT-REQUIRED`` quote header from an
unpaid probe. Everything here is local and SYNTHETIC beyond those captures: nothing pays, signs or reaches a
network, and the strict service double stands in for the gateway where a full exchange is driven.
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

import pytest

from core.usepod import transport as tp
from core.usepod.descriptor import TransportMode
from tests.usepod.test_usepod_transport_x402 import OPENAI_PATH, _BufferedBody, _execute, _seal, _x402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURED_BLOCK_PAGE = (FIXTURES / "cloudflare_block_nousresearch_20260915.html").read_bytes()
CAPTURED_QUOTE_HEADER = (FIXTURES / "live_quote_payment_required_20260915.b64.txt").read_text().strip()
#: The captured quote said ``"expires_at": "2026-09-15T15:56:19.341232610Z"`` on every option.
CAPTURED_QUOTE_EXPIRY = datetime.datetime(2026, 9, 15, 15, 56, 19, 341232, tzinfo=datetime.timezone.utc).timestamp()
MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"


def _rfc3339(epoch: float) -> str:
    """Nine fractional digits, the way the live gateway writes its expiries."""
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def _with_expiry(value: str):
    """A quote mutator for the strict service: every option states this expiry."""
    return lambda quote: {**quote, "accepts": [{**option, "expires_at": value} for option in quote["accepts"]]}


def _block_page(host: str, ray: str) -> bytes:
    return (
        "<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head><body>"
        '<div id="cf-error-details"><h2><span data-translate="unable_to_access">You are unable to access</span> '
        f'{host}</h2><span class="cf-footer-item">Cloudflare Ray ID: <strong class="font-semibold">{ray}</strong></span>'
        "</div></body></html>"
    ).encode("utf-8")


def _response(status: int, body: bytes, content_type: str) -> tp.UsePodResponse:
    return tp.UsePodResponse(status=status, headers={"content-type": content_type}, raw=_BufferedBody(body), streaming=False)


# --- the paid 403: an edge block page relayed where a completion was owed ------------------------------------


def test_the_captured_paid_403_names_the_cloudflare_block_and_the_host_that_issued_it() -> None:
    error = tp.error_for_response(_response(403, CAPTURED_BLOCK_PAGE, "text/html; charset=UTF-8"), origin="https://api.usepod.ai")
    assert (error.code, error.http_status, error.dispatch_state) == ("request_forbidden", 403, tp.DISPATCH_RESPONSE_RECEIVED)
    assert error.detail == "edge_block:cloudflare:nousresearch.com:ray-a3b8e8e98a4f46fd"
    document = error.provider_evidence["response_document"]
    assert (document["kind"], document["edge"], document["blocked_host"], document["ray_id"]) == (
        "html",
        "cloudflare",
        "nousresearch.com",
        "a3b8e8e98a4f46fd",
    )
    assert document["title"] == "Attention Required! | Cloudflare"
    assert "hint" not in document and document["bytes"] == len(CAPTURED_BLOCK_PAGE)

    # The paid-result-unknown wrapper that the chat, the receipts and the resume door see keeps that provenance.
    proof = tp.X402PaymentProof(
        operation_id="upo_captured",
        envelope_binding_sha256="b" * 64,
        quote_header_sha256="q" * 64,
        quote_id="6935fd7e-31c7-4473-8b3f-e7fa81b5cd26",
        network=MAINNET,
        asset="USDC",
        pay_to="GXfqVnZENHzvim8rNN8TPwqxWXQe8EBbxhcEMYE8Z7BS",
        amount_atomic=363,
        payer_wallet="E29oE6ok8gHYbRnbjfNVF5ftZcTo3oTkGx1TwPPMhKhZ",
        signature="4iYLeRTotLxLMsqACvdXrAGQD9MujxP5Dd3Kdwbz9tqCkJ4gzkjG8cBwnYRT6zRPXZaMDqtYbz2i4tjmzywdGcB2",
        authority_label="core.wallet.usepod_x402:v1",
        issued_at=0.0,
    )
    unknown = tp.X402PaidResultUnknownError(error, proof=proof, operation_id="upo_captured", paid_attempts=1)
    assert str(unknown) == (
        "usepod_x402_paid_result_unknown:request_forbidden status=403 dispatch=response_received: "
        "edge_block:cloudflare:nousresearch.com:ray-a3b8e8e98a4f46fd"
    )
    assert unknown.provider_evidence["response_document"]["blocked_host"] == "nousresearch.com"
    assert unknown.provider_evidence["payment_retained"] is True


@pytest.mark.parametrize(
    ("status", "code", "body", "content_type", "detail", "document"),
    [
        pytest.param(
            503,
            "no_provider_available",
            _block_page("relay.example-upstream.test", "0123456789abcdef"),
            "text/html",
            "edge_block:cloudflare:relay.example-upstream.test:ray-0123456789abcdef",
            {"edge": "cloudflare", "blocked_host": "relay.example-upstream.test", "ray_id": "0123456789abcdef"},
            id="another-host-another-ray-another-status",
        ),
        pytest.param(
            502,
            "upstream_failure",
            b"<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>",
            "text/html",
            "html_document",
            {"title": "502 Bad Gateway"},
            id="plain-html-page-is-named-not-guessed",
        ),
        pytest.param(
            403,
            "request_forbidden",
            b'{"error": {"type": "forbidden_by_policy"}}',
            "application/json",
            "forbidden_by_policy",
            None,
            id="json-error-hint-unchanged",
        ),
        pytest.param(403, "request_forbidden", b"forbidden", "text/plain", "", None, id="plain-text-unchanged"),
    ],
)
def test_other_html_answers_are_named_without_guessing_and_non_html_answers_are_unchanged(
    status: int, code: str, body: bytes, content_type: str, detail: str, document: dict | None
) -> None:
    error = tp.error_for_response(_response(status, body, content_type), origin="https://api.usepod.ai")
    assert (error.code, error.http_status, error.detail) == (code, status, detail)
    if document is None:
        assert "response_document" not in error.provider_evidence
        return
    facts = error.provider_evidence["response_document"]
    assert facts["kind"] == "html"
    for key, value in document.items():
        assert facts[key] == value
    if "edge" not in document:
        assert "edge" not in facts and "blocked_host" not in facts and "ray_id" not in facts


# --- the quote's own expiry ----------------------------------------------------------------------------------


def test_the_captured_live_quote_states_its_expiry_and_an_expired_quote_is_refused_before_any_reservation(
    usepod_service, x402_journal
) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt="name the keeper of the lamp at 'north-reef-4'")
    quote = tp.parse_payment_required(
        CAPTURED_QUOTE_HEADER, envelope=envelope, origin="https://api.usepod.ai", received_at=CAPTURED_QUOTE_EXPIRY - 300.0
    )
    usdc, sol, base = quote.options
    assert (usdc.asset, usdc.amount_atomic, usdc.mode, usdc.usable) == ("USDC", 10, "cap-with-surplus-credit", True)
    assert usdc.expires_at_epoch == sol.expires_at_epoch == CAPTURED_QUOTE_EXPIRY
    assert "expires_at" not in usdc.extra_fields and "balance_hint" in usdc.extra_fields
    assert quote.as_evidence()["expiry"] == {"state": "stated_per_option", "earliest_epoch": CAPTURED_QUOTE_EXPIRY}
    # the Base option is as unpayable as before: an unrecognized asset, recipient shape and mode
    assert base.usable is False and {"asset_unrecognized", "pay_to_malformed", "mode_unrecognized"} <= set(base.problems)

    # The same expiry on a quote judged NOW (long after 15:56:19Z on 15 September 2026): refused before money moves.
    service.faults["quote_mutator"] = _with_expiry("2026-09-15T15:56:19.341232610Z")
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("quote_expired", tp.DISPATCH_NOT_SENT)
    assert caught.value.provider_evidence["quote_expires_at_epoch"] == CAPTURED_QUOTE_EXPIRY
    assert monetary.calls == [] and payment.calls == []
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["last_code"]) == (tp.X402_QUOTE_REFUSED, "quote_expired")
    assert len(service.requests_to(OPENAI_PATH)) == 1


def test_a_quote_the_wallet_cannot_pay_in_time_is_refused_and_a_valid_expiry_is_carried_into_the_liability(
    usepod_service, x402_journal
) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)

    # five seconds of validity when signing, confirming and settling need more: refused, nothing reserved
    service.faults["quote_mutator"] = _with_expiry(_rfc3339(time.time() + 5.0))
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt="count the buoys in 'channel-set-2'")
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("quote_expires_too_soon", tp.DISPATCH_NOT_SENT)
    assert caught.value.provider_evidence["minimum_validity_seconds"] == tp.X402_MIN_QUOTE_VALIDITY_SECONDS
    assert monetary.calls == [] and payment.calls == []

    # a stated expiry nobody can read is a validity limit nobody can honour: the option is not paid
    service.faults["quote_mutator"] = _with_expiry("tomorrow at noon")
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt="how many steps climb 'spiral-tower-9'")
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert caught.value.code == "no_permitted_payment_option"
    usdc, sol = caught.value.provider_evidence["quote"]["options"]
    assert usdc["problems"] == ["expiry_unreadable"] and sol["problems"] == ["expiry_unreadable"]
    assert monetary.calls == [] and payment.calls == []

    # ten minutes of validity is paid, and the option, the liability and the journal all carry the expiry
    valid_until = time.time() + 600.0
    service.faults["quote_mutator"] = _with_expiry(_rfc3339(valid_until))
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt="which fog signal sounds at 'gull-point-3'")
    exchange = _execute(client, service, envelope)
    assert exchange.option.expires_at_epoch == pytest.approx(valid_until, abs=0.001)
    liability = monetary.calls[0][1]
    assert liability.basis["quote_expires_at_epoch"] == pytest.approx(valid_until, abs=0.001)
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["paid_attempts"]) == (tp.X402_COMPLETED, 1)
    recorded = json.loads(row["quote_json"])
    assert recorded["expiry"]["state"] == "stated_per_option"
    rebuilt = tp._quote_from_evidence(recorded)
    assert rebuilt.options[0].expires_at_epoch == pytest.approx(valid_until, abs=0.001)

    # control: a quote that states no expiry (the published schema) is paid exactly as before
    service.faults.pop("quote_mutator")
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt="what colour is the lantern at 'east-mole-1'")
    exchange = _execute(client, service, envelope)
    assert exchange.option.expires_at_epoch is None
    assert exchange.quote.as_evidence()["expiry"] == {"state": "not_stated_by_provider"}
    assert x402_journal.get(envelope.operation_id)["state"] == tp.X402_COMPLETED


@pytest.mark.parametrize(
    ("value", "epoch"),
    [
        ("2026-09-15T15:56:19.341232610Z", CAPTURED_QUOTE_EXPIRY),
        ("2026-09-15T15:56:19Z", CAPTURED_QUOTE_EXPIRY - 0.341232),
        ("2026-09-15T17:56:19.341232+02:00", CAPTURED_QUOTE_EXPIRY),
        ("2026-09-15 15:56:19.3412Z", CAPTURED_QUOTE_EXPIRY - 0.000032),
        ("2026-09-15T15:56:19", None),
        ("1789487779", None),
        (1789487779, None),
        ("", None),
        (None, None),
    ],
)
def test_an_expiry_is_read_at_any_precision_and_offset_and_never_guessed(value, epoch) -> None:
    parsed = tp.parse_rfc3339_epoch(value)
    if epoch is None:
        assert parsed is None
    else:
        assert parsed == pytest.approx(epoch, abs=1e-6)


def test_the_wallet_closes_its_approval_window_before_the_quote_expires() -> None:
    # imported here so the module collects on the pinned base, where this fails on the missing bound
    from core.wallet.usepod_x402 import QUOTE_EXPIRY_MARGIN_SECONDS, payment_deadline

    assert payment_deadline(1_000.0, 240.0, None) == 1_240.0
    assert payment_deadline(1_000.0, 240.0, 2_000.0) == 1_240.0
    assert payment_deadline(1_000.0, 240.0, 1_100.0) == 1_100.0 - QUOTE_EXPIRY_MARGIN_SECONDS
    assert QUOTE_EXPIRY_MARGIN_SECONDS < tp.X402_MIN_QUOTE_VALIDITY_SECONDS


# --- the client names itself ---------------------------------------------------------------------------------


def test_every_usepod_post_identifies_this_client(usepod_service, x402_journal) -> None:
    service, client, _monetary, _payment = _x402(usepod_service, x402_journal)
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt="tally the lamps along 'breakwater-set-5'")
    _execute(client, service, envelope)
    quote_request, paid_retry = service.requests_to(OPENAI_PATH)
    assert quote_request["headers"]["user-agent"] == paid_retry["headers"]["user-agent"] == tp.USER_AGENT == "vool-usepod/1"
