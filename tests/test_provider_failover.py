from __future__ import annotations

import time
import unittest
from unittest import mock

from adapters.base_adapter import ModelRequest, ModelResponse
from core.cloud_escalation_policy import CloudEscalationPolicy
from core.compute_mode import ComputeBudget
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter
from core.model_health import circuit_is_open, get_provider_health, record_provider_failure, reset_provider_health
from core.model_registry import ModelRegistry
from core.normalized_provider_result import EmptyProviderResponseError
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextResult
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest


class ProviderFailoverTests(unittest.TestCase):
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

    def _register_providers(self):
        local_manifest = self.registry.register_manifest(
            {
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
        )
        cloud_manifest = self.registry.register_manifest(
            {
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
        )
        # The authorship fence refuses uncertified LOCAL models before any adapter is built
        # (core.final_answer_authorship.precall_author_verdict), so a rig whose stubs are not
        # certified never exercises the failover ladder at all: every candidate comes back
        # author_not_certified_for_final_answer and no provider failure is ever recorded.
        from tests._authorship_certification import certify_for_authorship

        certify_for_authorship(local_manifest)
        certify_for_authorship(cloud_manifest)
        return local_manifest, cloud_manifest

    def _register_backup_local(self):
        backup = self.registry.register_manifest(
            {
                "provider_name": "local-backup-http",
                "model_name": "qwen-backup",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:2234"},
                "enabled": True,
                "metadata": {"orchestration_role": "drone"},
            }
        )
        from tests._authorship_certification import certify_for_authorship

        certify_for_authorship(backup)
        return backup

    def _context_result(self) -> TieredContextResult:
        result = TieredContextResult(
            bootstrap_items=[],
            relevant_items=[],
            cold_items=[],
            local_candidates=[],
            swarm_metadata=[],
            report=mock.Mock(
                retrieval_confidence="low",
                swarm_metadata_consulted=False,
                cold_archive_opened=False,
            ),
            retrieval_confidence_score=0.15,
            cold_decision=mock.Mock(allow=False),
        )
        result.report.to_dict.return_value = {}
        result.report.total_tokens_used.return_value = 0
        result.assembled_context = lambda: "No strong local memory."
        return result

    def test_local_provider_failure_does_not_trigger_unreserved_paid_failover(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
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

        local_adapter = mock.Mock()
        local_adapter.health_check.return_value = {"ok": True}
        local_adapter.get_license_metadata.return_value = {"license_name": "Apache-2.0", "license_reference": "https://www.apache.org/licenses/LICENSE-2.0"}
        local_adapter.estimate_cost_class.return_value = "local_free"
        # Both task methods, not just the structured one: this turn dispatches run_text_task
        # (plain-text output mode), and an unstubbed method happily returns a child Mock whose
        # provider_metadata normalization explodes -- the provider then "fails" for a reason the
        # test never set up, and no provider failure is recorded.
        local_adapter.run_text_task.side_effect = RuntimeError("timeout")
        local_adapter.run_structured_task.side_effect = RuntimeError("timeout")

        cloud_adapter = mock.Mock()
        cloud_adapter.health_check.return_value = {"ok": True}
        cloud_adapter.run_structured_task.return_value = mock.Mock(output_text='{"summary":"Use regional meet nodes","steps":["pick regions","sync summaries"]}', confidence=0.81)
        cloud_adapter.get_license_metadata.return_value = {"license_name": "Provider", "license_reference": "user-managed"}
        cloud_adapter.estimate_cost_class.return_value = "paid_cloud"

        def build_adapter(manifest):
            if manifest.provider_name == "local-qwen-http":
                return local_adapter
            return cloud_adapter

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ) as rank_candidates, mock.patch.object(self.registry, "build_adapter", side_effect=build_adapter):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
                # A9: the routing plan mint refuses a turn without identity.
                source_context={
                    "surface": "api",
                    "session_id": "provider-failover-suite",
                    "turn_id": "turn-local-failure",
                },
            )

        self.assertFalse(result.used_model)
        self.assertTrue(result.failover_used)
        self.assertEqual(result.source, "no_provider_available")
        self.assertEqual(rank_candidates.call_args.kwargs["role"], "queen")
        self.assertFalse(rank_candidates.call_args.kwargs["allow_paid_fallback"])
        self.assertEqual(get_provider_health("local-qwen-http:qwen-local").consecutive_failures, 1)
        self.assertEqual(result.details["provider_role"], "queen")

    def test_requested_model_in_source_context_steers_provider_preferences(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
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

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[cloud_manifest, local_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ) as invoke_manifest:
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
                source_context={
                    # A9: the routing plan mint refuses a turn without identity.
                    "surface": "api",
                    "session_id": "provider-failover-suite",
                    "turn_id": "turn-local-qwen-http:qwen-local",
                    "requested_model": "local-qwen-http:qwen-local",
                },
            )

        # History: this pinned `no_provider_available`. An EXPLICIT model pin that cannot run now
        # ends at the typed `selected_model_blocked` terminal (memory_first_router
        # ._selected_model_blocked_decision: "the selected model does not get answered for by a
        # different model ... no silent substitution") — the same outcome, named for what it is.
        self.assertEqual(result.source, "selected_model_blocked")
        self.assertEqual(rank_candidates.call_args.kwargs["preferred_provider"], "local-qwen-http")
        self.assertEqual(rank_candidates.call_args.kwargs["preferred_model"], "qwen-local")
        self.assertEqual(invoke_manifest.call_count, 1)
        self.assertEqual(invoke_manifest.call_args.kwargs["manifest"].provider_id, local_manifest.provider_id)

    def test_manual_selected_model_retries_empty_once_but_never_crosses_models(self) -> None:
        selected, _cloud = self._register_providers()
        backup = self._register_backup_local()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        selected_adapter = mock.Mock()
        selected_adapter.supports_streaming.return_value = False
        selected_adapter.run_structured_task.side_effect = [
            EmptyProviderResponseError("first empty completion"),
            EmptyProviderResponseError("second empty completion"),
        ]
        selected_adapter.run_text_task.side_effect = [
            EmptyProviderResponseError("first empty completion"),
            EmptyProviderResponseError("second empty completion"),
        ]
        backup_adapter = mock.Mock()
        backup_adapter.supports_streaming.return_value = False
        backup_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Backup must never answer a Manual turn.","steps":["stop"]}',
            confidence=0.8,
        )
        backup_adapter.run_text_task.return_value = backup_adapter.run_structured_task.return_value

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[selected, backup],
        ), mock.patch.object(
            self.registry,
            "build_adapter",
            side_effect=lambda manifest: (
                selected_adapter if manifest.provider_id == selected.provider_id else backup_adapter
            ),
        ), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=False
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={
                    # A9: the routing plan mint refuses a turn without identity.
                    "surface": "api",
                    "session_id": "provider-failover-suite",
                    "turn_id": "turn-manual-selected",
                    "requested_model": selected.provider_id,
                },
            )

        self.assertFalse(result.used_model)
        self.assertEqual(
            selected_adapter.run_structured_task.call_count
            + selected_adapter.run_text_task.call_count,
            2,
        )
        backup_adapter.run_structured_task.assert_not_called()
        backup_adapter.run_text_task.assert_not_called()

    def test_auto_policy_falls_back_only_after_same_model_empty_retry_exhausts(self) -> None:
        primary, _cloud = self._register_providers()
        backup = self._register_backup_local()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        primary_adapter = mock.Mock()
        primary_adapter.supports_streaming.return_value = False
        primary_adapter.run_structured_task.side_effect = [
            EmptyProviderResponseError("empty candidate list"),
            EmptyProviderResponseError("still empty on the bounded retry"),
        ]
        primary_adapter.run_text_task.side_effect = [
            EmptyProviderResponseError("empty candidate list"),
            EmptyProviderResponseError("still empty on the bounded retry"),
        ]
        backup_adapter = mock.Mock()
        backup_adapter.supports_streaming.return_value = False
        backup_adapter.get_license_metadata.return_value = {
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
        }
        backup_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Use resilient regional coordinators.","steps":["partition","synchronize"]}',
            confidence=0.82,
            usage={"prompt_tokens": 20, "completion_tokens": 12},
            provider_id=backup.provider_id,
            model_name=backup.model_name,
        )
        backup_adapter.run_text_task.return_value = backup_adapter.run_structured_task.return_value

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[primary, backup],
        ), mock.patch.object(
            self.registry,
            "build_adapter",
            side_effect=lambda manifest: (
                primary_adapter if manifest.provider_id == primary.provider_id else backup_adapter
            ),
        ), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=False
        ), mock.patch(
            "core.memory_first_router.record_candidate_output", return_value="candidate-backup"
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={
                    # A9: the routing plan mint refuses a turn without identity.
                    "surface": "api",
                    "session_id": "provider-failover-suite",
                    "turn_id": "turn-auto-empty-retry",
                },
            )

        self.assertTrue(result.used_model)
        self.assertEqual(result.model_name, backup.model_name)
        self.assertEqual(
            primary_adapter.run_structured_task.call_count
            + primary_adapter.run_text_task.call_count,
            2,
        )
        self.assertEqual(
            backup_adapter.run_structured_task.call_count
            + backup_adapter.run_text_task.call_count,
            1,
        )

    def test_requested_paid_cloud_model_still_requires_reservation(self) -> None:
        """A paid pick is only selectable once the SERVER built a reservation for it.

        The reservation is now built server-side for an owner-local explicit pick
        (core/paid_call_reservation.py). This case pins the other half: when the builder
        declines — here, because the daily cloud cap is spent — the paid manifest stays
        excluded and the turn does not reach it.
        """
        _local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("reply with provider identity only")
        classification = {"task_class": "chat_conversation"}
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

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[cloud_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ), mock.patch(
            "core.paid_call_reservation.daily_call_cap_available",
            return_value=False,
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
                # Owner-local (the loopback HTTP dispatcher stamps _owner_local) and an explicit
                # paid pick — but the cap is spent, so no reservation is built.
                source_context={"surface": "api", "session_id": "provider-failover-suite", "turn_id": "turn-paid-cloud", "requested_model": "cloud", "_owner_local": True},
            )

        # Same history as the steering test: an explicit paid pin with no reservation is a
        # blocked SELECTION (typed terminal), not a generic provider shortage.
        self.assertEqual(result.source, "selected_model_blocked")
        self.assertFalse(rank_candidates.call_args.kwargs["allow_paid_fallback"])
        self.assertEqual(rank_candidates.call_args.kwargs["preferred_model"], "cloud")

    def test_a_non_owner_explicit_paid_pick_gets_no_reservation(self) -> None:
        """The same explicit pick from a non-owner surface is refused with the cap wide open."""
        _local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("reply with provider identity only")
        classification = {"task_class": "chat_conversation"}
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

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[cloud_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ), mock.patch(
            "core.paid_call_reservation.daily_call_cap_available",
            return_value=True,
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
                source_context={"surface": "openclaw", "session_id": "provider-failover-suite", "turn_id": "turn-non-owner-paid", "requested_model": "cloud", "_owner_local": False},
            )

        # Same history as the steering test: a non-owner's explicit paid pick that cannot be
        # authorized is a blocked SELECTION (typed terminal), not a generic provider shortage.
        self.assertEqual(result.source, "selected_model_blocked")
        self.assertFalse(rank_candidates.call_args.kwargs["allow_paid_fallback"])

    def test_selected_verified_free_openrouter_model_can_execute_without_reservation(self) -> None:
        from core.openrouter_catalog import OpenRouterModel

        verified_free = OpenRouterModel(
            model_id="tencent/hy3:free",
            name="HY3 Free",
            context_length=262144,
            prompt_usd_per_token=0.0,
            completion_usd_per_token=0.0,
            request_usd=0.0,
            supported_parameters=("tools",),
            input_modalities=("text",),
            output_modalities=("text",),
            fetched_at="2026-07-20T00:00:00+00:00",
        )
        manifest = self.registry.register_manifest(
            ModelProviderManifest(
                provider_name="openrouter-byok",
                model_name="tencent/hy3:free",
                source_type="http",
                adapter_type="openai_compatible",
                license_name="Provider",
                license_reference="user-managed",
                capabilities=["summarize", "format", "structured_json"],
                runtime_config={"base_url": "https://openrouter.ai/api/v1"},
                metadata={"deployment_class": "remote", "cost_class": "paid_cloud"},
            )
        )
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.run_text_task.return_value = ModelResponse(
            output_text="hy3 response",
            confidence=0.8,
            usage={"prompt_tokens": 2, "completion_tokens": 3},
        )
        task = create_task_record("reply with the selected cloud model identity")

        with mock.patch("core.openrouter_catalog.safe_all_models", return_value=((verified_free,), 0.0)), mock.patch.object(
            self.registry, "build_adapter", return_value=adapter
        ):
            built_adapter, response, error = self.router._invoke_manifest(
                manifest=manifest,
                request=ModelRequest(task_kind="chat", prompt="hello"),
                output_mode="plain_text",
                task=task,
                source_context={"session_id": "provider-failover-suite", "turn_id": "turn-health-cycle", "requested_model": manifest.model_name, "_owner_local": True},
            )

        self.assertIs(built_adapter, adapter)
        self.assertIsNotNone(response)
        self.assertIsNone(error)
        self.assertEqual(response.output_text, "hy3 response")

    def test_circuit_breaker_trips_and_recovers_after_cooldown(self) -> None:
        state = record_provider_failure(
            "local-qwen-http:qwen-local",
            error="timeout",
            timeout=True,
            failure_threshold=1,
            cooldown_seconds=10,
        )
        self.assertTrue(circuit_is_open("local-qwen-http:qwen-local"))
        with mock.patch("core.model_health.time.time", return_value=state.circuit_open_until + 1):
            self.assertFalse(circuit_is_open("local-qwen-http:qwen-local"))

    def test_unreserved_remote_is_excluded_from_local_remote_race(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
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

        local_adapter = mock.Mock()
        local_adapter.health_check.return_value = {"ok": True}
        local_adapter.get_license_metadata.return_value = {}
        local_adapter.estimate_cost_class.return_value = "free_local"
        local_adapter.run_structured_task.side_effect = lambda *_args, **_kwargs: (
            time.sleep(0.2),
            ModelResponse(output_text='{"summary":"local"}', confidence=0.7),
        )[1]

        cloud_adapter = mock.Mock()
        cloud_adapter.health_check.return_value = {"ok": True}
        cloud_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"remote"}',
            confidence=0.83,
        )
        cloud_adapter.get_license_metadata.return_value = {"license_name": "Provider", "license_reference": "user-managed"}
        cloud_adapter.estimate_cost_class.return_value = "paid_cloud"

        def build_adapter(manifest):
            if manifest.provider_name == "local-qwen-http":
                return local_adapter
            return cloud_adapter

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ), mock.patch("core.memory_first_router.get_active_compute_budget", return_value=ComputeBudget(mode="max_push", cpu_threads=8, gpu_memory_fraction=0.9, worker_pool_cap=4, reason="test")), mock.patch.object(
            self.registry,
            "build_adapter",
            side_effect=build_adapter,
        ), mock.patch(
            # The paid cloud lane in the race requires cloud escalation opt-in (off by default).
            "core.cloud_escalation_policy.load_policy",
            return_value=CloudEscalationPolicy(mode="auto", daily_cap=1000),
        ), mock.patch("core.cloud_escalation_policy.used_today", return_value=0):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
                # A9: the routing plan mint refuses a turn without identity.
                source_context={
                    "session_id": "provider-failover-suite",
                    "turn_id": "turn-health-cycle",
                },
            )

        self.assertEqual(result.source, "provider_execution")
        self.assertEqual(result.provider_name, "local-qwen-http")
        self.assertFalse(result.failover_used)


if __name__ == "__main__":
    unittest.main()
