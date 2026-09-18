from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from core.learning import LearningPolicy
from core.learning.procedure_shards import ProcedureShardV1
from core.model_health import get_provider_health, reset_provider_health
from core.model_registry import ModelRegistry
from core.model_teacher_pipeline import ModelTeacherPipeline
from core.orchestration import (
    TaskGraph,
    build_task_envelope,
    evaluate_task_envelope_capacity,
    get_role_contract,
    merge_task_results,
    request_cancellation,
    resume_task,
    schedule_task_envelopes,
)
from core.provider_routing import resolve_provider_routing_plan_for_envelope
from core.task_router import build_task_envelope_for_request
from storage.db import get_connection
from storage.migrations import run_migrations


def _clear_manifests() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


class OrchestrationPhase1Tests(unittest.TestCase):
    def test_build_task_envelope_uses_role_defaults(self) -> None:
        envelope = build_task_envelope(role="coder", goal="Patch the failing test")

        self.assertEqual(envelope.role, "coder")
        self.assertIn("workspace.write", envelope.tool_permissions)
        self.assertIn("workspace_write", envelope.allowed_side_effects)

    def test_role_contracts_keep_verifier_read_only(self) -> None:
        contract = get_role_contract("verifier")

        self.assertFalse(contract.can_mutate_workspace)
        self.assertIn("workspace.validate", contract.default_tool_permissions)
        self.assertEqual(contract.provider_role, "drone")

    def test_merge_task_results_is_deterministic(self) -> None:
        envelope = build_task_envelope(role="queen", goal="Merge worker results", merge_strategy="highest_score")
        merged = merge_task_results(
            envelope,
            [
                {"task_id": "b", "ok": True, "score": 0.8},
                {"task_id": "a", "ok": True, "score": 0.8},
                {"task_id": "c", "ok": True, "score": 0.9},
            ],
        )

        self.assertEqual(merged["winner"]["task_id"], "c")
        self.assertEqual(merged["strategy"], "highest_score")

    def test_merge_task_results_fails_closed_when_verifier_child_fails(self) -> None:
        envelope = build_task_envelope(role="queen", goal="Merge worker results", merge_strategy="highest_score")
        merged = merge_task_results(
            envelope,
            [
                {"task_id": "coder-child", "role": "coder", "ok": True, "score": 0.95, "text": "Patch applied."},
                {
                    "task_id": "verify-child",
                    "role": "verifier",
                    "ok": False,
                    "status": "executed",
                    "score": 0.0,
                    "text": "Tests still fail after the patch.",
                },
            ],
        )

        self.assertFalse(merged["ok"])
        self.assertEqual(merged["winner"]["task_id"], "verify-child")
        self.assertEqual(merged["winner"]["text"], "Tests still fail after the patch.")

    def test_merge_task_results_can_return_last_success_for_recovery_flow(self) -> None:
        envelope = build_task_envelope(role="queen", goal="Merge worker results", merge_strategy="last_success")
        merged = merge_task_results(
            envelope,
            [
                {"task_id": "coder-primary", "role": "coder", "ok": False, "status": "executed", "text": "Primary patch failed."},
                {"task_id": "coder-fallback", "role": "coder", "ok": True, "status": "completed", "text": "Fallback patch applied."},
                {"task_id": "verify-final", "role": "verifier", "ok": True, "status": "completed", "text": "Recovered cleanly."},
            ],
        )

        self.assertTrue(merged["ok"])
        self.assertEqual(merged["winner"]["task_id"], "verify-final")
        self.assertEqual(merged["strategy"], "last_success")

    def test_merge_task_results_last_success_fails_closed_when_final_verifier_fails(self) -> None:
        envelope = build_task_envelope(role="queen", goal="Merge worker results", merge_strategy="last_success")
        merged = merge_task_results(
            envelope,
            [
                {"task_id": "coder-primary", "role": "coder", "ok": False, "status": "executed", "text": "Primary patch failed."},
                {"task_id": "coder-fallback", "role": "coder", "ok": True, "status": "completed", "text": "Fallback patch applied."},
                {"task_id": "verify-final", "role": "verifier", "ok": False, "status": "executed", "text": "Recovered patch still fails."},
            ],
        )

        self.assertFalse(merged["ok"])
        self.assertEqual(merged["winner"]["task_id"], "verify-final")
        self.assertEqual(merged["winner"]["text"], "Recovered patch still fails.")

    def test_cancel_and_resume_flow_updates_children(self) -> None:
        graph = TaskGraph()
        parent = build_task_envelope(role="queen", goal="Coordinate", task_id="parent")
        child = build_task_envelope(role="coder", goal="Patch", task_id="child", parent_task_id="parent")
        graph.add_task(parent)
        graph.add_task(child)
        graph.mark_status("parent", "running")
        graph.mark_status("child", "running")

        request_cancellation(graph, "parent", reason="user requested")
        self.assertEqual(graph.get("parent").status, "cancel_requested")
        self.assertEqual(graph.get("child").status, "cancel_requested")

        resume_task(graph, "parent")
        self.assertEqual(graph.get("parent").status, "pending")
        self.assertEqual(graph.get("child").status, "pending")

    def test_scheduler_prioritizes_low_latency_tasks(self) -> None:
        fast = build_task_envelope(role="verifier", goal="Verify", task_id="fast", latency_budget="low_latency")
        slow = build_task_envelope(role="coder", goal="Implement", task_id="slow", latency_budget="deep")
        scheduled = schedule_task_envelopes([slow, fast])

        self.assertEqual(scheduled[0].task_id, "fast")

    def test_scheduler_deprioritizes_saturated_lane_when_capacity_truth_is_present(self) -> None:
        saturated = build_task_envelope(
            role="coder",
            goal="Implement on busy lane",
            task_id="busy",
            model_constraints={
                "provider_capability_truth": [
                    {
                        "provider_id": "local-busy:qwen",
                        "role_fit": "drone",
                        "locality": "local",
                        "tool_support": ["structured_json", "code_complex"],
                        "structured_output_support": True,
                        "queue_depth": 3,
                        "max_safe_concurrency": 1,
                    }
                ]
            },
        )
        ready = build_task_envelope(
            role="coder",
            goal="Implement on ready lane",
            task_id="ready",
            model_constraints={
                "provider_capability_truth": [
                    {
                        "provider_id": "local-ready:qwen",
                        "role_fit": "drone",
                        "locality": "local",
                        "tool_support": ["structured_json", "code_complex"],
                        "structured_output_support": True,
                        "queue_depth": 0,
                        "max_safe_concurrency": 2,
                    }
                ]
            },
        )

        scheduled = schedule_task_envelopes([saturated, ready])

        self.assertEqual(scheduled[0].task_id, "ready")
        self.assertEqual(scheduled[1].availability_state, "degraded")
        self.assertGreater(scheduled[1].queue_pressure, 1.0)

    def test_capacity_evaluator_blocks_local_writer_without_local_lane(self) -> None:
        envelope = build_task_envelope(
            role="coder",
            goal="Patch on remote-only lane",
            task_id="remote-writer",
            model_constraints={
                "provider_capability_truth": [
                    {
                        "provider_id": "kimi:k2",
                        "role_fit": "queen",
                        "locality": "remote",
                        "tool_support": ["structured_json", "code_complex"],
                        "structured_output_support": True,
                        "queue_depth": 0,
                        "max_safe_concurrency": 4,
                    }
                ]
            },
        )

        capacity = evaluate_task_envelope_capacity(envelope)

        self.assertEqual(capacity.availability_state, "blocked")
        self.assertIn("requires_local_provider", capacity.notes)

    def test_build_task_envelope_for_request_attaches_reusable_procedure_citations(self) -> None:
        # PB01 amendment: only an evidence-carrying, promoted, live-expiry lesson is rankable, so
        # the fixture states that explicitly instead of relying on the retired promoted default.
        shard = ProcedureShardV1.create(
            task_class="debugging",
            title="Patch Python code and run tests",
            preconditions=["workspace is writable"],
            steps=["apply diff", "run tests"],
            tool_receipts=[],
            validation={"ok": True},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
            status=LearningPolicy.STATUS_PROMOTED,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )
        with mock.patch("core.task_router.load_procedure_shards", return_value=[shard]):
            envelope = build_task_envelope_for_request(
                "fix this traceback in my python repo",
                task_id="task-1",
            )

        self.assertEqual(envelope.role, "coder")
        self.assertEqual(envelope.inputs["task_class"], "debugging")
        self.assertIn(shard.procedure_id, envelope.inputs["reused_procedure_ids"])
        self.assertEqual(envelope.inputs["reused_procedures"][0]["reuse_count"], 0)
        self.assertEqual(envelope.inputs["reused_procedures"][0]["verified_reuse_count"], 0)

    def test_build_task_envelope_for_request_attaches_routing_constraints(self) -> None:
        with mock.patch("core.task_router.classify", return_value={"task_class": "research"}):
            envelope = build_task_envelope_for_request(
                "research the latest regional cluster approach for hive routing",
                context={"share_scope": "local_only"},
                task_id="task-routing-1",
            )

        self.assertEqual(envelope.role, "researcher")
        self.assertEqual(envelope.latency_budget, "deep")
        self.assertEqual(envelope.model_constraints["routing_task_kind"], "summarization")
        self.assertEqual(envelope.model_constraints["routing_output_mode"], "summary_block")
        self.assertTrue(envelope.model_constraints["allow_paid_fallback"])
        self.assertEqual(envelope.model_constraints["queue_pressure_strategy"], "degrade")
        self.assertIn("web_search", envelope.model_constraints["preferred_tool_support"])
        self.assertTrue(envelope.model_constraints["prefer_long_context"])

    def test_build_task_envelope_for_request_promotes_patch_and_validate_flow_to_queen(self) -> None:
        envelope = build_task_envelope_for_request(
            "replace `return 41` with `return 42` in app.py, then run `python3 -m pytest -q test_app.py`",
            context={"share_scope": "local_only"},
            task_id="task-routing-operator-1",
        )

        self.assertEqual(envelope.role, "queen")
        self.assertEqual(envelope.inputs["task_class"], "debugging")
        self.assertEqual(envelope.model_constraints["required_locality"], "")
        self.assertEqual(envelope.model_constraints["preferred_provider_role"], "queen")
        self.assertTrue(envelope.model_constraints["prefer_code_complex"])

    def test_provider_routing_plan_for_envelope_exposes_capability_truth(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "qwen2.5:14b",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone", "max_safe_concurrency": 2},
                "enabled": True,
            }
        )
        envelope = build_task_envelope(role="coder", goal="Patch tests", task_id="coder-1")

        plan = resolve_provider_routing_plan_for_envelope(
            registry,
            envelope=envelope,
            task_kind="action_plan",
            output_mode="action_plan",
        )

        self.assertEqual(plan.role, "drone")
        self.assertEqual(plan.task_envelope["task_id"], "coder-1")
        self.assertEqual(plan.capability_truth[0].role_fit, "drone")
        self.assertEqual(plan.capability_truth[0].max_safe_concurrency, 2)
        self.assertEqual(plan.routing_requirements["required_locality"], "local")
        self.assertIn("local provider", " ".join(plan.selection_notes).lower())

    def test_provider_routing_plan_for_local_only_coder_fails_closed_without_local_candidate(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "kimi-remote",
                "model_name": "kimi-k2",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "classify", "format", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "https://kimi.example"},
                "metadata": {"deployment_class": "cloud", "orchestration_role": "queen"},
                "enabled": True,
            }
        )
        envelope = build_task_envelope(role="coder", goal="Patch the repo", task_id="coder-remote-only")

        plan = resolve_provider_routing_plan_for_envelope(
            registry,
            envelope=envelope,
            task_kind="action_plan",
            output_mode="action_plan",
        )

        self.assertIsNone(plan.selected)
        self.assertEqual(plan.candidates, ())
        self.assertEqual(plan.routing_requirements["required_locality"], "local")
        self.assertEqual(plan.rejected_candidates[0]["reason"], "requires_local_provider")

    def test_provider_routing_plan_for_envelope_penalizes_saturated_local_lane(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "local-busy",
                "model_name": "qwen2.5:14b-busy",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {
                    "deployment_class": "local",
                    "orchestration_role": "drone",
                    "queue_depth": 3,
                    "max_safe_concurrency": 1,
                },
                "enabled": True,
            }
        )
        registry.register_manifest(
            {
                "provider_name": "local-ready",
                "model_name": "qwen2.5:14b-ready",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "http://127.0.0.1:11435"},
                "metadata": {
                    "deployment_class": "local",
                    "orchestration_role": "drone",
                    "queue_depth": 0,
                    "max_safe_concurrency": 2,
                },
                "enabled": True,
            }
        )
        envelope = build_task_envelope(role="coder", goal="Patch the repo", task_id="coder-ready-lane")

        plan = resolve_provider_routing_plan_for_envelope(
            registry,
            envelope=envelope,
            task_kind="action_plan",
            output_mode="action_plan",
        )

        assert plan.selected is not None
        self.assertEqual(plan.selected.provider_name, "local-ready")

    def test_teacher_pipeline_carries_task_envelope_into_provenance(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "kimi-remote",
                "model_name": "kimi-k2",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "structured_json", "long_context"],
                "runtime_config": {"base_url": "https://kimi.example"},
                "metadata": {"deployment_class": "cloud", "orchestration_role": "queen"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(registry)
        envelope = build_task_envelope(role="queen", goal="Merge worker outputs", task_id="queen-1")
        fake_response = mock.Mock(output_text="Merged answer", confidence=0.88)

        with mock.patch.object(registry, "build_adapter") as build_adapter:
            build_adapter.return_value.health_check.return_value = {"ok": True, "provider_id": "kimi-remote:kimi-k2"}
            build_adapter.return_value.invoke.return_value = fake_response
            build_adapter.return_value.get_license_metadata.return_value = {
                "provider_name": "kimi-remote",
                "model_name": "kimi-k2",
                "license_name": "Provider",
                "license_reference": "user-managed",
            }
            candidate = pipeline.run(
                task_kind="action_plan",
                prompt="merge worker outputs",
                output_mode="action_plan",
                task_envelope=envelope,
            )

        assert candidate is not None
        self.assertEqual(candidate.provider_role, "queen")
        self.assertEqual(candidate.provenance["task_envelope"]["task_id"], "queen-1")

    def test_teacher_pipeline_fails_closed_for_local_only_coder_without_local_provider(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "kimi-remote",
                "model_name": "kimi-k2",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "https://kimi.example"},
                "metadata": {"deployment_class": "cloud", "orchestration_role": "queen"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(registry)
        envelope = build_task_envelope(role="coder", goal="Patch local code", task_id="coder-local-only")

        candidate = pipeline.run(
            task_kind="action_plan",
            prompt="patch the local repo",
            output_mode="action_plan",
            task_envelope=envelope,
        )

        self.assertIsNone(candidate)

    def test_teacher_pipeline_skips_saturated_secondary_lane_during_execution(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "local-busy",
                "model_name": "qwen2.5:14b-busy",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {
                    "deployment_class": "local",
                    "orchestration_role": "drone",
                    "queue_depth": 4,
                    "max_safe_concurrency": 1,
                },
                "enabled": True,
            }
        )
        registry.register_manifest(
            {
                "provider_name": "local-ready",
                "model_name": "qwen2.5:14b-ready",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "http://127.0.0.1:11435"},
                "metadata": {
                    "deployment_class": "local",
                    "orchestration_role": "drone",
                    "queue_depth": 0,
                    "max_safe_concurrency": 2,
                },
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(registry)
        envelope = build_task_envelope(role="coder", goal="Patch local code", task_id="coder-backoff")
        invoked: list[str] = []

        def build_adapter(manifest):
            adapter = mock.Mock()
            invoked.append(manifest.provider_name)
            adapter.health_check.return_value = {"ok": True, "provider_id": manifest.provider_id}
            adapter.invoke.return_value = mock.Mock(
                output_text=f"{manifest.provider_name} says patch the code",
                confidence=0.75 if manifest.provider_name == "local-ready" else 0.52,
            )
            adapter.get_license_metadata.return_value = {
                "provider_name": manifest.provider_name,
                "model_name": manifest.model_name,
                "license_name": manifest.license_name,
                "license_reference": manifest.resolved_license_reference,
            }
            return adapter

        with mock.patch.object(registry, "build_adapter", side_effect=build_adapter):
            candidate = pipeline.run(
                task_kind="action_plan",
                prompt="patch the local repo",
                output_mode="action_plan",
                task_envelope=envelope,
            )

        assert candidate is not None
        self.assertEqual(candidate.provider_name, "local-ready")
        self.assertEqual(invoked, ["local-ready"])
        self.assertTrue(candidate.provenance["capacity_backoff_applied"])
        self.assertIn("skipped_saturated_candidates", candidate.provenance["capacity_backoff_notes"])

    def test_teacher_pipeline_fails_over_after_health_check_failure(self) -> None:
        _clear_manifests()
        registry = ModelRegistry()
        registry.register_manifest(
            {
                "provider_name": "local-dead",
                "model_name": "qwen2.5:14b-dead",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
                "enabled": True,
            }
        )
        registry.register_manifest(
            {
                "provider_name": "local-ready",
                "model_name": "qwen2.5:14b-ready",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "ollama",
                "capabilities": ["summarize", "structured_json", "code_complex"],
                "runtime_config": {"base_url": "http://127.0.0.1:11435"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(registry)

        def build_adapter(manifest):
            adapter = mock.Mock()
            if manifest.provider_name == "local-dead":
                adapter.health_check.return_value = {"ok": False, "error": "connection refused"}
            else:
                adapter.health_check.return_value = {"ok": True, "provider_id": manifest.provider_id}
                adapter.invoke.return_value = mock.Mock(output_text="ready lane answer", confidence=0.8)
                adapter.get_license_metadata.return_value = {
                    "provider_name": manifest.provider_name,
                    "model_name": manifest.model_name,
                    "license_name": manifest.license_name,
                    "license_reference": manifest.resolved_license_reference,
                }
            return adapter

        with mock.patch.object(registry, "build_adapter", side_effect=build_adapter):
            candidate = pipeline.run(
                task_kind="action_plan",
                prompt="patch the repo",
                output_mode="action_plan",
                provider_role="drone",
                swarm_size=2,
            )

        assert candidate is not None
        self.assertEqual(candidate.provider_name, "local-ready")
        self.assertIn("failed_attempts", candidate.provenance)
        self.assertEqual(candidate.provenance["failed_attempts"][0]["status"], "health_check_failed")
        self.assertEqual(get_provider_health("local-dead:qwen2.5:14b-dead").consecutive_failures, 1)
        self.assertEqual(get_provider_health("local-ready:qwen2.5:14b-ready").consecutive_failures, 0)


if __name__ == "__main__":
    unittest.main()
