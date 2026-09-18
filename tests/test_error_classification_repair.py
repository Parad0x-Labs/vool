"""SWITCHBOARD Repair 1: unknown errors must not become retryable PROVIDER_INTERNAL_ERROR.

classify_error_class's own docstring already promised None for an unmatched error; the
implementation returned ProviderErrorClass.PROVIDER_INTERNAL_ERROR instead, and is_retryable()
then reported that as retryable -- so an arbitrary unknown exception, a JSONDecodeError with
unpredictable wording, and (via the generic except-Exception branch's blanket record_provider_
failure call) even a capability mismatch all silently became "retry this exact provider, it just
had an internal hiccup."
"""
from __future__ import annotations

import json

import pytest
import requests

from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    MalformedToolArgumentsError,
    UnknownToolNameError,
)
from core.execution_requirements import RequiredToolsNotOfferedError
from core.normalized_provider_result import ProviderErrorClass, classify_error_class, is_retryable


class _ArbitraryLocalBugError(Exception):
    """Stands in for a completely unrelated, never-seen-before exception type -- e.g. a bug in
    unrelated code that happens to surface through the same try/except, NOT provider evidence."""


def test_an_arbitrary_unknown_exception_classifies_to_none_and_is_not_retryable() -> None:
    error_class = classify_error_class(_ArbitraryLocalBugError("something nobody anticipated"))
    assert error_class is None
    assert is_retryable(error_class) is False


def test_an_unknown_string_message_classifies_to_none_and_is_not_retryable() -> None:
    error_class = classify_error_class("xyzzy-unrecognized-91827")
    assert error_class is None
    assert is_retryable(error_class) is False


def test_json_decode_error_classifies_as_malformed_provider_response() -> None:
    """JSONDecodeError's own message ("Expecting value: line 1 column 1 (char 0)") contains no
    marker string at all -- this must be caught by TYPE, not by string-matching."""
    try:
        json.loads("not valid json")
    except json.JSONDecodeError as exc:
        error_class = classify_error_class(exc)
    else:
        pytest.fail("json.loads did not raise")
    assert error_class == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE
    assert is_retryable(error_class) is False


def test_unsupported_tools_classifies_as_required_tools_not_offered_and_is_not_retryable() -> None:
    """The exact message shape CloudflareWorkersAIProvider/GenericOpenAICloudProvider raise when
    asked to carry tools they cannot ("{provider} does not support required tools: ...")."""
    error_class = classify_error_class("cloudflare-workers-ai does not support required tools: this adapter cannot carry a tool catalog")
    assert error_class == ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED
    assert is_retryable(error_class) is False


def test_required_tools_not_offered_error_type_classifies_correctly() -> None:
    error_class = classify_error_class(RequiredToolsNotOfferedError("REQUIRED_TOOLS_NOT_OFFERED: lane=x offered=0"))
    assert error_class == ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED
    assert is_retryable(error_class) is False


@pytest.mark.parametrize("exc_cls", [UnknownToolNameError, MalformedToolArgumentsError, DuplicateToolCallError])
def test_tool_call_parse_error_subclasses_classify_as_malformed_tool_call(exc_cls) -> None:
    error_class = classify_error_class(exc_cls("synthetic"))
    assert error_class == ProviderErrorClass.MALFORMED_TOOL_CALL
    assert is_retryable(error_class) is False


def test_a_connection_failure_classifies_as_retryable() -> None:
    error_class = classify_error_class(ConnectionError("connection refused"))
    assert error_class == ProviderErrorClass.PROVIDER_CONNECTION_ERROR
    assert is_retryable(error_class) is True


def test_a_requests_connection_error_classifies_as_retryable() -> None:
    error_class = classify_error_class(requests.exceptions.ConnectionError("connection refused"))
    assert error_class == ProviderErrorClass.PROVIDER_CONNECTION_ERROR
    assert is_retryable(error_class) is True


def test_a_timeout_classifies_as_retryable() -> None:
    error_class = classify_error_class(TimeoutError("Read timed out (read timeout=59.99)"))
    assert error_class == ProviderErrorClass.PROVIDER_TIMEOUT
    assert is_retryable(error_class) is True


def test_a_requests_timeout_classifies_as_retryable() -> None:
    error_class = classify_error_class(requests.exceptions.ReadTimeout("Read timed out"))
    assert error_class == ProviderErrorClass.PROVIDER_TIMEOUT
    assert is_retryable(error_class) is True


def test_a_rate_limit_classifies_as_retryable() -> None:
    error_class = classify_error_class("provider rate or quota limit reached")
    assert error_class == ProviderErrorClass.PROVIDER_RATE_LIMIT
    assert is_retryable(error_class) is True


def test_a_provider_internal_error_classifies_as_retryable() -> None:
    """PROVIDER_INTERNAL_ERROR must still be REACHABLE -- via a genuine positive signal (the
    provider itself reporting a server-side fault), never via the unmatched-fallback path."""
    error_class = classify_error_class("provider_http_500:internal server error")
    assert error_class == ProviderErrorClass.PROVIDER_INTERNAL_ERROR
    assert is_retryable(error_class) is True


# --- mutation guard: restoring the old unmatched->PROVIDER_INTERNAL_ERROR fallback must fail these ---


def test_mutation_guard_unmatched_error_is_not_retryable() -> None:
    """If someone reverts classify_error_class's final `return None` back to
    `return ProviderErrorClass.PROVIDER_INTERNAL_ERROR`, this must catch it: an unmatched error
    would then incorrectly report retryable=True."""
    error_class = classify_error_class("completely novel unrecognized failure mode 42")
    assert is_retryable(error_class) is False, "an unclassifiable error must never be retryable"


# --- router integration: a capability mismatch must not degrade provider health -----------------


def test_a_does_not_support_tools_failure_does_not_degrade_provider_health() -> None:
    """The generic except-Exception branch in _invoke_manifest used to call
    record_provider_failure UNCONDITIONALLY -- so Cloudflare/GenericOpenAICloudProvider's own
    "does not support required tools" RuntimeError (a fixed capability fact about that lane, not
    a transient failure) opened the circuit breaker exactly like a genuine connection drop would.
    """
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import get_provider_health, reset_provider_health
    from core.model_registry import ModelRegistry
    from core.task_router import create_task_record
    from storage.db import get_connection
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()

    registry = ModelRegistry()
    manifest = registry.register_manifest(
        {
            "provider_name": "cloudflare-workers-ai", "model_name": "some-model", "source_type": "http",
            "adapter_type": "cloud_fallback_provider", "license_name": "Provider", "license_reference": "user-managed",
            "weight_location": "external", "weights_bundled": False, "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "https://provider.example"}, "enabled": True,
            # cost_class forced free_local so this test reaches _invoke_manifest's adapter call at
            # all -- adapter_type=cloud_fallback_provider is otherwise classified paid_cloud
            # (core.model_selection_policy.provider_cost_class) and _invoke_manifest short-circuits
            # BEFORE calling the adapter without an authorized paid-call reservation, which would
            # make this test pass for the wrong reason (never reaching the code under test at all).
            "metadata": {"orchestration_role": "queen", "cost_class": "free_local"},
        }
    )
    router = MemoryFirstRouter(registry)
    task = create_task_record("show the current directory")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = RuntimeError(
        "cloudflare-workers-ai does not support required tools: this adapter cannot carry a tool catalog"
    )

    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )

    assert adapter.run_structured_task.called, "the adapter must actually have been invoked for this test to mean anything"
    assert response is None and error is not None
    assert get_provider_health(manifest.provider_id).consecutive_failures == 0


def test_a_genuine_connection_failure_still_degrades_provider_health() -> None:
    """Control: the fix must not have disabled health tracking generally -- an ordinary transport
    failure must still be recorded exactly as before."""
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import get_provider_health, reset_provider_health
    from core.model_registry import ModelRegistry
    from core.task_router import create_task_record
    from storage.db import get_connection
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()

    registry = ModelRegistry()
    manifest = registry.register_manifest(
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
    router = MemoryFirstRouter(registry)
    task = create_task_record("show the current directory")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = ConnectionError("connection refused")

    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )

    assert adapter.run_structured_task.called
    assert get_provider_health(manifest.provider_id).consecutive_failures == 1
