"""A manifest that does not declare tool-calling support must never rank for a tool_intent turn.

`_hard_exclusion_reason` is the live enforcement point (`core/model_selection_policy.py`) that
keeps a tool-required turn from reaching a model with no tool support: `capability_score(...) <=
0.0` for the turn's `required_capabilities` excludes the manifest outright, before ranking ever
scores it. This is what "mark tools_supported incorrectly" attacks -- a manifest whose declared
`capabilities` list wrongly claims (or wrongly omits) tool support changes which lane a
tool-required turn can reach.
"""
from __future__ import annotations

from core.model_capabilities import required_capabilities
from core.model_selection_policy import ModelSelectionRequest, _hard_exclusion_reason
from storage.model_provider_manifest import ModelProviderManifest


def _manifest(*, capabilities: list[str]) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="local-qwen-http",
        model_name="qwen-local",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        weight_location="user-supplied",
        weights_bundled=False,
        redistribution_allowed=True,
        runtime_dependency="openai-compatible-local-runtime",
        capabilities=capabilities,
        runtime_config={"base_url": "http://127.0.0.1:1234"},
        enabled=True,
        metadata={"orchestration_role": "drone"},
    )


def _tool_intent_request() -> tuple[ModelSelectionRequest, object]:
    request = ModelSelectionRequest(task_kind="chat", output_mode="tool_intent")
    required = required_capabilities(request.task_kind, request.output_mode)
    assert "tool_intent" in required, "this test assumes tool_intent output_mode requires the tool_intent capability"
    return request, required


def test_a_manifest_without_tool_intent_capability_is_hard_excluded() -> None:
    manifest = _manifest(capabilities=["summarize", "format"])
    request, required = _tool_intent_request()

    assert _hard_exclusion_reason(manifest, request, required=required) == "no_required_capability"


def test_a_manifest_that_declares_tool_intent_capability_is_not_excluded_on_that_ground() -> None:
    manifest = _manifest(capabilities=["summarize", "format", "structured_json", "tool_intent"])
    request, required = _tool_intent_request()

    assert _hard_exclusion_reason(manifest, request, required=required) == ""


def test_a_plain_text_turn_does_not_require_the_tool_intent_capability() -> None:
    """Control: the SAME under-capable manifest is fine for a turn that never asked for tools."""
    manifest = _manifest(capabilities=["summarize", "format"])
    request = ModelSelectionRequest(task_kind="chat", output_mode="plain_text")
    required = required_capabilities(request.task_kind, request.output_mode)

    assert _hard_exclusion_reason(manifest, request, required=required) == ""
