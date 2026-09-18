"""UsePod on VOOL's primary adapter hierarchy.

Everything a turn sees stays what every OpenAI-compatible lane gives it -- the same payload assembly
(egress projection, native tool schemas, output budget), the same readers for text and tool calls, the
same typed failures -- because this adapter inherits them from ``OpenAICompatibleAdapter`` and changes
only what UsePod actually changes:

* **Where the request goes and how it authenticates.** A prepaid call goes to ``/proxy/<token>/...``
  with no ``Authorization`` header; an accountless call goes to ``/proxy/x402/...`` with no token.
  The token comes from the credential store at dispatch time and never enters the manifest.
* **The dialect.** ``runtime_config.protocol`` selects the OpenAI or Anthropic Messages wire format;
  the Anthropic side is translated by ``core.anthropic_messages_protocol`` from, and back into, the
  one OpenAI-shaped payload, so tools and validation are shared rather than re-implemented.
* **What must be true before a byte leaves.** An owner-approved route bound, re-checked against a
  fresh price snapshot; a route-trust decision; an exact liability bound; a reservation from the
  monetary authority (unavailable until integrated -- then nothing is sent); one sealed envelope.
* **What is kept afterwards.** The route UsePod reports, whether it was permitted, usage priced at
  the approved ceiling as an UPPER bound (UsePod supplies no exact charge), and a settlement,
  retained-unknown or proven-unsent record for the monetary authority. A response from a route the
  approval did not permit, or with no route evidence, is not returned as a compliant answer.
"""
from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from adapters.base_adapter import ModelRequest, ModelResponse, ModelStreamChunk
from adapters.openai_compatible_adapter import (
    OpenAICompatibleAdapter,
    _extract_native_openai_tool_result,
    _extract_openai_text,
    _extract_stream_delta_text,
    _parse_stream_line,
    _repaired_tool_call_from_payload,
)
from core.anthropic_messages_protocol import (
    AnthropicStreamAssembler,
    AnthropicStreamError,
    IncompleteStreamError,
    ProtocolTranslationError,
    anthropic_response_to_openai,
    openai_request_to_anthropic,
)
from core.cloud_provider_contract import CloudToolCall, CloudToolDefinition
from core.cloud_tool_call_contract import MalformedToolArgumentsError, ToolCallParseError
from core.execution_requirements import assert_envelope_carries_tools
from core.model_output_guard import scrub_foreign_markers, strip_reasoning_block
from core.normalized_provider_result import EmptyProviderResponseError, MalformedProviderResponseError
from core.provider_invocation_gateway import payload_hash, seal_provider_invocation
from core.provider_verification import response_body_facts, response_header_facts
from core.secret_redaction import redact_secrets
from core.usepod import descriptor, pricing, routing, trust
from core.usepod.discovery import ResolvedCredential, configured_origin, resolve_credential
from core.usepod.monetary import (
    ACCOUNT_KIND_PREPAID_TOKEN,
    ASSET_USDC,
    EXACT_COST_NOT_SUPPLIED,
    NETWORK_USEPOD_ACCOUNT,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED_AFTER_SEND,
    OUTCOME_PARTIAL_STREAM,
    OUTCOME_ROUTE_POLICY_VIOLATED,
    OUTCOME_ROUTE_UNVERIFIED,
    OUTCOME_UNKNOWN,
    UNIT_USDC_MICROUNIT,
    MonetaryAuthorityRefusedError,
    MonetaryAuthorityUnavailableError,
    MonetaryReservation,
    ProviderLiability,
    SettlementEvidence,
    monetary_authority,
)
from core.usepod.transport import (
    DISPATCH_NOT_SENT,
    RequestEnvelope,
    UsePodHttpTransport,
    UsePodResponse,
    UsePodTransportError,
    UsePodX402Client,
    X402OperationStateError,
    X402PaidExchange,
    decode_payment_response,
    error_for_response,
    seal_request_envelope,
)

EVIDENCE_SCHEMA = "vool.usepod.call_evidence.v1"
RECEIPT_SCHEMA = "vool.usepod.receipt.v1"
COST_BASIS_USAGE_AT_CEILING = "usepod_reported_usage_at_approved_ceiling"
COST_BASIS_LIABILITY_BOUND = "usepod_liability_bound_usage_not_reported"
_USD_PER_USDC_DECLARATION = "USDC counted 1:1 as USD for the legacy USD spend cap; no FX rate was observed"
_CERTIFICATION_REFUSAL = (
    "tool certification is restricted to a loopback model endpoint; a UsePod lane is a paid remote marketplace "
    "even when a local service stands in for its origin"
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


#: What happened to the inference itself, judged apart from the payment and from any provider credit.
_INFERENCE_BY_STATUS = {
    "completed": "completed",
    OUTCOME_PARTIAL_STREAM: "incomplete",
    OUTCOME_ROUTE_POLICY_VIOLATED: "withheld_route_not_approved",
    OUTCOME_ROUTE_UNVERIFIED: "withheld_route_unverified",
    "response_unusable": "answer_unusable",
    "failed_after_send": "failed",
    "refused_before_send": "not_sent",
}


def _inference_outcome(evidence: dict[str, Any], settlement: dict[str, Any]) -> dict[str, Any]:
    status = str(evidence.get("status") or "")
    recording = str(evidence.get("settlement_recording") or "")
    state = _INFERENCE_BY_STATUS.get(status) or (
        "result_unknown" if recording == "retained_unknown" else "not_sent" if recording == "released_unsent" else "failed"
    )
    return {"state": state, "finish_reason": str(evidence.get("finish_reason") or "") or None, "output_tokens": settlement.get("output_tokens")}


def _named_surplus_credit(decoded_receipt: Any) -> int | None:
    """The positive integer surplus an x402 PAYMENT-RESPONSE names, or None: nothing is inferred from an absent,
    undecodable, zero, negative, boolean or non-integer value."""
    fields = _mapping(_mapping(decoded_receipt).get("fields"))
    surplus = fields.get("surplus_credited_microunits")
    if isinstance(surplus, bool) or not isinstance(surplus, int) or surplus <= 0:
        return None
    return int(surplus)


_BALANCE_OBSERVATION_FIELDS = ("state", "usdc_balance_microunits", "observed_at", "age_seconds", "evidence", "http_status", "error_code")


def _public_balance_observation(observation: Any) -> dict[str, Any] | None:
    """The bounded, secret-free view of a pre-dispatch balance observation (no fingerprint, no note)."""
    if not isinstance(observation, dict) or not observation:
        return None
    return {key: observation.get(key) for key in _BALANCE_OBSERVATION_FIELDS if key in observation}


def _balance_verified(liability: dict[str, Any]) -> dict[str, Any] | None:
    """What the adapter knew about the account balance BEFORE reserving: the observation the ONE balance
    door returned at the dispatch boundary. ``state`` is ``reported`` only for a provider-answered
    integer balance; anything else names why the balance was unverified. None for a call that never
    reached the prepaid reservation (an x402 call, or a refusal before the liability existed)."""
    return _public_balance_observation(_mapping(liability.get("basis")).get("balance_observation"))


def _provider_credit(settlement: dict[str, Any], *, x402_lane: bool) -> dict[str, Any]:
    """Credit the provider reported on its own account for this call. It is never a wallet refund."""
    credit = settlement.get("provider_credit_atomic")
    counted = isinstance(credit, int) and not isinstance(credit, bool) and credit > 0
    return {
        "state": "credited" if counted else ("not_reported_by_provider" if x402_lane else "not_applicable"),
        "atomic": credit if counted else None,
        "unit": UNIT_USDC_MICROUNIT,
        "account": (str(settlement.get("provider_credit_account") or "") or None) if counted else None,
    }


def call_receipt(evidence: dict[str, Any]) -> dict[str, Any]:
    """The bounded, secret-free receipt for one UsePod call: what routing events and ledgers carry.

    Built only from evidence already recorded. A value the provider did not report stays ``None`` with a
    state saying so: a missing ``X-Balance-Remaining`` is ``not_reported``, never a zero balance, and an
    absent exact charge is ``not_supplied_by_provider``, never the upper bound restated as a bill.
    """
    route = _mapping(evidence.get("route"))
    settlement = _mapping(evidence.get("settlement"))
    liability = _mapping(evidence.get("liability"))
    reservation = _mapping(evidence.get("reservation"))
    policy = _mapping(evidence.get("route_policy"))
    transport_side = _mapping(evidence.get("transport_evidence"))
    balance_raw = route.get("balance_remaining_raw")
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "provider": descriptor.PROVIDER_ID,
        "status": str(evidence.get("status") or ""),
        "code": str(evidence.get("code") or evidence.get("error_code") or ""),
        "operation_id": str(evidence.get("operation_id") or ""),
        "transport_mode": str(evidence.get("transport_mode") or ""),
        "protocol": str(evidence.get("protocol") or ""),
        "endpoint": str(evidence.get("endpoint") or ""),
        "credential_fingerprint": str(evidence.get("credential_fingerprint") or ""),
        "model": str(evidence.get("resolved_model") or ""),
        "provider_attested_model": evidence.get("provider_attested_model"),
        "route_approval_id": str(policy.get("approval_id") or ""),
        "price_snapshot_sha256": str(_mapping(policy.get("price_source")).get("snapshot_sha256") or ""),
        "reservation_id": str(reservation.get("reservation_id") or ""),
        "routing_mode": str(policy.get("routing_mode") or ""),
        # route ceilings and usage estimates are priced in USDC microunits, whatever asset paid
        "pricing_unit": UNIT_USDC_MICROUNIT,
        "price_ceiling_microunits_per_million": (
            {"input": policy.get("max_input_microunits_per_million"), "output": policy.get("max_output_microunits_per_million")} if policy else None
        ),
        "route": (
            {
                "reported": route.get("route_raw"),
                "class": route.get("route_class"),
                "provider_id": route.get("provider_id"),
                "compliance": route.get("compliance"),
                "reasons": list(route.get("reasons") or []),
                "fallback_used": bool(route.get("fallback_used")),
                "verification": route.get("route_verification"),
            }
            if route
            else None
        ),
        "balance_remaining": {
            "state": "reported" if balance_raw is not None else "not_reported",
            "raw": balance_raw,
            "decimal": route.get("balance_remaining_decimal"),
            "unit": route.get("balance_unit"),
        },
        # The balance VERIFIED before sending (a read of /proxy/<token>/balance the reservation was
        # judged against), kept apart from the balance the provider REPORTED after answering above.
        "balance_verified": _balance_verified(liability),
        "cost": {
            "asset": str(liability.get("asset") or ASSET_USDC),
            "unit": str(liability.get("unit") or UNIT_USDC_MICROUNIT),
            "liability_bound_atomic": liability.get("max_amount_atomic"),
            "usage_upper_bound_atomic": settlement.get("upper_bound_cost_atomic"),
            "exact_atomic": settlement.get("exact_cost_atomic"),
            "exact_state": str(settlement.get("exact_cost_state") or EXACT_COST_NOT_SUPPLIED),
        },
        "usage": {"input_tokens": settlement.get("input_tokens"), "output_tokens": settlement.get("output_tokens")},
        **({"monetary_refusal_detail": str(evidence.get("monetary_refusal_detail") or "")[:400]} if evidence.get("monetary_refusal_detail") else {}),
        "settlement": {"outcome": settlement.get("outcome"), "recording": str(evidence.get("settlement_recording") or "")},
        "monetary_authority": str(
            reservation.get("authority_label") or evidence.get("monetary_authority") or transport_side.get("monetary_authority") or ""
        ),
    }
    x402 = _mapping(evidence.get("x402"))
    if x402 or "payment_retained" in transport_side:
        # a completed call carries its bound proof; a paid call whose answer was lost carries the same public facts
        proof = _mapping(x402.get("proof")) or _mapping(transport_side.get("x402_payment"))
        option = _mapping(x402.get("paid_option"))
        receipt["x402"] = {
            "quote_id": _mapping(x402.get("quote")).get("quote_id") or proof.get("quote_id"),
            "network": proof.get("network") or option.get("network"),
            "payment_signature": proof.get("signature"),
            "network_fee_state": "owned_by_wallet_authority_not_observed_by_provider_transport",
            "wallet_outflow_atomic": x402.get("wallet_outflow_atomic", proof.get("amount_atomic")),
            "payment_authority": proof.get("authority_label") or transport_side.get("payment_authority"),
            "payment_response_state": _mapping(x402.get("payment_response")).get("state"),
            # the receipt's own scalar fields, kept as evidence: its schema is unpublished, so nothing here is a bill
            "payment_response_fields": _mapping(_mapping(x402.get("payment_response")).get("fields")) or None,
            "paid_attempts": x402.get("paid_attempts", transport_side.get("paid_attempts")),
            "surplus_credit_state": x402.get("surplus_credit_state"),
            "chain_confirmation": _mapping(x402.get("chain_confirmation")) or None,
            "payment_retained": transport_side.get("payment_retained"),
            # the full destination and the exact amount, for the receipt's details
            "pay_to": proof.get("pay_to") or option.get("pay_to"),
            "payer_wallet": proof.get("payer_wallet"),
            "asset": proof.get("asset") or option.get("asset"),
            "amount_atomic": proof.get("amount_atomic", option.get("amount_atomic")),
            # the payment transaction on its own: the wallet issues a proof only for a confirmed principal
            "transaction_state": "confirmed" if proof.get("signature") else "not_recorded",
        }
    receipt["inference"] = _inference_outcome(evidence, settlement)
    receipt["provider_credit"] = _provider_credit(settlement, x402_lane=str(evidence.get("transport_mode") or "") == "x402")
    receipt["dna_fee"] = _dna_fee_receipt(evidence)
    return receipt


def _dna_fee_receipt(evidence: dict[str, Any]) -> dict[str, Any]:
    """The DNA service fee of one accountless call as a receipt block: reserved (not owed) while the outcome is
    open, accrued (owed, not collected) once the payment was accepted, and what happened to the collection of
    PREVIOUSLY accrued fees planned with this payment (collected, in flight, failed, or deferred with its reason). A
    prepaid or refused call carries no fee. Never a treasury income claim: a collection is its own transaction with
    its own receipt, under the payment's one approval."""
    fee = _mapping(evidence.get("dna_fee"))
    if not fee:
        return {"state": "not_charged", "reason": "no_native_x402_payment_with_a_bound_treasury"}
    state = str(fee.get("state") or "")
    label = {
        "reserved_not_owed": "reserved as a ceiling; owed only once the payment is accepted",
        "accrued_owed": "accrued: owed to the DNA treasury, not yet collected",
        "settled_without_accrual": "no fee accrued for this operation",
        "released_not_owed": "released: nothing was paid, nothing is owed",
    }.get(state, state)
    position = _mapping(fee.get("position"))
    block: dict[str, Any] = {
        "state": state,
        "state_label": label,
        "policy_id": fee.get("policy_id"),
        "rate_bps": fee.get("rate_bps"),
        "basis": "the provider payment",
        "basis_atomic": fee.get("basis_atomic"),
        "asset": fee.get("asset"),
        "unit": "usdc_microunit" if str(fee.get("asset") or "") == "USDC" else ("lamport" if str(fee.get("asset") or "") == "SOL" else None),
        "fee_numerator_scale": 10_000,
        "fee_numerator": fee.get("fee_numerator"),
        "fee_exact": fee.get("fee_exact"),
        "fee_exact_atomic": fee.get("fee_exact_atomic"),
        "reserved_ceiling_atomic": fee.get("reserved_ceiling_atomic"),
        "treasury_owner": fee.get("treasury_owner"),
        "reversed_numerator": fee.get("reversed_numerator"),
        "owed_after_atomic": position.get("owed_atomic"),
        "carry_after_numerator": position.get("carry_numerator"),
        "collectible_atomic": position.get("collectible_atomic"),
        "collection": _mapping(fee.get("collection")) or None,
    }
    return block


def _attach_evidence(exc: BaseException, evidence: dict[str, Any], *, error_code: str = "") -> None:
    """Put this call's evidence and receipt on a failure without discarding what the transport recorded."""
    container = _mapping(getattr(exc, "provider_evidence", None))
    transport_side = {key: value for key, value in container.items() if key not in {"usepod", "receipt"}}
    if transport_side:
        evidence.setdefault("transport_evidence", transport_side)
    if error_code:
        evidence.setdefault("error_code", error_code)
    container.setdefault("usepod", evidence)
    container.setdefault("receipt", call_receipt(container["usepod"]))
    with contextlib.suppress(Exception):
        exc.provider_evidence = container  # type: ignore[attr-defined]


class UsePodDispatchRefusedError(RuntimeError):
    """Refused BEFORE anything was sent. ``code`` is stable; evidence is redaction-safe."""

    def __init__(self, code: str, *, evidence: dict[str, Any] | None = None) -> None:
        self.code = str(code)
        body = {"schema": EVIDENCE_SCHEMA, "status": "refused_before_send", "code": self.code, **dict(evidence or {})}
        self.provider_evidence = {"usepod": body, "receipt": call_receipt(body)}
        super().__init__(f"usepod_dispatch_refused:{self.code}")


class UsePodRouteNotCompliantError(MalformedProviderResponseError):
    """The response came from a route the approval did not permit, or carried no route evidence."""

    def __init__(self, compliance: str, reasons: tuple[str, ...], *, evidence: dict[str, Any]) -> None:
        self.compliance = str(compliance)
        self.reasons = tuple(reasons)
        self.provider_evidence = {"usepod": evidence, "receipt": call_receipt(evidence)}
        super().__init__(f"usepod route policy {self.compliance}: {','.join(self.reasons) or 'no reason recorded'}")


@dataclass
class _Plan:
    mode: descriptor.TransportMode
    protocol: descriptor.WireProtocol
    origin: str
    credential: ResolvedCredential | None
    native_tools: tuple[CloudToolDefinition, ...]
    envelope: RequestEnvelope
    approval: routing.DispatchRouteApproval
    trust_decision: trust.RouteTrustDecision
    input_token_bound: int
    max_charge_atomic: int
    liability_basis: dict[str, Any]
    permit: Any
    liability: ProviderLiability | None = None
    reservation: MonetaryReservation | None = None


def _usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _has_non_text_parts(payload: dict[str, Any]) -> bool:
    for message in list(payload.get("messages") or []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") not in {"text", "tool_result", "tool_use"} for part in content):
            return True
    return False


class UsePodAdapter(OpenAICompatibleAdapter):
    """A UsePod lane. See the module docstring for what differs from the OpenAI-compatible parent."""

    def supports_streaming(self) -> bool:
        return True

    # --- configuration -------------------------------------------------------------------------------

    def _mode(self) -> descriptor.TransportMode:
        return descriptor.TransportMode(str(self.manifest.runtime_config.get("transport_mode") or descriptor.TransportMode.PREPAID.value))

    def _protocol(self) -> descriptor.WireProtocol:
        return descriptor.WireProtocol(str(self.manifest.runtime_config.get("protocol") or descriptor.WireProtocol.OPENAI.value))

    def _http_transport(self) -> UsePodHttpTransport:
        return UsePodHttpTransport()

    def validate_runtime(self) -> list[str]:
        warnings: list[str] = []
        try:
            self._mode()
            self._protocol()
        except ValueError:
            warnings.append(f"{self.manifest.provider_id}: unknown usepod transport_mode or protocol")
        try:
            descriptor.normalize_origin(str(self.manifest.runtime_config.get("base_url") or ""))
        except descriptor.UsePodConfigError as exc:
            warnings.append(f"{self.manifest.provider_id}: usepod origin invalid ({exc.code})")
        return warnings

    def health_check(self) -> dict[str, Any]:
        """Configuration only. A network probe belongs to Settings' Test, where it spends nothing."""
        problems = self.validate_runtime()
        if problems:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": problems[0]}
        if self._mode() is descriptor.TransportMode.PREPAID and resolve_credential() is None:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "usepod_token_not_configured"}
        return {"ok": True, "provider_id": self.manifest.provider_id, "status": "configured"}

    # --- nothing inherited may put the token in a header or send a request outside the sealed path -----

    def _resolve_api_key(self) -> str:
        """Never a bearer key. UsePod's credential is a URL path segment, read only at dispatch time."""
        return ""

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _uses_native_ollama_chat(self) -> bool:
        return False

    def _probe_native_tool_support(self) -> bool | None:
        """Never probed. A probe is a real UsePod request -- billable and outside the sealed dispatch path --
        even when a loopback service stands in for the origin."""
        return None

    def prewarm(self) -> dict[str, Any]:
        return {"ok": True, "provider_id": self.manifest.provider_id, "status": "skipped", "reason": "remote_marketplace_lane"}

    def tool_certification_backend_version(self) -> str:
        raise ValueError(_CERTIFICATION_REFUSAL)

    def tool_certification_runtime_identity(self) -> dict[str, str]:
        raise ValueError(_CERTIFICATION_REFUSAL)

    def tool_certification_exchange(self, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError(_CERTIFICATION_REFUSAL)

    def _read_timeout(self, request: ModelRequest) -> float:
        from core.provider_call_deadline import effective_timeout_seconds

        return effective_timeout_seconds(request, float(self.manifest.runtime_config.get("timeout_seconds") or 180.0))

    # --- the pre-dispatch authorization ---------------------------------------------------------------

    def _prepare(self, request: ModelRequest, *, force_json: bool, stream: bool) -> _Plan:
        mode = self._mode()
        protocol = self._protocol()
        credential: ResolvedCredential | None = None
        if mode is descriptor.TransportMode.PREPAID:
            from core.usepod.discovery import UsePodCredentialPairUnavailableError

            try:
                credential = resolve_credential(strict=True)
            except UsePodCredentialPairUnavailableError:
                # The token and its origin are not one decided binding right now (a save or delete whose
                # completion is unknown, or bytes that disagree with the committed pair): nothing is sent.
                raise UsePodDispatchRefusedError(UsePodCredentialPairUnavailableError.code) from None
            if credential is None:
                raise UsePodDispatchRefusedError("usepod_token_not_configured")
            origin = credential.origin
        else:
            origin = configured_origin()
        try:
            manifest_origin = descriptor.normalize_origin(str(self.manifest.runtime_config.get("base_url") or ""))
        except descriptor.UsePodConfigError:
            manifest_origin = ""
        if manifest_origin != origin:
            # The lane was registered for another origin than the one the credential is bound to now.
            raise UsePodDispatchRefusedError("lane_origin_differs_from_credential_origin")

        payload = self._build_openai_payload(request, force_json=force_json, stream=stream)
        if (urlparse(origin).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}:
            # The parent projects for egress only toward non-loopback hosts; UsePod is a cloud
            # destination even when a local test service stands in for it.
            from core.egress_gate import project_messages_for_destination

            payload["messages"] = project_messages_for_destination(list(payload.get("messages") or []), destination_class="cloud_provider")
        max_output = payload.get("max_tokens")
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 1:
            raise UsePodDispatchRefusedError("max_output_tokens_required")
        assert_envelope_carries_tools(
            tools_required=bool(getattr(request, "tools_required", False)),
            offered_tool_count=len(request.tools or ()),
            native_tools_in_envelope=bool(payload.get("tools")),
            structured_fallback_in_envelope=bool(payload.get("response_format")),
            lane_name=self.manifest.provider_id,
        )
        native_tools = self._native_tools(request) if getattr(request, "tools", None) else ()
        if _has_non_text_parts(payload):
            # No byte bound exists for image parts, so no liability can be stated before sending.
            raise UsePodDispatchRefusedError("input_bound_unavailable_for_non_text_parts")
        if protocol is descriptor.WireProtocol.ANTHROPIC:
            try:
                wire_payload = openai_request_to_anthropic(payload)
            except ProtocolTranslationError as exc:
                raise UsePodDispatchRefusedError(f"protocol_translation:{exc.code}") from None
        else:
            wire_payload = payload

        state = routing.load_route_state()
        if state.error:
            raise UsePodDispatchRefusedError(state.error)
        model_id = str(self.manifest.model_name or "")
        snapshot_result = pricing.current_snapshot(origin=origin, allow_network=True)
        try:
            # A stale or absent snapshot is refused inside authorize_dispatch (price_stale /
            # price_feed_unavailable); it is passed through so the refusal names which one it was.
            from core.usepod.price_wait import authorize_running_dispatch
            approval = authorize_running_dispatch(state, snapshot_result.snapshot, model_id=model_id, request=request)
        except routing.RouteUnavailableError as exc:
            raise UsePodDispatchRefusedError(
                exc.code, evidence={"route_evidence": exc.evidence, "snapshot_state": snapshot_result.state, "snapshot_error": snapshot_result.error_code}
            ) from None
        decision = trust.evaluate_route_trust(
            privacy_class=str((request.metadata or {}).get("privacy_class") or ""),
            allowed_route_classes=approval.allowed_route_classes,
            grant=None,
        )
        if not decision.allowed:
            raise UsePodDispatchRefusedError(f"route_trust:{decision.reason}", evidence={"trust": decision.as_dict()})

        try:
            envelope = seal_request_envelope(
                payload=wire_payload,
                transport_mode=mode,
                protocol=protocol,
                origin=origin,
                model_id=model_id,
                route_approval_id=approval.approval_id,
                control_headers=approval.request_headers(),
                operation_id=f"upo_{uuid.uuid4().hex}",
            )
        except UsePodTransportError as exc:
            raise UsePodDispatchRefusedError(exc.code) from None
        input_bound = pricing.input_token_upper_bound(envelope.body, has_non_text_parts=False)
        assert input_bound is not None
        max_charge = pricing.charge_upper_bound_microunits(
            input_tokens=input_bound,
            output_tokens=envelope.max_output_tokens,
            input_rate=approval.max_input_microunits_per_million,
            output_rate=approval.max_output_microunits_per_million,
        )
        basis = {
            "rule": "ceil((input_token_upper_bound * max_input_price + max_output_tokens * max_output_price) / 1e6)",
            # The route classes this approval permits -- the money law's grant binds its route
            # allowlist against this exact set (sorted, '+'-joined).
            "route_classes": sorted(approval.allowed_route_classes),
            "input_token_upper_bound": input_bound,
            "input_token_bound_rule": f"request body bytes + {pricing.PROMPT_OVERHEAD_TOKENS} provider-side overhead tokens",
            "max_output_tokens": envelope.max_output_tokens,
            "max_input_microunits_per_million": approval.max_input_microunits_per_million,
            "max_output_microunits_per_million": approval.max_output_microunits_per_million,
            "price_source_snapshot_sha256": approval.snapshot_sha256,
        }
        return _Plan(
            mode=mode,
            protocol=protocol,
            origin=origin,
            credential=credential,
            native_tools=native_tools,
            envelope=envelope,
            approval=approval,
            trust_decision=decision,
            input_token_bound=input_bound,
            max_charge_atomic=max_charge,
            liability_basis=basis,
            permit=None,
        )

    def _reserve_prepaid(self, plan: _Plan) -> None:
        assert plan.credential is not None
        # The dispatch boundary obtains the account-bound balance observation the money law will judge:
        # the ONE balance door (core.usepod.discovery.observe_balance) answers from its record while
        # the observation is fresh, otherwise with one read-only GET of /proxy/<token>/balance -- no
        # inference, no spend, no authority. Before this read, the observation was written only by the
        # Settings "Refresh" action and expired 900 s later, so every send after that window was
        # refused MONEY_LIQUIDITY_UNVERIFIED (observed live 2026-09-16 02:26). A failed read is recorded
        # as what it is; the money law then refuses against it and the refusal names the failure --
        # never a cached success, never a renewed timestamp.
        from core.usepod import discovery as _discovery

        observation = _discovery.observe_balance(
            transport=self._http_transport(),
            credential=plan.credential,
            max_age_seconds=_discovery.BALANCE_REUSE_SECONDS,
        )
        basis = dict(plan.liability_basis)
        basis["balance_observation"] = _public_balance_observation(observation) or {"state": "not_observed"}
        plan.liability_basis = basis
        liability = ProviderLiability(
            operation_id=plan.envelope.operation_id,
            provider_id=descriptor.PROVIDER_ID,
            transport_mode=plan.mode.value,
            asset=ASSET_USDC,
            unit=UNIT_USDC_MICROUNIT,
            account_kind=ACCOUNT_KIND_PREPAID_TOKEN,
            account_ref=plan.credential.fingerprint,
            network=NETWORK_USEPOD_ACCOUNT,
            max_amount_atomic=plan.max_charge_atomic,
            model_id=plan.envelope.model_id,
            route_approval_id=plan.approval.approval_id,
            envelope_binding_sha256=plan.envelope.binding_sha256,
            basis=plan.liability_basis,
        )
        authority = monetary_authority()
        refusal = ""
        refusal_detail = ""
        try:
            reservation = authority.reserve(liability)
        except (MonetaryAuthorityUnavailableError, MonetaryAuthorityRefusedError) as exc:
            refusal = str(getattr(exc, "code", "") or "monetary_authority_refused")
            refusal_detail = str(exc)
        if refusal:
            raise UsePodDispatchRefusedError(
                refusal,
                evidence={
                    "monetary_authority": getattr(authority, "label", ""),
                    "monetary_refusal_detail": redact_secrets(refusal_detail)[:400],
                    "liability": liability.as_dict(),
                    "balance_observation": basis["balance_observation"],
                },
            )
        plan.liability = liability
        plan.reservation = reservation

    def _seal(self, request: ModelRequest, plan: _Plan, *, operation: str) -> None:
        header_names = (*tuple(name for name, _ in plan.envelope.control_headers), "Content-Type", "Accept")
        plan.permit = seal_provider_invocation(
            request=request,
            provider_id=self.manifest.provider_id,
            model_id=self.manifest.model_name,
            operation=operation,
            payload=plan.envelope.payload(),
            header_names=header_names,
        )

    def _consume_permit(self, plan: _Plan) -> None:
        consumed = plan.permit.consume()
        if payload_hash(consumed) != payload_hash(plan.envelope.payload()):
            raise UsePodDispatchRefusedError("sealed_payload_differs_from_envelope")

    @staticmethod
    def _claim_money(plan: _Plan, *, executor: str) -> None:
        """Persist the pre-effect monetary claim, BEFORE the request is written (and before any
        payment proof is obtained on the x402 path). Authorities without a claim step (labelled
        test doubles) keep their existing behaviour; the production money-law authority refuses a
        second claimant and releases a never-sent reservation on revocation here."""
        if plan.reservation is None:
            return
        claim = getattr(monetary_authority(), "claim", None)
        if claim is None:
            return
        claim(plan.reservation, executor=executor)

    # --- evidence -------------------------------------------------------------------------------------

    def _settlement(
        self,
        plan: _Plan,
        *,
        outcome: str,
        usage: dict[str, Any],
        route: routing.RouteEvidence | None,
        http_status: int | None,
        detail: str = "",
    ) -> SettlementEvidence:
        input_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
        upper = None
        if input_tokens is not None and output_tokens is not None:
            upper = pricing.charge_upper_bound_microunits(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_rate=plan.approval.max_input_microunits_per_million,
                output_rate=plan.approval.max_output_microunits_per_million,
            )
        exceeds = bool(
            (input_tokens is not None and input_tokens > plan.input_token_bound)
            or (output_tokens is not None and output_tokens > plan.envelope.max_output_tokens)
        )
        return SettlementEvidence(
            operation_id=plan.envelope.operation_id,
            outcome=outcome,
            http_status=http_status,
            usage=dict(usage),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            upper_bound_cost_atomic=upper,
            exact_cost_atomic=None,
            exact_cost_state=EXACT_COST_NOT_SUPPLIED,
            route=route.as_dict() if route is not None else {},
            balance_remaining_raw=route.balance_remaining_raw if route is not None else None,
            usage_exceeds_liability_bound=exceeds,
            detail=redact_secrets(detail)[:240],
        )

    def _evidence(
        self,
        plan: _Plan,
        *,
        status: str,
        route: routing.RouteEvidence | None,
        settlement: SettlementEvidence | None,
        attested_model: str | None = None,
        finish_reason: str = "",
        x402: X402PaidExchange | None = None,
        payment_response: str | None = None,
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "schema": EVIDENCE_SCHEMA,
            "status": status,
            "operation_id": plan.envelope.operation_id,
            "transport_mode": plan.mode.value,
            "protocol": plan.protocol.value,
            "origin": plan.origin,
            "endpoint": plan.envelope.path_template,
            "resolved_model": self.manifest.model_name,
            "provider_attested_model": attested_model,
            "credential_fingerprint": plan.credential.fingerprint if plan.credential else "",
            "route_policy": plan.approval.as_evidence(),
            "route": route.as_dict() if route is not None else None,
            "trust": plan.trust_decision.as_dict(),
            "envelope": plan.envelope.as_evidence(),
            "liability": plan.liability.as_dict() if plan.liability else None,
            "reservation": plan.reservation.as_dict() if plan.reservation else None,
            "settlement": settlement.as_dict() if settlement else None,
            "finish_reason": finish_reason,
            "recorded_at": time.time(),
        }
        if plan.mode is descriptor.TransportMode.X402:
            # the DNA service fee's own facts, read from the money law by operation id (a read; the law wrote them at
            # reservation and settlement), so a lost or refused paid call still states its reserved, not-owed fee
            from core.usepod.money_law import service_fee_summary

            try:
                fee = service_fee_summary(plan.envelope.operation_id)
            except Exception:
                fee = None
            if fee is not None:
                evidence["dna_fee"] = fee
        if x402 is not None:
            option = x402.option
            decoded_receipt = decode_payment_response(payment_response)
            evidence["x402"] = {
                "quote": x402.quote.as_evidence(),
                "paid_option": option.as_dict(),
                "proof": x402.proof.public_fields(),
                "paid_attempts": x402.attempts,
                "payment_response": decoded_receipt,
                "wallet_outflow_atomic": option.amount_atomic,
                "consumed_cost_state": "not_supplied_by_documented_headers",
                "surplus_credit_state": (
                    "credited_per_provider_receipt_unverified"
                    if _named_surplus_credit(decoded_receipt) is not None
                    else "unknown_until_the_actual_charge_is_known"
                ),
            }
        return evidence

    def _cost_estimate(self, plan: _Plan, settlement: SettlementEvidence) -> dict[str, Any]:
        if settlement.upper_bound_cost_atomic is not None:
            atomic, basis = settlement.upper_bound_cost_atomic, COST_BASIS_USAGE_AT_CEILING
        else:
            atomic, basis = plan.max_charge_atomic, COST_BASIS_LIABILITY_BOUND
        return {
            "usd": round(atomic / pricing.MICROUNITS_PER_USDC, 8),
            "known": False,
            "basis": basis,
            "source": f"UsePod {plan.mode.value}: upper bound in USDC microunits ({atomic}); exact charge not supplied; {_USD_PER_USDC_DECLARATION}",
            "provider_id": descriptor.PROVIDER_ID,
            "model_id": self.manifest.model_name,
            "prompt_tokens": int(settlement.input_tokens or 0),
            "completion_tokens": int(settlement.output_tokens or 0),
            "attempts": 1,
            "upper_bound_atomic": atomic,
            "asset": ASSET_USDC,
        }

    def _retain_unknown(self, plan: _Plan, evidence: SettlementEvidence) -> None:
        if plan.reservation is not None:
            monetary_authority().retain_unknown(plan.reservation, evidence)

    def _settle_admission_refusal(self, plan: _Plan, evidence: SettlementEvidence, *, refusal_code: str, http_status: int) -> str:
        """Settle a prepaid admission refusal at its proven zero; fall back to a held unknown.

        The evidence is the provider's typed refusal to these exact bytes, one of the codes whose
        refused-before-service basis the descriptor documents -- never the status alone. If the
        law refuses the settlement (a conflict, a closed liability), the hold stays unknown.
        """
        if plan.reservation is None:
            return "retained_unknown"
        authority = monetary_authority()
        try:
            authority.settle_admission_refusal(plan.reservation, refusal_code=refusal_code, http_status=http_status)
            return "settled_admission_refusal_no_charge"
        except Exception:
            self._retain_unknown(plan, evidence)
            return "retained_unknown"

    @staticmethod
    def _with_payment_credit(settlement: SettlementEvidence, exchange: X402PaidExchange | None, payment_response: str | None) -> SettlementEvidence:
        """Carry an x402 payment response's credited surplus onto the settlement as PROVIDER
        credit (the quote's pay-to account). It is not a wallet refund and never reduces the
        wallet outflow; the money law records it as an unverified credit line."""
        if exchange is None or not payment_response:
            return settlement
        # decode_payment_response reports ``decoded_schema_unpublished`` with the receipt's scalar fields under
        # ``fields``: the schema is unpublished, so a surplus the provider names is carried as an UNVERIFIED credit
        # line -- and a receipt that names none, or one nobody can decode, credits nothing.
        surplus = _named_surplus_credit(decode_payment_response(payment_response))
        if surplus is None:
            return settlement
        from dataclasses import replace

        return replace(
            settlement,
            provider_credit_atomic=int(surplus),
            provider_credit_account=str(exchange.option.pay_to),
        )

    # --- dispatch ---------------------------------------------------------------------------------------

    def _dispatch(self, request: ModelRequest, plan: _Plan, *, operation: str) -> tuple[UsePodResponse, X402PaidExchange | None]:
        transport = self._http_transport()
        timeout = self._read_timeout(request)
        if plan.mode is descriptor.TransportMode.PREPAID:
            self._reserve_prepaid(plan)
            assert plan.reservation is not None and plan.credential is not None
            authority = monetary_authority()
            try:
                self._seal(request, plan, operation=operation)
            except Exception:
                authority.release_unsent(plan.reservation, reason="provider_invocation_seal_failed")
                raise
            try:
                self._consume_permit(plan)
            except Exception:
                authority.release_unsent(plan.reservation, reason="provider_invocation_permit_refused")
                raise
            # The pre-effect CLAIM is persisted before the transport writes a single byte; the
            # dispatch record follows only after the request has actually left. A claim refusal
            # carries NO cleanup here: the money law itself released the never-sent reservation
            # when the refusal was revocation, expiry or the freeze, and a claim conflict means
            # another invocation owns this dispatch -- releasing "it" would cancel the winner.
            self._claim_money(plan, executor="usepod_prepaid_transport")
            try:
                response = transport.post_envelope(plan.envelope, origin=plan.origin, token=plan.credential.token, read_timeout_seconds=timeout)
            except UsePodTransportError as exc:
                settlement: SettlementEvidence | None = None
                if exc.dispatch_state == DISPATCH_NOT_SENT:
                    authority.release_unsent(plan.reservation, reason=exc.code)
                    recording = "released_unsent"
                else:
                    settlement = self._settlement(plan, outcome=OUTCOME_UNKNOWN, usage={}, route=None, http_status=exc.http_status, detail=exc.code)
                    self._retain_unknown(plan, settlement)
                    recording = "retained_unknown"
                evidence = self._evidence(plan, status=exc.dispatch_state, route=None, settlement=settlement)
                evidence["settlement_recording"] = recording
                _attach_evidence(exc, evidence, error_code=exc.code)
                raise
            # The request left: only now is the dispatch recorded against the claimed liability.
            try:
                authority.mark_dispatched(plan.reservation)
            except Exception:
                response.close()
                settlement = self._settlement(plan, outcome=OUTCOME_UNKNOWN, usage={}, route=None, http_status=None, detail="dispatch_record_failed")
                self._retain_unknown(plan, settlement)
                raise
            return response, None
        # Accountless x402: the quote request is the first transmission of these bytes.
        self._seal(request, plan, operation=operation)
        self._consume_permit(plan)
        client = UsePodX402Client(transport=transport)
        # Which assets may pay, and the local bound for each, come from the operator's x402 consents for
        # this model and route: USDC under the route's price ceiling, SOL only under a lamport consent.
        from core.usepod.money_law import x402_payment_bounds

        allowed_assets, local_bounds = x402_payment_bounds(
            model_id=plan.envelope.model_id,
            route="+".join(sorted(plan.approval.allowed_route_classes)),
            usdc_route_bound_atomic=plan.max_charge_atomic,
        )
        try:
            exchange = client.execute(
                plan.envelope,
                origin=plan.origin,
                allowed_assets=allowed_assets,
                local_bounds_atomic=local_bounds,
                liability_basis=plan.liability_basis,
                read_timeout_seconds=timeout,
                resume_context=self._resume_context(plan),
            )
        except X402OperationStateError as exc:
            raise UsePodDispatchRefusedError(exc.code) from None
        except UsePodTransportError as exc:
            evidence = self._evidence(plan, status=exc.dispatch_state, route=None, settlement=None)
            if _mapping(getattr(exc, "provider_evidence", None)).get("payment_retained"):
                # the transport kept this operation's liability as unknown: the wallet paid and no usable answer came back
                evidence["settlement_recording"] = "retained_unknown"
            _attach_evidence(exc, evidence, error_code=exc.code)
            raise
        plan.reservation = exchange.reservation
        plan.liability = exchange.reservation.liability
        return exchange.response, exchange

    def _route_gate(self, request: ModelRequest, plan: _Plan, response: UsePodResponse, exchange: X402PaidExchange | None) -> routing.RouteEvidence:
        """Status and route checks that must pass before any content is read or streamed."""
        if not 200 <= response.status < 300:
            error = error_for_response(response, origin=plan.origin)
            settlement = self._settlement(plan, outcome=OUTCOME_FAILED_AFTER_SEND, usage={}, route=None, http_status=response.status, detail=error.code)
            if exchange is None:
                # A prepaid admission refusal (the proxy's typed answer that THIS request was
                # refused before any upstream service) meters nothing: settle at the proven zero
                # so a refusal storm cannot hold the day's envelope. Every other refusal keeps
                # its maximum held until evidence resolves it.
                from core.usepod.money_law import ADMISSION_REFUSAL_NO_CHARGE_CODES

                if str(error.code) in ADMISSION_REFUSAL_NO_CHARGE_CODES:
                    recording = self._settle_admission_refusal(plan, settlement, refusal_code=str(error.code), http_status=response.status)
                else:
                    self._retain_unknown(plan, settlement)
                    recording = "retained_unknown"
            else:
                recording = "retained_unknown"
            evidence = self._evidence(plan, status="failed_after_send", route=None, settlement=settlement)
            evidence["settlement_recording"] = recording
            evidence["response_headers"] = response_header_facts(response.headers)
            _attach_evidence(error, evidence, error_code=error.code)
            raise error
        route = routing.evaluate_route_evidence(plan.approval, response.headers)
        if route.compliance != routing.COMPLIANCE_COMPLIANT:
            outcome = OUTCOME_ROUTE_POLICY_VIOLATED if route.compliance == routing.COMPLIANCE_VIOLATED else OUTCOME_ROUTE_UNVERIFIED
            usage: dict[str, Any] = {}
            if not response.streaming:
                try:
                    body = response.json()
                    shaped = body if plan.protocol is descriptor.WireProtocol.OPENAI else anthropic_response_to_openai(body)
                    usage = dict(shaped.get("usage") or {}) if isinstance(shaped, dict) else {}
                except Exception:
                    usage = {}
            else:
                response.close()
            settlement = self._settlement(plan, outcome=outcome, usage=usage, route=route, http_status=response.status, detail=",".join(route.reasons))
            # Served and possibly billed on a route nobody approved: the liability is retained for
            # reconciliation, and the answer is not handed to the turn as a compliant one.
            self._retain_unknown(plan, settlement)
            evidence = self._evidence(plan, status=outcome, route=route, settlement=settlement, x402=exchange, payment_response=response.header("payment-response"))
            evidence["settlement_recording"] = "retained_unknown"
            evidence["response_headers"] = response_header_facts(response.headers)
            raise UsePodRouteNotCompliantError(route.compliance, route.reasons, evidence=evidence)
        return route

    def _settle(self, plan: _Plan, settlement: SettlementEvidence) -> str:
        if plan.reservation is None:
            return "no_reservation"
        try:
            monetary_authority().settle(plan.reservation, settlement)
        except Exception as exc:
            return f"settlement_recording_failed:{type(exc).__name__}"
        return "settled_with_evidence"

    def _confirm_chain(self, plan: _Plan, exchange: X402PaidExchange | None, settlement_state: str) -> dict[str, Any] | None:
        """After the provider's settlement of a PAID x402 call: the wallet authority reads what left the wallet from the
        chain (the principal and the fee the transaction charged) and the money law records it as chain evidence under
        the transaction signature. Never before a settled provider receipt, never for a call nothing paid."""
        if exchange is None or plan.reservation is None:
            return None
        if settlement_state != "settled_with_evidence":
            return {"state": "not_recorded_provider_settlement_missing"}
        from core.usepod.transport import payment_authority

        reader = getattr(payment_authority(), "chain_confirmation", None)
        recorder = getattr(monetary_authority(), "record_chain_confirmation", None)
        if reader is None or recorder is None:
            return {"state": "not_supported_by_installed_authorities"}
        try:
            confirmation = reader(proof=exchange.proof)
        except Exception as exc:
            return {"state": f"chain_read_failed:{type(exc).__name__}"}
        if confirmation is None:
            return {"state": "not_confirmed_by_wallet", "signature": exchange.proof.signature}
        facts = {
            "signature": confirmation.signature,
            "wallet_outflow_atomic": confirmation.wallet_outflow_atomic,
            "network_fee_atomic": confirmation.network_fee_atomic,
            "fee_asset": confirmation.fee_asset,
        }
        try:
            recorder(plan.reservation, confirmation)
        except Exception as exc:
            return {"state": f"recording_failed:{getattr(exc, 'code', '') or type(exc).__name__}", **facts}
        outcome = getattr(payment_authority(), "service_fee_collection_outcome", None)
        if outcome is not None:
            # the collection of previously accrued DNA fees planned WITH this payment's approval: what happened to it
            try:
                facts["service_fee_collection"] = outcome(proof=exchange.proof)
            except Exception as exc:
                facts["service_fee_collection"] = {"planned": False, "reason": f"outcome_unreadable:{type(exc).__name__}"}
        return {"state": "recorded", **facts}

    def _settle_completed(
        self, plan: _Plan, *, route: routing.RouteEvidence, response: UsePodResponse, exchange: X402PaidExchange | None, data: dict[str, Any]
    ) -> tuple[SettlementEvidence, dict[str, Any], str | None, str]:
        """A completed, readable answer: its usage settles the liability, a paid call's chain read follows, and the
        evidence names both. Shared by an ordinary call and a resumed one, so the two settle identically."""
        usage = dict(data.get("usage") or {})
        choices = list(data.get("choices") or [])
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        finish_reason = str(first.get("finish_reason") or "").strip()
        attested = str(data.get("model")) if data.get("model") else None
        settlement = self._settlement(plan, outcome=OUTCOME_COMPLETED, usage=usage, route=route, http_status=response.status)
        settlement = self._with_payment_credit(settlement, exchange, response.header("payment-response"))
        settlement_state = self._settle(plan, settlement)
        chain = self._confirm_chain(plan, exchange, settlement_state)
        evidence = self._evidence(
            plan,
            status="completed",
            route=route,
            settlement=settlement,
            attested_model=attested,
            finish_reason=finish_reason,
            x402=exchange,
            payment_response=response.header("payment-response"),
        )
        evidence["settlement_recording"] = settlement_state
        evidence["response_headers"] = response_header_facts(response.headers)
        if chain is not None and isinstance(evidence.get("x402"), dict):
            evidence["x402"]["chain_confirmation"] = chain
            if isinstance(evidence.get("dna_fee"), dict) and isinstance(chain.get("service_fee_collection"), dict):
                evidence["dna_fee"]["collection"] = chain["service_fee_collection"]
        return settlement, evidence, attested, finish_reason

    def _resume_context(self, plan: _Plan) -> dict[str, Any]:
        """What a resumed paid operation needs to be judged exactly as this dispatch would judge it: the lane, the route
        approval bound at dispatch, the trust decision and the charge bounds. Public facts and ceilings only."""
        approval = plan.approval
        return {
            "provider_name": str(self.manifest.provider_name),
            "model_name": str(self.manifest.model_name),
            "approval": {
                "approval_id": approval.approval_id,
                "bound_approval_id": approval.bound_approval_id,
                "model_id": approval.model_id,
                "header_routing_mode": approval.header_routing_mode,
                "header_providers": approval.header_providers,
                "pinned_providers": list(approval.pinned_providers),
                "max_input_microunits_per_million": approval.max_input_microunits_per_million,
                "max_output_microunits_per_million": approval.max_output_microunits_per_million,
                "allowed_route_classes": list(approval.allowed_route_classes),
                "eligible_prices": [price.as_dict() for price in approval.eligible_prices],
                "fallback_expected": approval.fallback_expected,
                "snapshot_sha256": approval.snapshot_sha256,
                "snapshot_fetched_at": approval.snapshot_fetched_at,
                "snapshot_evidence": approval.snapshot_evidence,
                "snapshot_age_seconds": approval.snapshot_age_seconds,
                "checked_at": approval.checked_at,
                "notes": list(approval.notes),
            },
            "trust": plan.trust_decision.as_dict(),
            "input_token_bound": plan.input_token_bound,
            "max_charge_atomic": plan.max_charge_atomic,
        }

    def resume_paid_operation(self, operation_id: str) -> dict[str, Any]:
        """Finish one already-paid x402 operation whose answer never arrived (a lost answer, or a process that ended while
        waiting). The transport resends the recorded bytes and proof once; the answer is judged against the route approval
        bound at dispatch, the provider receipt settles the liability and the chain confirmation follows. The conversation
        that asked has ended, so the answer comes back to the owner; tool calls in it are counted, never executed."""
        from core.usepod.transport import _envelope_from_record

        client = UsePodX402Client(transport=self._http_transport())
        record = client.journal.resume_record(operation_id)
        timeout = float(self.manifest.runtime_config.get("timeout_seconds") or 180)
        exchange = client.resume(operation_id, read_timeout_seconds=timeout)
        assert record is not None  # resume() refuses an operation without its record before anything is sent
        context = json.loads(record["context_json"] or "{}")
        envelope = _envelope_from_record(json.loads(record["envelope_json"]))
        plan = _Plan(
            mode=descriptor.TransportMode.X402,
            protocol=descriptor.WireProtocol(envelope.protocol),
            origin=envelope.origin,
            credential=None,
            native_tools=(),
            envelope=envelope,
            approval=_approval_from_context(dict(context.get("approval") or {})),
            trust_decision=_RecordedTrust(dict(context.get("trust") or {})),
            input_token_bound=int(context.get("input_token_bound") or 0),
            max_charge_atomic=int(context.get("max_charge_atomic") or 0),
            liability_basis=dict(exchange.reservation.liability.basis or {}),
            permit=None,
            liability=exchange.reservation.liability,
            reservation=exchange.reservation,
        )
        request = _ResumeRequest()
        response = exchange.response
        route = self._route_gate(request, plan, response, exchange)  # type: ignore[arg-type]
        if envelope.stream:
            parts: list[str] = []
            final: ModelStreamChunk | None = None
            for chunk in self._stream_chunks(request, plan, response, route, exchange):  # type: ignore[arg-type]
                if chunk.done:
                    final = chunk
                elif chunk.delta_text:
                    parts.append(chunk.delta_text)
            metadata = dict((final.provider_metadata if final is not None else None) or {})
            answer, tool_calls = "".join(parts), 0
        else:
            body = response.json()
            data = body if plan.protocol is descriptor.WireProtocol.OPENAI else anthropic_response_to_openai(body)
            if not isinstance(data, dict):
                raise MalformedProviderResponseError("malformed provider response: usepod completion is not an object")
            _settlement, evidence, _attested, _finish = self._settle_completed(plan, route=route, response=response, exchange=exchange, data=data)
            choices = list(data.get("choices") or [])
            first = choices[0] if choices and isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first.get("message"), dict) else {}
            answer = strip_reasoning_block(_extract_openai_text(data))
            tool_calls = len(message.get("tool_calls") or [])
            metadata = {"usepod": evidence, "receipt": call_receipt(evidence)}
        receipt = dict(metadata.get("receipt") or {})
        return {
            "operation_id": operation_id,
            "state": "completed",
            "resumed": True,
            "answer": answer,
            "tool_calls_not_executed": tool_calls,
            "receipt": receipt,
            "settlement_recording": str((receipt.get("settlement") or {}).get("recording") or ""),
        }

    def _interpret(self, request: ModelRequest, data: dict[str, Any], native_tools: tuple[CloudToolDefinition, ...]) -> tuple[str, tuple[CloudToolCall, ...], str]:
        """The parent lane's reading of an OpenAI-shaped completion, unchanged in meaning."""
        response_output_mode = request.output_mode
        if not native_tools:
            output_text = strip_reasoning_block(_extract_openai_text(data))
            offered = tuple(item for item in (request.tools or ()) if isinstance(item, CloudToolDefinition))
            repaired = False
            if offered and (request.output_mode == "tool_intent" or not scrub_foreign_markers(output_text).strip()):
                candidate = _repaired_tool_call_from_payload(data, definitions=offered)
                if candidate:
                    output_text = candidate
                    repaired = True
                    response_output_mode = "tool_intent"
            if not repaired and not output_text.strip():
                if bool(getattr(request, "tools_required", False)):
                    raise MalformedToolArgumentsError("native tool call required but the response produced no usable content")
                empty = EmptyProviderResponseError("provider response has no usable text and no tool call")
                # The reply's own facts ride the error (ids, finish reason, usage, reasoning
                # tokens, which fields carried text -- never the text), exactly as the parent
                # OpenAI-compatible lane records them; see `core.normalized_provider_result`.
                try:
                    from core.normalized_provider_result import empty_reply_diagnostics

                    empty.diagnostics = empty_reply_diagnostics(  # type: ignore[attr-defined]
                        data, max_tokens_sent=getattr(request, "max_output_tokens", None)
                    )
                except Exception:
                    pass
                raise empty
            return output_text, (), response_output_mode
        try:
            output_text, calls = _extract_native_openai_tool_result(data, definitions=native_tools, metadata=request.metadata)
        except ToolCallParseError:
            raise
        except ValueError as exc:
            raise MalformedProviderResponseError(f"malformed provider response: {exc}") from None
        return output_text, calls, response_output_mode

    def _invoke_openai_compatible(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        if request.is_cancelled():
            raise RuntimeError("model_call_cancelled")
        plan = self._prepare(request, force_json=force_json, stream=False)
        response, exchange = self._dispatch(request, plan, operation="structured" if force_json else "chat")
        route = self._route_gate(request, plan, response, exchange)
        data: dict[str, Any] | None = None
        try:
            body = response.json()
            shaped = body if plan.protocol is descriptor.WireProtocol.OPENAI else anthropic_response_to_openai(body)
            if not isinstance(shaped, dict):
                raise MalformedProviderResponseError("malformed provider response: usepod completion is not an object")
            data = shaped
            output_text, tool_calls, output_mode = self._interpret(request, data, plan.native_tools)
        except Exception as exc:
            chain = None
            if isinstance(data, dict):
                # The provider served and reported usage; it is the ANSWER that is unusable. Billing is
                # settled on the evidence; the turn still gets the typed failure.
                usage_seen = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                settlement = self._settlement(
                    plan, outcome=OUTCOME_COMPLETED, usage=dict(usage_seen), route=route, http_status=response.status, detail=f"answer_unusable:{type(exc).__name__}"
                )
                recording = self._settle(plan, settlement)
                chain = self._confirm_chain(plan, exchange, recording)
            else:
                # The body itself could not be read: served, possibly billed, cost unknown.
                settlement = self._settlement(plan, outcome=OUTCOME_UNKNOWN, usage={}, route=route, http_status=response.status, detail=type(exc).__name__)
                self._retain_unknown(plan, settlement)
                recording = "retained_unknown"
            evidence = self._evidence(plan, status="response_unusable", route=route, settlement=settlement, x402=exchange)
            evidence["settlement_recording"] = recording
            evidence["response_headers"] = response_header_facts(response.headers)
            if chain is not None and isinstance(evidence.get("x402"), dict):
                evidence["x402"]["chain_confirmation"] = chain
            _attach_evidence(exc, evidence)
            raise
        usage = dict(data.get("usage") or {})
        settlement, evidence, attested, finish_reason = self._settle_completed(plan, route=route, response=response, exchange=exchange, data=data)
        return ModelResponse(
            output_text=output_text,
            confidence=float(self.manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=data,
            usage=usage,
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=output_mode,
            tool_calls=tool_calls,
            provider_attested_model=attested,
            finish_reason=finish_reason,
            effective_max_output_tokens=plan.envelope.max_output_tokens,
            provider_metadata={"usepod": evidence, "receipt": call_receipt(evidence), "cost_estimate": self._cost_estimate(plan, settlement)},
        )

    def _stream_openai_compatible(self, request: ModelRequest):
        plan = self._prepare(request, force_json=False, stream=True)
        response, exchange = self._dispatch(request, plan, operation="stream")
        route = self._route_gate(request, plan, response, exchange)
        return self._stream_chunks(request, plan, response, route, exchange)

    def _stream_chunks(
        self,
        request: ModelRequest,
        plan: _Plan,
        response: UsePodResponse,
        route: routing.RouteEvidence,
        exchange: X402PaidExchange | None,
    ) -> Iterator[ModelStreamChunk]:
        terminal_recorded = False
        text_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = ""
        attested: str | None = None
        assembler = AnthropicStreamAssembler() if plan.protocol is descriptor.WireProtocol.ANTHROPIC else None
        saw_done = False
        response_metadata = {}
        try:
            for line in response.iter_lines():
                response_metadata.update(response_body_facts(_parse_stream_line(line)))
                if request.is_cancelled():
                    raise RuntimeError("model_call_cancelled")
                if assembler is not None:
                    events = assembler.feed_line(line)
                    attested = assembler.model or attested
                else:
                    event = _parse_stream_line(line)
                    if event is None:
                        continue
                    if event == "__DONE__":
                        saw_done = True
                        break
                    events = [event] if isinstance(event, dict) else []
                for event in events:
                    if event.get("usage"):
                        usage = dict(event["usage"])
                    if attested is None and event.get("model"):
                        attested = str(event["model"])
                    choices = list(event.get("choices") or [])
                    first = choices[0] if choices and isinstance(choices[0], dict) else {}
                    if first.get("finish_reason"):
                        finish_reason = str(first["finish_reason"])
                    delta = _extract_stream_delta_text(event)
                    if delta:
                        text_parts.append(delta)
                        yield ModelStreamChunk(delta_text=delta, raw_event=event, done=False, provider_attested_model=attested, finish_reason=finish_reason)
            if assembler is not None:
                final = assembler.finalize()
                usage = dict(final.get("usage") or {})
                finish_reason = str(final.get("finish_reason") or finish_reason)
                attested = final.get("model") or attested
            elif not saw_done:
                raise IncompleteStreamError("openai", partial_text="".join(text_parts), usage=usage, events_seen=len(text_parts))
        except (IncompleteStreamError, AnthropicStreamError, UsePodTransportError, MalformedProviderResponseError) as exc:
            partial_usage = dict(getattr(exc, "usage", None) or (assembler.usage() if assembler is not None else usage) or {})
            settlement = self._settlement(plan, outcome=OUTCOME_PARTIAL_STREAM, usage=partial_usage, route=route, http_status=response.status, detail=type(exc).__name__)
            self._retain_unknown(plan, settlement)
            terminal_recorded = True
            evidence = self._evidence(plan, status=OUTCOME_PARTIAL_STREAM, route=route, settlement=settlement, attested_model=attested, x402=exchange)
            evidence["settlement_recording"] = "retained_unknown"
            evidence["response_headers"] = response_header_facts(response.headers)
            evidence["response_metadata"] = response_metadata
            _attach_evidence(exc, evidence)
            raise
        except BaseException as exc:
            # The consumer stopped reading (GeneratorExit), the turn was cancelled, or something else
            # broke the loop. The request was served and may be billed: the liability is retained for
            # reconciliation, never released as if nothing happened.
            if not terminal_recorded:
                settlement = self._settlement(plan, outcome=OUTCOME_PARTIAL_STREAM, usage=usage, route=route, http_status=response.status, detail=type(exc).__name__)
                self._retain_unknown(plan, settlement)
                terminal_recorded = True
            raise
        finally:
            response.close()
        settlement = self._settlement(plan, outcome=OUTCOME_COMPLETED, usage=usage, route=route, http_status=response.status)
        settlement = self._with_payment_credit(settlement, exchange, response.header("payment-response"))
        settlement_state = self._settle(plan, settlement)
        chain = self._confirm_chain(plan, exchange, settlement_state)
        terminal_recorded = True
        evidence = self._evidence(
            plan,
            status="completed",
            route=route,
            settlement=settlement,
            attested_model=attested,
            finish_reason=finish_reason,
            x402=exchange,
            payment_response=response.header("payment-response"),
        )
        evidence["settlement_recording"] = settlement_state
        evidence["response_headers"] = response_header_facts(response.headers)
        evidence["response_metadata"] = response_metadata
        if chain is not None and isinstance(evidence.get("x402"), dict):
            evidence["x402"]["chain_confirmation"] = chain
        yield ModelStreamChunk(
            delta_text="",
            done=True,
            usage=usage or None,
            provider_attested_model=attested,
            finish_reason=finish_reason,
            provider_metadata={"usepod": evidence, "receipt": call_receipt(evidence), "cost_estimate": self._cost_estimate(plan, settlement)},
        )


class _RecordedTrust:
    """The trust decision a dispatch recorded, carried unchanged into a resumed operation's evidence."""

    def __init__(self, recorded: dict[str, Any]) -> None:
        self._recorded = dict(recorded)
        self.allowed = bool(recorded.get("allowed"))

    def as_dict(self) -> dict[str, Any]:
        return dict(self._recorded)


class _ResumeRequest:
    """A resumed operation's stand-in for its ended turn: never cancelled, no metadata, no tools to run."""

    metadata: dict[str, Any] = {}
    tools: tuple[Any, ...] = ()

    def is_cancelled(self) -> bool:
        return False


def _approval_from_context(data: dict[str, Any]) -> routing.DispatchRouteApproval:
    """The route approval bound at dispatch, rebuilt field for field from the resume context."""
    from core.usepod.routing import _route_price_from_dict

    return routing.DispatchRouteApproval(
        approval_id=str(data["approval_id"]),
        bound_approval_id=str(data["bound_approval_id"]),
        model_id=str(data["model_id"]),
        header_routing_mode=str(data["header_routing_mode"]),
        header_providers=str(data["header_providers"]),
        pinned_providers=tuple(str(item) for item in data["pinned_providers"]),
        max_input_microunits_per_million=int(data["max_input_microunits_per_million"]),
        max_output_microunits_per_million=int(data["max_output_microunits_per_million"]),
        allowed_route_classes=tuple(str(item) for item in data["allowed_route_classes"]),
        eligible_prices=tuple(_route_price_from_dict(item) for item in data["eligible_prices"]),
        fallback_expected=bool(data["fallback_expected"]),
        snapshot_sha256=str(data["snapshot_sha256"]),
        snapshot_fetched_at=float(data["snapshot_fetched_at"]),
        snapshot_evidence=str(data["snapshot_evidence"]),
        snapshot_age_seconds=float(data["snapshot_age_seconds"]),
        checked_at=float(data["checked_at"]),
        notes=tuple(str(item) for item in data["notes"]),
    )


def resume_x402_operation(operation_id: str) -> dict[str, Any]:
    """Owner action: finish one unresolved paid x402 operation through the UsePod lane that dispatched it."""
    from core.model_registry import ModelRegistry

    client = UsePodX402Client(transport=UsePodHttpTransport())
    record = client.journal.resume_record(operation_id)
    if record is None:
        # resume() names the precise refusal (unknown, not unresolved, record missing) before anything is sent
        client.resume(operation_id, read_timeout_seconds=1.0)
        raise UsePodDispatchRefusedError("resume_record_missing")
    context = json.loads(record["context_json"] or "{}")
    registry = ModelRegistry()
    manifest = registry.get_manifest(str(context.get("provider_name") or ""), str(context.get("model_name") or ""))
    if manifest is None or str(manifest.adapter_type or "") != "usepod":
        raise UsePodDispatchRefusedError("resume_lane_not_registered")
    adapter = registry.build_adapter(manifest)
    return adapter.resume_paid_operation(operation_id)


__all__ = ["UsePodAdapter", "UsePodDispatchRefusedError", "UsePodRouteNotCompliantError", "call_receipt", "resume_x402_operation"]
