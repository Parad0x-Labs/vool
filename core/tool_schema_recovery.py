"""Regenerate rejected arguments before execution, within the existing call authority."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any

from adapters.base_adapter import ModelRequest

_CORRECTION = (
    "The preceding tool selection was rejected by argument-schema validation. "
    "No tool from that selection was executed. Generate a fresh selection for the "
    "same request using only the offered function names and their exact JSON schemas. "
    "Include required fields, use the declared value types, and omit undeclared fields. "
    "Do not invent identifiers or silently substitute one argument name for another. "
    "If a required value cannot be grounded in the request or observations, ask for "
    "clarification with respond.direct if that function is offered. This correction "
    "grants no additional permission to execute any action."
)


def corrected_tool_request(request: ModelRequest) -> ModelRequest:
    # Never replay rejected output as instructions or mutate the caller's history. The
    # offered schemas and original observations remain the source of argument truth.
    messages = [dict(message) for message in request.messages]
    if messages:
        messages.append({"role": "user", "content": _CORRECTION})
    metadata = dict(request.metadata or {})
    metadata.pop("tool_call_resolution", None)
    metadata["tool_schema_correction"] = True
    return replace(
        request, messages=messages, prompt=request.prompt + "\n\n" + _CORRECTION,
        metadata=metadata,
    )


def admit_paid_tool_correction(
    *, manifest: Any, authorization: Any, original: ModelRequest,
    corrected: ModelRequest, prompt_tokens: int,
) -> bool:
    """Price BOTH attempts, including schemas/feedback, against the same held funds.

    This is the existing tariff estimator, not a promise about a provider's invoice.
    Unknown pricing or missing authority refuses the extra dispatch. No reservation
    is enlarged and an unpriced first response is never treated as a free call.
    """
    from core.cloud_tool_call_contract import openai_tool_payload
    from core.model_pricing import estimate_call_usd, provider_slot_for_manifest
    from core.model_spend_ledger import get_spend_reservation, settle_spend
    from core.output_budget_policy import lane_resolved_output_tokens

    model_call_id = str(getattr(authorization, "model_call_id", "") or "")
    if not model_call_id:
        return False
    try:
        held = get_spend_reservation(model_call_id)
        if held is None or str(held.status) != "reserved":
            return False
        total = 0.0
        for request in (original, corrected):
            # Include the actual request and schemas, not just the original task's
            # often much smaller token hint. Bytes/3 is a conservative estimate for
            # this JSON/text envelope; exact tokenizer accounting is provider-owned.
            envelope = json.dumps({
                "messages": request.messages, "prompt": request.prompt,
                "system": request.system_prompt,
                "context": request.context, "attachments": request.attachments,
                "tools": [openai_tool_payload(tool) for tool in request.tools],
            }, ensure_ascii=False)
            estimated_prompt = max(int(prompt_tokens or 0), math.ceil(len(envelope.encode()) / 3))
            estimate = estimate_call_usd(
                provider_id=provider_slot_for_manifest(manifest),
                model_id=str(manifest.model_name or ""),
                prompt_tokens=estimated_prompt,
                completion_tokens=lane_resolved_output_tokens(
                    manifest, base_tokens=int(request.max_output_tokens or 0),
                    output_mode=request.output_mode,
                ),
            )
            if not estimate.known or not math.isfinite(float(estimate.usd)) or estimate.usd < 0:
                return False
            total += float(estimate.usd)
        if not math.isfinite(float(held.reserved_usd)) or total > float(held.reserved_usd) + 1e-9:
            return False
        # Parsing failed after transport returned. The replacement's usage cannot
        # price that first attempt. Retain its uncertainty DURABLY before the second
        # dispatch; successful settlement will record the replacement's known charge
        # while keeping this hold for reconciliation, even after restart.
        terminal = settle_spend(model_call_id, actual_usd=None)
        return str(terminal.status) == "billing_ambiguous"
    except Exception:  # Any admission/ledger failure must prevent the extra paid dispatch.
        return False
