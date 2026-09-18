"""SWITCHBOARD final micro-repair N1: an UNCLASSIFIED local error must never degrade provider
health.

Confirmed defect: `core.memory_first_router.MemoryFirstRouter._invoke_manifest`'s generic
except-Exception branch gated `record_provider_failure` on:

    generic_error_class != ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED

which is ALSO true when `generic_error_class` is None -- an exception classify_error_class could
not recognize at all (a local programming bug: TypeError, AttributeError, KeyError, an unmatched
RuntimeError -- nothing about the provider). SWITCHBOARD reproduced: 5 local TypeError failures
opened the circuit breaker (consecutive_failures=5, circuit_open=true) for a provider that was
never actually unhealthy.

Fix: only a POSITIVELY classified, non-capability-mismatch error class is provider evidence --
`generic_error_class is not None and generic_error_class != REQUIRED_TOOLS_NOT_OFFERED`.
"""
from __future__ import annotations

from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from core.memory_first_router import MemoryFirstRouter
from core.model_health import get_provider_health, reset_provider_health
from core.model_registry import ModelRegistry
from core.task_router import create_task_record
from storage.db import get_connection
from storage.migrations import run_migrations


def _reset_manifests() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _manifest(registry: ModelRegistry, provider_name: str):
    return registry.register_manifest(
        {
            "provider_name": provider_name,
            "model_name": "some-model",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:1234"},
            "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )


def _invoke_once(registry: ModelRegistry, manifest, *, side_effect: Exception):
    router = MemoryFirstRouter(registry)
    task = create_task_record("probe")
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.run_structured_task.side_effect = side_effect
    with mock.patch.object(registry, "build_adapter", return_value=adapter):
        result = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="tool_intent", prompt="hi", output_mode="tool_intent"),
            output_mode="tool_intent",
            task=task,
            source_context={"requested_model": manifest.model_name, "_owner_local": True},
        )
    assert adapter.run_structured_task.called, "the adapter must actually have been invoked"
    return result


class _ArbitraryLocalBugError(Exception):
    """Stands in for a completely unrelated local exception type -- classify_error_class must
    never have seen this before."""


# --- the 9 required classification-vs-degradation cases -----------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        TypeError("'NoneType' object is not subscriptable"),
        AttributeError("'dict' object has no attribute 'foo'"),
        KeyError("missing_key"),
        RuntimeError("completely novel unrecognized failure mode 42"),
        _ArbitraryLocalBugError("never seen before"),
    ],
    ids=["TypeError", "AttributeError", "KeyError", "unmatched_RuntimeError", "arbitrary_local_bug"],
)
def test_unclassified_local_errors_do_not_degrade_provider_health(exc) -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "unclassified-probe")
    _invoke_once(registry, manifest, side_effect=exc)
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 0, f"{type(exc).__name__} incorrectly degraded provider health"
    assert health.circuit_open is False


def test_required_tools_not_offered_does_not_degrade_provider_health() -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "capability-mismatch-probe")
    _invoke_once(
        registry, manifest,
        side_effect=RuntimeError("required_tools_not_offered:REQUIRED_TOOLS_NOT_OFFERED: lane=x offered=0"),
    )
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 0
    assert health.circuit_open is False


def test_connection_error_degrades_provider_health() -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "connection-probe")
    _invoke_once(registry, manifest, side_effect=ConnectionError("connection refused"))
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 1


def test_timeout_degrades_provider_health() -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "timeout-probe")
    _invoke_once(registry, manifest, side_effect=TimeoutError("Read timed out (read timeout=59.99)"))
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 1


def test_rate_limit_degrades_provider_health_per_existing_policy() -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "rate-limit-probe")
    _invoke_once(registry, manifest, side_effect=RuntimeError("provider rate or quota limit reached"))
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 1


def test_real_provider_internal_failure_degrades_provider_health() -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "internal-failure-probe")
    _invoke_once(registry, manifest, side_effect=RuntimeError("provider_http_500:internal server error"))
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 1


# --- explicit "5 repeated" demonstrations ---------------------------------------------------------


def test_five_repeated_local_type_errors_leave_health_and_circuit_untouched() -> None:
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "five-typeerrors-probe")
    for _ in range(5):
        _invoke_once(registry, manifest, side_effect=TypeError("'NoneType' object is not subscriptable"))
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 0, "5 local TypeErrors must not accumulate as provider failures"
    assert health.circuit_open is False, "the circuit must never open on local programming errors alone"


def test_five_repeated_genuine_provider_internal_failures_open_the_circuit() -> None:
    """Control: the fix must not have disabled health tracking generally -- 5 REAL provider
    failures must still trip the breaker exactly as before (failure_threshold=5)."""
    _reset_manifests()
    registry = ModelRegistry()
    manifest = _manifest(registry, "five-real-failures-probe")
    for _ in range(5):
        _invoke_once(registry, manifest, side_effect=RuntimeError("provider_http_500:internal server error"))
    health = get_provider_health(manifest.provider_id)
    assert health.consecutive_failures == 5
    assert health.circuit_open is True
