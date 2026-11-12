"""After a command ran, the reply is backed by its result: never "nothing this turn ... backs it".

Ledger V-050 (2026-11-12, served chat on the v0.8 line, buffered and streamed): an uncertified
model asked to run `python3 build.py`; the command ran (its file was rewritten); the model wrote
nothing after it; and the reply was the uncertified-writer notice, whose details said "nothing this
turn retrieved, computed or observed backs it".

The cause, read off the served daemon: the tool loop minted the command's support rows BEFORE the
turn had an authorship record (`record_runtime_support` found none and dropped them: "No record,
no minting"); the synthesis fence then created the record with no rows, and the publication gate
read an empty-handed turn. Two smaller seams sat behind it: the runtime's own reply for a command
said "Last result: Command executed in `.`:" without the output, and nothing told the gate that
those bytes were the runtime's own composition.

Pinned here:
- rows minted before the record are held by the turn's fenced identity and join the record the
  turn creates; an unfenced identity, or another execution attempt sharing an id, never gets them;
- the reply the runtime writes without a model names the command and shows what it printed;
- the tool loop marks that reply as runtime output, so the gate publishes it.

The served journey on the real page is tests/test_a_command_that_ran_backs_the_answer_served.py.
"""

from __future__ import annotations

import pathlib
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from core import final_answer_authorship as authorship
from core.agent_runtime.orchestrator import tool_loop_final_message
from core.semantic.semantic_admissions import bound_execution_context

NOTHING_BACKS_IT = "nothing this turn retrieved, computed or observed backs it"
COMMAND_ROW = {
    "summary": "Command executed in `.`:",
    "intent": "sandbox.run_command",
    "source": "tool_loop_executed_steps",
    "observation": {"intent": "sandbox.run_command", "command": "python3 build.py", "returncode": 0, "stdout": "built\n"},
}


def _context(name: str) -> dict:
    return {
        "session_id": f"sess-{name}",
        "runtime_session_id": f"sess-{name}",
        "turn_id": f"turn-{name}",
        "request_id": f"req-{name}",
        "surface": "api",
    }


def _fence(attempt: str) -> dict:
    return {"execution_id": f"attempt-{attempt}", "generation": 1, "runtime_epoch": "epoch-1"}


def _precall_block(context: dict) -> None:
    """The synthesis fence refusing the uncertified writer, exactly as the router records it."""
    authorship.record_authorship_decision(
        context,
        authorship.AuthorshipDecision(
            eligible=False,
            author_role="answer_generation",
            task_class="shell_guidance",
            requested_model="ollama-local:qwen3-stub:2b",
            reason="blocked_before_generation",
        ),
        blocked_model="ollama-local:qwen3-stub:2b",
    )


@pytest.fixture(autouse=True)
def _clean_records():
    authorship.reset_for_tests()
    yield
    authorship.reset_for_tests()


# ------------------------------------------------------------- rows minted before the record


def test_rows_minted_before_the_record_join_the_record_the_turn_creates() -> None:
    context = _context("held")
    with bound_execution_context(_fence("held")):
        # The tool loop, before anything raised the authorship question: no record is minted.
        assert authorship.record_runtime_support(context, support_rows=[COMMAND_ROW], request_text="run the build") is False
        assert authorship.authorship_record_for_publication(turn_id="turn-held") is None
        # The synthesis fence raises it.
        _precall_block(context)
        record = authorship.authorship_record_for_publication(turn_id="turn-held")

    assert record is not None
    assert record.supported_by_runtime is True
    assert [row.get("intent") for row in record.support_rows] == ["sandbox.run_command"]
    assert record.request_text == "run the build"
    assert not record.must_refuse
    # Were it refused anyway, the refusal would name the evidence, never deny it.
    refusal = record.decision.refusal_text(grounded_steps=len(record.support_rows))
    assert NOTHING_BACKS_IT not in refusal and "1 step succeeded" in refusal


def test_the_gate_publishes_the_runtimes_own_report_of_the_command() -> None:
    context = _context("gate")
    report = "I ran `python3 build.py`. It finished without errors and printed:\n\n```\nbuilt\n```"
    with bound_execution_context(_fence("gate")):
        authorship.record_runtime_support(context, support_rows=[COMMAND_ROW])
        _precall_block(context)
        # The tool loop marks the reply it composed itself as runtime output.
        assert authorship.record_runtime_support(
            context, support_rows=[{"summary": report, "intent": "tool_loop_composed_result", "source": "tool_loop_composed_result"}]
        )
        published, payload = authorship.gate_authored_content(report, turn_id="turn-gate")

    assert published == report, payload
    assert payload.get("publication") == "claim_adjudicated:published", payload


def test_without_the_held_rows_the_turn_reads_as_empty_handed() -> None:
    """The control: the same order with the rows never minted is the V-050 notice."""
    context = _context("control")
    with bound_execution_context(_fence("control")):
        _precall_block(context)
        published, payload = authorship.gate_authored_content("Some composed text.", turn_id="turn-control")

    assert payload.get("publication") == "refused", payload
    assert NOTHING_BACKS_IT in published


def test_an_unfenced_identity_holds_nothing() -> None:
    """Unfenced ids are reused across turns (fixed ids, the fast lane), so their rows are not held."""
    context = _context("unfenced")
    assert authorship.record_runtime_support(context, support_rows=[COMMAND_ROW]) is False
    _precall_block(context)
    record = authorship.authorship_record_for_publication(turn_id="turn-unfenced")

    assert record is not None and not record.support_rows and not record.supported_by_runtime
    assert record.must_refuse


def test_another_attempt_sharing_the_request_id_does_not_get_the_rows() -> None:
    context = _context("shared")
    with bound_execution_context(_fence("first")):
        authorship.record_runtime_support(context, support_rows=[COMMAND_ROW])
    with bound_execution_context(_fence("second")):
        _precall_block(context)
        record = authorship.authorship_record_for_publication(turn_id="turn-shared")

    assert record is not None and not record.support_rows
    assert record.must_refuse


def test_another_turn_sharing_the_request_id_does_not_get_the_rows() -> None:
    with bound_execution_context(_fence("same")):
        authorship.record_runtime_support({**_context("a"), "request_id": "req-reused"}, support_rows=[COMMAND_ROW])
        _precall_block({**_context("b"), "request_id": "req-reused"})
        record = authorship.authorship_record_for_publication(turn_id="turn-b")

    assert record is not None and not record.support_rows


# ------------------------------------------------------------ the reply written without a model


def _command_step(**observation) -> dict:
    return {
        "tool_name": "sandbox.run_command",
        "ok": observation.get("returncode") == 0,
        "status": "executed" if observation.get("returncode") == 0 else "command_failed",
        "summary": "Command executed in `.`:",
        "observation": {"intent": "sandbox.run_command", "command": "python3 build.py", **observation},
    }


def test_the_reply_names_the_command_and_shows_what_it_printed() -> None:
    text = tool_loop_final_message(None, [_command_step(returncode=0, stdout="built\n")])

    assert text == "I ran `python3 build.py`. It finished without errors and printed:\n\n```\nbuilt\n```"
    assert "Last result" not in text and not text.rstrip().endswith(":")


def test_a_failed_command_says_it_stopped_and_shows_its_error_output() -> None:
    text = tool_loop_final_message(None, [_command_step(returncode=2, stdout="", stderr="python3: can't open file 'build.py'\n")])

    assert text.startswith("I ran `python3 build.py`. It stopped with an error (exit code 2).")
    assert "Its error output:\n\n```\npython3: can't open file 'build.py'\n```" in text


def test_a_command_that_printed_nothing_says_so() -> None:
    assert tool_loop_final_message(None, [_command_step(returncode=0, stdout="")]) == (
        "I ran `python3 build.py`. It finished without errors and printed nothing."
    )


def test_the_tool_history_excerpt_is_read_too() -> None:
    text = tool_loop_final_message(None, [_command_step(returncode=0, stdout_excerpt="receipt-51ab-cobalt,125")])
    assert "```\nreceipt-51ab-cobalt,125\n```" in text


def test_long_output_is_shortened_and_says_so() -> None:
    text = tool_loop_final_message(None, [_command_step(returncode=0, stdout="x" * 5000)])
    assert "shortened here; the full output is saved with this turn" in text
    assert len(text) < 3400


def test_earlier_steps_are_counted_before_the_command() -> None:
    read = {"tool_name": "workspace.read_file", "ok": True, "summary": "Read build.py"}
    text = tool_loop_final_message(None, [read, _command_step(returncode=0, stdout="built\n")])
    assert text.startswith("2 of 2 steps succeeded.\n\nI ran `python3 build.py`.")


def test_a_command_that_never_started_keeps_the_generic_line() -> None:
    step = {"tool_name": "sandbox.run_command", "ok": False, "status": "sandbox_unavailable", "summary": "Sandbox unavailable",
            "observation": {"intent": "sandbox.run_command", "command": "python3 build.py"}}
    assert tool_loop_final_message(None, [step]) == "0 of 1 tool steps succeeded. Last result: Sandbox unavailable"


def test_a_model_reply_is_never_replaced() -> None:
    synthesis = SimpleNamespace(structured_output=None, output_text="The build ran and printed built.")
    assert tool_loop_final_message(synthesis, [_command_step(returncode=0, stdout="built\n")]) == "The build ran and printed built."


# --------------------------------------------------------- the real loop marks its own reply


class _Router:
    """Scripted at the provider seam: two reads, then the answer-writing call returns nothing."""

    def __init__(self) -> None:
        self.rounds = 0

    def resolve_tool_intent(self, **_kwargs):
        from core.memory_first_router import ModelExecutionDecision

        self.rounds += 1
        if self.rounds > 2:
            return ModelExecutionDecision(
                source="scripted", task_hash="h", used_model=True, confidence=0.8,
                structured_output={"intent": "respond.direct", "arguments": {"message": ""}},
                tool_calls=(), validation_state="validated", provider_id="p",
            )
        path = "orders.csv" if self.rounds == 1 else "notes.txt"
        call = SimpleNamespace(intent="workspace.read_file", arguments={"path": path}, call_id=f"c-{self.rounds}", name="workspace.read_file")
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": call.intent, "arguments": dict(call.arguments)},
            tool_calls=(call,), validation_state="validated", provider_id="p",
        )

    def resolve(self, **_kwargs):
        return SimpleNamespace(
            output_text="", provider_id="", used_model=False, confidence=0.0, trust_score=0.0,
            validation_state="not_run", details={}, source="no_provider_available",
            model_name="", provider_name="", cache_hit=False, candidate_id=None,
            failover_used=False, structured_output=None, task_hash="h",
        )


def test_the_loop_marks_the_reply_it_composed_itself_as_runtime_output() -> None:
    from apps.vool_agent import VoolAgent

    workspace = pathlib.Path(tempfile.mkdtemp())
    (workspace / "orders.csv").write_text("id,total\nreceipt-51ab-cobalt,125\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("cobalt order settled on Tuesday\n", encoding="utf-8")
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = _Router()
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    context = {
        "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
        "workspace_binding": "project", "project_id": workspace.name, "turn_id": "turn-loop", "request_id": "req-loop",
    }

    with bound_execution_context(_fence("loop")):
        _precall_block(context)
        result = agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="t-loop"),
            effective_input="read orders.csv and notes.txt and tell me about the cobalt order",
            classification={"task_class": "debugging"},
            interpretation=SimpleNamespace(),
            context_result=SimpleNamespace(),
            persona=SimpleNamespace(),
            session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
            source_context=context,
            surface="api",
        ) or {}
        reply = str(result.get("response") or "")
        record = authorship.authorship_record_for_publication(turn_id="turn-loop")
        published, payload = authorship.gate_authored_content(reply, turn_id="turn-loop")

    assert result.get("status") == "tool_synthesis_failed", result.get("status")
    assert record is not None
    composed = [row for row in record.support_rows if row.get("source") == "tool_loop_composed_result"]
    assert [row.get("summary") for row in composed] == [reply]
    assert published == reply, payload
    assert NOTHING_BACKS_IT not in published
