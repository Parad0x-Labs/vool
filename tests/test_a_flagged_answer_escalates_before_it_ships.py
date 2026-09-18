"""A verifier-flagged answer buys one escalation; it never ships silently (MF-19).

Measured live 2026-08-15 (color-cascade turn, receipts 11:52:48, session dbac716c...):

    MODEL_LANE_VERIFIER_FLAGGED  Verifier lane flagged the primary answer (qwen3:4b)
    DONE  Completed task with final response: Yellow

same second, correct answer Purple. The gate set needs_review and capped trust, but the answer
shipped — and on exact-shape turns even the draft caveat is suppressed into response_control
metadata the page never renders, so the user saw a bare wrong answer with no signal anywhere.

The rule: a flag buys ONE escalation to the next ranked candidate, which gets its own verifier
pass. A clean escalated answer ships clean; if every candidate is flagged, the first flagged
decision ships WITH its caveat and trust cap — degrade honestly, never silently.
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


def _manifest_payload(provider_name: str, model_name: str) -> dict:
    return {
        "provider_name": provider_name,
        "model_name": model_name,
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


class FlaggedAnswerEscalationTests(unittest.TestCase):
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
        self.first = self.registry.register_manifest(_manifest_payload("first-model-http", "first-small"))
        self.second = self.registry.register_manifest(_manifest_payload("second-model-http", "second-bigger"))

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

    def _resolve(self, verdict_by_provider: dict):
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())

        def _invoke(manifest=None, request=None, **_kw):
            name = str(getattr(manifest, "provider_name", ""))
            return (
                mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                ModelResponse(
                    output_text=f"answer from {name}",
                    confidence=0.8,
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                ),
                None,
            )

        def _verify(primary_manifest=None, **_kw):
            return verdict_by_provider.get(
                str(getattr(primary_manifest, "provider_name", "")), "independent_completed"
            )

        # The autopilot reorders candidates to its own pick; pin it to the FIRST manifest so the
        # escalation order under test is deterministic (measured: without this the loop started at
        # "second-bigger" and the escalation branch never executed while two of three assertions
        # still passed coincidentally).
        import core.memory_first_router as router_module

        real_plan = router_module.build_local_inference_autopilot_plan

        def _pinned_plan(*args, **kwargs):
            plan = real_plan(*args, **kwargs)
            try:
                plan.selected_provider_id = self.first.provider_id
            except Exception:
                import dataclasses

                plan = dataclasses.replace(plan, selected_provider_id=self.first.provider_id)
            return plan

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[self.first, self.second],
        ), mock.patch.object(
            router_module, "build_local_inference_autopilot_plan", side_effect=_pinned_plan
        ), mock.patch.object(self.router, "_invoke_manifest", side_effect=_invoke), mock.patch.object(
            self.router, "_verify_primary_response", side_effect=_verify
        ) as verify:
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context={"surface": "api", "session_id": "router-test-session",
                            "turn_id": "router-test-turn"},
            )
        return result, verify

    def test_a_flagged_first_answer_escalates_and_the_clean_second_ships(self) -> None:
        result, verify = self._resolve(
            {"first-model-http": "flagged", "second-model-http": "independent_completed"}
        )
        self.assertIn("second-model-http", str(result.output_text or ""))
        self.assertFalse((result.details or {}).get("needs_review", False))
        self.assertGreaterEqual(verify.call_count, 2)

    def test_all_candidates_flagged_ships_the_first_with_its_caveat_state(self) -> None:
        result, _ = self._resolve(
            {"first-model-http": "flagged", "second-model-http": "flagged"}
        )
        # Honest degrade: an answer still ships, and it carries the review flag — never silent.
        self.assertTrue((result.details or {}).get("needs_review", False))
        self.assertEqual((result.details or {}).get("verifier_gate"), "flagged")

    def test_a_clean_first_answer_never_escalates(self) -> None:
        # Negative control: no flag, no second call, first answer ships untouched.
        result, verify = self._resolve({"first-model-http": "independent_completed"})
        self.assertIn("first-model-http", str(result.output_text or ""))
        self.assertFalse((result.details or {}).get("needs_review", False))
        self.assertEqual(verify.call_count, 1)

    def test_the_verifier_sees_the_users_question(self) -> None:
        """A verdict must be grounded in the request. Before this fix the verifier prompt carried
        only the response plus routing labels — PASS/FAIL on correctness was structurally blind
        (audit finding, 2026-08-15): it could not know "Yellow" was wrong for a puzzle it never
        saw, and the escalation above would spend its extra call on that blind verdict."""
        from adapters.base_adapter import ModelRequest

        task = create_task_record("what colour is Delta after the swaps in my puzzle")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        captured: list = []

        def _invoke(manifest=None, request=None, **_kw):
            captured.append(request)
            return (
                mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                ModelResponse(output_text="VERDICT: PASS ok", confidence=0.9, usage={}),
                None,
            )

        with mock.patch.object(self.router, "_invoke_manifest", side_effect=_invoke):
            status = self.router._verify_primary_response(
                primary_manifest=self.first,
                primary_request=ModelRequest(task_kind="chat", prompt="ignored", context={}),
                primary_response=ModelResponse(output_text="Yellow", confidence=0.8, usage={}),
                ranked_manifests=[self.first, self.second],
                autopilot_plan={
                    "verifier_required": True,
                    "verifier_provider_id": self.second.provider_id,
                    "selected_provider_id": self.first.provider_id,
                },
                task=task,
                classification=classification,
                task_kind="chat",
                output_mode="plain_text",
                source_context={"surface": "api", "session_id": "router-test-session",
                            "turn_id": "router-test-turn"},
                failed_provider_ids=set(),
            )

        self.assertEqual(status, "independent_completed")
        self.assertEqual(len(captured), 1)
        prompt = str(getattr(captured[0], "prompt", ""))
        self.assertIn("what colour is Delta after the swaps in my puzzle", prompt)
        self.assertIn("Yellow", prompt)


if __name__ == "__main__":
    unittest.main()
