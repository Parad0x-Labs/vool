"""A10 P1-1 — one canonical credential vocabulary for slot names ↔ env aliases.

Pre-fix truth (mechanically verified at SHA 380879c9): ``llm.cloud.openrouter``
and ``OPENROUTER_API_KEY`` existed as duplicated literals in BOTH
``adapters/openrouter_cloud_provider.py`` (class attributes) and
``core/agent_runtime/fast_command_surface.py`` (_cloud_key_configured), beside
the authoritative table in ``core/cloud_providers.py`` — so a new provider or an
alias rename required N+1 coordinated edits and a mismatch produced a false
negative ``has_cloud_key``.

Post-fix law: both served surfaces DERIVE their facts from that one table.
Adding a provider is a ONE-file edit (the PROVIDERS entry).

Nothing here reads, prints, or stores secret values — only names and presence.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
from core.agent_runtime.fast_command_surface import _cloud_key_configured
from core.cloud_credential_broker import CloudCredentialBroker
from core.cloud_providers import PROVIDERS, all_slots, credential_env_for, provider_for_slot, slot_for


class CanonicalVocabularyIsOneTable(unittest.TestCase):
    def test_openrouter_adapter_derives_from_table(self) -> None:
        self.assertEqual(OpenRouterCloudProvider.credential_name, slot_for("openrouter"))
        self.assertEqual(OpenRouterCloudProvider.credential_env, credential_env_for("openrouter"))

    def test_new_provider_is_a_single_file_edit(self) -> None:
        # Registering a provider in the table alone must yield a complete,
        # consistent vocabulary everywhere it is consumed — no second surface to
        # teach. (In-memory entry; nothing persists.)
        from core.cloud_providers import ProviderConfig

        cfg = ProviderConfig(
            provider_id="a10prov", label="A10", base_url="", probe_path="/x",
            model_id_style="bare", model_catalog_source="static", default_model="",
            key_prefixes=(), env_names=("A10_PROV_KEY",),
        )
        with mock.patch.dict(PROVIDERS, {"a10prov": cfg}):
            self.assertEqual(slot_for("a10prov"), "llm.cloud.a10prov")
            self.assertEqual(credential_env_for("a10prov"), "A10_PROV_KEY")
            self.assertIn("llm.cloud.a10prov", all_slots())
            self.assertEqual(provider_for_slot("llm.cloud.a10prov"), "a10prov")

    def test_slot_roundtrip_and_alias_priority(self) -> None:
        for pid, cfg in PROVIDERS.items():
            if not cfg.env_names:
                continue
            self.assertEqual(credential_env_for(pid), cfg.env_names[0])
            self.assertTrue(slot_for(pid).startswith("llm.cloud."))
            self.assertEqual(provider_for_slot(slot_for(pid)), pid)

    def test_missing_env_helper_returns_empty_not_crash(self) -> None:
        self.assertEqual(credential_env_for("no-such-provider"), "")
        self.assertEqual(slot_for("no-such-provider"), "")


class ServedCallerConvergence(unittest.TestCase):
    def test_cloud_key_configured_follows_canonical_fact(self) -> None:
        env_alias = credential_env_for("openrouter")
        canonical_slot = slot_for("openrouter")
        broker = CloudCredentialBroker()
        with mock.patch.dict(os.environ, {env_alias: "sk-or-test-value-only-presence-checked"}):
            self.assertTrue(_cloud_key_configured())
            self.assertTrue(broker.has(canonical_slot, env_name=env_alias))
        # Missing credential fails correctly on every path — never permissive.
        os.environ.pop(env_alias, None)
        self.assertFalse(broker.has(canonical_slot, env_name=env_alias))
        from core import credential_store

        with mock.patch.object(credential_store, "has_credential", return_value=False):
            self.assertFalse(_cloud_key_configured())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
