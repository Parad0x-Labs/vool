"""Stable application diagnostics for HTTP error responses.

The HTTP status number keeps its STANDARD meaning -- 200 success, 400 invalid request,
401 unauthenticated, 403 forbidden, 404 absent, 409 conflict, 429 throttled, 5xx
server/upstream failure -- and is never repurposed as a private code. What an error
response ADDS is a ``diagnostic`` envelope that carries the application-level truth:

* ``code``       -- a stable, namespaced condition identifier (``vool.<condition>``).
                    Codes identify CONDITIONS; they never carry secrets, prompt text,
                    or filesystem paths, and they are append-only: a code is never
                    renamed or reused for a new meaning.
* ``message``    -- plain words first; safe to show any user, no interpolated detail.
* ``detail``     -- the technical facts, for the expandable section of a UI.
* ``recovery``   -- the suggested next step for this class of failure.
* ``retryable``  -- whether retrying the same request can plausibly succeed.
* ``correlation_id`` / ``turn_key`` -- occurrence identity: the same condition on two
                    requests yields the same code and different correlation ids.
* ``upstream_status`` -- the ACTUAL status the upstream returned, when one did.

Two vocabularies meet here, both reused rather than reinvented:

1. the fault plane (``core.faults.catalog``) -- every catalog code is addressable as
   ``vool.<fault_code>`` with message/recovery/retryability taken from its frozen
   :class:`~core.faults.catalog.FaultSpec` (provider, tool/retrieval, wallet,
   integrity/publication, unknown-exception families);
2. a small closed set of request/upstream conditions that are genuinely absent from
   the fault catalog because they describe the HTTP boundary itself, not a runtime
   failure family.

Transport failures are mapped from TYPED FACTS (dispatch state, upstream status), never
from prose: a request that never left the machine is a 400 contract refusal, not a fake
gateway error; a genuine x402 payment challenge keeps its HTTP 402; an upstream 429
surfaces as 429; an upstream refusal surfaces as 502 with the refused status attached;
a sent-but-unknown-outcome is a 504 gateway timeout.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from core.faults.catalog import (
    RETRY_LATER,
    RETRY_NOW,
    UnknownFaultCodeError,
    get_spec,
)

#: Schema tag carried by every envelope so consumers can pin the vocabulary.
DIAGNOSTIC_SCHEMA = "vool.diagnostic.v1"

#: Every condition code is namespaced with this prefix.
NAMESPACE = "vool"

# Typed dispatch states (values match core.usepod.transport's contract; kept literal
# here so the mapping function stays pure data-in/data-out with no import coupling).
DISPATCH_NOT_SENT = "not_sent"
DISPATCH_OUTCOME_UNKNOWN = "sent_outcome_unknown"
DISPATCH_RESPONSE_RECEIVED = "response_received"


@dataclass(frozen=True)
class RequestCondition:
    """One HTTP-boundary condition absent from the fault catalog, with its contract."""

    code: str
    http_status: int
    message: str
    recovery: str
    retryable: bool


def _cond(code: str, http_status: int, message: str, recovery: str, retryable: bool) -> RequestCondition:
    return RequestCondition(code=f"{NAMESPACE}.{code}", http_status=http_status, message=message, recovery=recovery, retryable=retryable)


#: The closed request/upstream vocabulary. Append-only, like the fault catalog: entries
#: are never renamed or reused. A duplicate code would collapse two contracts silently,
#: so construction below validates uniqueness.
_REQUEST_CONDITIONS: tuple[RequestCondition, ...] = (
    _cond("request.invalid", 400,
          "That request was not valid, so nothing was done.",
          "Check the request fields and try again.", False),
    _cond("request.unknown_fields", 400,
          "The request carried fields this endpoint does not accept.",
          "Remove the unknown fields and try again.", False),
    _cond("request.missing_field", 400,
          "A required field was missing from the request.",
          "Fill in the required field and try again.", False),
    _cond("request.unsupported_media_type", 415,
          "That content type is not accepted here.",
          "Send the request as application/json.", False),
    _cond("request.payload_too_large", 413,
          "The request body was too large.",
          "Reduce the payload size and try again.", False),
    _cond("request.unauthenticated", 401,
          "There is no credential available for this action, so nothing was sent.",
          "Add the needed credential (or sign in), then try again.", True),
    _cond("request.forbidden", 403,
          "This action is only permitted from VOOL's own window on this machine.",
          "Open VOOL locally and try again from there.", False),
    _cond("request.not_found", 404,
          "What the request named does not exist here.",
          "Check the identifier and try again.", False),
    _cond("request.conflict", 409,
          "The request conflicts with the current state, so nothing was changed.",
          "Refresh the current state, then try again.", False),
    _cond("request.throttled", 429,
          "Too many requests were sent too quickly.",
          "Wait a moment, then try again.", True),
    _cond("upstream.payment_challenge", 402,
          "The paid resource asked for payment, so the answer was not delivered.",
          "Approve the payment (or choose a free resource), then try again.", True),
    _cond("upstream.throttled", 429,
          "The upstream service is throttling requests.",
          "Wait a moment, then try again.", True),
    _cond("upstream.refused", 502,
          "The upstream service refused the request, so it did not complete.",
          "The upstream's own status is attached; check the service and retry.", True),
    _cond("upstream.failed", 502,
          "The upstream service failed while handling the request.",
          "The upstream's own status is attached; retrying later may succeed.", True),
    _cond("upstream.timeout", 504,
          "The request was sent but no answer arrived in time, so its outcome is unknown.",
          "Check whether the operation completed before retrying, so it is not done twice.", True),
    _cond("upstream.unreachable", 502,
          "The upstream service could not be reached.",
          "Check network reachability, then try again.", True),
    _cond("model.paid_confirm_required", 400,
          "That model is paid per token, so switching to it needs your explicit spend confirmation first. Nothing was changed.",
          "Confirm the paid switch (the confirmation applies to this switch only), or choose a free model.", False),
    _cond("model.cost_unknown", 400,
          "What that model costs could not be classified, so it was not pinned. Nothing was changed.",
          "Confirm the switch explicitly to spend provider credits, or choose a model with a known price.", False),
)

_CONDITION_INDEX: dict[str, RequestCondition] = {}
for _c in _REQUEST_CONDITIONS:
    if _c.code in _CONDITION_INDEX:
        raise RuntimeError(f"diagnostic condition declared twice: {_c.code}")
    _CONDITION_INDEX[_c.code] = _c


def condition_codes() -> tuple[str, ...]:
    """Every request/upstream condition code, sorted."""
    return tuple(sorted(_CONDITION_INDEX))


def namespaced_fault_code(fault_code: str) -> str:
    """``provider_unavailable`` -> ``vool.provider_unavailable`` (fault passthrough)."""
    return f"{NAMESPACE}.{str(fault_code or '').strip()}"


def diagnostic_envelope(
    condition: str,
    *,
    message: str = "",
    detail: str = "",
    recovery: str = "",
    retryable: bool | None = None,
    correlation_id: str = "",
    turn_key: str = "",
    http_status: int | None = None,
    upstream_status: int | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    """Build one ``diagnostic`` envelope from a condition code.

    ``condition`` is either a request/upstream condition (``vool.request.conflict``) or
    a bare fault-catalog code (``timeout``, ``wallet_outbound_refused``) which is
    namespaced automatically. Message, recovery and retryability default from the
    owning vocabulary entry -- the frozen FaultSpec for fault codes, the closed
    registry above for boundary conditions -- so two call sites cannot drift apart.
    """
    code = str(condition or "").strip()
    if not code.startswith(f"{NAMESPACE}."):
        code = namespaced_fault_code(code)
    spec_message = ""
    spec_recovery = ""
    spec_retryable: bool | None = None
    entry = _CONDITION_INDEX.get(code)
    if entry is not None:
        spec_message = entry.message
        spec_recovery = entry.recovery
        spec_retryable = entry.retryable
        if http_status is None:
            http_status = entry.http_status
    elif code.startswith(f"{NAMESPACE}."):
        fault_code = code[len(NAMESPACE) + 1:]
        try:
            spec = get_spec(fault_code)
        except UnknownFaultCodeError:
            spec = None
        if spec is not None:
            spec_message = spec.user_message
            spec_recovery = spec.operator_action
            spec_retryable = spec.retry in (RETRY_NOW, RETRY_LATER)
    envelope: dict[str, Any] = {
        "schema": DIAGNOSTIC_SCHEMA,
        "code": code,
        "message": str(message or spec_message),
        "recovery": str(recovery or spec_recovery),
        "retryable": bool(spec_retryable) if retryable is None else bool(retryable),
    }
    if detail:
        envelope["detail"] = str(detail)[:400]
    if correlation_id:
        envelope["correlation_id"] = str(correlation_id)[:128]
    if turn_key:
        envelope["turn_key"] = str(turn_key)[:128]
    if http_status is not None:
        envelope["http_status"] = int(http_status)
    if upstream_status is not None:
        envelope["upstream_status"] = int(upstream_status)
    if retry_after_seconds is not None:
        with contextlib.suppress(TypeError, ValueError):
            envelope["retry_after_seconds"] = max(0.0, float(retry_after_seconds))
    return envelope


def with_diagnostic(payload: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    """Attach the envelope to an existing error payload, keeping every legacy key.

    Existing consumers read ``error``/``code``/``detail``; the envelope is additive so
    no endpoint needs to be wholesale renamed to gain stable diagnostics."""
    merged = dict(payload or {})
    merged["diagnostic"] = envelope
    return merged


def gateway_condition(dispatch_state: str, upstream_status: int | None) -> tuple[int, str]:
    """Map TYPED transport facts to ``(http_status, condition_code)`` for a gateway-style
    operation (VOOL resending/approving something on the user's behalf).

    Laws:
    - a request that never left this machine is NOT a transport failure: HTTP 400,
      ``vool.request.invalid`` -- never a fake gateway error for a contract refusal;
    - a genuine x402 payment challenge keeps HTTP 402 (protocol exception preserved);
    - upstream throttling surfaces as 429;
    - sent but unanswered in time is a 504 gateway timeout with unknown outcome;
    - an upstream that answered and failed/refused is a 502 with the real status
      attached in the envelope (the response's own status never claims to BE the
      upstream's status).
    """
    state = str(dispatch_state or "").strip()
    status = int(upstream_status) if isinstance(upstream_status, int) else None
    if state == DISPATCH_NOT_SENT:
        return 400, f"{NAMESPACE}.request.invalid"
    if status == 402:
        return 402, f"{NAMESPACE}.upstream.payment_challenge"
    if status == 429:
        return 429, f"{NAMESPACE}.upstream.throttled"
    if state == DISPATCH_OUTCOME_UNKNOWN:
        return 504, f"{NAMESPACE}.upstream.timeout"
    if status is not None and 500 <= status <= 599:
        return 502, f"{NAMESPACE}.upstream.failed"
    if status is not None and 400 <= status <= 499:
        return 502, f"{NAMESPACE}.upstream.refused"
    return 502, f"{NAMESPACE}.upstream.failed"


#: Machine-readable reason codes ``core.cloud_model_control.set_cloud_model`` embeds at
#: the START of its paid-pin refusal messages (its documented structured contract), each
#: mapped to its registered condition above.
_MODEL_REFUSAL_CONDITIONS: dict[str, str] = {
    "PAID_MODEL_CONFIRM_REQUIRED": f"{NAMESPACE}.model.paid_confirm_required",
    "MODEL_COST_UNKNOWN": f"{NAMESPACE}.model.cost_unknown",
    "PAID_STATUS_UNKNOWN": f"{NAMESPACE}.model.cost_unknown",
}


def model_pin_refusal(message: str) -> tuple[int, str] | None:
    """Map a ``set_cloud_model`` refusal to ``(http_status, condition_code)`` from its
    STRUCTURED leading machine reason, never by sniffing prose.

    Returns ``None`` when the message carries no machine reason (callers keep their
    existing behavior for those).
    """
    text = str(message or "")
    for machine_code, condition in _MODEL_REFUSAL_CONDITIONS.items():
        if text.startswith(f"{machine_code}: "):
            entry = _CONDITION_INDEX.get(condition)
            return (entry.http_status if entry else 400), condition
    return None


__all__ = [
    "DIAGNOSTIC_SCHEMA",
    "DISPATCH_NOT_SENT",
    "DISPATCH_OUTCOME_UNKNOWN",
    "DISPATCH_RESPONSE_RECEIVED",
    "NAMESPACE",
    "RequestCondition",
    "condition_codes",
    "diagnostic_envelope",
    "gateway_condition",
    "model_pin_refusal",
    "namespaced_fault_code",
    "with_diagnostic",
]
