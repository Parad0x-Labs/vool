"""Exact-closure context delivery ledger (surveyor's close, bookkeeper's trial).

What is EXACT here: the payload measurement — UTF-8 bytes, code points and
the canonical-bytes hash (the same bytes the permit hash binds). What is an
ESTIMATE: every token figure. Token counts come from the char-class
estimator (prompt_budget), calibrated with margin against one measured local
tokenizer (qwen2.5:7b); they are not model tokenization, not a proven bound
for every served tokenizer/chat-template, and carry no claim of exactness.

At provider-request seal time this module measures the payload the provider
will actually receive, re-estimates the input tokens over the EXACT payload
(message content AND message tool-call fields, plus the tool and
structured-output schemas the message fitter never sees), reconciles against
what the chain debited, records a receipt, and — behind
``VOOL_CONTEXT_ENVELOPE_ENFORCE`` — refuses pre-send when the payload's
token ESTIMATE exceeds the resolved window envelope.

Law (deterministic, on the estimator's own numbers):

    refuse  <=>  W_ctx > 0  AND  E > W_ctx - R
    W_ctx = payload.options.num_ctx | metadata.prompt_budget.num_ctx | 0
    R     = _wire_reserved_output_tokens(payload, request.max_output_tokens)
    E     = estimate_message_tokens(payload.messages)
          + ceil(estimate_text_tokens(native tool text))
          + ceil(estimate_text_tokens(response_format/format JSON))

``W_ctx == 0`` (cloud lanes with no local window) is receipt-only: without a
resolved envelope, an oversize claim is not one this module may invent.
A chars-based law is deliberately NOT used: chars/4 under-reads Han, Hangul
and digit runs by 3-4.6x against the measured tokenizer (pinned in
prompt_budget's own tests), so it would false-pass exactly the payloads that
are the documented failure mode.

Two lanes, never conflated:

- ENFORCEMENT (mandatory when the flag is on): if the measurement itself
  fails, the seal refuses (ProviderPayloadMeasurementFailedError) — an
  unmeasurable payload is a payload that cannot be proven to fit. If the
  measurement succeeds and the law says refuse, the receipt records the
  actual refusal BEFORE the typed error raises.
- OBSERVABILITY (always best-effort): receipt build/persist/attach failures
  are logged and swallowed; they can never disable enforcement and never
  fail the seal.

Receipt truth law: ``over_envelope`` reports the observation (the estimate
exceeds the envelope); ``refused`` reports what actually happened at this
seal. With enforcement off, an over-estimate is recorded as observed, NOT
refused. With enforcement on, ``refused`` is true only for the receipt that
accompanied a real typed refusal.

No prompt text is stored — sizes, hashes and identifiers only.
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from core.prompt_budget import (
    PromptBudgetExceededError,
    estimate_message_tokens,
    estimate_text_tokens,
)
from core.provider_invocation_gateway import (
    ProviderInvocationValidationError,
    _wire_reserved_output_tokens,
    canonical_payload_bytes,
    payload_hash,
)

LOGGER = logging.getLogger(__name__)

SCHEMA = "vool.context_delivery_ledger.v1"
ENFORCE_FLAG = "VOOL_CONTEXT_ENVELOPE_ENFORCE"


class ProviderPayloadEnvelopeExceededError(PromptBudgetExceededError):
    """The measured payload's token estimate exceeds the resolved window
    envelope and enforcement refused it.

    Subclasses PromptBudgetExceededError so the router's existing handler
    retries other candidates, skips health poisoning and classifies the
    failure as CONTEXT_LENGTH_EXCEEDED.
    """


class ProviderPayloadMeasurementFailedError(ProviderInvocationValidationError):
    """Enforcement was enabled and the sealed payload could not be measured.

    Subclasses ProviderInvocationValidationError so the router's existing
    fail-closed branch applies (local context-contract defect, no provider
    health poisoning). An unmeasurable payload cannot be proven to fit the
    envelope, so the mandatory lane refuses rather than return a permit.
    """


def _native_tool_text(tools: Iterable[Any]) -> str:
    try:
        from core.prompt_payload_breakdown import _native_tool_text as _ntt
        return _ntt(tools)
    except Exception:
        # the schema-serialization helper is internal; a failure to import
        # must not silence the ledger — fall back to canonical JSON
        try:
            return json.dumps(list(tools or []), sort_keys=True,
                              ensure_ascii=False)
        except Exception:
            return ""


def measure_provider_payload(
    payload: dict[str, Any],
    *,
    metadata_num_ctx: Any = None,
    max_output_tokens: Any = None,
) -> dict[str, Any]:
    """Exact measurement + envelope verdict for one sealed payload (PURE)."""
    canonical = canonical_payload_bytes(payload)
    options = payload.get("options")
    w_ctx = 0
    w_source = "unresolved"
    try:
        candidate = int(options.get("num_ctx")) if isinstance(options, dict) else 0
    except (TypeError, ValueError):
        candidate = 0
    if candidate and candidate > 0:
        w_ctx, w_source = candidate, "payload.options.num_ctx"
    else:
        try:
            fallback = int(metadata_num_ctx)
        except (TypeError, ValueError):
            fallback = 0
        if fallback and fallback > 0:
            w_ctx, w_source = fallback, "metadata.prompt_budget"

    reserved = _wire_reserved_output_tokens(payload, max_output_tokens)
    available = max(0, w_ctx - reserved)

    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        messages_tokens = estimate_message_tokens(messages)
    else:
        prompt_text = payload.get("prompt")
        prompt_tokens = (
            math.ceil(estimate_text_tokens(str(prompt_text))) + 10
            if prompt_text is not None else 0
        )
        messages_tokens = prompt_tokens
    tools_tokens = math.ceil(
        estimate_text_tokens(_native_tool_text(payload.get("tools") or ())))
    fmt = payload.get("response_format") or payload.get("format") or ""
    try:
        fmt_text = fmt if isinstance(fmt, str) else json.dumps(
            fmt, sort_keys=True, ensure_ascii=False)
    except Exception:
        fmt_text = str(fmt or "")
    format_tokens = math.ceil(estimate_text_tokens(fmt_text)) if fmt_text else 0
    estimated_input = messages_tokens + tools_tokens + format_tokens

    over = bool(w_ctx > 0 and estimated_input > available)
    return {
        "payload_hash": payload_hash(payload),
        "payload_chars": len(canonical.decode("utf-8", errors="replace")),
        "payload_bytes": len(canonical),
        "estimated_input_tokens": estimated_input,
        "estimated_messages_tokens": messages_tokens,
        "estimated_tool_schema_tokens": tools_tokens,
        "estimated_format_schema_tokens": format_tokens,
        "envelope": {
            "num_ctx": w_ctx,
            "reserved_output_tokens": reserved,
            "available_prompt_tokens": available,
            "source": w_source,
        },
        "over_envelope": over,
    }


def build_receipt(
    measurement: dict[str, Any],
    *,
    request_id: str = "",
    provider_id: str = "",
    model_id: str = "",
    operation: str = "",
    manifest_id: str = "",
    prompt_budget: dict[str, Any] | None = None,
    enforcement_enabled_at_seal: bool = False,
) -> dict[str, Any]:
    """Assemble the v1 receipt with the chain's closure reconciliation."""
    budget = prompt_budget if isinstance(prompt_budget, dict) else None
    debits: list[dict[str, Any]] = []
    d_total = 0
    debits_source = "none"
    if budget and budget.get("estimated_prompt_tokens_after") is not None:
        d_total = int(budget.get("estimated_prompt_tokens_after") or 0)
        debits_source = "fitter"
        debits.append({
            "name": "fitter_messages",
            "estimator": "content_aware",
            "chars": None,
            "tokens": d_total,
        })
    closure_error_tokens = int(
        measurement["estimated_input_tokens"]) - d_total
    return {
        "schema": SCHEMA,
        "receipt_id": f"cdl-{uuid.uuid4().hex}",
        "manifest_id": manifest_id,
        "request_id": str(request_id),
        "provider_id": str(provider_id),
        "model_id": str(model_id),
        "operation": str(operation),
        "payload_hash": measurement["payload_hash"],
        "payload_chars": measurement["payload_chars"],
        "payload_bytes": measurement["payload_bytes"],
        "estimated_input_tokens": measurement["estimated_input_tokens"],
        "estimated_messages_tokens": measurement.get(
            "estimated_messages_tokens"),
        "estimated_tool_schema_tokens": measurement.get(
            "estimated_tool_schema_tokens"),
        "estimated_format_schema_tokens": measurement.get(
            "estimated_format_schema_tokens"),
        "sum_of_estimated_token_debits": d_total,
        "debits_source": debits_source,
        "debits": debits,
        "closure_error_tokens_est": closure_error_tokens,
        "envelope": measurement["envelope"],
        "over_envelope": measurement["over_envelope"],
        "enforcement_enabled": bool(enforcement_enabled_at_seal),
        "refused": False,
        "refusal_reason": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _init_table() -> None:
    from storage.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS context_delivery_ledgers (
                receipt_id TEXT PRIMARY KEY,
                manifest_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                provider_id TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                operation TEXT NOT NULL DEFAULT '',
                payload_hash TEXT NOT NULL,
                over_envelope INTEGER NOT NULL,
                refused INTEGER NOT NULL,
                receipt_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cdl_created ON "
            "context_delivery_ledgers(created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def record_delivery_receipt(receipt: dict[str, Any]) -> str:
    """Persist one receipt (observability lane, best-effort by contract).

    Any persist failure — sqlite or not — is swallowed after a debug log:
    the receipt also rides the request metadata, and an observability
    failure here must never mask the envelope verdict or fail the seal."""
    try:
        _init_table()
        from storage.db import get_connection
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO context_delivery_ledgers (
                    receipt_id, manifest_id, request_id, provider_id,
                    model_id, operation, payload_hash, over_envelope,
                    refused, receipt_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(receipt.get("receipt_id")),
                    str(receipt.get("manifest_id") or ""),
                    str(receipt.get("request_id") or ""),
                    str(receipt.get("provider_id") or ""),
                    str(receipt.get("model_id") or ""),
                    str(receipt.get("operation") or ""),
                    str(receipt.get("payload_hash")),
                    1 if receipt.get("over_envelope") else 0,
                    1 if receipt.get("refused") else 0,
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                    str(receipt.get("created_at")),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        LOGGER.debug("context delivery receipt persist failed", exc_info=True)


def enforcement_enabled() -> bool:
    return os.environ.get(ENFORCE_FLAG, "").strip() not in ("", "0", "false")


def maybe_refuse(
    measurement: dict[str, Any],
    *,
    enforce: bool | None = None,
) -> None:
    """The fail-closed law. Raises the typed error when the payload's token
    estimate exceeds the resolved envelope AND enforcement is enabled.

    ``enforce`` lets a caller use one flag reading for both the receipt's
    ``refused`` truth and this refusal, so the two can never disagree."""
    enabled = enforcement_enabled() if enforce is None else bool(enforce)
    if not (enabled and measurement["over_envelope"]):
        return
    envelope = measurement["envelope"]
    raise ProviderPayloadEnvelopeExceededError(
        "prompt_budget_exceeded: exact sealed payload "
        f"({measurement['estimated_input_tokens']} est. input tokens) exceeds "
        f"the resolved window envelope ({envelope['num_ctx']} num_ctx - "
        f"{envelope['reserved_output_tokens']} reserved = "
        f"{envelope['available_prompt_tokens']} available); refusing pre-send",
        telemetry={
            "num_ctx": envelope["num_ctx"],
            "available_prompt_tokens": envelope["available_prompt_tokens"],
            "estimated_prompt_tokens_after": measurement["estimated_input_tokens"],
            "estimated_tool_schema_tokens": measurement.get(
                "estimated_tool_schema_tokens"),
            "source": "context_delivery_ledger",
        },
    )
