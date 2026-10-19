"""Which check turned a model reply into a degraded notice, recorded as codes only.

A benchmark run (2026-10-15) saw "I couldn't get a usable model response in this run ..." on
turns whose provider was healthy seconds before and after, and nothing recorded which check had
set the reply aside: the turn kept the execution source and a generic failure code, never the
branch that refused the text or the validation that emptied it. This receipt names both so the
next real occurrence can be diagnosed. It holds codes, counts and model identifiers only, never
the user's text or the model's text.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA = "vool.reply_rejection.v1"

#: rendered_via values under which the turn published a runtime notice instead of a model reply.
REJECTED_BRANCHES = frozenset({
    "honest_degraded_chat",
    "output_contract_failed_refused",
    "internal_payload_refused",
    "multi_part_incomplete_refused",
})

_NON_CODE_RE = re.compile(r"[^a-z0-9_.\-]+")


def _code(value: Any, *, limit: int = 64) -> str:
    return _NON_CODE_RE.sub("_", str(value or "").strip().lower()).strip("_")[:limit]


_LEADING_CODE_RE = re.compile(r"[a-z0-9_.\-]+")


def _leading_code(value: Any) -> str:
    # Block reasons are a gate or policy code, sometimes followed by an exception message
    # ("explicit_heavy_lane_failed: <provider error>"). Only the leading code is kept.
    match = _LEADING_CODE_RE.match(str(value or "").strip().lower())
    return match.group(0)[:64] if match else ""


def _contract_error_code(error: Any) -> str:
    # Contract errors are `name` or `name:detail`. Only `missing_keys` carries a detail worth
    # keeping (schema key names); the others carry parser messages, which are dropped.
    text = str(error or "").strip()
    if not text:
        return ""
    name, _, detail = text.partition(":")
    if _code(name) == "missing_keys":
        keys = [_code(key, limit=32) for key in detail.split(",") if _code(key, limit=32)]
        return "missing_keys:" + ",".join(keys[:8])
    return _code(name)


def _rejecting_check(
    *,
    rendered_via: str,
    source: str,
    validation_state: str,
    used_model: bool,
    has_output: bool,
) -> str:
    if source in {"exact_cache_hit", "memory_hit"}:
        return "cache_or_memory_source"
    if validation_state == "contract_failed":
        return "output_contract"
    if rendered_via == "internal_payload_refused":
        return "internal_payload"
    if rendered_via == "multi_part_incomplete_refused":
        return "missing_requested_parts"
    if not used_model:
        return "no_model_reply"
    if not has_output:
        return "empty_model_output"
    # A reply was produced, was valid, and was still set aside: no check above explains it.
    return "unknown"


def reply_rejection_receipt(model_execution: Any, *, rendered_via: str, response_reason: str) -> dict[str, Any] | None:
    """The receipt for a turn that set its model reply aside, or None when it did not."""
    if rendered_via not in REJECTED_BRANCHES:
        return None
    details = getattr(model_execution, "details", None)
    details = details if isinstance(details, dict) else {}
    source = _code(getattr(model_execution, "source", ""))
    validation_state = _code(getattr(model_execution, "validation_state", ""))
    used_model = bool(getattr(model_execution, "used_model", False))
    output_chars = len(str(getattr(model_execution, "output_text", "") or "").strip())
    structured_present = getattr(model_execution, "structured_output", None) is not None
    warnings = [_code(item) for item in list(details.get("warnings") or [])[:8] if _code(item)]
    return {
        "schema": SCHEMA,
        "rejecting_check": _rejecting_check(
            rendered_via=rendered_via,
            source=source,
            validation_state=validation_state,
            used_model=used_model,
            has_output=bool(output_chars or structured_present),
        ),
        "branch": _code(rendered_via),
        "response_reason": _code(response_reason),
        "execution_source": source,
        "validation_state": validation_state,
        "used_model": used_model,
        "model_was_attempted": bool(details.get("model_was_attempted")) or used_model,
        "output_chars": output_chars,
        "structured_output_present": structured_present,
        "contract_error": _contract_error_code(details.get("contract_error")),
        "warnings": warnings,
        "block_reason": _leading_code(details.get("block_reason") or details.get("reason")),
        "provider_id": str(getattr(model_execution, "provider_id", "") or "")[:120],
        "model_name": str(getattr(model_execution, "model_name", "") or "")[:120],
        "attempted_count": len(list(details.get("attempted") or [])),
    }
