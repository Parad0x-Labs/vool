from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    side_effect_class: str
    approval_requirement: str
    timeout_policy: str
    retry_policy: str
    artifact_emission: str
    error_contract: str

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


def validate_tool_contract(contract: ToolContract) -> ToolContract:
    if not str(contract.name or "").strip():
        raise ValueError("Tool contract requires a non-empty name.")
    if not str(contract.description or "").strip():
        raise ValueError(f"Tool `{contract.name}` requires a description.")
    if not isinstance(contract.input_schema, dict) or not contract.input_schema:
        raise ValueError(f"Tool `{contract.name}` requires an input schema.")
    if not isinstance(contract.output_schema, dict) or not contract.output_schema:
        raise ValueError(f"Tool `{contract.name}` requires an output schema.")
    for field_name in (
        "side_effect_class",
        "approval_requirement",
        "timeout_policy",
        "retry_policy",
        "artifact_emission",
        "error_contract",
    ):
        if not str(getattr(contract, field_name, "") or "").strip():
            raise ValueError(f"Tool `{contract.name}` requires `{field_name}`.")
    return contract
