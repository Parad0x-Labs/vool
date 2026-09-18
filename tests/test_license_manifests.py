from __future__ import annotations

import unittest

from core.model_registry import ModelRegistry
from storage.db import get_connection
from storage.migrations import run_migrations


class LicenseManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()

    def test_missing_license_metadata_warning(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "missing-license",
                "model_name": "helper",
                "source_type": "http",
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"},
                "enabled": True,
            }
        )
        warnings = self.registry.startup_warnings()
        self.assertTrue(any("missing license metadata" in warning for warning in warnings))

    def test_provider_audit_rows_include_runtime_and_weight_fields(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "qwen",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"},
                "enabled": True,
            }
        )
        row = self.registry.provider_audit_rows()[0]
        self.assertEqual(row.license_reference, "https://www.apache.org/licenses/LICENSE-2.0")
        self.assertEqual(row.runtime_dependency, "openai-compatible-local-runtime")
        self.assertFalse(row.weights_bundled)

    def test_no_bundled_weights_required_for_normal_registration(self) -> None:
        manifest = self.registry.register_manifest(
            {
                "provider_name": "user-local-model",
                "model_name": "custom",
                "source_type": "local_path",
                "adapter_type": "local_model_path",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "optional-transformers",
                "capabilities": ["summarize"],
                "runtime_config": {"model_path": "/replace/me", "command": ["external-runtime"]},
                "enabled": True,
            }
        )
        self.assertFalse(manifest.weights_are_bundled)


if __name__ == "__main__":
    unittest.main()
