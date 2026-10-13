"""Independent builder recovery: a later command's successful retry must not clear an EARLIER, different
command's unresolved failure; and the same command retried in a different order still needs its own pass."""
from types import SimpleNamespace

from core.agent_runtime.builder import controller
from core.execution.models import ToolIntentExecution, WorkflowPlannerDecision
from tests.test_agent_runtime_builder import _build_agent


def run(results):
    executed = []
    def plan(**kwargs):
        i = len(kwargs["executed_steps"])
        if i < len(results):
            return WorkflowPlannerDecision(True, "concrete_next_step", next_payload={"intent": results[i].tool_name, "arguments": {}})
        return WorkflowPlannerDecision(True, "done", stop_after=True)
    def execute(_p, **k):
        v = results[len(executed)]; executed.append(v); return v
    return controller.run_bounded_builder_loop(
        _build_agent(), task=SimpleNamespace(task_id="builder-recovery"), session_id="builder-recovery",
        effective_input="run both checks and repair", task_class="build", source_context={},
        initial_payloads=[{"intent": results[0].tool_name, "arguments": {}}],
        plan_tool_workflow_fn=plan, execute_tool_intent_fn=execute)

def cmd(command, code):
    ok = code == 0
    return ToolIntentExecution(True, ok, "executed" if ok else "command_failed", mode="tool_executed" if ok else "tool_failed",
                               tool_name="sandbox.run_command", details={"command": command, "cwd": ".", "returncode": code})

def test_earlier_distinct_failure_survives_later_retry_pass():
    steps, _c, stop, failed = run([cmd("python3 a.py", 1), cmd("python3 b.py", 1), cmd("python3 b.py", 0)])
    assert failed is not None, f"a.py never passed but failure was cleared; stop={stop}, steps={len(steps)}"

def test_same_command_fail_pass_fail_is_failure():
    steps, _c, stop, failed = run([cmd("python3 a.py", 1), cmd("python3 a.py", 0), cmd("python3 a.py", 1)])
    assert failed is not None, f"last run failed but reported clean; stop={stop}"


def test_all_distinct_commands_must_recover():
    _steps, _ctx, _stop, failed = run([cmd("python3 a.py", 1), cmd("python3 b.py", 1), cmd("python3 b.py", 0), cmd("python3 a.py", 0)])
    assert failed is None


def test_retry_in_different_cwd_does_not_recover_original():
    first = cmd("python3 a.py", 1)
    second = cmd("python3 a.py", 0)
    second.details["cwd"] = "other"
    _steps, _ctx, _stop, failed = run([first, second])
    assert failed is first


def test_passing_unrelated_command_cannot_recover_failure():
    first = cmd("python3 a.py", 1)
    _steps, _ctx, _stop, failed = run([first, cmd("python3 b.py", 0)])
    assert failed is first


def test_failure_without_cwd_cannot_be_cleared_by_a_guessed_retry():
    first = cmd("python3 a.py", 1)
    first.details.pop("cwd")
    steps, _ctx, _stop, failed = run([first, cmd("python3 a.py", 0)])
    assert failed is first
    assert len(steps) == 1
