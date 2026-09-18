"""Activity's `model.call_failed` event must carry the typed ProviderErrorClass, not just a
free-text reason -- so a caller (or a human reading the trace) can answer "is retrying this exact
provider/model worth it" from a fixed enum value instead of re-parsing the message string.
"""
from __future__ import annotations

from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from core.cloud_tool_call_contract import MalformedToolArgumentsError
from core.execution_requirements import RequiredToolsNotOfferedError
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from core.normalized_provider_result import ProviderErrorClass
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


def _run_and_capture_events(*, adapter_error: Exception):
    registry = ModelRegistry()
    manifest = _manifest(registry)
    router = MemoryFirstRouter(registry)
    task = create_task_record("show the current directory")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = adapter_error

    events = []

    def _capture(source_context, event_type, message, **details):
        events.append((event_type, details))

    with mock.patch.object(registry, "build_adapter", return_value=adapter), mock.patch(
        "core.memory_first_router._emit_model_routing_event", side_effect=_capture
    ):
        router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )
    return events


def test_required_tools_not_offered_persists_its_error_class() -> None:
    events = _run_and_capture_events(adapter_error=RequiredToolsNotOfferedError("synthetic"))
    failed = [d for t, d in events if t == "model.call_failed"]
    assert failed, "expected a model.call_failed event"
    assert failed[0]["error_class"] == ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED.value
    assert failed[0]["retryable"] is False


def test_malformed_tool_call_persists_its_error_class() -> None:
    events = _run_and_capture_events(adapter_error=MalformedToolArgumentsError("synthetic"))
    failed = [d for t, d in events if t == "model.call_failed"]
    assert failed[0]["error_class"] == ProviderErrorClass.MALFORMED_TOOL_CALL.value
    assert failed[0]["retryable"] is False


def test_a_timeout_persists_as_a_retryable_error_class() -> None:
    events = _run_and_capture_events(adapter_error=TimeoutError("Read timed out (read timeout=59.99)"))
    failed = [d for t, d in events if t == "model.call_failed"]
    assert failed[0]["error_class"] == ProviderErrorClass.PROVIDER_TIMEOUT.value
    assert failed[0]["retryable"] is True
