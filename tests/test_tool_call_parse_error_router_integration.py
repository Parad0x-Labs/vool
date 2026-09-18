"""A malformed/unknown/duplicate tool call must reach the router as a typed, labeled failure.

core.cloud_tool_call_contract.ToolCallParseError (and its UNKNOWN_TOOL_NAME /
MALFORMED_TOOL_ARGUMENTS / DUPLICATE_TOOL_CALL subclasses) is raised by both the Ollama and cloud
parsers. This proves memory_first_router._invoke_manifest catches it as its own branch -- not the
generic `except Exception` -- and reports a specific error_kind, with the call itself never
producing a response (nothing is executed).
"""
from __future__ import annotations

from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from core.cloud_tool_call_contract import DuplicateToolCallError, MalformedToolArgumentsError, UnknownToolNameError
from core.memory_first_router import MemoryFirstRouter
from core.model_health import get_provider_health, reset_provider_health
from core.model_registry import ModelRegistry
from core.task_router import create_task_record
from storage.db import get_connection
from storage.migrations import run_migrations


@pytest.fixture(autouse=True)
def _clean_state():
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _manifest(registry: ModelRegistry):
    return registry.register_manifest(
        {
            "provider_name": "local-qwen-http", "model_name": "qwen-local", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied", "weights_bundled": False, "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:1234"}, "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )


@pytest.mark.parametrize(
    ("exc_cls", "expected_kind"),
    [
        (UnknownToolNameError, "unknown_tool_name"),
        (MalformedToolArgumentsError, "malformed_tool_arguments"),
        (DuplicateToolCallError, "duplicate_tool_call"),
    ],
)
def test_each_typed_tool_call_error_is_labeled_and_never_produces_a_response(exc_cls, expected_kind) -> None:
    registry = ModelRegistry()
    manifest = _manifest(registry)
    router = MemoryFirstRouter(registry)
    task = create_task_record("show the current directory")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = exc_cls("synthetic")

    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        _built_adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )

    assert response is None
    assert error is not None
    assert error.startswith(f"{expected_kind}:")


def test_a_malformed_tool_call_is_tracked_as_provider_health_evidence() -> None:
    """Distinct from RequiredToolsNotOfferedError (a capability mismatch, health untouched): a
    malformed call is real model/provider-output evidence and IS tracked."""
    registry = ModelRegistry()
    manifest = _manifest(registry)
    router = MemoryFirstRouter(registry)
    task = create_task_record("show the current directory")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = MalformedToolArgumentsError("synthetic")

    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )

    assert get_provider_health(manifest.provider_id).consecutive_failures == 1
