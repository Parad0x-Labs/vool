"""Plan step 3 (honesty half) — never claim work is "in progress" when nothing is running.

The reported failure: VOOL repeatedly answered "audit in progress" / "one moment" after the task had
already completed with no active task. Runtime state, not model prose, decides whether something runs.
"""
from __future__ import annotations

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

_NO_RECEIPT_SESSION = "test-continuation-no-receipt-zzz"


def test_short_fabricated_in_progress_is_replaced_with_the_truth():
    out = enforce_final_action_honesty(
        {"response": "Audit in progress — one moment.", "confidence": 0.9},
        user_input="audit the repo",
        session_id=_NO_RECEIPT_SESSION,
        source_context={},
    )
    assert "nothing is running" in out["response"].lower()
    assert out["action_honesty_validator"]["reason"] == "fabricated_in_progress_no_active_task"


def test_working_on_it_with_no_task_is_replaced():
    out = enforce_final_action_honesty(
        {"response": "Working on it, still analysing.", "confidence": 0.9},
        user_input="how's the audit going",
        session_id=_NO_RECEIPT_SESSION,
        source_context={},
    )
    assert "nothing is running" in out["response"].lower()


def test_long_substantive_answer_with_progress_phrase_is_kept():
    # The guard only nukes SHORT fabrications; a long substantive answer is preserved even if it
    # happens to contain "in progress".
    long_answer = "in progress. " + ("Here are the real findings from reading the files. " * 8)
    assert len(long_answer) >= 220
    out = enforce_final_action_honesty(
        {"response": long_answer},
        user_input="x",
        session_id=_NO_RECEIPT_SESSION,
        source_context={},
    )
    assert out["response"] == long_answer


def test_ordinary_answer_is_untouched():
    out = enforce_final_action_honesty(
        {"response": "The capital of France is Paris."},
        user_input="capital of france",
        session_id=_NO_RECEIPT_SESSION,
        source_context={},
    )
    assert out["response"] == "The capital of France is Paris."
