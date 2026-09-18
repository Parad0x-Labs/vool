"""The UsePod HTTP boundary and accountless x402, against the SYNTHETIC strict local service.

Wire facts are asserted on what the service RECORDED arriving (path with the token masked, headers, body
hash), never on the client's own account of what it sent. Requests travel through the runtime's real
provider HTTP worker. The two authorities the transport calls are TEST DOUBLES labelled ``test_double:``;
nothing here is a payment, a wallet or a budget, and every token, quote, signature and address is
synthetic. Model names are invented for these tests.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import traceback
import uuid

import pytest

from core.usepod import pricing
from core.usepod import transport as tp
from core.usepod.descriptor import TransportMode, WireProtocol
from core.usepod.monetary import OUTCOME_FAILED_AFTER_SEND, OUTCOME_UNKNOWN
from tests.usepod._usepod_doubles import (
    RecordingMonetaryTestDouble,
    SyntheticChainPaymentTestDouble,
    synthetic_signature,
)
from tests.usepod.strict_usepod_service import SYNTHETIC_PAY_TO, Listing, StrictUsePodService

MODEL = "quillfeather-9b-synth"
MARKET_ID = "3c9e1f2a-7b4d-4e8f-9a1b-2c3d4e5f6a7b"
IN_RATE, OUT_RATE = 300_000, 900_000
CEILING_HEADERS = (
    ("X-Pod-Routing-Mode", "marketplace-only"),
    ("X-Pod-Max-Price-Input", str(IN_RATE)),
    ("X-Pod-Max-Price-Output", str(OUT_RATE)),
)
OPENAI_PATH = "/proxy/x402/v1/chat/completions"
ANTHROPIC_PATH = "/proxy/x402/v1/messages"


def _started(make, *, balance: int = 5_000_000, cls=StrictUsePodService):
    token = str(uuid.uuid4())
    service = make(
        cls,
        tokens={token: balance},
        models={MODEL: [Listing("marketplace", MARKET_ID, IN_RATE, OUT_RATE), Listing("centralized", "together", 500_000, 1_500_000)]},
    )
    return service, token


def _payload(protocol: WireProtocol, prompt: str, *, max_tokens: int = 64, stream: bool = False) -> dict:
    if protocol is WireProtocol.OPENAI:
        payload: dict = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
    else:
        payload = {"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
    if stream:
        payload["stream"] = True
    return payload


def _seal(
    origin: str,
    *,
    mode: TransportMode = TransportMode.PREPAID,
    protocol: WireProtocol = WireProtocol.OPENAI,
    prompt: str = "tally the consonants in 'marmoset-31'",
    headers: tuple[tuple[str, str], ...] = CEILING_HEADERS,
    operation_id: str | None = None,
    **payload_options,
) -> tp.RequestEnvelope:
    return tp.seal_request_envelope(
        payload=_payload(protocol, prompt, **payload_options),
        transport_mode=mode,
        protocol=protocol,
        origin=origin,
        model_id=MODEL,
        route_approval_id="upd_synthetic_approval",
        control_headers=headers,
        operation_id=operation_id,
    )


def _service_cap(envelope: tp.RequestEnvelope) -> int:
    """The SYNTHETIC service's x402 cap rule (see SYNTHETIC_CHOICES), computed from the sealed bytes."""
    return max(1, -((-(len(envelope.body) * IN_RATE + envelope.max_output_tokens * OUT_RATE)) // 1_000_000))


def _local_bound(envelope: tp.RequestEnvelope) -> int:
    """The bound the provider lane reserves against, by the lane's own rule."""
    return pricing.charge_upper_bound_microunits(
        input_tokens=pricing.input_token_upper_bound(envelope.body, has_non_text_parts=False),
        output_tokens=envelope.max_output_tokens,
        input_rate=IN_RATE,
        output_rate=OUT_RATE,
    )


def _reply_text(prompt: str, protocol: str) -> str:
    return f"synthetic reply {hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12]} via {protocol}"


def _rendered(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


# --- the sealed envelope ---------------------------------------------------------------------------------


def test_an_envelope_is_serialized_once_and_cannot_change_after_sealing() -> None:
    envelope = _seal("https://api.usepod.ai", prompt="naïve café, ünïcode stays exact")
    assert envelope.body.isascii()
    assert envelope.payload()["messages"][0]["content"] == "naïve café, ünïcode stays exact"
    assert hashlib.sha256(envelope.body).hexdigest() == envelope.body_sha256
    assert envelope.path_template == "/proxy/{token}/v1/chat/completions"
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.body = b"{}"  # type: ignore[misc]
    with pytest.raises(ValueError, match="does not match its hash"):
        dataclasses.replace(envelope, body=envelope.body.replace(b"exact", b"exacT"))
    with pytest.raises(ValueError, match="binding"):
        dataclasses.replace(envelope, path_template="/proxy/{token}/v1/messages")
    assert "exact" not in repr(envelope)


@pytest.mark.parametrize(
    ("payload", "headers", "code"),
    [
        pytest.param({"model": "someone-else-7b", "messages": [], "max_tokens": 8}, CEILING_HEADERS, "payload_model_mismatch", id="model-mismatch"),
        pytest.param({"model": MODEL, "messages": []}, CEILING_HEADERS, "max_output_tokens_required", id="no-output-ceiling"),
        pytest.param({"model": MODEL, "messages": [], "max_tokens": True}, CEILING_HEADERS, "max_output_tokens_required", id="boolean-ceiling"),
        pytest.param({"model": MODEL, "messages": [], "max_tokens": 0}, CEILING_HEADERS, "max_output_tokens_required", id="zero-ceiling"),
        pytest.param({"model": MODEL, "messages": [], "max_tokens": 8, "temperature": float("nan")}, CEILING_HEADERS, "payload_not_serializable", id="nan-in-payload"),
        pytest.param({"model": MODEL, "messages": [], "max_tokens": 8}, (("Authorization", "Bearer x"),), "request_header_forbidden", id="authorization-header"),
        pytest.param({"model": MODEL, "messages": [], "max_tokens": 8}, (("X-Pod-Providers", "openai\r\nX-Injected: 1"),), "request_header_value_invalid", id="header-injection"),
    ],
)
def test_sealing_refuses_what_cannot_be_bounded_or_sent(payload, headers, code) -> None:
    with pytest.raises(tp.UsePodTransportError) as caught:
        tp.seal_request_envelope(
            payload=payload,
            transport_mode=TransportMode.PREPAID,
            protocol=WireProtocol.OPENAI,
            origin="https://api.usepod.ai",
            model_id=MODEL,
            route_approval_id="upd_x",
            control_headers=headers,
        )
    assert (caught.value.code, caught.value.dispatch_state) == (code, tp.DISPATCH_NOT_SENT)


def test_a_request_larger_than_the_bound_is_refused_before_sending(monkeypatch) -> None:
    monkeypatch.setattr(tp, "MAX_REQUEST_BYTES", 128)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _seal("https://api.usepod.ai", prompt="x" * 400)
    assert caught.value.code == "request_too_large"


# --- the prepaid wire --------------------------------------------------------------------------------------


def test_prepaid_post_writes_the_sealed_bytes_to_the_token_path_and_nothing_else(usepod_service) -> None:
    service, token = _started(usepod_service)
    prompt = "list three anagrams of 'lemon-drift'"
    envelope = _seal(service.origin, prompt=prompt)
    response = tp.UsePodHttpTransport().post_envelope(envelope, origin=service.origin, token=token, read_timeout_seconds=15)
    assert response.status == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == _reply_text(prompt, "openai")
    [arrived] = service.requests
    assert arrived["path"] == "/proxy/{token}/v1/chat/completions"
    assert arrived["body_sha256"] == envelope.body_sha256
    assert "authorization" not in arrived["headers"] and "x-api-key" not in arrived["headers"]
    assert {name: arrived["headers"][name.lower()] for name, _ in CEILING_HEADERS} == dict(CEILING_HEADERS)
    assert (response.header("x-pod-route"), response.header("x-pod-provider-id")) == ("marketplace", MARKET_ID)
    assert response.header("x-balance-remaining") is not None
    assert set(response.headers) <= tp._KEPT_RESPONSE_HEADERS


def test_the_transport_refuses_to_send_an_envelope_where_it_was_not_sealed_to_go(usepod_service) -> None:
    service, token = _started(usepod_service)
    elsewhere = usepod_service(tokens={token: 1})
    transport = tp.UsePodHttpTransport()
    prepaid = _seal(service.origin)
    accountless = _seal(service.origin, mode=TransportMode.X402)
    attempts = [
        (lambda: transport.post_envelope(prepaid, origin=service.origin, token=None, read_timeout_seconds=5), "prepaid_token_missing"),
        (lambda: transport.post_envelope(accountless, origin=service.origin, token=token, read_timeout_seconds=5), "x402_request_must_not_carry_a_token"),
        (lambda: transport.post_envelope(prepaid, origin=elsewhere.origin, token=token, read_timeout_seconds=5), "envelope_origin_mismatch"),
        (
            lambda: transport.post_envelope(dataclasses.replace(accountless, transport_mode=TransportMode.PREPAID.value), origin=service.origin, token=token, read_timeout_seconds=5),
            "envelope_path_mismatch",
        ),
        (
            lambda: transport.post_envelope(prepaid, origin=service.origin, token=token, extra_headers=(("Cookie", "session=1"),), read_timeout_seconds=5),
            "request_header_forbidden",
        ),
    ]
    for attempt, code in attempts:
        with pytest.raises(tp.UsePodTransportError) as caught:
            attempt()
        assert (caught.value.code, caught.value.dispatch_state) == (code, tp.DISPATCH_NOT_SENT)
        assert token not in _rendered(caught.value)
    assert service.requests == [] and elsewhere.requests == []


@pytest.mark.parametrize("same_origin", [False, True], ids=["cross-origin", "same-origin"])
def test_a_redirect_is_reported_as_refused_and_never_followed(usepod_service, same_origin: bool) -> None:
    service, token = _started(usepod_service)
    elsewhere = usepod_service(tokens={token: 5_000_000}, models=dict(service.models))
    path = f"/proxy/{token}/v1/chat/completions"
    service.faults["redirect_location"] = path if same_origin else f"{elsewhere.origin}{path}"
    envelope = _seal(service.origin)
    response = tp.UsePodHttpTransport().post_envelope(envelope, origin=service.origin, token=token, read_timeout_seconds=10)
    assert response.status == 302
    error = tp.error_for_response(response, origin=service.origin)
    assert (error.code, error.dispatch_state) == ("redirect_refused", tp.DISPATCH_RESPONSE_RECEIVED)
    assert f"cross_origin={str(not same_origin).lower()}" in error.detail
    assert len(service.requests) == 1 and elsewhere.requests == []
    assert token not in _rendered(error) and token not in json.dumps(response.headers)


def test_a_send_that_outlives_its_deadline_is_outcome_unknown_never_unsent(usepod_service) -> None:
    service, token = _started(usepod_service)
    service.faults["delay_seconds"] = 4
    envelope = _seal(service.origin)
    with pytest.raises(tp.UsePodTransportError) as caught:
        tp.UsePodHttpTransport().post_envelope(envelope, origin=service.origin, token=token, read_timeout_seconds=1.0)
    error = caught.value
    assert (error.code, error.dispatch_state) == ("transfer_ended_without_response", tp.DISPATCH_OUTCOME_UNKNOWN)
    assert error.__cause__ is None and error.__context__ is None
    assert token not in _rendered(error)
    # The request did arrive before the deadline: "unknown" is the only defensible state for it.
    assert len(service.requests) == 1


class _BufferedBody:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "request_rejected"),
        (401, "token_rejected"),
        (402, "payment_or_balance_required"),
        (403, "request_forbidden"),
        (404, "model_or_surface_not_found"),
        (409, "request_conflict"),
        (413, "request_too_large_for_provider"),
        (422, "request_rejected"),
        (429, "throttled"),
        (503, "no_provider_available"),
        (500, "upstream_failure"),
        (502, "upstream_failure"),
        (418, "unexpected_status"),
    ],
)
def test_every_failure_status_has_a_typed_code_and_a_sanitized_hint(status: int, code: str) -> None:
    response = tp.UsePodResponse(
        status=status, headers={"retry-after": "7"}, raw=_BufferedBody(b'{"error": {"type": "no provider <at> price\\n"}}'), streaming=False
    )
    error = tp.error_for_response(response, origin="https://api.usepod.ai")
    assert (error.code, error.http_status, error.dispatch_state) == (code, status, tp.DISPATCH_RESPONSE_RECEIVED)
    assert error.retry_after_seconds == 7.0
    assert error.detail == "no_provider__at__price_"


@pytest.mark.parametrize("value", ["-3", "soon", "nan", ""])
def test_an_unusable_retry_after_is_absent_rather_than_zero(value: str) -> None:
    response = tp.UsePodResponse(status=429, headers={"retry-after": value}, raw=_BufferedBody(b"<html>busy</html>"), streaming=False)
    error = tp.error_for_response(response, origin="https://api.usepod.ai")
    # the body is an HTML document where JSON was owed: named as such, never read as a hint about the throttle
    assert (error.code, error.retry_after_seconds, error.detail) == ("throttled", None, "html_document")
    assert error.provider_evidence["response_document"]["kind"] == "html" and "edge" not in error.provider_evidence["response_document"]


def test_a_response_body_past_its_bound_is_refused_not_truncated() -> None:
    response = tp.UsePodResponse(status=200, headers={}, raw=_BufferedBody(b"x" * 64), streaming=False)
    with pytest.raises(tp.UsePodTransportError) as caught:
        response.read_bytes(limit=16)
    assert caught.value.code == "response_too_large"


# --- credentialed discovery -----------------------------------------------------------------------------


def test_credentialed_discovery_reads_models_and_balance_through_the_governed_door(usepod_home, usepod_service) -> None:
    service, token = _started(usepod_service, balance=2_500_000)
    transport = tp.UsePodHttpTransport()
    listing = tp.list_token_models(transport=transport, origin=service.origin, token=token)
    balance = tp.read_token_balance(transport=transport, origin=service.origin, token=token)
    assert (listing.model_ids, listing.rejected_entries) == ((MODEL,), 0)
    assert (balance.usdc_balance_microunits, balance.state) == (2_500_000, "reported")
    assert [item["path"] for item in service.requests] == ["/proxy/{token}/v1/models", "/proxy/{token}/balance"]
    assert all("authorization" not in item["headers"] for item in service.requests)


@pytest.mark.parametrize(
    ("payload", "state"),
    [
        pytest.param({"usdc_balance": -40}, "field_malformed", id="negative"),
        pytest.param({"usdc_balance": "12.5"}, "field_malformed", id="decimal-string"),
        pytest.param({"usdc_balance": 1.5}, "field_malformed", id="float"),
        pytest.param({"usdc_balance": True}, "field_malformed", id="boolean"),
        pytest.param({}, "field_absent", id="absent"),
    ],
)
def test_a_balance_that_is_absent_or_malformed_is_never_read_as_zero(usepod_home, usepod_service, payload, state) -> None:
    service, token = _started(usepod_service)
    service.faults["balance_payload"] = payload
    observed = tp.read_token_balance(transport=tp.UsePodHttpTransport(), origin=service.origin, token=token)
    assert (observed.usdc_balance_microunits, observed.state) == (None, state)


def test_an_unknown_token_is_refused_without_the_token_in_any_rendering(usepod_home, usepod_service) -> None:
    service, _token = _started(usepod_service)
    stranger = str(uuid.uuid4())
    with pytest.raises(tp.UsePodTransportError) as caught:
        tp.read_token_balance(transport=tp.UsePodHttpTransport(), origin=service.origin, token=stranger)
    assert (caught.value.code, caught.value.http_status) == ("token_rejected", 401)
    assert stranger not in _rendered(caught.value)


def test_a_redirected_discovery_read_is_refused_and_the_other_host_sees_nothing(usepod_home, usepod_service) -> None:
    service, token = _started(usepod_service)
    elsewhere = usepod_service(tokens={token: 1})
    service.faults["redirect_location"] = f"{elsewhere.origin}/proxy/{token}/balance"
    with pytest.raises(tp.UsePodTransportError) as caught:
        tp.read_token_balance(transport=tp.UsePodHttpTransport(), origin=service.origin, token=token)
    assert caught.value.code == "redirect_refused"
    assert elsewhere.requests == []
    assert token not in _rendered(caught.value)


# --- accountless x402 --------------------------------------------------------------------------------------


def _x402(usepod_service, x402_journal, *, cls=StrictUsePodService, monetary="double", payment="double", **payment_options):
    service, _token = _started(usepod_service, cls=cls)
    monetary_authority = RecordingMonetaryTestDouble() if monetary == "double" else monetary
    payment_authority = SyntheticChainPaymentTestDouble(service, **payment_options) if payment == "double" else payment
    client = tp.UsePodX402Client(transport=tp.UsePodHttpTransport(), journal=x402_journal, payment=payment_authority, monetary=monetary_authority)
    return service, client, monetary_authority, payment_authority


def _execute(client, service, envelope, *, bound: int | None = None, assets: tuple[str, ...] = ("USDC",), timeout: float = 15.0):
    return client.execute(
        envelope,
        origin=service.origin,
        allowed_assets=assets,
        local_bounds_atomic={"USDC": _local_bound(envelope) if bound is None else bound},
        liability_basis={"rule": "the provider lane's liability rule", "input_rate": IN_RATE, "output_rate": OUT_RATE},
        read_timeout_seconds=timeout,
    )


@pytest.mark.parametrize(("protocol", "path"), [(WireProtocol.OPENAI, OPENAI_PATH), (WireProtocol.ANTHROPIC, ANTHROPIC_PATH)], ids=["openai", "anthropic"])
def test_x402_quotes_reserves_binds_a_proof_and_sends_the_same_bytes_once_paid(usepod_service, x402_journal, protocol, path) -> None:
    service, client, monetary, _payment = _x402(usepod_service, x402_journal)
    prompt = f"summarize the ledger line 'otter-{protocol.value}-58' in five words"
    envelope = _seal(service.origin, mode=TransportMode.X402, protocol=protocol, prompt=prompt)
    exchange = _execute(client, service, envelope)
    data = exchange.response.json()

    quote_request, paid_retry = service.requests_to(path)
    assert quote_request["body_sha256"] == paid_retry["body_sha256"] == envelope.body_sha256
    assert "payment-signature" not in quote_request["headers"]
    assert all("authorization" not in item["headers"] for item in service.requests)
    assert not [item for item in service.requests if "{token}" in item["path"]]
    claim = json.loads(base64.b64decode(paid_retry["headers"]["payment-signature"]))
    assert list(claim) == ["quote_id", "network", "asset", "payer_wallet", "signature"]
    assert (claim["quote_id"], claim["asset"]) == (exchange.quote.quote_id, "USDC")

    assert exchange.option.amount_atomic == _service_cap(envelope) <= _local_bound(envelope)
    assert (exchange.option.pay_to, exchange.attempts) == (SYNTHETIC_PAY_TO, 1)
    assert monetary.names() == ["reserve", "mark_dispatched"]
    liability = monetary.calls[0][1]
    assert (liability.max_amount_atomic, liability.account_kind, liability.envelope_binding_sha256) == (
        exchange.option.amount_atomic,
        "x402_payer_wallet",
        envelope.binding_sha256,
    )
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["paid_attempts"], row["last_status"]) == (tp.X402_COMPLETED, 1, 200)
    assert "otter" not in json.dumps(row)
    receipt = tp.decode_payment_response(exchange.response.header("payment-response"))
    assert receipt["state"] == "decoded_schema_unpublished" and receipt["fields"]["quote_id"] == exchange.quote.quote_id
    text = data["choices"][0]["message"]["content"] if protocol is WireProtocol.OPENAI else data["content"][0]["text"]
    assert text == _reply_text(prompt, protocol.value)


def test_a_quote_above_the_local_liability_bound_is_refused_before_any_reservation_or_payment(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope, bound=_service_cap(envelope) - 1)
    assert (caught.value.code, caught.value.dispatch_state) == ("quote_exceeds_local_liability_bound", tp.DISPATCH_NOT_SENT)
    assert caught.value.provider_evidence["quote_amount_atomic"] == _service_cap(envelope)
    assert monetary.calls == [] and payment.calls == []
    assert len(service.requests_to(OPENAI_PATH)) == 1
    assert x402_journal.get(envelope.operation_id)["state"] == tp.X402_QUOTE_REFUSED


def test_an_absurd_quoted_amount_is_compared_exactly_and_refused(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    service.faults["quote_mutator"] = lambda quote: {**quote, "accepts": [{**quote["accepts"][0], "amount_microunits": 10**30}]}
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert caught.value.code == "quote_exceeds_local_liability_bound"
    assert caught.value.provider_evidence["quote_amount_atomic"] == 10**30
    assert monetary.calls == [] and payment.calls == []


def test_without_a_monetary_authority_nothing_is_reserved_or_paid(usepod_service, x402_journal) -> None:
    service, client, _monetary, payment = _x402(usepod_service, x402_journal, monetary=None)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("monetary_authority_unavailable", tp.DISPATCH_NOT_SENT)
    assert caught.value.provider_evidence["monetary_authority"] == "unavailable:monetary_authority_not_integrated"
    assert payment.calls == []
    assert x402_journal.get(envelope.operation_id)["last_code"] == "monetary_authority_unavailable"


def test_without_a_wallet_authority_nothing_is_reserved(usepod_service, x402_journal) -> None:
    service, client, monetary, _payment = _x402(usepod_service, x402_journal, payment=None)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("wallet_payment_authority_unavailable", tp.DISPATCH_NOT_SENT)
    assert caught.value.provider_evidence["payment_authority"] == "unavailable:wallet_payment_authority_not_integrated"
    assert monetary.calls == []
    assert len(service.requests_to(OPENAI_PATH)) == 1
    assert x402_journal.get(envelope.operation_id)["last_code"] == "wallet_payment_authority_unavailable"


def test_a_quote_on_a_network_the_wallet_authority_has_not_verified_is_refused_before_any_reservation(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    elsewhere = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
    service.faults["quote_mutator"] = lambda quote: {**quote, "accepts": [{**option, "network": elsewhere} for option in quote["accepts"]]}
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("quote_network_not_verified_by_payment_authority", tp.DISPATCH_NOT_SENT)
    assert caught.value.provider_evidence["payment_authority_networks"] == list(payment.networks)
    assert {option["network"] for option in caught.value.provider_evidence["quote"]["options"]} == {elsewhere}
    assert monetary.calls == [] and payment.calls == []


def test_a_wallet_refusal_releases_the_reservation(usepod_service, x402_journal) -> None:
    service, client, monetary, _payment = _x402(usepod_service, x402_journal, refuse="wallet_funds_insufficient")
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("wallet_funds_insufficient", tp.DISPATCH_NOT_SENT)
    assert monetary.names() == ["reserve", "release_unsent"]
    assert x402_journal.get(envelope.operation_id)["state"] == tp.X402_QUOTE_REFUSED


def test_a_wallet_failure_of_unknown_outcome_is_retained_and_blocks_a_second_payment_for_the_same_bytes(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal, raise_unknown=True)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("payment_authority_outcome_unknown", tp.DISPATCH_OUTCOME_UNKNOWN)
    assert monetary.names() == ["reserve", "retain_unknown"]
    assert monetary.calls[-1][1].outcome == OUTCOME_UNKNOWN
    assert x402_journal.get(envelope.operation_id)["state"] == tp.X402_OUTCOME_UNKNOWN

    again = _seal(service.origin, mode=TransportMode.X402)
    assert again.binding_sha256 == envelope.binding_sha256 and again.operation_id != envelope.operation_id
    with pytest.raises(tp.X402OperationStateError) as refused:
        _execute(client, service, again)
    assert refused.value.code == "identical_request_has_unresolved_paid_operation"
    assert len(service.requests_to(OPENAI_PATH)) == 1 and len(payment.calls) == 1


PROOF_TAMPERING = [
    pytest.param(lambda proof: dataclasses.replace(proof, envelope_binding_sha256="0" * 64), "proof_bound_to_different_request_bytes", id="other-bytes"),
    pytest.param(lambda proof: dataclasses.replace(proof, quote_id="quote-for-another-request"), "proof_bound_to_another_quote", id="other-quote"),
    pytest.param(lambda proof: dataclasses.replace(proof, operation_id="upo_" + "f" * 32), "proof_bound_to_another_operation", id="other-operation"),
    pytest.param(lambda proof: dataclasses.replace(proof, amount_atomic=proof.amount_atomic - 1), "proof_does_not_match_a_quoted_option", id="underpaid-amount"),
    pytest.param(lambda proof: dataclasses.replace(proof, pay_to="9Other" + "2" * 38), "proof_does_not_match_a_quoted_option", id="other-recipient"),
    pytest.param(lambda proof: dataclasses.replace(proof, payer_wallet="0x52908400098527886E0F7030069857D2E4169EE7"), "payer_wallet_malformed", id="non-base58-payer"),
    pytest.param(lambda proof: dataclasses.replace(proof, signature="0OIl" * 22), "signature_malformed", id="non-base58-signature"),
    pytest.param(lambda proof: dataclasses.replace(proof, authority_label="unavailable:wallet"), "proof_without_a_payment_authority", id="unavailable-label"),
]


@pytest.mark.parametrize(("tamper", "code"), PROOF_TAMPERING)
def test_a_proof_bound_to_anything_else_is_never_sent_and_the_payment_is_retained(usepod_service, x402_journal, tamper, code) -> None:
    service, client, monetary, _payment = _x402(usepod_service, x402_journal, mutate_proof=tamper)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == (code, tp.DISPATCH_OUTCOME_UNKNOWN)
    assert monetary.names() == ["reserve", "retain_unknown"]
    retained = monetary.calls[-1][1]
    assert (retained.outcome, retained.detail) == (OUTCOME_UNKNOWN, code)
    assert len(service.requests_to(OPENAI_PATH)) == 1
    assert x402_journal.get(envelope.operation_id)["state"] == tp.X402_OUTCOME_UNKNOWN


def test_a_paid_retry_that_times_out_is_retained_and_only_the_same_proof_and_bytes_may_be_resent(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    service.faults["paid_retry_delay_seconds"] = 4
    prompt = "reverse the word 'lantern-904'"
    envelope = _seal(service.origin, mode=TransportMode.X402, prompt=prompt)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope, timeout=1.5)
    assert (caught.value.code, caught.value.dispatch_state) == ("transfer_ended_without_response", tp.DISPATCH_OUTCOME_UNKNOWN)
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["paid_attempts"]) == (tp.X402_OUTCOME_UNKNOWN, 1)
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    [issued] = payment.issued
    assert (monetary.calls[-1][1].outcome, dict(monetary.calls[-1][1].route)) == (OUTCOME_UNKNOWN, {"quote_id": issued["quote"].quote_id})

    # While that payment's outcome is unknown, the same bytes cannot be quoted (and so paid) again.
    with pytest.raises(tp.X402OperationStateError) as refused:
        _execute(client, service, _seal(service.origin, mode=TransportMode.X402, prompt=prompt))
    assert refused.value.code == "identical_request_has_unresolved_paid_operation"

    resend = {
        "origin": service.origin,
        "quote": issued["quote"],
        "option": issued["option"],
        "proof": issued["proof"],
        "reservation": issued["reservation"],
        "read_timeout_seconds": 15,
    }
    altered = _seal(service.origin, mode=TransportMode.X402, prompt="reverse the word 'lantern-905'", operation_id=envelope.operation_id)
    with pytest.raises(tp.X402OperationStateError) as mismatch:
        client.resend_paid_retry(altered, **resend)
    assert mismatch.value.code == "resend_bytes_differ_from_paid_request"
    with pytest.raises(tp.X402OperationStateError) as other_proof:
        client.resend_paid_retry(envelope, **{**resend, "proof": dataclasses.replace(issued["proof"], signature=synthetic_signature("a-second-payment"))})
    assert other_proof.value.code == "resend_proof_differs_from_recorded_payment"
    assert len(service.requests_to(OPENAI_PATH)) == 2

    # The same proof and bytes may be re-sent. The service settled that signature on the attempt that
    # timed out, so the replay is a conflict: the payment stays retained, never refunded by assumption.
    service.faults.pop("paid_retry_delay_seconds")
    with pytest.raises(tp.UsePodTransportError) as replay:
        client.resend_paid_retry(envelope, **resend)
    assert (replay.value.code, replay.value.http_status) == ("request_conflict", 409)
    assert replay.value.provider_evidence["payment_retained"] is True
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["paid_attempts"]) == (tp.X402_PAID_RETRY_FAILED, 2)
    assert monetary.calls[-1][0] == "retain_unknown" and monetary.calls[-1][1].outcome == OUTCOME_FAILED_AFTER_SEND

    with pytest.raises(tp.X402OperationStateError) as exhausted:
        client.resend_paid_retry(envelope, **resend)
    assert exhausted.value.code == "paid_retry_attempts_exhausted"

    arrived = service.requests_to(OPENAI_PATH)
    assert len(arrived) == 3 and {item["body_sha256"] for item in arrived} == {envelope.body_sha256}
    assert len(payment.calls) == 1


class _ServesWithoutPayment(StrictUsePodService):
    """SYNTHETIC hostile variant: answers the accountless path without asking for payment."""

    def _x402(self, handler, path, headers, raw, protocol):
        body = json.loads(raw.decode("utf-8"))
        return self._send_json(handler, 200, self._openai_body(body, self.reply(body, protocol)))


def test_an_accountless_request_served_without_payment_is_refused_as_evidence_of_nothing(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal, cls=_ServesWithoutPayment)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.http_status) == ("x402_unpaid_request_was_served", 200)
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["last_code"]) == (tp.X402_QUOTE_REFUSED, "unpaid_request_was_served")
    assert monetary.calls == [] and payment.calls == []


HOSTILE_QUOTES = [
    pytest.param({"raw_payment_required_header": "%%%not-base64%%%"}, "quote_header_not_base64", id="not-base64"),
    pytest.param({"raw_payment_required_header": base64.b64encode(b"<html>pay here</html>").decode()}, "quote_not_json", id="not-json"),
    pytest.param({"raw_payment_required_header": base64.b64encode(b"[1, 2]").decode()}, "quote_not_an_object", id="json-array"),
    pytest.param({"raw_payment_required_header": "QUFB" * 5000}, "quote_header_too_large", id="oversized"),
    pytest.param({"omit_payment_required_header": True}, "quote_header_missing", id="missing"),
    pytest.param({"quote_mutator": lambda q: {k: v for k, v in q.items() if k != "x402_version"}}, "quote_version_missing", id="version-missing"),
    pytest.param({"quote_mutator": lambda q: {**q, "x402_version": True}}, "quote_version_missing", id="version-boolean"),
    pytest.param({"quote_mutator": lambda q: {**q, "x402_version": 1}}, "quote_version_unsupported", id="version-one"),
    pytest.param({"quote_mutator": lambda q: {**q, "quote_id": "has spaces; and more"}}, "quote_id_invalid", id="quote-id-invalid"),
    pytest.param({"quote_mutator": lambda q: {**q, "accepts": []}}, "quote_has_no_payment_options", id="no-options"),
]


@pytest.mark.parametrize(("faults", "code"), HOSTILE_QUOTES)
def test_a_hostile_quote_is_refused_before_any_reservation(usepod_service, x402_journal, faults, code) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    service.faults.update(faults)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.X402QuoteError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.http_status) == (code, 402)
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["last_code"]) == (tp.X402_QUOTE_REFUSED, code)
    assert monetary.calls == [] and payment.calls == []


def _usdc_option(**changes):
    return lambda quote: {**quote, "accepts": [{**quote["accepts"][0], **changes}, quote["accepts"][1]]}


UNUSABLE_USDC_OPTIONS = [
    pytest.param({"amount_microunits": 0.5}, "amount_not_integer_atomic_units", id="fractional-amount"),
    pytest.param({"amount_microunits": "103"}, "amount_not_integer_atomic_units", id="string-amount"),
    pytest.param({"amount_microunits": True}, "amount_not_integer_atomic_units", id="boolean-amount"),
    pytest.param({"amount_microunits": -4}, "amount_not_positive", id="negative-amount"),
    pytest.param({"asset": "USDT"}, "asset_unrecognized", id="unrecognized-asset"),
    pytest.param({"scheme": "upto"}, "scheme_unrecognized", id="unrecognized-scheme"),
    pytest.param({"network": "solana"}, "network_malformed", id="network-without-reference"),
    pytest.param({"pay_to": "0x52908400098527886E0F7030069857D2E4169EE7"}, "pay_to_malformed", id="non-solana-recipient"),
    pytest.param({"mode": "prepay-everything"}, "mode_unrecognized", id="unrecognized-mode"),
]


@pytest.mark.parametrize(("changes", "problem"), UNUSABLE_USDC_OPTIONS)
def test_an_unusable_usdc_option_is_not_paid_even_when_another_asset_is_usable(usepod_service, x402_journal, changes, problem) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    service.faults["quote_mutator"] = _usdc_option(**changes)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.dispatch_state) == ("no_permitted_payment_option", tp.DISPATCH_NOT_SENT)
    usdc, sol = caught.value.provider_evidence["quote"]["options"]
    assert problem in usdc["problems"] and sol["problems"] == []
    assert monetary.calls == [] and payment.calls == []


def test_a_bound_in_one_unit_is_never_compared_with_an_amount_in_another(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope, assets=("SOL",))
    assert (caught.value.code, caught.value.detail) == ("no_local_liability_bound_for_asset", "SOL")
    assert monetary.calls == [] and payment.calls == []


def test_a_quote_request_that_redirects_is_refused_and_nothing_is_paid(usepod_service, x402_journal) -> None:
    service, client, monetary, payment = _x402(usepod_service, x402_journal)
    service.faults["redirect_location"] = "https://checkout.example.test/pay?next=/proxy/x402/v1/chat/completions"
    envelope = _seal(service.origin, mode=TransportMode.X402)
    with pytest.raises(tp.UsePodTransportError) as caught:
        _execute(client, service, envelope)
    assert (caught.value.code, caught.value.http_status) == ("redirect_refused", 302)
    assert "location_host=checkout.example.test" in caught.value.detail and "cross_origin=true" in caught.value.detail
    row = x402_journal.get(envelope.operation_id)
    assert (row["state"], row["last_code"]) == (tp.X402_QUOTE_REFUSED, "redirect_refused")
    assert monetary.calls == [] and payment.calls == []


def test_the_journal_refuses_illegal_transitions_foreign_fields_and_reused_operation_ids(x402_journal) -> None:
    envelope = _seal("https://api.usepod.ai", mode=TransportMode.X402)
    assert x402_journal.open(envelope)["state"] == tp.X402_CREATED
    assert x402_journal.open(envelope)["operation_id"] == envelope.operation_id
    with pytest.raises(tp.X402OperationStateError) as illegal:
        x402_journal.transition(envelope.operation_id, tp.X402_COMPLETED)
    assert illegal.value.code == "transition_not_allowed"
    with pytest.raises(tp.X402OperationStateError) as foreign:
        x402_journal.transition(envelope.operation_id, tp.X402_QUOTE_REQUESTED, body=envelope.body)
    assert foreign.value.code == "journal_field_not_allowed"
    reused = _seal("https://api.usepod.ai", mode=TransportMode.X402, prompt="a different request", operation_id=envelope.operation_id)
    with pytest.raises(tp.X402OperationStateError) as clash:
        x402_journal.open(reused)
    assert clash.value.code == "operation_id_reused_for_different_request"
    with pytest.raises(tp.X402OperationStateError) as prepaid:
        x402_journal.open(_seal("https://api.usepod.ai"))
    assert prepaid.value.code == "not_an_x402_envelope"


@pytest.mark.parametrize(
    ("value", "state"),
    [(None, "absent"), ("", "absent"), ("%%%", "undecodable"), (base64.b64encode(b"[1]").decode(), "not_an_object")],
)
def test_an_unpublished_payment_receipt_is_kept_as_evidence_and_nothing_is_inferred(value, state) -> None:
    assert tp.decode_payment_response(value)["state"] == state


def test_a_decoded_payment_receipt_keeps_scalars_and_drops_structures() -> None:
    value = base64.b64encode(json.dumps({"quote_id": "q1", "charged_microunits": 40, "nested": {"x": 1}, "rate": 0.5}).encode()).decode()
    decoded = tp.decode_payment_response(value)
    assert decoded["fields"] == {"quote_id": "q1", "charged_microunits": 40, "rate": "0.5"}
