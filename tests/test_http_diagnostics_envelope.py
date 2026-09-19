"""Stable application diagnostics: the vool.* envelope and the HTTP seams that use it.

NEW regressions for the diagnostics-reporting goal. These exercise failure paths the
base had no vocabulary for:

- a namespaced condition code + plain message + recovery + retryability + correlation
  per error response, derived from the EXISTING fault catalog (provider, tool/retrieval,
  artifact/publication, wallet refusal, unknown-exception families) -- the base had the
  catalog but never surfaced it at any HTTP boundary;
- budget/price pin refusals mapped from their structured leading machine reason instead
  of prose substring sniffing;
- transport failures mapped from typed dispatch facts: not-sent is a 400 (the base
  fabricated a 502 "transport" error for a request that never left the machine), a
  genuine x402 challenge keeps HTTP 402 (the base masked it as 502), an upstream 429
  surfaces as 429, an unanswered send is 504, an upstream refusal stays 502 with the
  real upstream status carried as data.
"""
from __future__ import annotations

import pytest

from core.web.api import diagnostics as diag

# --- the closed request/upstream vocabulary ------------------------------------------

def test_request_conditions_are_unique_and_complete() -> None:
    codes = diag.condition_codes()
    assert len(codes) == len(set(codes))
    for expected in (
        "vool.request.invalid", "vool.request.unauthenticated", "vool.request.forbidden",
        "vool.request.not_found", "vool.request.conflict", "vool.request.throttled",
        "vool.upstream.payment_challenge", "vool.upstream.refused",
        "vool.upstream.failed", "vool.upstream.timeout", "vool.upstream.unreachable",
        "vool.model.paid_confirm_required", "vool.model.cost_unknown",
    ):
        assert expected in codes, expected


def test_envelope_carries_the_full_contract() -> None:
    envelope = diag.diagnostic_envelope(
        "vool.request.conflict",
        detail="payload hash differs",
        correlation_id="req-123",
        turn_key="turn-abc",
        http_status=409,
        upstream_status=422,
    )
    assert envelope["schema"] == diag.DIAGNOSTIC_SCHEMA
    assert envelope["code"] == "vool.request.conflict"
    assert envelope["message"]  # plain words, from the registry
    assert envelope["recovery"]  # suggested next step
    assert envelope["retryable"] is False
    assert envelope["correlation_id"] == "req-123"
    assert envelope["turn_key"] == "turn-abc"
    assert envelope["http_status"] == 409
    assert envelope["upstream_status"] == 422


def test_codes_carry_no_secrets_paths_or_prompt_text() -> None:
    for code in diag.condition_codes():
        lowered = code.lower()
        for forbidden in ("token", "key", "secret", "path", "prompt", "ghp_", "sk-"):
            assert forbidden not in lowered, (code, forbidden)


# --- the six case families, mapped from the EXISTING fault catalog -------------------

def test_provider_family_maps_from_the_fault_catalog() -> None:
    from core.faults.mapping import fault_code_for_provider_error_class

    assert fault_code_for_provider_error_class("rate_limited") == "provider_exhausted"
    envelope = diag.diagnostic_envelope("provider_exhausted")
    assert envelope["code"] == "vool.provider_exhausted"
    assert envelope["message"] == "The model provider's usage limit is reached. You can try again later."
    assert envelope["recovery"]
    assert envelope["retryable"] is True  # catalog retry policy: retry_later

    unreachable = diag.diagnostic_envelope("provider_unavailable")
    assert unreachable["code"] == "vool.provider_unavailable"
    assert unreachable["retryable"] is True


def test_budget_price_family_maps_model_pin_refusals_from_the_machine_reason() -> None:
    # The documented structured contract: the machine reason rides at the START of the
    # refusal message. The base chose this endpoint's status by sniffing "local session"
    # prose instead.
    mapped = diag.model_pin_refusal("PAID_MODEL_CONFIRM_REQUIRED: That model is PAID per the catalog. Confirm the pin.")
    assert mapped == (400, "vool.model.paid_confirm_required")
    unknown_cost = diag.model_pin_refusal("MODEL_COST_UNKNOWN: The catalog could not classify what this model costs.")
    assert unknown_cost == (400, "vool.model.cost_unknown")
    # prose without a machine reason is not guessed at
    assert diag.model_pin_refusal("I could not save the model choice just now.") is None


def test_budget_price_family_maps_the_wallet_x402_cap() -> None:
    envelope = diag.diagnostic_envelope("wallet_x402_cap_exceeded")
    assert envelope["code"] == "vool.wallet_x402_cap_exceeded"
    assert "was not paid" in envelope["message"]
    assert envelope["retryable"] is False  # catalog: retry_never -- raise the cap deliberately


def test_tool_retrieval_family_maps_tool_and_permission_codes() -> None:
    tool = diag.diagnostic_envelope("tool_unavailable")
    assert tool["code"] == "vool.tool_unavailable"
    assert tool["retryable"] is False  # retry_after_change: enabling the tool comes first
    permission = diag.diagnostic_envelope("permission_denied")
    assert permission["code"] == "vool.permission_denied"
    assert permission["retryable"] is False


def test_artifact_publication_family_maps_integrity_codes() -> None:
    integrity = diag.diagnostic_envelope("integrity_verification_failure")
    assert integrity["code"] == "vool.integrity_verification_failure"
    assert "tamper-evidence" in integrity["recovery"] or "compromised" in integrity["recovery"]


def test_wallet_refusal_family_maps_wallet_codes() -> None:
    wallet = diag.diagnostic_envelope("wallet_outbound_refused")
    assert wallet["code"] == "vool.wallet_outbound_refused"
    assert "no connection was made" in wallet["message"]
    disabled = diag.diagnostic_envelope("wallet_disabled")
    assert disabled["code"] == "vool.wallet_disabled"
    assert disabled["retryable"] is False


def test_generic_unexpected_exception_maps_to_unknown_never_blind_retry() -> None:
    from core.faults.mapping import map_exception

    record = map_exception(
        RuntimeError("socket exploded somewhere"),
        authority="core.faults.mapping",
        turn_key="turn-envelope-test",
    )
    envelope = diag.diagnostic_envelope(record.code)
    assert envelope["code"] == "vool.unknown"
    assert envelope["retryable"] is False


def test_every_fault_catalog_code_is_addressable() -> None:
    from core.faults.catalog import all_codes

    for code in all_codes():
        envelope = diag.diagnostic_envelope(code)
        assert envelope["code"] == f"vool.{code}"
        assert envelope["message"], code
        assert envelope["recovery"], code


# --- transport facts -> standard statuses --------------------------------------------

def test_gateway_condition_maps_typed_dispatch_facts() -> None:
    # a request that never left is NOT a transport failure -- the base fabricated 502
    assert diag.gateway_condition("not_sent", None) == (400, "vool.request.invalid")
    # a genuine x402 payment challenge keeps its protocol status -- the base masked 402 as 502
    assert diag.gateway_condition("response_received", 402) == (402, "vool.upstream.payment_challenge")
    assert diag.gateway_condition("response_received", 429) == (429, "vool.upstream.throttled")
    # sent but unanswered: gateway timeout with unknown outcome, never a success guess
    assert diag.gateway_condition("sent_outcome_unknown", None) == (504, "vool.upstream.timeout")
    # upstream answered and failed/refused: 502 with the REAL status as data
    assert diag.gateway_condition("response_received", 500) == (502, "vool.upstream.failed")
    assert diag.gateway_condition("response_received", 409) == (502, "vool.upstream.refused")


# --- the resume endpoint: statuses from typed facts, legacy payload keys preserved ----

def _resume_call(monkeypatch, exc) -> tuple[int, dict]:
    import adapters.usepod_adapter as adapter
    from core.web.api.service import _usepod_owner_action

    def raise_it(operation_id):
        raise exc

    monkeypatch.setattr(adapter, "resume_x402_operation", raise_it)
    return _usepod_owner_action("/api/cloud/usepod/x402/resume", {"operation_id": "op-1"}, {})


def test_resume_refused_before_sending_is_400_not_a_fake_transport_error(monkeypatch) -> None:
    from core.usepod.transport import UsePodTransportError

    status, payload = _resume_call(
        monkeypatch,
        UsePodTransportError("payload_not_an_object", dispatch_state="not_sent"),
    )
    assert status == 400
    assert payload["diagnostic"]["code"] == "vool.request.invalid"
    assert "refused before anything was sent" in payload["error"]


def test_resume_preserves_a_genuine_x402_challenge_as_http_402(monkeypatch) -> None:
    from core.usepod.transport import X402QuoteError

    status, payload = _resume_call(monkeypatch, X402QuoteError("quote_unsigned"))
    assert status == 402
    assert payload["diagnostic"]["code"] == "vool.upstream.payment_challenge"
    assert payload["http_status"] == 402


def test_resume_upstream_throttle_surfaces_as_429(monkeypatch) -> None:
    from core.usepod.transport import UsePodTransportError

    status, payload = _resume_call(
        monkeypatch,
        UsePodTransportError("provider_rejected", dispatch_state="response_received", http_status=429),
    )
    assert status == 429
    assert payload["diagnostic"]["code"] == "vool.upstream.throttled"


def test_resume_unanswered_send_is_504_with_unknown_outcome(monkeypatch) -> None:
    from core.usepod.transport import UsePodTransportError

    status, payload = _resume_call(
        monkeypatch,
        UsePodTransportError("read_timeout", dispatch_state="sent_outcome_unknown"),
    )
    assert status == 504
    assert payload["diagnostic"]["code"] == "vool.upstream.timeout"
    assert "outcome is unknown" in payload["diagnostic"]["message"]


def test_resume_upstream_refusal_keeps_502_and_the_real_status_as_data(monkeypatch) -> None:
    """The served contract pins (502, payload.http_status == 409): the response's own
    status is the gateway's, and the upstream's REAL status rides as data."""
    from core.usepod.transport import UsePodTransportError

    status, payload = _resume_call(
        monkeypatch,
        UsePodTransportError("provider_rejected", dispatch_state="response_received", http_status=409),
    )
    assert status == 502
    assert payload["http_status"] == 409
    assert payload["diagnostic"]["code"] == "vool.upstream.refused"
    assert payload["diagnostic"]["upstream_status"] == 409
