"""Opening a coding task followed by narration must get bounded continuation feedback."""

import pytest

from tests.test_a_narrated_batch_does_not_vanish import _call, _drive, _Router


def test_early_narration_gets_a_real_following_action(monkeypatch):
    original = _Router.resolve_tool_intent
    saw_feedback = []

    def respond(self, **kwargs):
        feedback = kwargs.get("source_context", {}).get("code_task_completion_feedback")
        if feedback and not saw_feedback:
            saw_feedback.append(feedback)
            self.first_batch = [_call("workspace.read_file", path="README.md")]
            # Reuse the provider fixture's real native-call construction at its seam.
            count = self.calls
            self.calls = 0
            result = original(self, **kwargs)
            self.calls = count + 1
            return result
        return original(self, **kwargs)

    monkeypatch.setattr(_Router, "resolve_tool_intent", respond)
    result, router = _drive([_call("code.task.open", objective="Repair the failing inventory checks")])
    assert saw_feedback, "The first early closing reply must be corrected, not returned as a dead end."
    assert "workspace.read_file" in result["details"]["tool_steps"]
    assert router.calls <= 6
    assert not result["success"], "One read still does not finish the coding task."


def test_uncooperative_model_has_a_strict_continuation_limit():
    result, router = _drive([_call("code.task.open", objective="Repair the failing stock checks")])
    assert router.calls == 4  # open, two rejected early endings, final refusal
    assert not result["success"]


def test_feedback_reaches_the_actual_prompt_block():
    from core.prompt_normalizer import _runtime_tool_observation_message

    text = str(
        _runtime_tool_observation_message(
            {
                "runtime_tool_observations": [{"intent": "code.task.open", "ok": True, "task_id": "ct-proof"}],
                "code_task_completion_feedback": [{"task_id": "ct-proof", "stage": "reproduce"}],
            }
        )
    )
    assert "reproduce" in text
    assert "unfinished" in text.lower()


def test_approved_call_at_round_limit_still_executes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import core.agent_runtime.research_tool_loop_facade as loop
    from apps.vool_agent import VoolAgent
    from core.mode_permission_policy import decide_tool_call, resolve_approval, set_active_mode

    monkeypatch.setattr(loop, "_MAX_MODEL_ROUNDS_PER_TURN", 1)
    session = "budget-resume"
    set_active_mode(session, "manual", client_turn_id="budget-turn")
    ctx = {
        "runtime_session_id": session,
        "session_id": session,
        "operating_mode": "manual",
        "cancel_turn_id": "budget-turn",
        "workspace": str(tmp_path),
        "workspace_root": str(tmp_path),
    }
    payload = {"intent": "workspace.write_file", "arguments": {"path": "result.txt", "content": "budget-marker"}}
    request = decide_tool_call(
        intent=payload["intent"], arguments=payload["arguments"], task_id="budget-task", source_context=ctx
    ).approval_request
    assert request
    resolve_approval(request["approval_id"], decision="allow", scope="once")
    ctx.update(runtime_checkpoint_resumed=True, mode_approval_token=request["approval_id"])
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = _Router([_call("respond.direct", message="The requested file was written.")])
    agent.memory_router.resolve = lambda **kw: SimpleNamespace(output_text="The turn stopped.")
    agent.hive_activity_tracker = agent.public_hive_bridge = None
    agent._should_keep_ai_first_chat_lane = lambda **kw: False
    agent._should_run_builder_controller = lambda **kw: False
    agent._plan_tool_workflow = lambda **kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None)
    agent._runtime_checkpoint_id = lambda context: "budget-checkpoint"
    agent._get_runtime_checkpoint = lambda checkpoint_id: {
        "state": {
            "pending_tool_payload": payload,
            "executed_steps": [
                {
                    "tool_name": "workspace.write_file",
                    "mode": "tool_preview",
                    "status": "pending_approval",
                    "round_index": 0,
                    "arguments": payload["arguments"],
                }
            ],
        }
    }
    agent._record_runtime_tool_progress = lambda *a, **kw: None
    agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="budget-task"),
        effective_input="Write result.txt containing budget-marker.",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=session,
        source_context=ctx,
        surface="api",
    )
    assert (tmp_path / "result.txt").read_text() == "budget-marker"



@pytest.mark.parametrize("narration", [
    "I need to understand the task structure first. Let me check what's in the backend-coding workspace and find the math.js and test.js files.",
    "Let me check the files.",
])
def test_scaffolding_rejected_by_prose_guard_still_gets_coding_recovery(monkeypatch, narration):
    original = _Router.resolve_tool_intent
    feedback_seen = []

    def respond(self, **kwargs):
        feedback = kwargs.get("source_context", {}).get("code_task_completion_feedback")
        if feedback:
            feedback_seen.append(feedback)
            if len(feedback_seen) == 1:
                self.first_batch = [_call("workspace.read_file", path="README.md")]
                count = self.calls
                self.calls = 0
                result = original(self, **kwargs)
                self.calls = count + 1
                return result
        result = original(self, **kwargs)
        if self.calls > 1 and len(feedback_seen) < 2:
            result.structured_output = {"intent": "respond.direct", "arguments": {"message": narration}}
            result.tool_calls = (_call("respond.direct", message=narration),)
        return result

    monkeypatch.setattr(_Router, "resolve_tool_intent", respond)
    result, router = _drive([_call("code.task.open", objective="Repair the failing project checks")])
    assert feedback_seen, "Rejecting scaffolding must not bypass the unfinished-task recovery gate."
    assert "workspace.read_file" in result["details"]["tool_steps"]
    assert len(feedback_seen) == 2
    assert router.calls <= 6
    assert not result["success"]
