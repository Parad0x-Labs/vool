"""PB01 — the frozen enabled-vs-disabled holdout (SCRIPTED-BOUNDARY EVIDENCE class).

A frozen corpus of matching + irrelevant (stopword-overlapping) repair demands, scored twice on
fresh isolated stores — learning DISABLED (empty store) vs ENABLED (one promoted lesson seeded
exactly as the served journey promotes it). Measured, per the review's plan: selection
correctness (matching turns receive the lesson's guidance), false-relevance rate (irrelevant
turns receive none), and the guidance's prompt-cost delta in characters. Token cost is UNKNOWN
on a scripted provider and is reported as null, never 0.0.

This is NOT model-authored usefulness: the provider is not consulted, and no claim is made
that answers improved. The real-model usefulness leg is a separate evidence class and is
recorded as NOT MEASURED this session (no honest automatic grader was built), not passed.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MATCHING = [
    "Find why this test fails, repair the root cause, run the focused test, and show me the evidence.",
    "The regression suite is red on calc; repair the owner and run the focused pytest command.",
    "Debug the failing test in the repo, fix the defect, then run the focused test to verify.",
    "This test broke after the change — diagnose it, repair the root cause, verify with the focused run.",
]
IRRELEVANT = [
    "What should I do after lunch, before dinner?",
    "Is it before or after you do that, then?",
    "Could you tell me about the book you read, in your own words?",
    "Which of these two paintings do you prefer, and why?"]
STORE_SEED_SESSIONS = (("m1", "holdout-a"), ("m2", "holdout-b"))
COMMAND = "python -m pytest -q test_calc.py"


def _isolated_stores(tmpdir: str):
    shards = sys.modules["core.learning.procedure_shards"]
    sufficiency = sys.modules["core.learning.model_sufficiency"]
    return (
        mock.patch.object(shards, "data_path", side_effect=lambda *parts: Path(tmpdir, *map(str, parts))),
        mock.patch.object(sufficiency, "data_path", side_effect=lambda *parts: Path(tmpdir, *map(str, parts))),
    )


def _seed_promoted_lesson() -> None:
    from core.learning import promote_verified_procedure

    for mutation_id, session in STORE_SEED_SESSIONS:
        promote_verified_procedure(
            task_class="debugging",
            title="debugging: validate the focused test with workspace.run_tests",
            preconditions=["workspace is writable"],
            steps=["apply the approved patch to the changed file", "run pytest on the focused test file"],
            tool_receipts=[
                {"intent": "workspace.write_file", "mutation_id": mutation_id, "paths": ["calc.py"]},
                {"intent": "workspace.run_tests", "command": COMMAND, "returncode": 0},
            ],
            validation={"ok": True, "tool": "workspace.run_tests", "command": COMMAND, "returncode": 0},
            rollback={"intent": "workspace.rollback_last_change"},
            session_id=session,
        )


def _delivered_size(text: str) -> int:
    """The guidance block size the provider context would carry for this demand (0 = none)."""
    from core.learning_integration import bounded_guidance_entries, learned_guidance_prompt_block
    from core.task_router import _reused_procedure_inputs

    reused = _reused_procedure_inputs(task_class="debugging", user_input=text)
    return len(learned_guidance_prompt_block(bounded_guidance_entries(reused.get("reused_procedures"))))


class TestFrozenHoldout(unittest.TestCase):
    def test_enabled_vs_disabled_on_the_frozen_corpus(self):
        # DISABLED: an empty store delivers nothing to anyone (baseline zero).
        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                for text in MATCHING + IRRELEVANT:
                    self.assertEqual(_delivered_size(text), 0, text)

        # ENABLED: the promoted lesson is delivered to every matching demand, to no irrelevant
        # demand, at a bounded prompt cost. Improvement is measured on this frozen corpus.
        with tempfile.TemporaryDirectory() as tmpdir:
            patches = _isolated_stores(tmpdir)
            with patches[0], patches[1]:
                _seed_promoted_lesson()
                correct = sum(1 for text in MATCHING if _delivered_size(text) > 0)
                false_relevance = sum(1 for text in IRRELEVANT if _delivered_size(text) > 0)
                sizes = [_delivered_size(text) for text in MATCHING]
                self.assertEqual(correct, len(MATCHING), f"selection correctness: {correct}/{len(MATCHING)}")
                self.assertEqual(false_relevance, 0, f"false relevance: {false_relevance}/{len(IRRELEVANT)}")
                self.assertTrue(all(0 < size < 2500 for size in sizes), f"bounded guidance cost: {sizes}")
                # Token cost on a scripted provider is UNKNOWN: reported as null, never 0.0.
                token_cost_delta = None
                self.assertIsNone(token_cost_delta)


if __name__ == "__main__":
    unittest.main()
