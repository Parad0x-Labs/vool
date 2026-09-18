"""An explicit model pick that cannot be resolved must fail closed, never get silently substituted.

Root cause traced across the routing map: `_execute_provider_task` resolves an explicit pick via
`_requested_model_manifest` (exact provider_id / unique model_name / "provider:model" match only).
When that returns None but `preferred_model` is still set, `rank_providers`' relaxed pass treats the
unresolved name as a scoring bonus, not a requirement -- so ranking can return ANY eligible manifest
and the turn answers as a completely different model with no signal a substitution happened. This is
exactly the failure the operator's invariant names: "When an explicitly selected model is
unavailable: MODEL_UNAVAILABLE, not an answer from a different model."

The fix short-circuits before ranking ever runs: an explicit pick that does not resolve to exactly
one live manifest returns a new terminal `ModelExecutionDecision(source="model_unavailable", ...)`.
The sabotage-proof assertion in every "should fail closed" test below is two-part: the result source
IS `model_unavailable`, AND `rank_provider_candidates` was never called -- reverting the fix makes
ranking run (the mock would be called) and lets whatever it returns silently answer instead.
"""
from __future__ import annotations

import unittest
from unittest import mock

from adapters.base_adapter import ModelResponse
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextResult
from storage.db import get_connection
from storage.migrations import run_migrations


class ModelUnavailableTerminalStateTests(unittest.TestCase):
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
        return local_manifest, cloud_manifest

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

    def test_unresolvable_explicit_model_returns_model_unavailable_not_a_substitution(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())

        # If the fix is reverted, ranking runs and this mock hands back an unrelated manifest, and
        # `_invoke_manifest` is mocked to SUCCEED for it -- proving the turn would silently answer
        # as `local-qwen-http:qwen-local` even though the user asked for "totally-unknown-model-xyz".
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(
                mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                ModelResponse(output_text="a different model's answer", confidence=0.8, usage={"prompt_tokens": 1, "completion_tokens": 1}),
                None,
            ),
        ) as invoke_manifest:
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={"surface": "api", "requested_model": "totally-unknown-model-xyz"},
            )

        self.assertEqual(result.source, "model_unavailable")
        self.assertFalse(result.used_model)
        self.assertIsNone(result.output_text)
        self.assertNotEqual(result.output_text, "a different model's answer")
        invoke_manifest.assert_not_called()
        self.assertEqual(result.details["requested_model"], "totally-unknown-model-xyz")
        self.assertEqual(result.details["reason"], "requested_model_unresolvable")
        rank_candidates.assert_not_called()

    def test_ambiguous_model_name_across_two_manifests_fails_closed(self) -> None:
        # Both manifests share model_name "shared-model" under different providers -- a bare
        # (unqualified) request for it cannot resolve to exactly one manifest.
        self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "shared-model",
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
        self.registry.register_manifest(
            {
                "provider_name": "cloud-fallback-http",
                "model_name": "shared-model",
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
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())

        with mock.patch("core.memory_first_router.rank_provider_candidates") as rank_candidates:
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={"surface": "api", "requested_model": "shared-model"},
            )

        self.assertEqual(result.source, "model_unavailable")
        self.assertEqual(result.details["requested_model"], "shared-model")
        rank_candidates.assert_not_called()

    def test_resolvable_explicit_pick_still_routes_normally(self) -> None:
        """Control: a pick that DOES resolve must not trip the new gate."""
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[cloud_manifest, local_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={"surface": "api", "turn_id": "mu-turn", "session_id": "mu-session", "requested_model": "local-qwen-http:qwen-local"},
            )

        self.assertNotEqual(result.source, "model_unavailable")
        rank_candidates.assert_called_once()

    def test_auto_routing_with_no_explicit_pick_still_routes_normally(self) -> None:
        """Control: no requested_model at all is ordinary auto-routing, not an unresolvable pick."""
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={"surface": "api", "turn_id": "mu-turn", "session_id": "mu-session"},
            )

        self.assertNotEqual(result.source, "model_unavailable")
        rank_candidates.assert_called_once()

    def test_default_sentinel_model_name_is_not_treated_as_an_explicit_pick(self) -> None:
        """`vool` / `vool:latest` mean "use the default", identical to no pick at all."""
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={"surface": "api", "turn_id": "mu-turn", "session_id": "mu-session", "requested_model": "vool:latest"},
            )

        self.assertNotEqual(result.source, "model_unavailable")
        rank_candidates.assert_called_once()


if __name__ == "__main__":
    unittest.main()
