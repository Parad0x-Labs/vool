from __future__ import annotations

import unittest
from unittest import mock

from core import policy_engine
from core.model_selection_policy import ModelSelectionRequest, select_provider
from storage.model_provider_manifest import ModelProviderManifest


class LocalOnlyPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cache = getattr(policy_engine, "_POLICY_CACHE", None)

    def tearDown(self) -> None:
        policy_engine._POLICY_CACHE = self._old_cache

    def test_local_only_mode_means_no_public_egress_whatever_the_ambient_flag_says(self) -> None:
        """REWRITTEN. This test used to assert Local Only KEEPS web enabled.

        That was the contradiction: the composer entry reads "this machine only ·
        cloud blocked", the README and the runtime capability copy promise the
        same, and this test encoded the opposite as the intended contract -- so
        search, direct fetch, browser navigation to public origins, fallback
        scrapers and attachment URL retrieval all stayed reachable in the one mode
        whose whole promise is that they are not.

        The stronger, user-facing meaning is now the law: LOCAL ONLY = no
        public-network egress of any kind. The ambient `allow_web_fallback` flag is
        a machine-wide default and no longer the answer to "can this turn use the
        web?" -- `effective_web_available` is, and the canonical door refuses the
        socket regardless of the flag.
        """
        from core.remote_fetch_policy import effective_web_available

        policy_engine._POLICY_CACHE = {
            "system": {
                "local_only_mode": True,
                "allow_web_fallback": True,
                "allow_remote_only_without_backend": True,
            }
        }
        self.assertFalse(policy_engine.allow_remote_only_without_backend())
        # The ambient flag may still read true -- it is a default, not a verdict --
        # but no turn under Local Only can act on it.
        self.assertFalse(effective_web_available({"session_id": "local-only-policy"}))

    def test_local_only_mode_filters_remote_model_providers(self) -> None:
        policy_engine._POLICY_CACHE = {"system": {"local_only_mode": True}}
        local_manifest = ModelProviderManifest(
            provider_name="local-qwen-http",
            model_name="qwen-local",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            capabilities=["summarize", "structured_json"],
            runtime_config={"base_url": "http://127.0.0.1:11434"},
        )
        remote_manifest = ModelProviderManifest(
            provider_name="remote-http",
            model_name="remote-model",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="remote-provider",
            capabilities=["summarize", "structured_json"],
            runtime_config={"base_url": "https://provider.example"},
        )
        selected = select_provider(
            [remote_manifest, local_manifest],
            ModelSelectionRequest(task_kind="summarization", output_mode="summary_block", allow_paid_fallback=True),
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.provider_id, local_manifest.provider_id)

    def test_outbound_shard_validation_blocks_secret_like_content(self) -> None:
        shard = {
            "schema_version": 1,
            "problem_class": "security_hardening",
            "summary": "Operator email is operator@example.com and the API key is sk-testsecret1234567890.",
            "resolution_pattern": ["identify_sensitive_surfaces", "remove_secret_exposure_paths"],
            "risk_flags": [],
        }
        self.assertFalse(
            policy_engine.validate_outbound_shard(
                shard,
                share_scope="hive_mind",
                restricted_terms=["operator@example.com"],
            )
        )

    def test_outbound_shard_validation_rejects_local_only_scope(self) -> None:
        shard = {
            "schema_version": 1,
            "problem_class": "system_design",
            "summary": "Generic reusable swarm topology pattern.",
            "resolution_pattern": ["review_problem", "compare_topology", "document_tradeoffs"],
            "risk_flags": [],
        }
        self.assertFalse(policy_engine.validate_outbound_shard(shard, share_scope="local_only"))


class WebOptInPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cache = getattr(policy_engine, "_POLICY_CACHE", None)

    def tearDown(self) -> None:
        policy_engine._POLICY_CACHE = self._old_cache

    def test_web_is_on_by_default(self) -> None:
        # No override env set: web is on after a fresh policy load, so live-data
        # questions (weather, prices, news) get a real answer out of the box.
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("VOOL_ENABLE_WEB", None)
            env.pop("VOOL_ALLOW_WEB", None)
            env.pop("VOOL_DISABLE_WEB", None)
            env.pop("VOOL_NO_WEB", None)
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())

    def test_web_default_value_in_code_default_policy_is_on(self) -> None:
        self.assertTrue(policy_engine._DEFAULT_POLICY["system"]["allow_web_fallback"])

    def test_vool_enable_web_env_turns_web_on(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_ENABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())

    def test_vool_allow_web_alias_turns_web_on(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_ALLOW_WEB": "true"}, clear=False) as env:
            env.pop("VOOL_ENABLE_WEB", None)
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())

    def test_vool_disable_web_env_turns_web_off(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_DISABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertFalse(policy_engine.allow_web_fallback())

    def test_vool_no_web_alias_turns_web_off(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_NO_WEB": "true"}, clear=False) as env:
            env.pop("VOOL_DISABLE_WEB", None)
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertFalse(policy_engine.allow_web_fallback())

    def test_disable_env_wins_over_enable_env(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_ENABLE_WEB": "1", "VOOL_DISABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertFalse(policy_engine.allow_web_fallback())

    def test_the_enable_web_env_cannot_reopen_egress_inside_local_only(self) -> None:
        """REWRITTEN. This asserted the enable env keeps web on under Local Only.

        Leaving Local Only is an explicit, visible operator action -- changing the
        model selection -- not an environment variable set once and forgotten. The
        env can still turn web on for an ordinary profile; it can no longer
        silently re-open egress inside a local-only one.
        """
        import urllib.request

        from core.remote_fetch_policy import (
            RemoteFetchRefusedError,
            effective_web_available,
            open_remote,
            remote_fetch_policy_scope,
        )

        with mock.patch.dict("os.environ", {"VOOL_ENABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = {
                "system": {"local_only_mode": True, "allow_web_fallback": True}
            }
            self.assertFalse(effective_web_available({"session_id": "env-override"}))
            with remote_fetch_policy_scope({"session_id": "env-override"}):
                with self.assertRaises(RemoteFetchRefusedError) as caught:
                    open_remote(
                        urllib.request.Request("https://duckduckgo.com/html/?q=x"), timeout=1.0
                    )
            self.assertIn("local only", str(caught.exception).lower())

    def test_the_enable_web_env_still_works_outside_local_only(self) -> None:
        """The law narrows Local Only only. An ordinary profile is untouched."""
        with mock.patch.dict("os.environ", {"VOOL_ENABLE_WEB": "1"}, clear=False):
            policy_engine._POLICY_CACHE = None
            policy_engine.load(force_reload=True)
            self.assertTrue(policy_engine.allow_web_fallback())


if __name__ == "__main__":
    unittest.main()
