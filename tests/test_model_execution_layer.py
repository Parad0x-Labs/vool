from __future__ import annotations

import json
import unittest
import uuid
from typing import Any
from unittest import mock

from adapters.base_adapter import ModelResponse
from apps.vool_agent import VoolAgent
from core.cloud_escalation_policy import CloudEscalationPolicy
from core.context_scope import ContextAccessPolicy
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter, ModelExecutionDecision
from core.model_registry import ModelRegistry
from core.reasoning_engine import build_plan
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


class _FixedMachine:
    """A deterministic stand-in for the host hardware probe.

    Only the three attributes `_hardware_fit_reason` reads are needed; `vram_free_gb` is carried
    because the autopilot's VRAM check reads it from the same shape.
    """

    def __init__(self, *, ram_gb: float, vram_gb: float = 0.0, accelerator: str = "cpu") -> None:
        self.ram_gb = ram_gb
        self.vram_gb = vram_gb
        self.accelerator = accelerator
        self.vram_free_gb = vram_gb or None


def _machine(*, ram_gb: float, vram_gb: float = 0.0, accelerator: str = "cpu"):
    """Pin the host hardware for a routing assertion.

    Whether a local manifest "fits" is decided by `core.provider_routing._hardware_fit_reason`
    from the machine's RAM, VRAM and accelerator. A manifest declaring `ram_budget_gb=7.5` fits a
    developer's 64 GB workstation and does not fit a small CI runner, and the autopilot then picks
    a DIFFERENT provider -- so any test asserting which lane wins is really asserting the
    hardware of whoever ran it.

    That is not hypothetical: it is why
    `test_role_aware_summary_execution_prefers_queen_lane` and
    `test_summary_block_uses_structured_model_path` passed on macOS and failed on CI with
    `'kimi-cloud-http' != 'local-qwen-http'` and `Called 0 times.` -- the router was correct both
    times, and only the host differed. Reproduced locally by running under `sys.platform="linux"`,
    which reports the local model as `hardware_fit=False,
    hardware_fit_reason='model_exceeds_hardware_budget'`.

    Pinning the probe removes the uncontrolled variable. It does not weaken the assertion: both
    branches of the rule are asserted, here and in
    `test_a_local_model_too_big_for_this_machine_yields_to_the_cloud_queen`.
    """

    return mock.patch(
        "core.provider_routing._device_probe",
        return_value=_FixedMachine(ram_gb=ram_gb, vram_gb=vram_gb, accelerator=accelerator),
    )


# Comfortably fits the 7.5 GB local manifests registered below; 64 * 0.70 = 44.8 GB available.
_ROOMY_MACHINE = dict(ram_gb=64.0)
# Too small for them: 8 * 0.70 = 5.6 GB available against a 7.5 GB budget.
_CRAMPED_MACHINE = dict(ram_gb=8.0)


class ModelExecutionLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("model_provider_manifests", "candidate_knowledge_lane", "learning_shards", "local_tasks"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        # Every manifest this pack registers is a LOOPBACK model, and
        # `core.final_answer_authorship` refuses an uncertified one before its adapter is built.
        # None of these tests is about authorship -- they are about lane selection and the
        # structured path -- so the probe models are certified as an operator would certify them,
        # at registration, rather than each test learning about a boundary it is not testing.
        _register = self.registry.register_manifest

        def _register_certified(manifest):
            entry = _register(manifest)
            from tests._authorship_certification import certify_for_authorship

            certify_for_authorship(entry)
            return entry

        self.registry.register_manifest = _register_certified  # type: ignore[method-assign]
        self.router = MemoryFirstRouter(self.registry)
        self.loader = TieredContextLoader()
        self.persona = load_active_persona("default")

    def _new_context_session(self) -> str:
        session_id = f"ctx-{uuid.uuid4().hex}"
        ContextAccessPolicy.for_request(
            session_id=session_id,
            source_context={"surface": "local"},
        )
        return session_id

    def _load_context(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: HumanInputInterpretation,
        session_id: str | None = None,
    ) -> Any:
        session_id = session_id or self._new_context_session()
        source_context = {
            "surface": "local",
            "runtime_session_id": session_id,
        }
        ContextAccessPolicy.for_request(
            session_id=session_id,
            source_context=source_context,
        )
        return self.loader.load(
            task=task,
            classification=classification,
            interpretation=interpretation,
            persona=self.persona,
            session_id=session_id,
            source_context=source_context,
        )

    def _insert_local_shard(self, *, session_id: str) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_session_id, share_scope, created_at, updated_at
                ) VALUES (?, 1, 'security_hardening', ?, ?, ?, ?, 'local_generated', ?, 0.95, 0.88, 0, 0, 'active', '[]', CURRENT_TIMESTAMP, NULL, '', ?, 'local_only', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    f"shard-{uuid.uuid4().hex}",
                    f"sig-{uuid.uuid4().hex}",
                    "Harden local credentials and prevent password leaks from your setup.",
                    json.dumps(["identify_sensitive_surfaces", "remove_secret_exposure_paths"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    get_local_peer_id(),
                    session_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _chat_turn_inputs(
        self,
        text: str,
        *,
        include_local_shard: bool = False,
    ) -> tuple[Any, HumanInputInterpretation, dict[str, Any], Any]:
        task = create_task_record(text)
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["chat"],
            reference_targets=[],
            understanding_confidence=0.82,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        session_id = self._new_context_session()
        if include_local_shard:
            self._insert_local_shard(session_id=session_id)
        context_result = self._load_context(
            task=task,
            classification=classification,
            interpretation=interpretation,
            session_id=session_id,
        )
        return task, interpretation, classification, context_result

    def test_memory_first_hit_skips_model_call(self) -> None:
        session_id = self._new_context_session()
        self._insert_local_shard(session_id=session_id)
        task = create_task_record("harden local credentials so passwords never leak")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["security hardening", "password leak"],
            reference_targets=[],
            understanding_confidence=0.82,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self._load_context(
            task=task,
            classification=classification,
            interpretation=interpretation,
            session_id=session_id,
        )
        with mock.patch.object(self.registry, "build_adapter") as build_adapter:
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
            )
        self.assertEqual(decision.source, "memory_hit")
        build_adapter.assert_not_called()

    def test_chat_surface_forces_provider_over_exact_cache_hit(self) -> None:
        task, interpretation, classification, context_result = self._chat_turn_inputs("do you think boredom is useful?")
        with mock.patch(
            "core.memory_first_router.get_exact_candidate",
            return_value={
                "provider_name": "cached",
                "model_name": "cached-model",
                "normalized_output": "Cached answer that should not speak directly.",
                "structured_output": None,
                "confidence": 0.88,
                "trust_score": 0.88,
                "candidate_id": "cached-1",
                "validation_state": "valid",
            },
        ), mock.patch("core.memory_first_router.should_revalidate", return_value=False), mock.patch.object(
            self.router,
            "_execute_provider_task",
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="chat-cache-forced",
                provider_id="test-provider",
                used_model=True,
                output_text="Fresh provider answer.",
            ),
        ) as execute_provider:
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=False,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw", "session_id": "mel-test-session", "turn_id": "mel-test-turn"},
            )

        self.assertEqual(decision.source, "provider_execution")
        execute_provider.assert_called_once()

    def test_chat_surface_forces_provider_over_memory_hit(self) -> None:
        task, interpretation, classification, context_result = self._chat_turn_inputs(
            "harden local credentials so passwords never leak",
            include_local_shard=True,
        )
        with mock.patch.object(
            self.router,
            "_execute_provider_task",
            return_value=ModelExecutionDecision(
                source="no_provider_available",
                task_hash="chat-memory-forced",
                used_model=False,
            ),
        ) as execute_provider:
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=False,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw", "session_id": "mel-test-session", "turn_id": "mel-test-turn"},
            )

        self.assertEqual(decision.source, "no_provider_available")
        execute_provider.assert_called_once()

    def test_provider_registration_and_routing_with_trust(self) -> None:
        self.registry.register_manifest(
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
            }
        )
        manifest = self.registry.select_manifest(
            request=mock.Mock(
                task_kind="action_plan",
                output_mode="action_plan",
                preferred_provider=None,
                preferred_model=None,
                preferred_source_types=[],
                require_license_metadata=True,
                forbid_bundled_weights=True,
                allow_paid_fallback=True,
                exclude_provider_ids=[],
                min_trust=0.0,
            )
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.provider_name, "local-qwen-http")

    def test_existing_local_agent_flow_still_returns_model_execution_metadata(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="local-model-test", persona_id="default")
        agent.start()
        with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
            "core.agent_runtime.agent.request_relevant_holders", return_value=[]
        ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
            result = agent.run_once("check current local setup status")
        self.assertIn("model_execution", result)
        self.assertIn("source", result["model_execution"])

    def test_valid_model_candidates_can_become_durable_plans(self) -> None:
        task = create_task_record("design persistent openclaw continuity")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["openclaw", "memory"],
            reference_targets=[],
            understanding_confidence=0.84,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        plan = build_plan(
            task,
            classification,
            evidence={
                "model_candidates": [
                    {
                        "summary": "Persist rolling session summaries and retrieve them by relevance.",
                        "resolution_pattern": ["persist_session_summary", "retrieve_relevant_memory"],
                        "score": 0.81,
                        "validation_state": "valid",
                        "provider_name": "local-qwen-http",
                    }
                ]
            },
            persona=self.persona,
        )
        self.assertGreaterEqual(plan.confidence, 0.8)

    def test_summary_block_uses_structured_model_path(self) -> None:
        self.registry.register_manifest(
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
            }
        )
        task = create_task_record("search latest qwen release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["research", "news"],
            reference_targets=[],
            understanding_confidence=0.86,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self._load_context(
            task=task,
            classification=classification,
            interpretation=interpretation,
        )
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.get_license_metadata.return_value = {}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Grounded summary","bullets":["Check official release notes"]}',
            confidence=0.74,
        )

        with mock.patch.object(self.registry, "build_adapter", return_value=adapter), _machine(
            **_ROOMY_MACHINE
        ):
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=True,
                surface="cli",
                source_context={"surface": "cli", "platform": "cli", "session_id": "mel-test-session", "turn_id": "mel-test-turn"},
            )

        adapter.run_structured_task.assert_called_once()
        adapter.run_text_task.assert_not_called()
        self.assertEqual(decision.validation_state, "valid")
        self.assertEqual(decision.structured_output["summary"], "Grounded summary")

    def test_role_aware_summary_execution_prefers_queen_lane(self) -> None:
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
        queen_manifest = self.registry.register_manifest(
            {
                "provider_name": "kimi-cloud-http",
                "model_name": "kimi-latest",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "structured_json", "long_context"],
                "runtime_config": {"base_url": "https://kimi.example", "api_key_env": "KIMI_API_KEY"},
                "enabled": True,
                "metadata": {"orchestration_role": "queen"},
            }
        )
        task, interpretation, classification, context_result = self._chat_turn_inputs("research the latest qwen release notes")
        local_adapter = mock.Mock()
        local_adapter.health_check.return_value = {"ok": True}
        local_adapter.estimate_cost_class.return_value = "free_local"
        local_adapter.get_license_metadata.return_value = {}
        local_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Local answer","bullets":["Use local lane"]}',
            confidence=0.61,
        )
        queen_adapter = mock.Mock()
        queen_adapter.health_check.return_value = {"ok": True}
        queen_adapter.estimate_cost_class.return_value = "paid_cloud"
        queen_adapter.get_license_metadata.return_value = {}
        queen_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Queen answer","bullets":["Use stronger synthesis"]}',
            confidence=0.84,
        )

        def _build_adapter(manifest):
            if manifest.provider_id == queen_manifest.provider_id:
                return queen_adapter
            if manifest.provider_id == local_manifest.provider_id:
                return local_adapter
            raise AssertionError(f"unexpected provider {manifest.provider_id}")

        with mock.patch(
            "core.memory_first_router.model_execution_profile",
            return_value={
                "task_kind": "summarization",
                "output_mode": "summary_block",
                "allow_paid_fallback": True,
                "provider_role": "queen",
            },
        ), mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[queen_manifest, local_manifest],
        ) as rank_candidates, mock.patch.object(self.registry, "build_adapter", side_effect=_build_adapter), mock.patch(
            # The paid queen lane requires cloud escalation to be opted in (off by default).
            "core.cloud_escalation_policy.load_policy",
            return_value=CloudEscalationPolicy(mode="auto", daily_cap=1000),
        ), mock.patch("core.cloud_escalation_policy.used_today", return_value=0), _machine(
            **_ROOMY_MACHINE
        ):
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=True,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw", "session_id": "mel-test-session", "turn_id": "mel-test-turn"},
            )

        self.assertEqual(rank_candidates.call_args.kwargs["role"], "queen")
        self.assertEqual(decision.provider_name, local_manifest.provider_name)
        self.assertEqual(decision.details["provider_role"], "queen")
        self.assertEqual(decision.details["ranked_candidates"][0], local_manifest.provider_id)

    def test_a_local_model_too_big_for_this_machine_yields_to_the_cloud_queen(self) -> None:
        """The other branch of the same rule, and the behaviour CI was actually exercising.

        Identical to `test_role_aware_summary_execution_prefers_queen_lane` except the machine is
        too small for the 7.5 GB local manifest. The router is right to send the turn to the cloud
        queen there -- it was right on CI too. Until this test existed, which branch ran was decided
        by the RAM of whoever happened to run the suite, and the failure looked like a routing bug
        instead of an unpinned host.
        """

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
        queen_manifest = self.registry.register_manifest(
            {
                "provider_name": "kimi-cloud-http",
                "model_name": "kimi-latest",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "structured_json", "long_context"],
                "runtime_config": {"base_url": "https://kimi.example", "api_key_env": "KIMI_API_KEY"},
                "enabled": True,
                "metadata": {"orchestration_role": "queen"},
            }
        )
        task, interpretation, classification, context_result = self._chat_turn_inputs(
            "research the latest qwen release notes"
        )

        def _adapter(_manifest):
            built = mock.Mock()
            built.health_check.return_value = {"ok": True}
            built.estimate_cost_class.return_value = "paid_cloud"
            built.get_license_metadata.return_value = {}
            built.run_structured_task.return_value = ModelResponse(
                output_text='{"summary":"Queen answer","bullets":["Use stronger synthesis"]}',
                confidence=0.84,
            )
            return built

        with mock.patch(
            "core.memory_first_router.model_execution_profile",
            return_value={
                "task_kind": "summarization",
                "output_mode": "summary_block",
                "allow_paid_fallback": True,
                "provider_role": "queen",
            },
        ), mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[queen_manifest, local_manifest],
        ), mock.patch.object(self.registry, "build_adapter", side_effect=_adapter), mock.patch(
            "core.cloud_escalation_policy.load_policy",
            return_value=CloudEscalationPolicy(mode="auto", daily_cap=1000),
        ), mock.patch("core.cloud_escalation_policy.used_today", return_value=0), _machine(
            **_CRAMPED_MACHINE
        ):
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=True,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw", "session_id": "mel-test-session", "turn_id": "mel-test-turn"},
            )

        self.assertEqual(decision.provider_name, queen_manifest.provider_name)

    def test_ungrounded_live_lookup_summary_is_downgraded(self) -> None:
        task = create_task_record("check hive mind tasks")
        classification = {"task_class": "research", "confidence_hint": 0.72}
        plan = build_plan(
            task,
            classification,
            evidence={
                "model_candidates": [
                    {
                        "summary": "I checked online and found some real AI hive tasks.",
                        "score": 0.83,
                        "validation_state": "valid",
                        "provider_name": "local-qwen-http",
                    }
                ],
                "web_notes": [],
            },
            persona=self.persona,
        )
        self.assertIn("No verified live lookup result", plan.summary)
        self.assertIn("ungrounded_live_claim", plan.risk_flags)
        self.assertLessEqual(plan.confidence, 0.38)

    def test_tool_intent_uses_structured_model_path(self) -> None:
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
                "capabilities": ["summarize", "structured_json", "tool_intent"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"},
                "enabled": True,
            }
        )
        task = create_task_record("latest qwen release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["research", "news"],
            reference_targets=[],
            understanding_confidence=0.87,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self._load_context(
            task=task,
            classification=classification,
            interpretation=interpretation,
        )
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.get_license_metadata.return_value = {}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"intent":"web.search","arguments":{"query":"latest qwen release notes","limit":2}}',
            confidence=0.78,
        )

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest],
        ) as rank_candidates, mock.patch.object(self.registry, "build_adapter", return_value=adapter):
            decision = self.router.resolve_tool_intent(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw", "session_id": "mel-test-session", "turn_id": "mel-test-turn"},
            )

        adapter.run_structured_task.assert_called_once()
        adapter.run_text_task.assert_not_called()
        self.assertEqual(rank_candidates.call_args.kwargs["role"], "drone")
        self.assertEqual(decision.validation_state, "valid")
        self.assertEqual(decision.structured_output["intent"], "web.search")
        self.assertEqual(decision.details["provider_role"], "drone")


if __name__ == "__main__":
    unittest.main()
