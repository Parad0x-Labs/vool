"""PB01 — task-class model-sufficiency feedback (selector side).

The existing selector ranked on static capability/trust/lane-fit plus a transport-health circuit
breaker. Nothing learned whether a provider produces USABLE QUALITY for a task class. These tests
pin the bounded closure: fresh canonical observations change eligible rankings, and only under the
evaluated rules — never below the observation floor, never past the caps, never stale, never able
to rescue a hard exclusion, and never mistaking transport health or retrieval failure for learned
model quality.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.learning import (
    LearningPolicy,
    derive_sufficiency_observations,
    list_sufficiency_observations,
    record_sufficiency_observation,
    reset_sufficiency_observations,
    sufficiency_adjustment,
)
from core.learning.model_sufficiency import outcome_from_stage_state
from core.model_selection_policy import ModelSelectionRequest, rank_providers
from storage.model_provider_manifest import ModelProviderManifest


def _local(model: str, *, provider: str = "ollama-local") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/" + model,
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local", "orchestration_role": "queen", "tokens_per_second": 8.0},
        enabled=True,
    )


def _paid(model: str = "gpt-4o") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="openai",
        model_name=model,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="Proprietary",
        license_reference="https://openai.com/policies",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "https://api.openai.com/v1"},
        metadata={"deployment_class": "remote"},
    )


def _req(**kw) -> ModelSelectionRequest:
    kw.setdefault("task_kind", "summarize")
    kw.setdefault("output_mode", "plain_text")
    return ModelSelectionRequest(**kw)


class ModelSufficiencyP1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch(
            "core.learning.model_sufficiency.data_path",
            side_effect=lambda *parts: Path(self._tmp.name, *map(str, parts)),
        )
        self._patch.start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._patch.stop)

    def _observe(self, provider_id: str, outcome: str, *, task_kind: str = "summarize", age_days: float = 0.0, turn: str = "", model_id: str = "") -> None:
        record_sufficiency_observation(
            task_kind=task_kind,
            provider_id=provider_id,
            model_id=model_id,
            outcome=outcome,
            stage_state="success" if outcome == "verified_success" else "synthesis_empty",
            turn_key=turn or f"turn-{provider_id}-{outcome}-{age_days}-{len(list_sufficiency_observations())}",
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        )

    def test_below_the_observation_floor_adjusts_nothing(self) -> None:
        for _ in range(LearningPolicy.SUFFICIENCY_MIN_OBSERVATIONS - 1):
            self._observe("ollama-local", "quality_failure")
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local"), 0.0)

    def test_fresh_quality_failures_penalize_within_the_cap(self) -> None:
        for _ in range(4):
            self._observe("ollama-local", "quality_failure")
        adjustment = sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local")
        self.assertAlmostEqual(adjustment, -LearningPolicy.SUFFICIENCY_MAX_PENALTY, places=4)
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="other-provider"), 0.0)

    def test_verified_success_rewards_within_the_cap(self) -> None:
        for _ in range(4):
            self._observe("ollama-local", "verified_success")
        adjustment = sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local")
        self.assertAlmostEqual(adjustment, LearningPolicy.SUFFICIENCY_MAX_BONUS, places=4)

    def test_mixed_fresh_evidence_moves_the_adjustment_proportionally(self) -> None:
        for _ in range(3):
            self._observe("ollama-local", "verified_success")
        self._observe("ollama-local", "quality_failure")
        adjustment = sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local")
        self.assertAlmostEqual(adjustment, round(LearningPolicy.SUFFICIENCY_MAX_BONUS * 0.5, 4), places=4)

    def test_stale_observations_stop_counting(self) -> None:
        stale = LearningPolicy.SUFFICIENCY_FRESHNESS_DAYS + 1
        for _ in range(4):
            self._observe("ollama-local", "quality_failure", age_days=stale)
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local"), 0.0)

    def test_task_classes_are_isolated(self) -> None:
        for _ in range(4):
            self._observe("ollama-local", "quality_failure", task_kind="summarize")
        self.assertEqual(sufficiency_adjustment(task_kind="action_plan", provider_id="ollama-local"), 0.0)

    def test_fresh_feedback_changes_eligible_rankings_only(self) -> None:
        strong = _local("qwen-strong")
        weak = _local("qwen-weak")
        baseline = rank_providers([weak, strong], _req())
        self.assertEqual(baseline[0].model_name, "qwen-weak", "equal metadata: deterministic name tie-break")
        for _ in range(4):
            self._observe(str(weak.provider_id), "quality_failure", model_id=str(weak.model_name))
        after = rank_providers([weak, strong], _req())
        self.assertEqual(after[0].model_name, "qwen-strong", "fresh negative feedback must demote the failing provider")
        self.assertEqual({m.model_name for m in after}, {"qwen-weak", "qwen-strong"}, "still eligible, only reordered")

    def test_adjustment_never_rescues_a_hard_exclusion(self) -> None:
        for _ in range(4):
            self._observe("openai", "verified_success")
        ranked = rank_providers([_paid()], _req())
        self.assertEqual(ranked, [], "paid gating is a hard exclusion; no bonus can rescue it")
        ranked_local_only = rank_providers([_paid()], _req(local_only=True))
        self.assertEqual(ranked_local_only, [], "local-only lane binds regardless of learned quality")

    def test_transport_and_retrieval_states_are_not_learned_quality(self) -> None:
        self.assertEqual(outcome_from_stage_state("provider_error"), "")
        self.assertEqual(outcome_from_stage_state("retrieval_empty"), "")
        self.assertEqual(outcome_from_stage_state("retrieval_irrelevant"), "")
        self.assertEqual(outcome_from_stage_state("success"), "verified_success")
        self.assertEqual(outcome_from_stage_state("synthesis_empty"), "quality_failure")
        self.assertEqual(outcome_from_stage_state("validator_rejected"), "quality_failure")
        self.assertEqual(outcome_from_stage_state("response_extraction_failed"), "quality_failure")

    def test_operator_reset_clears_the_feedback(self) -> None:
        for _ in range(4):
            self._observe("ollama-local", "quality_failure")
        removed = reset_sufficiency_observations(provider_id="ollama-local", task_kind="summarize")
        self.assertEqual(removed, 4)
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local"), 0.0)

    def test_writer_refuses_noise_and_dedups_turns(self) -> None:
        self.assertIsNone(record_sufficiency_observation(task_kind="", provider_id="p", outcome="verified_success"))
        self.assertIsNone(record_sufficiency_observation(task_kind="summarize", provider_id="", outcome="verified_success"))
        self.assertIsNone(record_sufficiency_observation(task_kind="summarize", provider_id="p", outcome="amazing"))
        first = record_sufficiency_observation(
            task_kind="summarize", provider_id="p", outcome="verified_success", turn_key="turn-dup"
        )
        replay = record_sufficiency_observation(
            task_kind="summarize", provider_id="p", outcome="quality_failure", turn_key="turn-dup"
        )
        self.assertEqual(first["observation_id"], replay["observation_id"])
        self.assertEqual(len(list_sufficiency_observations()), 1)

    def test_derivation_joins_canonical_events_by_turn_key(self) -> None:
        def _event(event_type: str, turn_key: str, **details):
            return {"event_type": event_type, "turn_key": turn_key, "details": details}

        events = [
            _event("model.call_completed", "t1", provider_id="ollama-local", model_id="qwen3:8b"),
            _event("turn.trace_completed", "t1", stage_verdict={"state": "synthesis_empty"}),
            _event("model.call_completed", "t2", provider_id="ollama-local", model_id="qwen3:8b"),
            _event("turn.trace_completed", "t2", stage_verdict={"state": "success"}),
            _event("model.call_completed", "t3", provider_id="ollama-local", model_id="qwen3:8b"),
            _event("turn.trace_completed", "t3", stage_verdict={"state": "provider_error"}),
            _event("model.call_completed", "t4", provider_id="other", model_id="m"),
            # t4 has no trace; t5 has a trace but no model call -- both must vanish
            _event("turn.trace_completed", "t5", stage_verdict={"state": "success"}),
        ]
        derived = derive_sufficiency_observations(events, task_kind="summarize")
        self.assertEqual(
            [(item["turn_key"], item["outcome"]) for item in derived],
            [("t1", "quality_failure"), ("t2", "verified_success")],
        )

    def test_unknown_task_kind_derived_observations_never_rank(self) -> None:
        events = [
            {"event_type": "model.call_completed", "turn_key": "t1", "details": {"provider_id": "ollama-local", "model_id": "qwen3:8b"}},
            {"event_type": "turn.trace_completed", "turn_key": "t1", "details": {"stage_verdict": {"state": "success"}}},
        ]
        derived = derive_sufficiency_observations(events)  # default task_kind="unknown"
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0]["task_kind"], "unknown")
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local"), 0.0)


if __name__ == "__main__":
    unittest.main()
