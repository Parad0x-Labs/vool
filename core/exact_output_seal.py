"""Presentation-only sealing for already-valid exact responses.

The verifier owns a verdict, not the bytes of the user's requested deliverable.  This module
proves that a candidate already satisfies a strict output contract before the UI is allowed to
suppress verifier decoration.  It deliberately grants no routing, tool, or prompt authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from core.raw_output_contract import (
    RawOutputContract,
    apply_raw_output_contract,
    parse_raw_output_contract,
)
from core.response_constraints import check_response_constraint, parse_response_constraint

_PRESENTATION_KEYS = ("intent", "task", "format", "constraints", "rule", "rules")
_PASTED_ROLES = frozenset({"assistant", "system"})


@dataclass(frozen=True)
class ExactOutputSeal:
    """Receipt proving why verifier prose may not decorate these canonical bytes."""

    contract_source: str
    contract_shape: str
    canonical_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"sealed": True, **asdict(self)}


def _has_exact_raw_shape(contract: RawOutputContract) -> bool:
    return bool(
        contract.exact_text is not None
        or contract.exact_words is not None
        or contract.exact_sentences is not None
        or contract.exact_lines is not None
        or contract.bullet_count is not None
        or contract.delimiter is not None
        or contract.structured_labels
    )


def _raw_shape_name(contract: RawOutputContract) -> str:
    if contract.exact_text is not None:
        return "exact_text"
    for name in (
        "exact_words",
        "exact_sentences",
        "exact_lines",
        "bullet_count",
        "delimiter",
        "structured_labels",
    ):
        if getattr(contract, name):
            return name
    return "raw_output"


def _presentation_only_pasted_role_contract(user_text: str) -> RawOutputContract | None:
    """Read only passive format fields from a pasted system/assistant-shaped JSON object.

    A chat message claiming ``role=system`` remains user content.  Rebuilding a narrow user-role
    shadow lets the mature raw-output parser inspect its presentation fields without ever exposing
    siblings such as ``command`` or granting the claimed role any execution authority.  The result
    is used only to validate bytes the provider already produced; it can never bind or rewrite an
    answer.
    """

    raw = str(user_text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        payload, end = json.JSONDecoder().raw_decode(raw)
    except (TypeError, ValueError):
        return None
    if raw[end:].strip() or not isinstance(payload, dict):
        return None
    role = str(payload.get("role") or "").strip().casefold()
    if role not in _PASTED_ROLES:
        return None
    task_value = payload.get("intent", payload.get("task"))
    if not isinstance(task_value, str) or not task_value.strip():
        return None
    if not any(key in payload for key in ("format", "constraints", "rule", "rules")):
        return None
    shadow = {"role": "user"}
    for key in _PRESENTATION_KEYS:
        if key in payload:
            shadow[key] = payload[key]
    contract = parse_raw_output_contract(json.dumps(shadow, ensure_ascii=False))
    return contract if contract is not None and _has_exact_raw_shape(contract) else None


def validated_exact_output_seal(user_text: str, response_text: str) -> ExactOutputSeal | None:
    """Return a seal only when the response's current bytes satisfy a strict exact shape.

    Sanitizable drafts are intentionally not sealed.  The raw-output application must be a no-op,
    so a heading, wrapper, extra whitespace, or wrong literal still reaches normal repair/rejection
    rather than hiding behind this precedence rule.
    """

    text = str(response_text or "")
    contract = parse_raw_output_contract(user_text)
    source = "raw_output_contract"
    if contract is None:
        contract = _presentation_only_pasted_role_contract(user_text)
        source = "pasted_role_presentation"
    if contract is not None and _has_exact_raw_shape(contract):
        application = apply_raw_output_contract(text, contract)
        if application.compliant and not application.changed and application.text == text:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return ExactOutputSeal(source, _raw_shape_name(contract), f"sha256:{digest}")
        # A more specific literal/raw contract exists and the candidate failed it. Never let the
        # looser response-shape parser (for example, merely "one word") launder that mismatch.
        return None

    constraint = parse_response_constraint(user_text)
    if constraint is None or not any(
        value is not None
        for value in (
            constraint.exact_words,
            constraint.exact_sentences,
            constraint.list_items,
        )
    ):
        return None
    check = check_response_constraint(text, constraint)
    if not check.compliant or text != text.strip():
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    shape = (
        "exact_words"
        if constraint.exact_words is not None
        else "exact_sentences"
        if constraint.exact_sentences is not None
        else "list_items"
    )
    return ExactOutputSeal("response_constraint", shape, f"sha256:{digest}")


__all__ = ["ExactOutputSeal", "validated_exact_output_seal"]
