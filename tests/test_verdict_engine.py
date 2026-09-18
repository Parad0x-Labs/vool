from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core.verdict_engine import evaluate_consensus


def _result(result_id: str, summary: str, confidence: float, evidence: list[str]) -> dict:
    return {
        "result_id": f"result-{result_id}-long",
        "task_id": "task-0001",
        "helper_agent_id": f"peer-{result_id}-000000000",
        "result_type": "validation",
        "summary": summary,
        "confidence": confidence,
        "evidence": evidence,
        "abstract_steps": ["check_constraints", "return_summary"],
        "risk_flags": [],
        "result_hash": (result_id * 16)[:16],
        "timestamp": datetime.now(timezone.utc),
    }


class VerdictEngineTests(unittest.TestCase):
    def test_conflicting_results_become_disputed(self) -> None:
        verdict = evaluate_consensus(
            [
                _result("a", "The safe answer is to keep the feature disabled.", 0.82, ["disable feature", "avoid risk"]),
                _result("b", "The safe answer is to immediately enable the feature globally.", 0.79, ["enable feature", "roll out"]),
            ]
        )
        self.assertEqual(verdict.verdict, "disputed")


if __name__ == "__main__":
    unittest.main()
