"""PB01 integration hooks — the production seams the frozen learning lane reserved.

Hook 1: validated steps/preconditions reach the provider system prompt (bounded, provenance-
    marked), projected by the router's own reused-procedure inputs.
Hook 2: terminal reuse feedback records failed/cancelled/unvalidated/successful — the
    success-only gap is closed; transport failures record nothing.
Hook 3: the model-sufficiency writer joins the turn's OWN session events (provider/model
    identity + routing task_kind x terminal stage verdict), model-isolated, turn-key deduped.
Hook 4: operator-facing id gate — the store's signature-hash shape is the only accepted id;
    traversal-shaped and invented ids never reach the filesystem.

Evidence-store isolation: every test patches the learning stores' ``data_path`` to a temp dir,
exactly like the lane's own packs; nothing touches a live VOOL_HOME.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _isolated_stores(tmpdir: str):
    """Both learning stores re-pointed at a temp dir (data_path joins parts, like the lane's own
    packs patch it — a constant return_value would name the directory itself)."""
    shards = sys.modules["core.learning.procedure_shards"]
    sufficiency = sys.modules["core.learning.model_sufficiency"]
    return (
        mock.patch.object(shards, "data_path", side_effect=lambda *parts: Path(tmpdir, *map(str, parts))),
        mock.patch.object(sufficiency, "data_path", side_effect=lambda *parts: Path(tmpdir, *map(str, parts))),
    )


def _witness(mutation_id: str, *, session: str, task_class: str = "debugging"):
    from core.learning import promote_verified_procedure

    evidence = {
        "tool_receipts": [
            {"intent": "workspace.apply_unified_diff", "mutation_id": mutation_id, "paths": ["app.py"]},
            {"intent": "workspace.run_tests", "command": "python3 -m pytest -q test_app.py", "returncode": 0},
        ],
        "validation": {"ok": True, "tool": "workspace.run_tests", "command": "python3 -m pytest -q test_app.py", "returncode": 0},
    }
    return promote_verified_procedure(
        task_class=task_class,
        title=f"Patch and verify ({mutation_id})",
        preconditions=["workspace is writable"],
        steps=["apply the diff to app.py", "run pytest on app tests"],
        tool_receipts=evidence["tool_receipts"],
        validation=evidence["validation"],
        rollback={"intent": "workspace.rollback_last_change"},
        session_id=session,
    )


class TestHook1GuidanceDelivery(unittest.TestCase):
    def test_learned_guidance_reaches_the_provider_system_prompt(self):
        """THE named boundary test: a promoted, matching lesson's steps/preconditions ride the
        provider system prompt, provenance-marked. Remove the delivery seam and this fails —
        ids in an envelope are not consumption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                _witness("mutation-hook1a", session="session-a")
                shard = _witness("mutation-hook1b", session="session-b")
                from core.learning_integration import (
                    bounded_guidance_entries,
                    learned_guidance_prompt_block,
                )
                from core.task_router import _reused_procedure_inputs

                reused = _reused_procedure_inputs(task_class="debugging", user_input="patch app.py and run pytest")
                assert reused, "the promoted shard must rank for a matching task"
                entry = next(item for item in reused["reused_procedures"] if item["procedure_id"] == shard.procedure_id)
                self.assertIn("steps", entry)  # the router projects guidance, not just counters
                block = learned_guidance_prompt_block(bounded_guidance_entries(reused["reused_procedures"]))
                self.assertIn("Learned guidance", block)
                self.assertIn(f"procedure_id: {shard.procedure_id}", block)
                self.assertIn("apply the diff to app.py", block)
                self.assertIn("learned_guidance: true", block)

                # The PROVIDER boundary: the real request builder appends the block to the
                # system prompt when the turn's envelope carries the ranked procedures.
                from core.memory_first_router import MemoryFirstRouter

                router = MemoryFirstRouter.__new__(MemoryFirstRouter)
                source_context = {
                    "task_envelope": {"inputs": dict(reused)},
                    "runtime_session_id": "hook1-sess",
                }
                request = router._build_request(
                    task=SimpleNamespace(task_id="t1", task_summary="patch app.py and run pytest"),
                    classification={"task_class": "debugging"},
                    interpretation=SimpleNamespace(
                        raw_text="patch app.py and run pytest",
                        normalized_text="patch app.py and run pytest",
                        as_context=lambda: {},
                    ),
                    context_result=SimpleNamespace(
                        local_candidates=[],
                        swarm_metadata=[],
                        retrieval_confidence_score=0.0,
                        report=SimpleNamespace(to_dict=lambda: {}),
                    ),
                    persona=SimpleNamespace(),
                    output_mode="plain_text",
                    task_kind="code_edit",
                    surface="api",
                    source_context=source_context,
                )
                self.assertIn("Learned guidance", request.system_prompt)
                self.assertIn(f"procedure_id: {shard.procedure_id}", request.system_prompt)
                # guidance is DATA: it must never enter constraints or permissions
                self.assertNotIn("Learned guidance", str(getattr(request, "contract", {}) or {}))

    def test_guidance_is_bounded(self):
        from core.learning_integration import GUIDANCE_MAX_STEPS, bounded_guidance_entries

        huge = {
            "reused_procedures": [
                {
                    "procedure_id": f"procedure-{'a' * 32}",
                    "title": "t" * 500,
                    "steps": [f"step {i} " + "x" * 500 for i in range(50)],
                    "preconditions": [f"pre {i} " + "y" * 500 for i in range(50)],
                }
            ]
        }
        entries = bounded_guidance_entries(huge["reused_procedures"])
        self.assertEqual(len(entries), 1)
        self.assertLessEqual(len(entries[0]["steps"]), GUIDANCE_MAX_STEPS)
        self.assertTrue(all(len(step) <= 160 for step in entries[0]["steps"]))


class TestHook2TerminalReuseFeedback(unittest.TestCase):
    def _envelope_result(self, *, ok: bool, status: str, output_text: str = "", receipts=()):
        from core.orchestration.executor import _attach_reused_procedure_metrics
        from core.orchestration.task_envelope import build_task_envelope

        envelope = build_task_envelope(
            role="coder",
            goal="reuse test",
            inputs={
                "task_class": "debugging",
                "runtime_tools": [],
                "reused_procedure_ids": [self.procedure_id],
            },
        )
        from core.orchestration.executor import EnvelopeExecutionResult

        result = EnvelopeExecutionResult(
            envelope=envelope,
            ok=ok,
            status=status,
            output_text=output_text,
            receipts=receipts,
        )
        return _attach_reused_procedure_metrics(result)

    def test_failed_reuse_records_failed_and_demotes_after_two(self):
        from core.learning import LearningPolicy, load_procedure_shards

        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                _witness("mutation-hook2a", session="session-a")
                shard = _witness("mutation-hook2b", session="session-b")
                self.procedure_id = shard.procedure_id
                validation_receipt = ({"intent": "workspace.run_tests", "returncode": 1, "ok": False},)
                first = self._envelope_result(ok=False, status="validation_failed", output_text="tests failed", receipts=validation_receipt)
                self.assertFalse(first.ok)
                stored = {s.procedure_id: s for s in load_procedure_shards()}[shard.procedure_id]
                self.assertEqual(stored.last_terminal_outcome, "failed")
                self.assertEqual(stored.failure_count, 1)
                self.assertEqual(stored.status, LearningPolicy.STATUS_PROMOTED, "one failure does not demote")
                self._envelope_result(ok=False, status="validation_failed", output_text="tests failed again", receipts=validation_receipt)
                stored = {s.procedure_id: s for s in load_procedure_shards()}[shard.procedure_id]
                self.assertEqual(stored.status, LearningPolicy.STATUS_DEMOTED, "two consecutive verified failures demote")

    def test_transport_failure_is_not_lesson_evidence(self):
        from core.learning import load_procedure_shards

        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                _witness("mutation-hook2ba", session="session-a")
                shard = _witness("mutation-hook2bb", session="session-b")
                self.procedure_id = shard.procedure_id
                self._envelope_result(ok=False, status="provider_error_timeout", output_text="transport died")
                stored = {s.procedure_id: s for s in load_procedure_shards()}[shard.procedure_id]
                self.assertEqual(stored.failure_count, 0, "transport errors teach the lesson nothing")
                self.assertEqual(stored.reuse_count, 0)

    def test_cancelled_records_but_counts_neither_way(self):
        from core.learning import load_procedure_shards

        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                _witness("mutation-hook2ca", session="session-a")
                shard = _witness("mutation-hook2cb", session="session-b")
                self.procedure_id = shard.procedure_id
                self._envelope_result(ok=False, status="cancelled_by_operator")
                stored = {s.procedure_id: s for s in load_procedure_shards()}[shard.procedure_id]
                self.assertEqual(stored.last_terminal_outcome, "cancelled")
                self.assertEqual(stored.reuse_count, 0)
                self.assertEqual(stored.failure_count, 0)


class TestHook3SufficiencyWriter(unittest.TestCase):
    def test_writer_joins_real_event_identity_once_per_turn(self):
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        events = [
            {
                "event_type": "model.call_completed",
                "turn_key": "turn-hook3",
                "session_id": "sess-hook3",
                "details": {"provider_id": "ollama-local", "model_id": "qwen3:8b", "task_kind": "code_edit"},
            },
            {
                "event_type": "turn.trace_completed",
                "turn_key": "turn-hook3",
                "details": {"stage_verdict": {"state": "success"}},
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                # C01 closure: the join is scoped to the NAMED turn — an unnamed join would
                # borrow every earlier turn's events in the session window.
                written = record_turn_sufficiency_from_events(
                    events, session_id="sess-hook3", turn_key="turn-hook3"
                )
                self.assertEqual(written, 1)
                observations = list_sufficiency_observations(provider_id="ollama-local")
                self.assertEqual(len(observations), 1)
                self.assertEqual(observations[0]["model_id"], "qwen3:8b")
                self.assertEqual(observations[0]["task_kind"], "code_edit")
                self.assertEqual(observations[0]["outcome"], "verified_success")
                self.assertEqual(observations[0]["turn_key"], "turn-hook3")
                # turn_key idempotency: replaying the same events writes nothing new
                record_turn_sufficiency_from_events(
                    events, session_id="sess-hook3", turn_key="turn-hook3"
                )
                self.assertEqual(len(list_sufficiency_observations(provider_id="ollama-local")), 1)

    def test_transport_stage_states_write_nothing(self):
        from core.learning import list_sufficiency_observations
        from core.learning_integration import record_turn_sufficiency_from_events

        events = [
            {"event_type": "model.call_completed", "turn_key": "t-x", "details": {"provider_id": "p1", "model_id": "m1", "task_kind": "chat"}},
            {"event_type": "turn.trace_completed", "turn_key": "t-x", "details": {"stage_verdict": {"state": "provider_error"}}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                self.assertEqual(record_turn_sufficiency_from_events(events, turn_key="t-x"), 0)
                self.assertEqual(list_sufficiency_observations(), [])


class TestHook4IdGate(unittest.TestCase):
    def test_only_signature_hash_ids_pass_the_gate(self):
        from core.learning_integration import valid_procedure_id

        self.assertTrue(valid_procedure_id(f"procedure-{'0' * 32}"))
        self.assertFalse(valid_procedure_id(""))
        self.assertFalse(valid_procedure_id("procedure-xyz"))
        self.assertFalse(valid_procedure_id("../../etc/passwd"))
        self.assertFalse(valid_procedure_id("procedure-../../secrets"))
        self.assertFalse(valid_procedure_id(f"procedure-{'g' * 32}"))
        self.assertFalse(valid_procedure_id(f"procedure-{'a' * 31}"))


if __name__ == "__main__":
    unittest.main()
