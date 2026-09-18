from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.optional_transformers_adapter import OptionalTransformersAdapter
from core.model_health import get_provider_health, reset_provider_health
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from core.model_teacher_pipeline import ModelTeacherPipeline
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests


class ModelIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        _clear_model_manifests()
        reset_provider_health()
        self.registry = ModelRegistry()

    def test_register_manifest_and_list_provider_licenses(self) -> None:
        manifest = self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "qwen2.5-7b-instruct",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "notes": "User-managed HTTP server",
                "capabilities": ["summarize", "classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8000"},
                "enabled": True,
            }
        )

        self.assertEqual(manifest.provider_id, "local-qwen-http:qwen2.5-7b-instruct")
        listed = list_provider_manifests(enabled_only=True)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].license_name, "Apache-2.0")
        self.assertFalse(self.registry.startup_warnings())

    def test_manifest_validation_rejects_unknown_capability(self) -> None:
        with self.assertRaises(ValueError):
            ModelProviderManifest.model_validate(
                {
                    "provider_name": "bad-provider",
                    "model_name": "bad-model",
                    "source_type": "http",
                    "license_name": "Apache-2.0",
                    "license_url_or_reference": "https://example.test/license",
                    "weight_location": "external",
                    "redistribution_allowed": True,
                    "runtime_dependency": "openai-compatible-local-runtime",
                    "capabilities": ["magic_reasoning"],
                }
            )

    def test_startup_warnings_report_missing_license_metadata(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "missing-license",
                "model_name": "helper",
                "source_type": "subprocess",
                "weight_location": "external",
                "redistribution_allowed": None,
                "runtime_dependency": "external-helper-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"command": ["echo"]},
            }
        )

        warnings = self.registry.startup_warnings()
        self.assertTrue(any("missing license metadata" in warning for warning in warnings))

    def test_optional_transformers_adapter_reports_absence_cleanly(self) -> None:
        manifest = ModelProviderManifest.model_validate(
            {
                "provider_name": "local-transformers",
                "model_name": "qwen-local",
                "source_type": "local_path",
                "adapter_type": "optional_transformers",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "optional-transformers",
                "capabilities": ["summarize"],
                "runtime_config": {"model_path": "/tmp/not-real"},
            }
        )
        adapter = OptionalTransformersAdapter(manifest)
        with mock.patch("importlib.util.find_spec", return_value=None):
            warnings = adapter.validate_runtime()
            self.assertTrue(any("optional dependency 'transformers' is not installed" in warning for warning in warnings))
            with self.assertRaises(RuntimeError):
                adapter.invoke(
                    request=mock.Mock(task_kind="summarization", prompt="hi", system_prompt=None, context={}, temperature=None, max_output_tokens=None)
                )

    def test_external_or_user_supplied_weights_can_register_without_bundled_assets(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "user-local-model",
                "model_name": "custom",
                "source_type": "local_path",
                "adapter_type": "local_model_path",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "external-runtime",
                "notes": "No weights shipped with VOOL",
                "capabilities": ["classify"],
                "runtime_config": {"model_path": "/replace/me", "command": ["external-runtime"]},
                "enabled": True,
            }
        )
        selected = self.registry.select_manifest(ModelSelectionRequest(task_kind="classification"))
        self.assertIsNotNone(selected)
        self.assertEqual(selected.weight_location, "user-supplied")

    def test_selection_policy_skips_bundled_weights_when_forbidden(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "bundled-bad",
                "model_name": "bad",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "bundled",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8001"},
                "enabled": True,
            }
        )
        self.registry.register_manifest(
            {
                "provider_name": "good-http",
                "model_name": "good",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "external",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8002"},
                "enabled": True,
            }
        )

        selected = self.registry.select_manifest(ModelSelectionRequest(task_kind="classification", forbid_bundled_weights=True))
        self.assertIsNotNone(selected)
        self.assertEqual(selected.provider_name, "good-http")

    def test_teacher_pipeline_returns_candidate_with_provenance(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "helper-http",
                "model_name": "helper",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "external",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["format"],
                "runtime_config": {"base_url": "http://127.0.0.1:8000"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(self.registry)
        fake_response = mock.Mock(output_text="Normalized: please harden my telegram setup", confidence=0.7)
        with mock.patch.object(self.registry, "build_adapter") as build_adapter:
            build_adapter.return_value.health_check.return_value = {"ok": True, "provider_id": "helper-http:helper"}
            build_adapter.return_value.invoke.return_value = fake_response
            candidate = pipeline.normalization_assist("pls harden tg setup")

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.candidate_only)
        self.assertEqual(candidate.provider_name, "helper-http")
        self.assertIn("license_name", candidate.provenance)

    def test_registry_prewarms_only_manifests_that_declare_prewarm(self) -> None:
        warm_manifest = self.registry.register_manifest(
            {
                "provider_name": "ollama-local",
                "model_name": "qwen2.5:14b",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "external",
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["classify"],
                "runtime_config": {
                    "base_url": "http://127.0.0.1:11434",
                    "prewarm": {"strategy": "ollama_generate"},
                },
                "metadata": {"runtime_family": "ollama"},
                "enabled": True,
            }
        )
        self.registry.register_manifest(
            {
                "provider_name": "vllm-local",
                "model_name": "qwen2.5:32b",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "external",
                "redistribution_allowed": True,
                "runtime_dependency": "vllm",
                "capabilities": ["classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8100/v1"},
                "metadata": {"runtime_family": "openai-compatible"},
                "enabled": True,
            }
        )

        with mock.patch.object(self.registry, "build_adapter") as build_adapter:
            adapter = mock.Mock()
            adapter.prewarm.return_value = {
                "ok": True,
                "provider_id": warm_manifest.provider_id,
                "status": "prewarmed",
            }
            build_adapter.return_value = adapter

            results = self.registry.prewarm_enabled_providers()

        self.assertEqual(results, [{"ok": True, "provider_id": warm_manifest.provider_id, "status": "prewarmed"}])
        build_adapter.assert_called_once()
        called_manifest = build_adapter.call_args.args[0]
        self.assertEqual(called_manifest.provider_id, warm_manifest.provider_id)

    def test_teacher_pipeline_drone_swarm_picks_best_candidate_and_tracks_swarm(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "qwen2.5:14b",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["format", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
                "enabled": True,
            }
        )
        self.registry.register_manifest(
            {
                "provider_name": "local-qwen-alt",
                "model_name": "qwen2.5:7b",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["format", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:22434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(self.registry)

        strong_response = mock.Mock(output_text="Normalized: harden your Telegram setup first", confidence=0.86)
        weak_response = mock.Mock(output_text="Maybe do a thing", confidence=0.42)

        def build_adapter(manifest):
            adapter = mock.Mock()
            adapter.health_check.return_value = {"ok": True, "provider_id": manifest.provider_id}
            if manifest.provider_name == "local-qwen-http":
                adapter.invoke.return_value = strong_response
            else:
                adapter.invoke.return_value = weak_response
            adapter.get_license_metadata.return_value = {
                "provider_name": manifest.provider_name,
                "model_name": manifest.model_name,
                "license_name": manifest.license_name,
                "license_reference": manifest.resolved_license_reference,
            }
            return adapter

        with mock.patch.object(self.registry, "build_adapter", side_effect=build_adapter):
            candidate = pipeline.run(
                task_kind="normalization_assist",
                prompt="pls harden tg setup",
                output_mode="summary_block",
                provider_role="drone",
                swarm_size=2,
            )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.provider_name, "local-qwen-http")
        self.assertEqual(candidate.provider_role, "drone")
        self.assertEqual(len(candidate.swarm_provider_ids), 2)
        self.assertIn("swarm_provider_ids", candidate.provenance)

    def test_teacher_pipeline_records_invoke_failure_and_survives_with_second_lane(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "local-timeout",
                "model_name": "qwen2.5:14b-timeout",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["format", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
                "enabled": True,
            }
        )
        self.registry.register_manifest(
            {
                "provider_name": "local-steady",
                "model_name": "qwen2.5:14b-steady",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["format", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:22434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(self.registry)

        def build_adapter(manifest):
            adapter = mock.Mock()
            adapter.health_check.return_value = {"ok": True, "provider_id": manifest.provider_id}
            adapter.get_license_metadata.return_value = {
                "provider_name": manifest.provider_name,
                "model_name": manifest.model_name,
                "license_name": manifest.license_name,
                "license_reference": manifest.resolved_license_reference,
            }
            if manifest.provider_name == "local-timeout":
                adapter.invoke.side_effect = RuntimeError("timeout while waiting for response")
            else:
                adapter.invoke.return_value = mock.Mock(output_text="steady lane answer", confidence=0.82)
            return adapter

        with mock.patch.object(self.registry, "build_adapter", side_effect=build_adapter):
            candidate = pipeline.run(
                task_kind="normalization_assist",
                prompt="pls harden tg setup",
                output_mode="summary_block",
                provider_role="drone",
                swarm_size=2,
            )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.provider_name, "local-steady")
        self.assertEqual(candidate.provenance["failed_attempts"][0]["provider_name"], "local-timeout")
        self.assertEqual(candidate.provenance["failed_attempts"][0]["status"], "invoke_failed")
        self.assertEqual(get_provider_health("local-timeout:qwen2.5:14b-timeout").timeout_failures, 1)
        self.assertEqual(get_provider_health("local-steady:qwen2.5:14b-steady").consecutive_failures, 0)

    def test_register_from_file_loads_sample_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "providers.json"
            sample.write_text(
                """
                {
                  "providers": [
                    {
                      "provider_name": "sample-http",
                      "model_name": "sample-model",
                      "source_type": "http",
                      "license_name": "Apache-2.0",
                      "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                      "weight_location": "external",
                      "redistribution_allowed": true,
                      "runtime_dependency": "openai-compatible-local-runtime",
                      "capabilities": ["summarize"],
                      "runtime_config": {"base_url": "http://127.0.0.1:8000"},
                      "enabled": false
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            loaded = self.registry.register_from_file(sample)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].provider_name, "sample-http")


def _clear_model_manifests() -> None:
    conn = get_connection()
    try:
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
