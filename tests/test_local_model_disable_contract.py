"""P0 contract: DISABLED local models must not register, appear, route, prewarm or run.

Root cause this file pins: `VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=0` only ever suppressed the
EXTRA installed-lane registration inside `_runtime_provider_model_roles`. The bundle's default
`ollama-local:<tag>` lane registered unconditionally, so a cloud-only launch still auto-registered
a local default provider and an unpinned turn could silently run local Qwen. Discovery,
registration, prewarm, health probes and fallback each made their own decision.

The fix is ONE canonical policy — `core.local_model_policy.LocalModelPolicy` — resolved from
`VOOL_LOCAL_MODELS_ENABLED` (normalized booleans: `0`/`false`/`off`/`no`/`disabled` disable) and
the operator's standing `data/config/local_models_disabled` marker. Disabled means: no Ollama
discovery, no local registration, no local manifest visible through the registry (including rows
persisted by an earlier enabled run — resident models never imply permission), no prewarm, no
health probe, no routing candidate, no fallback target, and a TYPED refusal
(`local_models_disabled`) for an explicit local pin. Enabled preserves prior behavior exactly.

Sabotage proofs are separated on purpose: registration sabotage (installed inventory present +
register-installed flag ON while disabled) and fallback sabotage (a failing cloud lane with a
healthy local lane persisted while disabled) fail through DIFFERENT gates, and reverting either
gate alone must redden its test while the other stays green.
"""
from __future__ import annotations

import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from adapters.base_adapter import ModelResponse
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.local_model_policy import (
    LOCAL_MODELS_DISABLED_REASON,
    LOCAL_MODELS_ENABLED_ENV,
    resolve_local_model_policy,
)
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from core.provider_routing import rank_provider_candidates, resolve_provider_routing_plan
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextResult
from storage.db import get_connection
from storage.migrations import run_migrations

_DISABLE_VALUES = ("0", "false", "off", "no", "disabled")
_ENABLE_VALUES = ("1", "true", "on", "yes", "enabled")

_LOCAL_MANIFEST = {
    "provider_name": "local-qwen-http",
    "model_name": "qwen-local",
    "source_type": "http",
    "adapter_type": "local_qwen_provider",
    "license_name": "Apache-2.0",
    "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
    "weight_location": "user-supplied",
    "weights_bundled": False,
    "redistribution_allowed": True,
    "runtime_dependency": "openai-compatible-local-runtime",
    "capabilities": ["summarize", "structured_json"],
    "runtime_config": {"base_url": "http://127.0.0.1:1234"},
    "enabled": True,
    "metadata": {"orchestration_role": "drone"},
}

_CLOUD_MANIFEST = {
    "provider_name": "cloud-fallback-http",
    "model_name": "cloud",
    "source_type": "http",
    "adapter_type": "cloud_fallback_provider",
    "license_name": "Provider",
    "license_reference": "user-managed",
    "weight_location": "external",
    "weights_bundled": False,
    "redistribution_allowed": False,
    "runtime_dependency": "remote-openai-compatible-provider",
    "capabilities": ["summarize", "structured_json", "long_context"],
    "runtime_config": {"base_url": "https://provider.example", "api_key_env": "VOOL_CLOUD_API_KEY"},
    "enabled": True,
    "metadata": {"orchestration_role": "queen"},
}


def _installed_lane_env() -> dict[str, str]:
    """The registration sabotage: installed Ollama inventory IS present and registration is ON."""
    return {
        "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
        "VOOL_INSTALLED_OLLAMA_MODELS": "qwen2.5:0.5b,qwen3:8b,qwen3:14b",
        "VOOL_CONTEXT_BUCKET": "A",
    }


def _mock_registry():
    manifests: dict[tuple[str, str], object] = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        values = list(manifests.values())[:limit]
        if enabled_only:
            return [item for item in values if item.enabled]
        return values

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.side_effect = lambda: [
        mock.Mock(provider_id=item.provider_id) for item in _list_manifests(enabled_only=True)
    ]
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    return registry, manifests


class LocalModelPolicyParsingTests(unittest.TestCase):
    def test_false_spellings_disable(self) -> None:
        for raw in _DISABLE_VALUES:
            with self.subTest(raw=raw):
                policy = resolve_local_model_policy(env={LOCAL_MODELS_ENABLED_ENV: raw})
                self.assertFalse(policy.local_models_enabled)
                self.assertTrue(policy.local_models_disabled)
                self.assertIn(LOCAL_MODELS_ENABLED_ENV, policy.decided_by)

    def test_true_spellings_and_unset_enable(self) -> None:
        for raw in _ENABLE_VALUES:
            with self.subTest(raw=raw):
                self.assertTrue(resolve_local_model_policy(env={LOCAL_MODELS_ENABLED_ENV: raw}).local_models_enabled)
        self.assertTrue(resolve_local_model_policy(env={}).local_models_enabled)

    def test_marker_file_disables_and_wins_over_env_enable(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as home:
            marker = Path(home) / "data" / "config" / "local_models_disabled"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("", encoding="utf-8")
            policy = resolve_local_model_policy(
                env={LOCAL_MODELS_ENABLED_ENV: "1"},
                runtime_home=home,
            )
            self.assertFalse(policy.local_models_enabled)
            self.assertIn("marker", policy.decided_by)

    def test_marker_absent_env_unparsable_falls_back_to_enabled(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as home:
            policy = resolve_local_model_policy(
                env={LOCAL_MODELS_ENABLED_ENV: "maybe"},
                runtime_home=home,
            )
            self.assertTrue(policy.local_models_enabled)

    def test_refusal_payload_is_typed(self) -> None:
        policy = resolve_local_model_policy(env={LOCAL_MODELS_ENABLED_ENV: "0"})
        payload = policy.refusal()
        self.assertEqual(payload["reason"], LOCAL_MODELS_DISABLED_REASON)
        self.assertFalse(payload["policy"]["local_models_enabled"])


class DisabledRegistrationTests(unittest.TestCase):
    """The defect itself: disabled + installed inventory present + register-installed ON."""

    def test_disabled_registers_no_local_lane_despite_installed_inventory(self) -> None:
        from core.runtime_provider_defaults import ensure_default_runtime_providers

        registry, manifests = _mock_registry()
        env = {**_installed_lane_env(), LOCAL_MODELS_ENABLED_ENV: "0"}

        changed = ensure_default_runtime_providers(
            registry,
            model_tag="qwen3:8b",
            env=env,
            install_profile="local-only",
        )

        self.assertEqual(changed, tuple())
        self.assertEqual(manifests, {})

    def test_disabled_registration_performs_no_installed_discovery(self) -> None:
        from core import runtime_provider_defaults
        from core.runtime_install_profiles import required_ollama_models_for_profile

        registry, _ = _mock_registry()
        env = {**_installed_lane_env(), LOCAL_MODELS_ENABLED_ENV: "0"}
        # Any attempt to discover what is installed (env inventory or /api/tags) or to resolve the
        # profile's required Ollama models must explode rather than silently run.
        boom = mock.Mock(side_effect=AssertionError("discovery attempted under a disabled policy"))
        with (
            mock.patch.object(runtime_provider_defaults, "installed_ollama_model_names", boom),
            mock.patch.object(runtime_provider_defaults, "default_runtime_model_tag", boom),
            mock.patch.object(runtime_provider_defaults, "required_ollama_models_for_profile", boom),
        ):
            changed = runtime_provider_defaults.ensure_default_runtime_providers(
                registry,
                model_tag="qwen3:8b",
                env=env,
                install_profile="local-only",
            )
        self.assertEqual(changed, tuple())
        boom.assert_not_called()
        # The storage-side helper must stay untouched by this gate (mock registry proves nothing
        # reached it); belt-and-braces: the real import still resolves for the enabled tests.
        self.assertTrue(callable(required_ollama_models_for_profile))

    def test_disabled_still_registers_cloud_lanes(self) -> None:
        from core.runtime_provider_defaults import ensure_default_runtime_providers

        registry, _ = _mock_registry()
        env = {
            LOCAL_MODELS_ENABLED_ENV: "0",
            "KIMI_API_KEY": "test-key",
            "VOOL_CONTEXT_BUCKET": "A",
        }

        changed = ensure_default_runtime_providers(
            registry,
            model_tag="qwen3:8b",
            env=env,
            install_profile="hybrid-kimi",
        )

        self.assertTrue(changed, "cloud lanes must still register when local models are disabled")
        for provider_id in changed:
            self.assertNotIn("ollama-local", provider_id)
            self.assertNotIn("llamacpp", provider_id)
            self.assertNotIn("vllm", provider_id)
            self.assertNotIn("mlx", provider_id)

    def test_enabled_registration_preserves_existing_behavior(self) -> None:
        from core.runtime_provider_defaults import ensure_default_runtime_providers

        registry, _ = _mock_registry()

        changed = ensure_default_runtime_providers(
            registry,
            model_tag="qwen3:8b",
            env=_installed_lane_env(),
            install_profile="local-only",
        )

        self.assertIn("ollama-local:qwen3:8b", changed)
        self.assertIn("ollama-local:qwen3:14b", changed)


class DisabledRegistryViewTests(unittest.TestCase):
    """The read seam: persisted local rows (an earlier enabled run, a restart) must not appear."""

    def setUp(self) -> None:
        run_migrations()
        reset_provider_health()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.execute("DELETE FROM candidate_knowledge_lane")
            conn.commit()
        finally:
            conn.close()

    def test_listing_hides_local_rows_when_disabled_and_recovers_when_enabled(self) -> None:
        registry = ModelRegistry()
        registry.register_manifest(dict(_LOCAL_MANIFEST))
        registry.register_manifest(dict(_CLOUD_MANIFEST))

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}):
            visible = ModelRegistry().list_manifests(enabled_only=True)
            self.assertEqual(
                sorted(item.provider_id for item in visible),
                [self._cloud_id()],
            )

        enabled_again = ModelRegistry().list_manifests(enabled_only=True)
        self.assertEqual(
            sorted(item.provider_id for item in enabled_again),
            sorted([self._cloud_id(), self._local_id()]),
        )

    def test_restart_cannot_resurrect_local_rows(self) -> None:
        registry = ModelRegistry()
        registry.register_manifest(dict(_LOCAL_MANIFEST))

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "false"}):
            # A brand-new registry instance is what a daemon restart builds; the rows persisted
            # by the earlier enabled run are still in the store, and still must not surface.
            fresh = ModelRegistry()
            self.assertEqual(fresh.list_manifests(enabled_only=True), [])
            self.assertEqual(fresh.list_manifests(enabled_only=False), [])

    def test_routing_never_ranks_or_plans_local_when_disabled(self) -> None:
        registry = ModelRegistry()
        registry.register_manifest(dict(_LOCAL_MANIFEST))
        registry.register_manifest(dict(_CLOUD_MANIFEST))

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}):
            ranked = rank_provider_candidates(
                registry,
                task_kind="summarize",
                output_mode="text",
                allow_paid_fallback=True,
            )
            self.assertTrue(ranked)
            self.assertNotIn(self._local_id(), [item.provider_id for item in ranked])

            plan = resolve_provider_routing_plan(
                registry,
                task_kind="summarize",
                output_mode="text",
                preferred_model="qwen-local",
                allow_paid_fallback=True,
            )
            self.assertNotIn(
                self._local_id(), [item.provider_id for item in plan.candidates]
            )

    def test_provider_snapshot_and_prewarm_report_zero_local_when_disabled(self) -> None:
        from core.runtime_backbone import build_provider_registry_snapshot

        registry = ModelRegistry()
        registry.register_manifest(dict(_LOCAL_MANIFEST))
        registry.register_manifest(dict(_CLOUD_MANIFEST))

        # The disabled flag rides the process env — the same way it reaches every seam of a real
        # cloud-only launch (registration takes it from the merged boot env; the registry listing
        # takes it from the process env, which merge_provider_env keeps authoritative).
        with mock.patch.dict(
            "os.environ",
            {
                **_installed_lane_env(),
                LOCAL_MODELS_ENABLED_ENV: "0",
            },
        ):
            snapshot = build_provider_registry_snapshot(
                registry,
                honor_install_profile=False,
                run_prewarm=True,
            )
        local_rows = [
            item
            for item in snapshot.capability_truth
            if item.locality == "local" or item.provider_id.startswith("ollama-local")
        ]
        self.assertEqual(local_rows, [])
        self.assertNotIn(self._local_id(), [row.provider_id for row in snapshot.audit_rows])
        for result in snapshot.prewarm_results:
            self.assertNotIn("ollama-local", str(result.get("provider_id") or ""))
            self.assertNotEqual(str(result.get("provider_id") or ""), self._local_id())

    def test_health_warnings_name_no_local_provider_when_disabled(self) -> None:
        # The local manifest deliberately carries a missing license reference so startup warnings
        # would name it when visible; disabled, it must not be probed or named at all.
        noisy_local = dict(_LOCAL_MANIFEST)
        noisy_local["license_reference"] = ""
        registry = ModelRegistry()
        registry.register_manifest(noisy_local)

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}):
            warnings = registry.startup_warnings()
        self.assertTrue(
            all("local-qwen-http" not in str(item) for item in warnings),
            warnings,
        )

    @staticmethod
    def _local_id() -> str:
        return "local-qwen-http:qwen-local"

    @staticmethod
    def _cloud_id() -> str:
        return "cloud-fallback-http:cloud"


class DisabledBootSurfaceTests(unittest.TestCase):
    def test_boot_model_pull_is_a_typed_noop_when_disabled(self) -> None:
        from core.web.api import runtime as web_runtime

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                web_runtime,
                "ensure_ollama_model",
                mock.Mock(side_effect=AssertionError("pull attempted under a disabled policy")),
            ) as ensure,
        ):
            progress = web_runtime.start_ollama_model_pull("qwen3:8b")
        self.assertTrue(progress.wait(timeout=1.0))
        self.assertEqual(progress.public["status"], "skipped")
        ensure.assert_not_called()

    def test_daily_alignment_skips_installed_inventory_when_disabled(self) -> None:
        from core.web.api import runtime as web_runtime

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "no"}):
            with mock.patch.object(
                web_runtime,
                "installed_ollama_model_inventory",
                mock.Mock(side_effect=AssertionError("inventory read under a disabled policy")),
            ):
                resolved = web_runtime.candidate_aware_daily_runtime_model_tag(
                    "qwen3:8b",
                    env={LOCAL_MODELS_ENABLED_ENV: "no"},
                    selection_pinned=False,
                )
        self.assertEqual(resolved, "qwen3:8b")

    def test_backbone_installed_filter_skips_probe_when_disabled(self) -> None:
        from core import runtime_backbone

        with mock.patch.object(
            runtime_backbone,
            "installed_ollama_model_names",
            mock.Mock(side_effect=AssertionError("/api/tags probe under a disabled policy")),
        ):
            manifests, audit_rows, truth = runtime_backbone._filter_snapshot_to_installed_ollama_inventory(
                manifests=(),
                audit_rows=(),
                capability_truth=(),
                env=_installed_lane_env() | {LOCAL_MODELS_ENABLED_ENV: "off"},
            )
        self.assertEqual((manifests, audit_rows, truth), ((), (), ()))

    def test_intent_arbiter_model_resolution_is_empty_when_disabled_by_env(self) -> None:
        from core import intent_arbiter

        intent_arbiter._MODEL_CACHE.clear()
        with mock.patch.dict(
            "os.environ",
            {LOCAL_MODELS_ENABLED_ENV: "0", "VOOL_ARBITER_MODEL": "qwen3:0.6b"},
        ):
            self.assertEqual(intent_arbiter._resolve_model(), "")

    def test_intent_arbiter_honors_explicit_model_when_enabled(self) -> None:
        from core import intent_arbiter

        intent_arbiter._MODEL_CACHE.clear()
        with mock.patch.dict(
            "os.environ",
            {"VOOL_ARBITER_MODEL": "qwen3:0.6b"},
        ):
            self.assertEqual(intent_arbiter._resolve_model(), "qwen3:0.6b")


class DisabledRouterContractTests(unittest.TestCase):
    """Turn-level truth: unpinned turns never route local, explicit local pins refuse typed,
    and a failing cloud lane cannot fall back locally. Fallback sabotage lives here on purpose:
    the cloud lane fails while a perfectly healthy local lane sits in the store."""

    def setUp(self) -> None:
        run_migrations()
        reset_provider_health()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.execute("DELETE FROM candidate_knowledge_lane")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        self.router = MemoryFirstRouter(self.registry)
        self.persona = load_active_persona("default")
        self.interpretation = HumanInputInterpretation(
            raw_text="design swarm topology",
            normalized_text="design swarm topology",
            reconstructed_text="design swarm topology",
            intent_mode="request",
            topic_hints=["swarm"],
            reference_targets=[],
            understanding_confidence=0.72,
            quality_flags=[],
        )

    def _context_result(self) -> TieredContextResult:
        context_result = TieredContextResult(
            bootstrap_items=[],
            relevant_items=[],
            cold_items=[],
            local_candidates=[],
            swarm_metadata=[],
            report=mock.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
            retrieval_confidence_score=0.15,
            cold_decision=mock.Mock(allow=False),
        )
        context_result.report.to_dict.return_value = {}
        context_result.report.total_tokens_used.return_value = 0
        context_result.assembled_context = lambda: "No strong local memory."
        return context_result

    def _resolve(self, source_context, task_text: str = "design swarm topology with resilient regions"):
        task = create_task_record(task_text)
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        # Door-stamped turn identity: the A9 plan mint refuses unnamed turns BEFORE
        # ranking (fail-closed), so a direct drive without it never reaches the lane.
        stamped = {
            "session_id": "disable-contract-suite",
            "turn_id": f"disable-contract-{abs(hash(task_text)) % 10**8}",
            **source_context,
        }
        return self.router.resolve(
            task=task,
            classification=classification,
            interpretation=self.interpretation,
            context_result=self._context_result(),
            persona=self.persona,
            source_context=stamped,
        )

    def test_explicit_local_pin_refuses_typed_when_disabled(self) -> None:
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))
        self.registry.register_manifest(dict(_CLOUD_MANIFEST))

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                self.router,
                "_invoke_manifest",
                mock.Mock(
                    return_value=(
                        mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                        ModelResponse(output_text="a different model's answer", confidence=0.8, usage={"prompt_tokens": 1, "completion_tokens": 1}),
                        None,
                    )
                ),
            ) as invoke_manifest,
        ):
            result = self._resolve({"surface": "api", "requested_model": "qwen-local"})

        self.assertEqual(result.source, "model_unavailable")
        self.assertFalse(result.used_model)
        self.assertIsNone(result.output_text)
        invoke_manifest.assert_not_called()
        self.assertEqual(result.details["reason"], LOCAL_MODELS_DISABLED_REASON)
        self.assertEqual(result.details["requested_model"], "qwen-local")
        self.assertFalse(result.details["policy"]["local_models_enabled"])
        self.assertTrue(
            all("local-qwen-http" not in str(item.get("provider_id") or "") for item in result.details["provider_inventory"]),
            result.details["provider_inventory"],
        )

    def test_explicit_local_pin_by_provider_model_form_also_refuses_typed(self) -> None:
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))
        self.registry.register_manifest(dict(_CLOUD_MANIFEST))

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "false"}):
            result = self._resolve(
                {"surface": "api", "requested_model": "local-qwen-http:qwen-local"}
            )

        self.assertEqual(result.source, "model_unavailable")
        self.assertEqual(result.details["reason"], LOCAL_MODELS_DISABLED_REASON)

    def test_unknown_model_refusal_stays_generic_under_disabled_policy(self) -> None:
        # The typed local refusal must come from the policy classification, not from every
        # unresolvable name accidentally reading as local. An unknown model under a disabled
        # policy keeps the pre-existing `requested_model_unresolvable` refusal.
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))
        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}):
            result = self._resolve({"surface": "api", "requested_model": "totally-unknown-model-xyz"})
        self.assertEqual(result.source, "model_unavailable")
        self.assertEqual(result.details["reason"], "requested_model_unresolvable")

    def test_refusal_is_gone_when_policy_is_enabled(self) -> None:
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))

        with mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(
                mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                ModelResponse(output_text="local answer", confidence=0.8, usage={"prompt_tokens": 1, "completion_tokens": 1}),
                None,
            ),
        ):
            result = self._resolve({"surface": "api", "requested_model": "qwen-local"})

        self.assertNotEqual(result.details.get("reason"), LOCAL_MODELS_DISABLED_REASON)

    def test_failing_cloud_cannot_fall_back_locally_when_disabled(self) -> None:
        # The cloud lane is auto-eligible (remote_unknown) and its endpoint is DEAD — every call
        # errors. A perfectly healthy local lane sits in the same store. The turn must fail over
        # to nothing rather than ever touch the local lane.
        cloud = dict(_CLOUD_MANIFEST)
        cloud["metadata"] = {**_CLOUD_MANIFEST["metadata"], "cost_class": "remote_unknown"}
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))
        self.registry.register_manifest(cloud)

        cloud_provider_id = "cloud-fallback-http:cloud"
        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                self.router,
                "_invoke_manifest",
                side_effect=lambda manifest, **_kwargs: (
                    None,
                    None,
                    "connection refused (dead endpoint)",
                ),
            ) as invoke_manifest,
        ):
            result = self._resolve({"surface": "api"})

        self.assertFalse(result.used_model)
        invoked_provider_ids = []
        for call in invoke_manifest.call_args_list:
            manifest = call.args[0] if call.args else call.kwargs.get("manifest")
            invoked_provider_ids.append(str(getattr(manifest, "provider_id", "")))
        self.assertNotIn("local-qwen-http:qwen-local", invoked_provider_ids)
        self.assertIn(cloud_provider_id, invoked_provider_ids)

    def test_sabotage_without_the_read_filter_local_becomes_the_fallback(self) -> None:
        # SABOTAGE PROOF for the read seam: with `filter_local_manifests_for_policy` reverted to
        # the identity (the pre-fix registry listing), the SAME failing-cloud turn reaches the
        # healthy local lane — the exact silent-local-fallback the contract forbids. This test
        # proves the negative assertion above is load-bearing, not vacuous.
        cloud = dict(_CLOUD_MANIFEST)
        cloud["metadata"] = {**_CLOUD_MANIFEST["metadata"], "cost_class": "remote_unknown"}
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))
        self.registry.register_manifest(cloud)

        from core import model_registry as model_registry_module

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                model_registry_module,
                "filter_local_manifests_for_policy",
                lambda manifests, **_kwargs: list(manifests),
            ),
            mock.patch.object(
                self.router,
                "_invoke_manifest",
                side_effect=lambda manifest, **_kwargs: (
                    None,
                    None,
                    "connection refused (dead endpoint)",
                ),
            ) as invoke_manifest,
        ):
            self._resolve({"surface": "api"})

        invoked_provider_ids = []
        for call in invoke_manifest.call_args_list:
            manifest = call.args[0] if call.args else call.kwargs.get("manifest")
            invoked_provider_ids.append(str(getattr(manifest, "provider_id", "")))
        self.assertIn("local-qwen-http:qwen-local", invoked_provider_ids)

    def test_enabled_unpinned_turn_can_still_route_local(self) -> None:
        # Enabled preserves behavior: ranking still sees the local lane (the actual answer path is
        # mocked; this asserts visibility, not generation).
        self.registry.register_manifest(dict(_LOCAL_MANIFEST))

        with mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(
                mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                ModelResponse(output_text="local answer", confidence=0.8, usage={"prompt_tokens": 1, "completion_tokens": 1}),
                None,
            ),
        ) as invoke_manifest:
            result = self._resolve({"surface": "api"})

        invoke_manifest.assert_called()
        invoked = set()
        for call in invoke_manifest.call_args_list:
            manifest = call.args[0] if call.args else call.kwargs.get("manifest")
            invoked.add(str(getattr(manifest, "provider_id", "")))
        self.assertIn("local-qwen-http:qwen-local", invoked)
        self.assertNotEqual(result.details.get("reason"), LOCAL_MODELS_DISABLED_REASON)


class DisabledDirectCallerTests(unittest.TestCase):
    """Aux lanes that talk to Ollama DIRECTLY (outside the registry) must obey the policy too.

    Live-caught 2026-09-01: with only the registry gated, a disabled daemon's background workers
    still loaded qwen3:0.6b + nomic-embed-text on the REAL local Ollama through hardcoded
    127.0.0.1:11434 call sites (conversation_summarizer / embedding_service family). Every gate
    below makes the caller take its EXISTING unreachable-Ollama fallback with zero socket use.
    """

    def test_embedding_service_never_calls_ollama_when_disabled(self) -> None:
        from core import embedding_service

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                embedding_service.urllib.request,
                "urlopen",
                mock.Mock(side_effect=AssertionError("ollama call under a disabled policy")),
            ),
        ):
            self.assertIsNone(embedding_service._best_embed_model())
            self.assertIsNone(embedding_service._ollama_embed("hello", "nomic-embed-text"))
            # The public API degrades to the deterministic hash-bag embedding, same dims.
            vec = embedding_service.embed("hello")
            self.assertEqual(len(vec), embedding_service.EMBED_DIM)

    def test_conversation_summarizer_never_calls_ollama_when_disabled(self) -> None:
        from core import conversation_summarizer

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                conversation_summarizer.urllib.request,
                "urlopen",
                mock.Mock(side_effect=AssertionError("ollama call under a disabled policy")),
            ),
        ):
            self.assertEqual(conversation_summarizer._pick_model(), "")
            self.assertEqual(
                conversation_summarizer._call_ollama("qwen3:0.6b", [{"role": "user", "content": "x"}]),
                "",
            )

    def test_task_classifier_never_probes_ollama_when_disabled(self) -> None:
        from core import task_router

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.dict("os.environ", {"PYTEST_CURRENT_TEST": ""}, clear=False),
        ):
            # The socket pre-probe must not even run: any create_connection attempt explodes.
            with mock.patch.object(
                task_router.socket,
                "create_connection",
                mock.Mock(side_effect=AssertionError("socket probe under a disabled policy")),
            ):
                self.assertEqual(task_router._classify_via_model("organize my files"), "")

    def test_brain_hive_research_skips_discovery_when_disabled(self) -> None:
        from core import brain_hive_research

        with (
            mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
            mock.patch.object(
                brain_hive_research.requests,
                "get",
                mock.Mock(side_effect=AssertionError("discovery under a disabled policy")),
            ),
        ):
            self.assertEqual(brain_hive_research._ollama_base_url(), "")

    def test_resource_governor_and_system_resources_report_nothing_when_disabled(self) -> None:
        from core import resource_governor, system_resources

        for module in (resource_governor, system_resources):
            with (
                mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}),
                mock.patch.object(
                    module.urllib.request,
                    "urlopen",
                    mock.Mock(side_effect=AssertionError("ollama probe under a disabled policy")),
                ),
            ):
                if module is resource_governor:
                    self.assertEqual(module.unload_idle_ollama(), 0.0)
                    self.assertEqual(module.resident_models(), [])
                    self.assertEqual(module._unload_models(["qwen3:8b"]), [])
                else:
                    self.assertEqual(system_resources._ollama_loaded_bytes(), 0.0)

    def test_gpu_inference_probe_skips_without_a_generation_when_disabled(self) -> None:
        from core.gpu_inference_probe import verify_gpu_inference

        def _explode(*_args, **_kwargs):
            raise AssertionError("generation probe under a disabled policy")

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}):
            verdict = verify_gpu_inference(model="qwen3:0.6b", gpu_present=True, http_post_fn=_explode)
        self.assertEqual(verdict.outcome, "skipped")
        self.assertIn("disabled", verdict.reason)

    def test_kernel_repl_local_judgment_lane_refuses_when_disabled(self) -> None:
        from core.kernel import repl

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "0"}):
            with self.assertRaises(urllib.error.URLError):
                repl._ollama_chat("system", "user")

    def test_direct_callers_still_work_when_enabled(self) -> None:
        from core import conversation_summarizer, embedding_service

        with mock.patch.dict("os.environ", {LOCAL_MODELS_ENABLED_ENV: "1"}):
            # Enabled: the gates must be transparent — the discovery probe RUNS (against a
            # dead test endpoint) and returns its normal no-Ollama answer, not the policy stub.
            with mock.patch.object(
                embedding_service.urllib.request,
                "urlopen",
                side_effect=OSError("no ollama in tests"),
            ):
                self.assertIsNone(embedding_service._best_embed_model())
            with mock.patch.object(
                conversation_summarizer.urllib.request,
                "urlopen",
                side_effect=OSError("no ollama in tests"),
            ):
                self.assertEqual(conversation_summarizer._pick_model_uncached(), "")


if __name__ == "__main__":
    unittest.main()
