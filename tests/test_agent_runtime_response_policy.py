from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import ChatTurnResult, ResponseClass, VoolAgent
from core.agent_runtime import (
    response_policy,
    response_policy_classification,
    response_policy_tool_history,
    response_policy_visibility,
)
from core.agent_runtime.response import suppress_internal_reasoning_leak


def test_fast_path_response_class_facade_matches_extracted_policy() -> None:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    assert agent._fast_path_response_class(
        reason="hive_research_followup",
        response="Research result: 1 new local research result landed.",
    ) == response_policy.fast_path_response_class(
        agent,
        reason="hive_research_followup",
        response="Research result: 1 new local research result landed.",
    )
    assert response_policy.fast_path_response_class is response_policy_classification.fast_path_response_class


def test_action_response_class_keeps_non_research_tool_intents_as_generic_conversation() -> None:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    assert response_policy.action_response_class(
        agent,
        reason="model_tool_intent_multi_step_executed",
        success=True,
        task_outcome="success",
        response="Git repo summary for `/tmp/repo`:\n- visible branches: 7 total (5 local, 2 remote tracking)",
    ) == ResponseClass.GENERIC_CONVERSATION


def test_action_response_class_preserves_research_progress_for_explicit_research_result_text() -> None:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    assert response_policy.action_response_class(
        agent,
        reason="model_tool_intent_multi_step_executed",
        success=True,
        task_outcome="success",
        response="Research result: 1 new local research result landed.",
    ) == ResponseClass.RESEARCH_PROGRESS


def test_should_attach_hive_footer_facade_matches_extracted_policy() -> None:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    result = ChatTurnResult(
        text="Approval required before file write.",
        response_class=ResponseClass.APPROVAL_REQUIRED,
    )

    assert agent._should_attach_hive_footer(
        result,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    ) == response_policy.should_attach_hive_footer(
        agent,
        result,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    assert response_policy.should_attach_hive_footer is response_policy_visibility.should_attach_hive_footer


def test_normalize_tool_history_message_facade_matches_extracted_policy() -> None:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    item = {
        "role": "assistant",
        "content": (
            "Real tool result from `workspace.search_text`:\n"
            'Search matches for "tool_intent":\n'
            "- core/tool_intent_executor.py:42 def execute_tool_intent("
        ),
    }

    assert agent._normalize_tool_history_message(item) == response_policy.normalize_tool_history_message(agent, item)
    assert response_policy.normalize_tool_history_message is response_policy_tool_history.normalize_tool_history_message


def test_append_tool_result_to_source_context_dedupes_observation_messages() -> None:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    execution = SimpleNamespace(
        details={"observation": {"schema": "tool_observation_v1", "intent": "workspace.search_text", "tool_surface": "workspace"}},
        response_text="Search matches for tool_intent",
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="workspace.search_text",
    )

    first = agent._append_tool_result_to_source_context(
        {"conversation_history": []},
        execution=execution,
        tool_name="workspace.search_text",
    )
    second = agent._append_tool_result_to_source_context(
        first,
        execution=execution,
        tool_name="workspace.search_text",
    )

    history = list(second.get("conversation_history") or [])
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert "Grounding observations for this turn" in history[0]["content"]
    assert "tool-receipt-" in history[0]["content"]
    observations = list(second.get("runtime_tool_observations") or [])
    assert len(observations) == 1
    assert observations[0]["receipt_id"].startswith("tool-receipt-")
    assert observations[0]["safe_summary"] == (
        "workspace.search_text: Search matches for tool_intent"
    )
    assert observations[0]["intent"] == "workspace.search_text"


def test_terminal_output_context_is_bounded_redacted_and_artifact_keeps_raw_output() -> None:
    raw_stdout = "\n".join(f"test_{i} PASSED" for i in range(200))
    execution = SimpleNamespace(
        details={
            "artifacts": [{"artifact_type": "command_output", "stdout": raw_stdout}],
            "observation": {
                "schema": "tool_observation_v1",
                "intent": "sandbox.run_command",
                "tool_surface": "sandbox",
                "status": "executed",
                "returncode": 0,
                "stdout": raw_stdout,
                "stderr": "",
            },
        },
        response_text="Command executed\n" + raw_stdout,
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="sandbox.run_command",
    )
    payload = response_policy_tool_history.tool_history_observation_payload(
        execution=execution,
        tool_name="sandbox.run_command",
    )
    preview = payload["response_preview"]
    assert "stdout" not in payload, "the raw dump is never carried as a field"
    assert "stdout 200 line(s)" in preview
    assert payload["artifact_refs"] == ["command_output"]
    assert payload["receipt_id"].startswith("tool-receipt-")
    assert "test_199 PASSED" not in payload["safe_summary"]
    assert len(payload["safe_summary"]) <= 500
    assert execution.details["artifacts"][0]["stdout"] == raw_stdout

    # A 200-line log is bounded rather than erased. Both ends survive -- the head shows what the
    # command began with, the tail carries the summary line a runner prints last -- and the middle is
    # replaced by a marker that says so, so an excerpt is never reported as the whole output.
    assert len(preview) < len(raw_stdout) // 2
    assert "test_0 PASSED" in preview
    assert "test_199 PASSED" in preview
    assert "more line(s) omitted" in preview
    assert "test_100 PASSED" not in preview


def test_short_command_output_reaches_the_next_step_whole() -> None:
    # The regression this guards: `ls -la` ran, exited 0, produced 36 lines, and the observation said
    # only "stdout 36 line(s); full output saved in artifact(s)". The model had no listing to report
    # and answered "I'm ready to help. Let me check the current directory for you."
    listing = "\n".join(f"drwxr-xr-x  3 user staff   96 Jul 28 11:0{i % 10} entry_{i}" for i in range(36))
    execution = SimpleNamespace(
        details={
            "artifacts": [{"artifact_type": "command_output", "stdout": listing}],
            "observation": {
                "schema": "tool_observation_v1",
                "intent": "sandbox.run_command",
                "tool_surface": "sandbox",
                "status": "executed",
                "returncode": 0,
                "stdout": listing,
                "stderr": "",
            },
        },
        response_text="Command executed\n" + listing,
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="sandbox.run_command",
    )

    payload = response_policy_tool_history.tool_history_observation_payload(
        execution=execution,
        tool_name="sandbox.run_command",
    )

    assert "entry_0" in payload["response_preview"]
    assert "entry_35" in payload["response_preview"]
    assert "omitted" not in payload["response_preview"]
    assert payload["stdout_excerpt"] == listing


def test_stderr_from_a_failing_command_reaches_the_next_step() -> None:
    execution = SimpleNamespace(
        details={
            "artifacts": [{"artifact_type": "command_output", "stdout": ""}],
            "observation": {
                "schema": "tool_observation_v1",
                "intent": "sandbox.run_command",
                "tool_surface": "sandbox",
                "status": "command_failed",
                "returncode": 1,
                "stdout": "",
                "stderr": "cat: missing.txt: No such file or directory",
            },
        },
        response_text="Command failed",
        ok=False,
        status="command_failed",
        mode="tool_failed",
        tool_name="sandbox.run_command",
    )

    payload = response_policy_tool_history.tool_history_observation_payload(
        execution=execution,
        tool_name="sandbox.run_command",
    )

    assert "No such file or directory" in payload["response_preview"]
    assert "exit code 1" in payload["response_preview"]


def test_file_listing_context_is_bounded_and_linked_to_a_receipt() -> None:
    paths = [f"src/file_{i}.py" for i in range(80)]
    execution = SimpleNamespace(
        details={"observation": {"intent": "workspace.list_files", "path": "src", "paths": paths, "count": 80, "truncated": False}},
        response_text="\n".join(paths),
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="workspace.list_files",
    )
    payload = response_policy_tool_history.tool_history_observation_payload(execution=execution, tool_name="workspace.list_files")
    assert payload["receipt_id"].startswith("tool-receipt-")
    assert payload["count"] == 80
    assert payload["sample_paths"] == paths[:60]
    assert "paths" not in payload
    assert len(payload["safe_summary"]) <= 500


def test_a_truncated_listing_names_its_total_and_forbids_an_absence_claim() -> None:
    """The bound above is fine; what was missing was the TOTAL.

    Measured on the live product: a 682-file repository listed 200 paths, this payload rendered
    `Listed 200 item(s)`, and the audit concluded "No tests discovered -- workspace-wide" on a
    project holding 275 test files. A count of the page, presented as the count, is how a prefix
    becomes evidence of absence.
    """

    paths = [f"api/file_{i}.py" for i in range(200)]
    execution = SimpleNamespace(
        details={
            "observation": {
                "intent": "workspace.list_files",
                "path": ".",
                "paths": paths,
                "count": 200,
                "total": 682,
                "dropped": 482,
                "truncated": True,
            }
        },
        response_text="\n".join(paths),
        ok=True,
        status="truncated",
        mode="tool_executed",
        tool_name="workspace.list_files",
    )
    payload = response_policy_tool_history.tool_history_observation_payload(execution=execution, tool_name="workspace.list_files")
    preview = payload["response_preview"]

    assert "200 of 682" in preview, preview[:300]
    assert "482 item(s) not shown" in preview, preview[:300]
    assert "ABSENT" in preview, "the model must be told a prefix cannot prove absence"


def test_tool_intent_direct_message_reads_explicit_direct_response() -> None:
    assert response_policy.tool_intent_direct_message(
        {"intent": "respond.direct", "arguments": {"message": "Use the local proof receipt."}},
    ) == "Use the local proof receipt."


def test_maybe_attach_workflow_keeps_openclaw_clean_without_debug_flag() -> None:
    with mock.patch(
        "core.agent_runtime.response_policy_visibility.load_preferences",
        return_value=SimpleNamespace(show_workflow=True),
    ):
        attached = response_policy.maybe_attach_workflow(
            None,
            "Patched the failing test and reran the pack.",
            "- recognized operator action `workspace.apply_unified_diff`\n- execution posture: `tool_executed`",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert attached == "Patched the failing test and reran the pack."


def test_maybe_attach_workflow_allows_explicit_debug_on_openclaw() -> None:
    with mock.patch(
        "core.agent_runtime.response_policy_visibility.load_preferences",
        return_value=SimpleNamespace(show_workflow=True),
    ):
        attached = response_policy.maybe_attach_workflow(
            None,
            "Patched the failing test and reran the pack.",
            "- recognized operator action `workspace.apply_unified_diff`\n- execution posture: `tool_executed`",
            source_context={"surface": "openclaw", "platform": "openclaw", "workflow_debug": True},
        )

    assert attached.startswith("Workflow:\n")
    assert "Patched the failing test and reran the pack." in attached


def test_internal_reasoning_monologue_never_reaches_chat() -> None:
    leaked = (
        "The user is asking for a verdict on the folder audit. Let me look at what was found in "
        "the previous tool result. I don't see the actual tree in the context provided. I should ask again."
    )
    assert suppress_internal_reasoning_leak(leaked) == (
        "I couldn't produce a clean final answer from that model response. No result is being claimed."
    )
    assert suppress_internal_reasoning_leak(
        "The user is asking about API design. Final answer: Use one versioned contract."
    ) == "Use one versioned contract."
    assert suppress_internal_reasoning_leak("The user is asking an important question about privacy.") is None
