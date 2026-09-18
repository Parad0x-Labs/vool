"""Agent-to-agent x402 payment tool intents.

- ``pay.x402`` — RETIRED as a payer. The only money authority is ``core.wallet``
  (proposal -> simulation -> limits/effect-budget reservation -> operator
  approval -> signing -> broadcast -> receipt); the x402 buy flow is the
  wallet's ``fetch_paid_resource`` / ``retry_paid_resource`` behind
  ``POST /api/wallet/x402/fetch``. This intent answers every call with the
  typed, receipt-backed refusal: no wallet is loaded, nothing is signed, no
  facilitator is contacted. The ``*_fn`` seams stay in the signature so a
  caller (or a test) can prove the retired payer is never invoked.

- ``sell.quote`` — QUOTE VOOL's own compute for another agent. Read-only: it
  builds and returns the :class:`~core.x402.client.X402Quote` that
  :func:`core.null_protocol.resolve_null_request` already derives. No spend, no
  signing, no wallet. A ``.null`` target can be resolved to its x402 endpoint
  with :func:`core.null_resolver.resolve_x402_endpoint`.
"""
from __future__ import annotations

from typing import Any

from core.execution.models import ToolIntentExecution, _tool_observation
from core.null_protocol import NullProtocolError, resolve_null_request
from core.null_resolver import resolve_x402_endpoint


def execute_payment_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None = None,
    dna_pay_and_unlock_fn=None,
    dna_get_quote_fn=None,
    resolve_null_request_fn=resolve_null_request,
    resolve_x402_endpoint_fn=resolve_x402_endpoint,
) -> ToolIntentExecution:
    if intent == "sell.quote":
        return _execute_sell_quote(
            arguments,
            resolve_null_request_fn=resolve_null_request_fn,
            resolve_x402_endpoint_fn=resolve_x402_endpoint_fn,
        )
    if intent == "pay.x402":
        return _execute_pay_x402(
            arguments,
            source_context=source_context,
            dna_pay_and_unlock_fn=dna_pay_and_unlock_fn,
            dna_get_quote_fn=dna_get_quote_fn,
            resolve_x402_endpoint_fn=resolve_x402_endpoint_fn,
        )
    return _failed(
        intent,
        status="unsupported",
        response_text=f"`{intent}` is not a wired payment tool on this runtime.",
    )


def _execute_sell_quote(
    arguments: dict[str, Any],
    *,
    resolve_null_request_fn,
    resolve_x402_endpoint_fn,
) -> ToolIntentExecution:
    resource = _text(arguments.get("resource") or arguments.get("resource_url") or arguments.get("uri"))
    null_target = _text(arguments.get("null_name") or arguments.get("target"))
    resolved_endpoint = ""
    if null_target:
        try:
            resolved_endpoint = _text(resolve_x402_endpoint_fn(null_target))
        except Exception:
            resolved_endpoint = ""
    if not resource:
        # Default to a metered "task" service quote; an explicit ?price= on the
        # URI still wins inside resolve_null_request.
        resource = "null://task/quote"
    try:
        request = resolve_null_request_fn(resource)
    except NullProtocolError as exc:
        return _failed(
            "sell.quote",
            status="invalid_request",
            response_text=f"Could not build a quote for `{resource}`: {exc}",
        )
    quote = request.quote
    if quote is None:
        return _failed(
            "sell.quote",
            status="no_quote",
            response_text=f"No quote could be derived for `{resource}`.",
        )
    quote_payload = {
        "amount_usdc": float(quote.amount_usdc),
        "recipient_wallet": str(quote.recipient_wallet),
        "facilitator_url": str(quote.facilitator_url),
        "usdc_mint": str(quote.usdc_mint),
        "quote_hash": str(quote.quote_hash),
        "expires_at": float(quote.expires_at),
        "service": str(request.uri.service),
        "path": str(request.uri.path),
    }
    if resolved_endpoint:
        quote_payload["resolved_x402_endpoint"] = resolved_endpoint
    response_text = (
        f"Quote for `{request.uri.service}` compute: {quote_payload['amount_usdc']:.6f} USDC "
        f"to {quote_payload['recipient_wallet']} (quote {quote_payload['quote_hash'][:12]}…). "
        "Read-only — no payment was made."
    )
    return ToolIntentExecution(
        handled=True,
        ok=True,
        status="quoted",
        response_text=response_text,
        mode="tool_executed",
        tool_name="sell.quote",
        details={
            "quote": quote_payload,
            "resource": resource,
            "observation": _tool_observation(
                intent="sell.quote",
                tool_surface="x402_market",
                ok=True,
                status="quoted",
                resource=resource,
                quote=quote_payload,
            ),
        },
    )


def _execute_pay_x402(
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
    dna_pay_and_unlock_fn,
    dna_get_quote_fn,
    resolve_x402_endpoint_fn,
) -> ToolIntentExecution:
    """RETIRED `pay.x402`: the only payment door is core.wallet (wallet.propose / the x402 fetch-retry flow).

    Typed, receipt-backed refusal; no wallet is loaded, nothing is signed, no facilitator is contacted.
    """
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("tool.pay.x402")
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=fault.code,
        response_text=f"{fault.user_message} Fetch the resource through the wallet's x402 flow (POST /api/wallet/x402/fetch) or propose the payment with wallet.propose.",
        user_safe_response_text=f"{fault.user_message}",
        mode="tool_failed",
        tool_name="pay.x402",
        details={"fault": fault.to_dict(), "executed": False, "observation": _tool_observation(intent="pay.x402", tool_surface="payment", ok=False, status=fault.code, fault_id=fault.fault_id)},
    )


def _failed(
    intent: str,
    *,
    status: str,
    response_text: str,
    extra_details: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    details: dict[str, Any] = dict(extra_details or {})
    details["observation"] = _tool_observation(
        intent=intent,
        tool_surface="x402_market",
        ok=False,
        status=status,
    )
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response_text,
        user_safe_response_text=response_text,
        mode="tool_failed",
        tool_name=intent,
        details=details,
    )


def _text(value: Any) -> str:
    return str(value or "").strip()
