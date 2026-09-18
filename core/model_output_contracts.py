from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from core.model_output_guard import foreign_markers, scrub_foreign_markers

OutputMode = Literal["plain_text", "json_object", "action_plan", "tool_intent", "summary_block"]


@dataclass(frozen=True)
class OutputContract:
    mode: OutputMode
    required_keys: tuple[str, ...] = ()
    description: str = ""


CONTRACTS: dict[str, OutputContract] = {
    "plain_text": OutputContract(mode="plain_text", description="Free-form plain text"),
    "json_object": OutputContract(mode="json_object", description="Single JSON object"),
    "action_plan": OutputContract(mode="action_plan", required_keys=("summary", "steps"), description="JSON with summary and ordered steps"),
    "tool_intent": OutputContract(mode="tool_intent", required_keys=("intent",), description="JSON tool intent with optional arguments"),
    "summary_block": OutputContract(mode="summary_block", required_keys=("summary",), description="JSON with summary and optional bullets"),
}


@dataclass
class ContractValidation:
    ok: bool
    mode: str
    normalized_text: str
    structured_output: Any = None
    error: str | None = None
    confidence_penalty: float = 0.0
    warnings: list[str] = field(default_factory=list)


def get_contract(mode: str) -> OutputContract:
    return CONTRACTS.get(mode, CONTRACTS["plain_text"])


def validate_contract(mode: str, raw_text: str) -> ContractValidation:
    """Validate a provider output and guarantee the user-visible string is guarded.

    The guard used to run ONLY inside the plain_text branch, so `json_object` / `summary_block` /
    `action_plan` / `tool_intent` reached the user with leaked tool syntax intact. The error paths
    were worse: they return the RAW model text as `normalized_text`, unguarded, on every malformed
    response. This wrapper closes both by scrubbing whatever string the caller will actually show.
    """
    result = _validate_contract(mode, raw_text)
    if get_contract(mode).mode == "plain_text":
        return result  # already guarded inside the plain_text branch
    visible = str(result.normalized_text or "")
    if not visible or not foreign_markers(visible):
        return result
    cleaned = scrub_foreign_markers(visible)
    return replace(
        result,
        normalized_text=cleaned,
        confidence_penalty=max(float(result.confidence_penalty or 0.0), 0.15),
        warnings=[*(result.warnings or []), "foreign_tool_markers_stripped"],
    )


def _validate_contract(mode: str, raw_text: str) -> ContractValidation:
    contract = get_contract(mode)
    if contract.mode == "plain_text":
        stripped = str(raw_text or "").strip()
        if not foreign_markers(stripped):
            return ContractValidation(ok=True, mode=mode, normalized_text=stripped, structured_output=None)
        # A model leaked another platform's tool-call vocabulary into a plain-text answer. Scrub it
        # so it never reaches the user or the memory store, and distrust the turn. If nothing real
        # survives, the "answer" was purely a foreign tool call — fail the contract so routing
        # degrades / falls back instead of showing the fake tool request.
        cleaned = scrub_foreign_markers(stripped)
        if cleaned:
            return ContractValidation(
                ok=True,
                mode=mode,
                normalized_text=cleaned,
                structured_output=None,
                confidence_penalty=0.15,
                warnings=["foreign_tool_markers_stripped"],
            )
        return ContractValidation(
            ok=False,
            mode=mode,
            normalized_text="",
            structured_output=None,
            error="foreign_tool_syntax",
            confidence_penalty=0.5,
            warnings=["foreign_tool_markers_only"],
        )

    try:
        payload = json.loads(raw_text)
    except Exception as exc:
        return ContractValidation(
            ok=False,
            mode=mode,
            normalized_text=str(raw_text or "").strip(),
            structured_output=None,
            error=f"invalid_json:{exc}",
            confidence_penalty=0.25,
        )

    if not isinstance(payload, dict):
        return ContractValidation(
            ok=False,
            mode=mode,
            normalized_text=str(raw_text or "").strip(),
            structured_output=payload,
            error="json_not_object",
            confidence_penalty=0.2,
        )

    missing = [key for key in contract.required_keys if key not in payload]
    if missing:
        return ContractValidation(
            ok=False,
            mode=mode,
            normalized_text=str(raw_text or "").strip(),
            structured_output=payload,
            error=f"missing_keys:{','.join(missing)}",
            confidence_penalty=0.18,
        )

    normalized_text = _normalize_payload(contract.mode, payload)
    warnings: list[str] = []
    if contract.mode == "action_plan" and not isinstance(payload.get("steps"), list):
        warnings.append("steps_not_list")
    return ContractValidation(
        ok=True,
        mode=mode,
        normalized_text=normalized_text,
        structured_output=payload,
        warnings=warnings,
    )


def _normalize_payload(mode: str, payload: dict[str, Any]) -> str:
    if mode == "json_object":
        return json.dumps(payload, sort_keys=True)
    if mode == "action_plan":
        steps = payload.get("steps") or []
        if not isinstance(steps, list):
            steps = [str(steps)]
        return f"{payload.get('summary', '')}\n" + "\n".join(f"- {step}" for step in steps[:8])
    if mode == "tool_intent":
        arguments = payload.get("arguments") or {}
        return f"Intent: {payload.get('intent', '')}\nArguments: {json.dumps(arguments, sort_keys=True)}"
    if mode == "summary_block":
        bullets = payload.get("bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]
        lines = [str(payload.get("summary") or "").strip()]
        lines.extend(f"- {bullet}" for bullet in bullets[:6])
        return "\n".join(line for line in lines if line.strip())
    return json.dumps(payload, sort_keys=True)


def json_schema_for_mode(mode: str) -> dict[str, Any] | None:
    """Return a provider-native schema; runtime validation remains authoritative."""
    normalized = str(mode or "").strip().lower()
    schemas: dict[str, dict[str, Any]] = {
        "json_object": {"type": "object", "additionalProperties": True},
        "action_plan": {
            "type": "object",
            "required": ["summary", "steps"],
            "properties": {"summary": {"type": "string"}, "steps": {"type": "array", "items": {}}},
            "additionalProperties": True,
        },
        "tool_intent": {
            "type": "object",
            "required": ["intent"],
            "properties": {
                "intent": {"type": "string", "minLength": 1},
                "arguments": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": True,
        },
        "summary_block": {
            "type": "object",
            "required": ["summary"],
            "properties": {
                "summary": {"type": "string"},
                "bullets": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
    }
    schema = schemas.get(normalized)
    return dict(schema) if schema is not None else None


__all__ = ["CONTRACTS", "ContractValidation", "OutputContract", "get_contract", "json_schema_for_mode", "validate_contract"]
