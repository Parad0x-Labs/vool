"""A normal provider reply cannot turn an unfinished coding task into completed work."""
from copy import deepcopy

import pytest


def _step(task_id, verdict="unresolved", *, intent="code.task.step", stage="reproduce"):
    return {"tool_name": intent, "arguments": {"task_id": task_id},
            "details": {"task_id": task_id, "code_task_verdict": verdict, "stage": stage}}


@pytest.mark.parametrize("label", ["I will read the files now.", "All fixed and tested."])
def test_prose_cannot_close_an_unresolved_task(label):
    from core.code_assistant.task_runtime import enforce_code_task_completion
    from core.runtime_task_outcome import terminal_fulfillment_outcome

    result = {"response": label, "success": True, "task_outcome": "success", "mode": "tool_executed"}
    closed = enforce_code_task_completion(result, [_step("ct-pending")])
    assert not closed["success"]
    assert closed["status"] == "code_task_incomplete"
    assert "reproduce" in closed["response"]
    assert terminal_fulfillment_outcome(closed).is_unfulfilled


def test_latest_completed_receipt_allows_closure_after_earlier_failure():
    from core.code_assistant.task_runtime import enforce_code_task_completion

    result = {"response": "Fixed and tested.", "success": True}
    assert enforce_code_task_completion(result, [
        _step("ct-one"), _step("ct-one", "completed", stage="report"),
    ]) == result


def test_one_completed_task_cannot_hide_another_unresolved_task():
    from core.code_assistant.task_runtime import enforce_code_task_completion

    closed = enforce_code_task_completion({"response": "Done.", "success": True}, [
        _step("ct-one", "completed", stage="report"), _step("ct-two", stage="narrow_test"),
    ])
    assert not closed["success"]
    assert "ct-two" in closed["response"]


def test_report_only_and_unrelated_turns_remain_answers():
    from core.code_assistant.task_runtime import enforce_code_task_completion

    result = {"response": "The task has not finished.", "success": True}
    assert enforce_code_task_completion(result, [_step("ct-old", intent="code.task.report")]) == result
    assert enforce_code_task_completion(result, [{"tool_name": "workspace.read_file"}]) == result


def test_source_result_and_receipts_are_not_modified():
    from core.code_assistant.task_runtime import enforce_code_task_completion

    result = {"response": "Starting.", "success": True}
    steps = [_step("ct-one")]
    original = deepcopy((result, steps))
    enforce_code_task_completion(result, steps)
    assert (result, steps) == original


def test_real_tool_loop_does_not_stamp_open_task_as_success():
    from core.runtime_task_outcome import terminal_fulfillment_outcome
    from tests.test_a_narrated_batch_does_not_vanish import _call, _drive

    result, _ = _drive([_call("code.task.open", objective="Repair the failing inventory tests")])
    assert result.get("success") is False
    assert result.get("status") == "code_task_incomplete"
    assert terminal_fulfillment_outcome(result).is_unfulfilled


def test_repeated_tool_synthesis_cannot_close_an_open_task(monkeypatch):
    from core.runtime_task_outcome import terminal_fulfillment_outcome
    from tests.test_a_narrated_batch_does_not_vanish import _call, _drive, _Router

    original = _Router.resolve_tool_intent

    def repeat_first(self, **kwargs):
        self.calls = 0
        return original(self, **kwargs)

    monkeypatch.setattr(_Router, "resolve_tool_intent", repeat_first)
    result, _ = _drive([_call("code.task.open", objective="Repair the failing stock checks")])
    assert result.get("success") is False
    assert result.get("status") == "code_task_incomplete"
    assert terminal_fulfillment_outcome(result).is_unfulfilled
