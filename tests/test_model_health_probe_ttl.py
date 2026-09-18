from __future__ import annotations

import unittest
from unittest import mock

from adapters.base_adapter import ModelRequest, ModelResponse
from core.model_health import (
    DEFAULT_HEALTH_PROBE_TTL_SECONDS,
    record_provider_failure,
    record_provider_success,
    reset_provider_health,
    should_probe_health,
)

_PROVIDER = "local-qwen-http:qwen-local"


class HealthProbeTtlTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_provider_health()

    def test_probes_on_first_use(self) -> None:
        # No recorded success yet -> probe must run.
        self.assertTrue(should_probe_health(_PROVIDER))

    def test_probe_skipped_within_ttl_after_success(self) -> None:
        base = 1_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            record_provider_success(_PROVIDER)
        # Still inside the TTL window -> skip the probe.
        with mock.patch(
            "core.model_health.time.time",
            return_value=base + (DEFAULT_HEALTH_PROBE_TTL_SECONDS / 2),
        ):
            self.assertFalse(should_probe_health(_PROVIDER))

    def test_probe_reissued_after_ttl(self) -> None:
        base = 1_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            record_provider_success(_PROVIDER)
        with mock.patch(
            "core.model_health.time.time",
            return_value=base + DEFAULT_HEALTH_PROBE_TTL_SECONDS + 1,
        ):
            self.assertTrue(should_probe_health(_PROVIDER))

    def test_probe_reissued_after_failure(self) -> None:
        base = 1_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            record_provider_success(_PROVIDER)
        # A failure after the success invalidates the recent-success window.
        with mock.patch("core.model_health.time.time", return_value=base + 1):
            record_provider_failure(_PROVIDER, error="boom")
        with mock.patch("core.model_health.time.time", return_value=base + 2):
            self.assertTrue(should_probe_health(_PROVIDER))

    def test_probe_runs_when_circuit_open_even_if_recent_success(self) -> None:
        base = 1_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            record_provider_success(_PROVIDER)
            # Force the circuit open with a single failure threshold.
            record_provider_failure(
                _PROVIDER,
                error="timeout",
                timeout=True,
                failure_threshold=1,
                cooldown_seconds=60,
            )
        with mock.patch("core.model_health.time.time", return_value=base + 1):
            # Circuit open -> always probe (must not be skipped).
            self.assertTrue(should_probe_health(_PROVIDER))

    def test_zero_ttl_always_probes(self) -> None:
        base = 1_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            record_provider_success(_PROVIDER)
        with mock.patch("core.model_health.time.time", return_value=base + 1):
            self.assertTrue(should_probe_health(_PROVIDER, ttl_seconds=0))

    def test_custom_ttl_window(self) -> None:
        base = 1_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            record_provider_success(_PROVIDER)
        with mock.patch("core.model_health.time.time", return_value=base + 5):
            self.assertFalse(should_probe_health(_PROVIDER, ttl_seconds=10))
            self.assertTrue(should_probe_health(_PROVIDER, ttl_seconds=4))


class InvokeManifestProbeGateTests(unittest.TestCase):
    """The per-invocation probe in MemoryFirstRouter must honour the TTL gate."""

    def setUp(self) -> None:
        reset_provider_health()

    def _build_router(self, adapter):
        from core.memory_first_router import MemoryFirstRouter

        registry = mock.Mock()
        registry.build_adapter.return_value = adapter
        return MemoryFirstRouter(registry)

    def _invoke(self, router, manifest, request):
        return router._invoke_manifest(
            manifest=manifest,
            request=request,
            output_mode="summary_block",
            task=mock.Mock(task_id="t-1"),
            source_context=None,
        )

    def test_probe_runs_first_then_skipped_within_ttl(self) -> None:
        manifest = mock.Mock(provider_id=_PROVIDER, provider_name="local-qwen-http")
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True, "provider_id": _PROVIDER}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text="ok",
            confidence=0.9,
        )
        router = self._build_router(adapter)
        request = ModelRequest(
            task_kind="summary",
            prompt="Summarize this.",
            output_mode="summary_block",
        )

        base = 5_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            self._invoke(router, manifest, request)
        # First use must have probed.
        self.assertEqual(adapter.health_check.call_count, 1)

        # Second invocation inside the TTL window: probe skipped, task still runs.
        with mock.patch("core.model_health.time.time", return_value=base + 1):
            _adapter_out, response, error = self._invoke(router, manifest, request)
        self.assertEqual(adapter.health_check.call_count, 1)
        self.assertIsNone(error)
        self.assertIsNotNone(response)
        self.assertEqual(adapter.run_structured_task.call_count, 2)

    def test_probe_reruns_after_failure(self) -> None:
        manifest = mock.Mock(provider_id=_PROVIDER, provider_name="local-qwen-http")
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True, "provider_id": _PROVIDER}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text="ok",
            confidence=0.9,
        )
        router = self._build_router(adapter)
        request = ModelRequest(
            task_kind="summary",
            prompt="Summarize this.",
            output_mode="summary_block",
        )

        base = 6_000.0
        with mock.patch("core.model_health.time.time", return_value=base):
            self._invoke(router, manifest, request)
        self.assertEqual(adapter.health_check.call_count, 1)

        # A failure clears the recent-success window.
        with mock.patch("core.model_health.time.time", return_value=base + 1):
            record_provider_failure(_PROVIDER, error="boom")

        with mock.patch("core.model_health.time.time", return_value=base + 2):
            self._invoke(router, manifest, request)
        self.assertEqual(adapter.health_check.call_count, 2)


if __name__ == "__main__":
    unittest.main()
