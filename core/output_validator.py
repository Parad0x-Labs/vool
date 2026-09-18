from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core import audit_logger
from core.model_output_contracts import ContractValidation, validate_contract


@dataclass
class OutputValidationResult:
    ok: bool
    normalized_text: str
    structured_output: Any = None
    error: str | None = None
    trust_penalty: float = 0.0
    warnings: list[str] = field(default_factory=list)


def validate_provider_output(
    *,
    provider_id: str,
    output_mode: str,
    raw_text: str,
    trace_id: str | None = None,
) -> OutputValidationResult:
    contract_result: ContractValidation = validate_contract(output_mode, raw_text)
    if not contract_result.ok:
        audit_logger.log(
            "model_output_contract_mismatch",
            target_id=provider_id,
            target_type="model_provider",
            trace_id=trace_id,
            details={
                "output_mode": output_mode,
                "error": contract_result.error,
            },
        )
        return OutputValidationResult(
            ok=False,
            normalized_text=contract_result.normalized_text,
            structured_output=contract_result.structured_output,
            error=contract_result.error,
            trust_penalty=contract_result.confidence_penalty,
            warnings=list(contract_result.warnings),
        )
    return OutputValidationResult(
        ok=True,
        normalized_text=contract_result.normalized_text,
        structured_output=contract_result.structured_output,
        trust_penalty=contract_result.confidence_penalty,
        warnings=list(contract_result.warnings),
    )
