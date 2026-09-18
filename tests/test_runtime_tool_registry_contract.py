from __future__ import annotations

from core.runtime_execution_tools import runtime_execution_capability_ledger, runtime_execution_tool_specs


def test_runtime_tool_specs_are_covered_by_capability_ledger() -> None:
    capability_intents = {
        str(intent).strip()
        for capability in runtime_execution_capability_ledger()
        for intent in list(capability.get("intents") or [])
        if str(intent).strip()
    }
    spec_intents = [str(spec.get("intent") or "").strip() for spec in runtime_execution_tool_specs()]

    assert spec_intents
    assert len(spec_intents) == len(set(spec_intents))
    assert set(spec_intents).issubset(capability_intents)


def test_runtime_tool_specs_expose_explicit_contract_shape() -> None:
    specs = runtime_execution_tool_specs()

    assert specs
    for spec in specs:
        assert isinstance(spec.get("intent"), str) and str(spec["intent"]).strip()
        assert isinstance(spec.get("description"), str) and str(spec["description"]).strip()
        assert isinstance(spec.get("read_only"), bool)
        assert isinstance(spec.get("arguments"), dict)
        assert isinstance(spec.get("output_schema"), dict)
        assert isinstance(spec.get("side_effect_class"), str) and str(spec["side_effect_class"]).strip()
        assert isinstance(spec.get("approval_requirement"), str) and str(spec["approval_requirement"]).strip()
        assert isinstance(spec.get("timeout_policy"), str) and str(spec["timeout_policy"]).strip()
        assert isinstance(spec.get("retry_policy"), str) and str(spec["retry_policy"]).strip()
        assert isinstance(spec.get("artifact_emission"), str) and str(spec["artifact_emission"]).strip()
        assert isinstance(spec.get("error_contract"), str) and str(spec["error_contract"]).strip()
