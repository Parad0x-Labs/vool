"""A sticky Auto model is a preference; only an operator pin may fail a turn closed (MF-22).

Measured live 2026-08-15 (receipts, runs 2 and 3): the composer showed AUTO with autofallback set,
yet every turn carried requested_model='nemotron-3.5-lightning:free' — the chat page's per-chat
STICKY model — and when a catalog refresh renamed the manifest to the vendor-prefixed
'nvidia/nemotron-3.5-lightning:free', exact-match resolution failed and the pinned honest-refusal
path ate ~19 turns across two runs ("...isn't available on this runtime right now... switch it to
Auto" — while provider_role said auto). In the same second, OTHER turns completed on that very
model with runtime proof: the model was healthy; the resolution and the pin semantics were wrong.

Two rules land here:
- vendor-prefix tolerance: a UNIQUE basename match resolves ("nemotron-x:free" finds
  "nvidia/nemotron-x:free"); two candidates stay ambiguous and fail closed;
- selection kind: the client stamps model_selection=sticky|pin|auto; a STICKY id that cannot be
  resolved degrades to auto ranking with a receipt, while a real pin keeps the terminal
  MODEL_UNAVAILABLE contract.
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


def _manifest_payload(provider_name: str, model_name: str, *, role: str = "drone") -> dict:
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
        "metadata": {"orchestration_role": role},
    }


class StickyModelPreferenceTests(unittest.TestCase):
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

    # ------------------------------------------------------------------ resolver

    def test_a_vendor_prefixed_model_resolves_from_its_bare_name(self) -> None:
        manifest = self.registry.register_manifest(
            _manifest_payload("openrouter-byok", "nvidia/nemotron-x-lightning:free")
        )
        resolved = self.router._requested_model_manifest(
            {"requested_model": "nemotron-x-lightning:free"}
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.provider_id, manifest.provider_id)

    def test_an_ambiguous_bare_suffix_still_fails_closed(self) -> None:
        self.registry.register_manifest(_manifest_payload("vendor-a", "vendor-a/twin:free"))
        self.registry.register_manifest(_manifest_payload("vendor-b", "vendor-b/twin:free"))
        self.assertIsNone(self.router._requested_model_manifest({"requested_model": "twin:free"}))

    def test_an_exact_model_name_match_still_wins(self) -> None:
        exact = self.registry.register_manifest(_manifest_payload("plain", "bare-model:free"))
        self.registry.register_manifest(_manifest_payload("prefixed", "vendor/bare-model:free"))
        resolved = self.router._requested_model_manifest({"requested_model": "bare-model:free"})
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.provider_id, exact.provider_id)

    # ------------------------------------------------------------------ router semantics

    def _resolve_with(self, source_context: dict):
        local = self.registry.register_manifest(_manifest_payload("local-qwen-http", "qwen-local"))
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(
                mock.Mock(get_license_metadata=mock.Mock(return_value={})),
                ModelResponse(
                    output_text="an auto-routed answer",
                    confidence=0.8,
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                ),
                None,
            ),
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=self._context_result(),
                persona=self.persona,
                source_context=source_context,
            )
        return result, rank_candidates

    def test_a_sticky_unresolvable_model_degrades_to_auto_routing(self) -> None:
        result, rank_candidates = self._resolve_with(
            {
                "surface": "api",
                "session_id": "router-test-session",
                "turn_id": "router-test-turn-sticky",
                "requested_model": "gone-from-catalog:free",
                "model_selection": "sticky",
            }
        )
        self.assertNotEqual(result.source, "model_unavailable")
        rank_candidates.assert_called()

    def test_a_pinned_unresolvable_model_still_fails_closed(self) -> None:
        # Negative control: the terminal contract for real pins is untouched, with the kind
        # explicit AND with it absent (older clients).
        for selection in ("pin", None):
            context = {
                "surface": "api",
                "session_id": "router-test-session",
                "turn_id": "router-test-turn-pin",
                "requested_model": "gone-from-catalog:free",
            }
            if selection:
                context["model_selection"] = selection
            result, rank_candidates = self._resolve_with(context)
            self.assertEqual(result.source, "model_unavailable", selection)
            rank_candidates.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def test_routing_events_and_rules_use_the_configured_runtime_home(tmp_path, monkeypatch):
    from core import runtime_paths, turn_routing
    monkeypatch.delenv("VOOL_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "runtime"
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    runtime_paths.configure_runtime_home(home)
    try:
        turn_routing._append_event({"event": "home-isolation-review"})
        assert (home / "data" / "turn_routing_events.jsonl").is_file()
        assert turn_routing._rules_path() == home / "data" / "turn_routing_rules.json"
        assert not (tmp_path / "data").exists()
    finally:
        runtime_paths.configure_runtime_home(previous)
