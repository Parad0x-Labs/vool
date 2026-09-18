from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime.builder import controller, scaffolds, support
from core.context_scope import ContextAccessPolicy


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_workspace_build_target_uses_app_level_root_and_heuristic_sources() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(topic_hints=["discord bot"])
    access_policy = ContextAccessPolicy(chat_id="builder-test")

    with mock.patch.object(agent, "_extract_requested_builder_root", return_value="sandbox/discord-bot") as extract_root, mock.patch.object(
        agent,
        "_search_user_heuristics",
        return_value=[{"category": "preferred_stack", "signal": "typescript"}],
    ) as search_user_heuristics_mock:
        target = agent._workspace_build_target(
            query_text="build a discord bot for this workspace",
            interpretation=interpretation,
            access_policy=access_policy,
        )

    # The workspace root travels with the query: it is what lets a rooted destination inside the
    # workspace be rebased instead of dropped (see builder/named_file_build.py).
    extract_root.assert_called_once_with("build a discord bot for this workspace", workspace_root="")
    search_user_heuristics_mock.assert_called_once_with(
        "build a discord bot for this workspace",
        access_policy=access_policy,
        topic_hints=["discord bot"],
        limit=4,
    )
    assert target == {
        "platform": "discord",
        "language": "typescript",
        "root_dir": "sandbox/discord-bot",
    }


def test_builder_controller_profile_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.builder.support.controller_profile",
        return_value={"should_handle": True, "mode": "scaffold"},
    ) as controller_profile:
        result = agent._builder_controller_profile(
            effective_input="build a discord bot",
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(topic_hints=[]),
            source_context={"workspace": "/tmp/test-builder"},
        )

    assert result == {"should_handle": True, "mode": "scaffold"}
    controller_profile.assert_called_once_with(
        agent,
        effective_input="build a discord bot",
        classification={"task_class": "system_design"},
        interpretation=mock.ANY,
        source_context={"workspace": "/tmp/test-builder"},
        access_policy=None,
        plan_tool_workflow_fn=mock.ANY,
        looks_like_workspace_bootstrap_request_fn=mock.ANY,
    )


def test_workspace_build_file_map_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    target = {"platform": "telegram", "language": "python", "root_dir": "generated/telegram-bot"}
    web_notes = [{"result_title": "Telegram docs", "result_url": "https://core.telegram.org"}]

    assert agent._workspace_build_file_map(
        target=target,
        user_request="build a telegram bot",
        web_notes=web_notes,
    ) == scaffolds.workspace_build_file_map(
        target=target,
        user_request="build a telegram bot",
        web_notes=web_notes,
    )


def test_builder_artifact_citation_block_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    artifacts = {
        "file_diffs": [{"path": "src/bot.py", "diff_preview": "print('hello world')"}],
        "command_outputs": [{"command": "python3 -m compileall -q generated/telegram-bot/src", "returncode": 0}],
        "failures": [{"summary": "initial compile failed on missing import"}],
        "retry_history": [{"command": "python3 -m compileall -q generated/telegram-bot/src", "attempts": 2}],
        "stop_reason": "command_stop_after_success",
    }

    assert agent._builder_artifact_citation_block(artifacts) == support.artifact_citation_block(agent, artifacts)


def test_append_builder_artifact_citations_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    artifacts = {"file_diffs": [{"path": "src/main.py", "diff_preview": "print('ready')"}], "stop_reason": "builder_complete"}

    assert agent._append_builder_artifact_citations("done", artifacts=artifacts) == support.append_artifact_citations(
        agent,
        "done",
        artifacts=artifacts,
    )


def test_run_bounded_builder_loop_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.builder.controller.run_bounded_builder_loop",
        return_value=([], {"surface": "openclaw"}, "bounded_loop_complete", None),
    ) as run_bounded_builder_loop:
        result = agent._run_bounded_builder_loop(
            task=SimpleNamespace(task_id="task-builder-loop"),
            session_id="builder-loop-session",
            effective_input="build a telegram bot in this workspace",
            task_class="system_design",
            source_context={"workspace": "/tmp/test-builder"},
            initial_payloads=[{"intent": "workspace.write_file"}],
        )

    assert result == ([], {"surface": "openclaw"}, "bounded_loop_complete", None)
    run_bounded_builder_loop.assert_called_once_with(
        agent,
        task=mock.ANY,
        session_id="builder-loop-session",
        effective_input="build a telegram bot in this workspace",
        task_class="system_design",
        source_context={"workspace": "/tmp/test-builder"},
        initial_payloads=[{"intent": "workspace.write_file"}],
        plan_tool_workflow_fn=mock.ANY,
        execute_tool_intent_fn=mock.ANY,
        trust_initial_payloads=False,
    )


def test_maybe_run_builder_controller_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.builder.controller.maybe_run_builder_controller",
        return_value={"response": "builder-controller"},
    ) as maybe_run_builder_controller:
        result = agent._maybe_run_builder_controller(
            task=SimpleNamespace(task_id="task-builder-controller"),
            effective_input="build a discord bot in this workspace",
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="builder-controller-session",
            source_context={"workspace": "/tmp/test-builder"},
        )

    assert result == {"response": "builder-controller"}
    maybe_run_builder_controller.assert_called_once_with(
        agent,
        task=mock.ANY,
        effective_input="build a discord bot in this workspace",
        classification={"task_class": "system_design"},
        interpretation=mock.ANY,
        web_notes=[],
        session_id="builder-controller-session",
        source_context={"workspace": "/tmp/test-builder"},
        render_capability_truth_response_fn=mock.ANY,
        load_active_persona_fn=mock.ANY,
    )


def test_maybe_run_builder_controller_uses_app_level_run_loop_override() -> None:
    agent = _build_agent()
    task = SimpleNamespace(task_id="task-builder-override")
    executed_steps = [{"tool_name": "workspace.write_file"}]
    artifacts = {"file_diffs": [{"path": "src/main.py"}], "stop_reason": "bounded_loop_complete"}

    with mock.patch.object(
        agent,
        "_builder_controller_profile",
        return_value={
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": {"platform": "discord", "language": "python", "root_dir": "generated/discord-bot"},
        },
    ), mock.patch.object(
        agent,
        "_builder_initial_payloads",
        return_value=([{"intent": "workspace.write_file"}], [{"title": "Discord docs", "url": "https://discord.com"}]),
    ), mock.patch.object(
        agent,
        "_run_bounded_builder_loop",
        return_value=(executed_steps, {"surface": "openclaw"}, "bounded_loop_complete", None),
    ) as run_bounded_builder_loop, mock.patch.object(
        agent,
        "_builder_controller_artifacts",
        return_value=artifacts,
    ), mock.patch.object(
        agent,
        "_builder_controller_observations",
        return_value={"channel": "workspace_build"},
    ), mock.patch.object(
        agent,
        "_builder_controller_degraded_response",
        return_value="builder degraded",
    ), mock.patch.object(
        agent,
        "_builder_controller_workflow_summary",
        return_value="builder workflow",
    ), mock.patch.object(
        agent,
        "_builder_controller_direct_response",
        return_value="builder direct",
    ), mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "builder direct"},
    ) as fast_path_result:
        result = agent._maybe_run_builder_controller(
            task=task,
            effective_input="build a discord bot in this workspace",
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="builder-controller-session",
            source_context={"workspace": "/tmp/test-builder"},
        )

    run_bounded_builder_loop.assert_called_once()
    fast_path_result.assert_called_once()
    assert result["mode"] == "tool_executed"
    assert result["workflow_summary"] == "builder workflow"
    assert result["details"]["builder_controller"]["step_count"] == 1


def test_builder_permission_preview_pauses_without_model_wording_or_conversation_persistence() -> None:
    agent = _build_agent()
    task = SimpleNamespace(task_id="task-builder-approval")
    pending = SimpleNamespace(
        mode="tool_preview",
        status="pending_approval",
        response_text="Approval required before creating approval-proof.txt.",
    )
    executed_steps = [{"tool_name": "workspace.write_file", "mode": "tool_preview"}]

    with mock.patch.object(
        agent,
        "_builder_controller_profile",
        return_value={
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": {"platform": "", "language": "", "root_dir": ""},
        },
    ), mock.patch.object(
        agent,
        "_builder_initial_payloads",
        return_value=([{"intent": "workspace.write_file"}], []),
    ), mock.patch.object(
        agent,
        "_run_bounded_builder_loop",
        return_value=(executed_steps, {"surface": "openclaw"}, "tool_preview:pending_approval", pending),
    ), mock.patch.object(
        agent, "_builder_controller_artifacts", return_value={"stop_reason": "tool_preview:pending_approval"}
    ), mock.patch.object(
        agent, "_builder_controller_observations", return_value={"channel": "bounded_builder"}
    ), mock.patch.object(
        agent, "_builder_controller_degraded_response", return_value="degraded"
    ), mock.patch.object(
        agent, "_builder_controller_workflow_summary", return_value="waiting for approval"
    ), mock.patch.object(
        agent, "_builder_controller_direct_response"
    ) as direct_response, mock.patch.object(
        agent, "_chat_surface_model_wording_result"
    ) as model_wording, mock.patch.object(
        agent,
        "_action_fast_path_result",
        return_value={"response": "Approval required before creating approval-proof.txt."},
    ) as action_result:
        result = agent._maybe_run_builder_controller(
            task=task,
            effective_input="create approval-proof.txt",
            classification={"task_class": "build"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="builder-approval-session",
            source_context={"surface": "openclaw", "persist_memory": True},
        )

    direct_response.assert_not_called()
    model_wording.assert_not_called()
    call = action_result.call_args.kwargs
    assert call["task_outcome"] == "pending_approval"
    assert call["mode_override"] == "tool_preview"
    assert call["source_context"]["persist_memory"] is False
    assert result["details"]["builder_controller"]["stop_reason"] == "tool_preview:pending_approval"


def test_workspace_build_response_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    target = {"platform": "telegram", "language": "python", "root_dir": "generated/telegram-bot"}
    write_results = [{"path": "generated/telegram-bot/src/bot.py"}]
    verification = {"status": "executed", "response_text": "compileall ok"}
    sources = [{"title": "Telegram docs", "url": "https://core.telegram.org"}]

    assert agent._workspace_build_response(
        target=target,
        write_results=write_results,
        write_failures=[],
        verification=verification,
        sources=sources,
    ) == controller.workspace_build_response(
        target=target,
        write_results=write_results,
        write_failures=[],
        verification=verification,
        sources=sources,
    )


def test_builder_controller_does_not_hijack_plain_workspace_search_requests() -> None:
    agent = _build_agent()

    should_run = agent._should_run_builder_controller(
        effective_input='find a file in this workspace mentioning "runtime_capabilities"',
        classification={"task_class": "research"},
        source_context={"workspace": "/tmp/test-builder", "workspace_root": "/tmp/test-builder"},
    )

    assert should_run is False


def test_controller_profile_routes_inspection_workflow_not_advice_only() -> None:
    # A builder-scoped inspect->read request whose next step is a read-only inspection
    # intent must run as a bounded workflow, not fall through to the "unsupported" branch
    # (which surfaces as advice_only with zero tool calls).
    probe = SimpleNamespace(
        handled=True,
        next_payload={"intent": "workspace.search_text", "arguments": {"query": "retry handler"}},
    )
    agent = SimpleNamespace(
        _should_run_builder_controller=lambda **_kw: True,
        _workspace_build_target=lambda **_kw: {"language": "python", "root_dir": "/ws", "platform": ""},
        _supports_bounded_builder_workflow_request=lambda **_kw: True,
        _looks_like_explicit_workspace_file_request=lambda _t: False,
        _looks_like_generic_workspace_bootstrap_request=lambda _t: False,
    )

    result = support.controller_profile(
        agent,
        effective_input="search the workspace code for the retry handler and read it",
        classification={"task_class": "file_inspection"},
        interpretation=None,
        source_context={"workspace": "/ws"},
        plan_tool_workflow_fn=lambda **_kw: probe,
        looks_like_workspace_bootstrap_request_fn=lambda _t: False,
    )

    assert result["supported"] is True
    assert result["mode"] == "workflow"
    assert result["initial_payloads"][0]["intent"] == "workspace.search_text"


def _chat_response(*, content: str = "", thinking: str = "", done_reason: str = "stop") -> mock.Mock:
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "message": {"role": "assistant", "content": content, "thinking": thinking},
        "done_reason": done_reason,
    }
    return response


def test_builder_keeps_the_thinking_parser_on_for_a_thinking_model() -> None:
    # Measured against live Ollama 0.31.1 + qwen3:4b: think:false does NOT stop the model reasoning,
    # it only switches off Ollama's parser, so the monologue lands in message.content as the answer.
    # think:true keeps the reasoning quarantined in message.thinking.
    payload = controller.builder_chat_payload("hi", model_tag="qwen3:4b")

    assert payload["think"] is True
    assert payload["options"]["num_predict"] == 6144


def test_builder_omits_think_for_a_model_that_rejects_the_flag() -> None:
    # Live: qwen2.5:7b answers HTTP 400 '"qwen2.5:7b" does not support thinking' when think is sent.
    assert "think" not in controller.builder_chat_payload("hi", model_tag="qwen2.5:7b")
    assert "think" not in controller.builder_chat_payload("hi", model_tag="vool-qwen3-30b-a3b:nothink")


def test_thinking_family_rule_agrees_with_the_adapter() -> None:
    # The builder and adapters/openai_compatible_adapter.py must select the SAME family (they act on
    # it in opposite directions, deliberately). Pinned so the two detectors cannot drift apart.
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    for model_name in (
        "qwen3:4b",
        "qwen3:8b",
        "qwen3:0.6b",
        "qwen2.5:7b",
        "deepseek-r1:14b",
        "vool-qwen3-30b-a3b:nothink",
        "qwen3-no-think:8b",
    ):
        adapter = OpenAICompatibleAdapter(
            SimpleNamespace(
                provider_id=f"ollama-local:{model_name}",
                model_name=model_name,
                metadata={"runtime_family": "ollama"},
                runtime_config={"base_url": "http://127.0.0.1:11434"},
            )
        )
        assert controller.is_thinking_capable_model(model_name) == adapter._is_thinking_capable_model(), model_name


def test_builder_base_url_accepts_ollamas_bare_host_port_convention() -> None:
    assert controller.builder_ollama_base_url({}) == "http://127.0.0.1:11434"
    assert controller.builder_ollama_base_url({"OLLAMA_HOST": "127.0.0.1:11434"}) == "http://127.0.0.1:11434"
    assert controller.builder_ollama_base_url({"OLLAMA_HOST": "http://box:1234/"}) == "http://box:1234"


def test_builder_never_hands_reasoning_to_the_file_writer() -> None:
    generate = controller.build_ollama_generate_fn(base_url="http://127.0.0.1:11434", model_tag="qwen3:4b")

    # message.thinking is never an answer, not even when content came back empty.
    with mock.patch(
        "requests.post", return_value=_chat_response(content="", thinking="Okay, let me process this step by step.")
    ):
        assert generate("list the files") == ""

    # A monologue that leaked into content is not the answer either.
    with mock.patch(
        "requests.post",
        return_value=_chat_response(content='Wait, the user might be confused.\n</think>\n\n["todo.py"]'),
    ):
        assert generate("list the files") == '["todo.py"]'


def test_builder_retries_once_when_reasoning_ate_the_whole_token_budget() -> None:
    # done_reason=length + empty content = the model reasoned past its budget and never answered.
    responses = [
        _chat_response(content="", thinking="thinking hard...", done_reason="length"),
        _chat_response(content='["todo.py"]'),
    ]
    generate = controller.build_ollama_generate_fn(base_url="http://127.0.0.1:11434", model_tag="qwen3:4b")

    with mock.patch("requests.post", side_effect=responses) as post:
        assert generate("list the files") == '["todo.py"]'

    assert post.call_count == 2
    assert [call.kwargs["json"]["options"]["num_predict"] for call in post.call_args_list] == [6144, 12288]


def test_builder_does_not_retry_a_model_that_simply_answered_nothing() -> None:
    # Empty but NOT truncated: the budget was not the problem, so a retry would just burn time.
    with mock.patch("requests.post", return_value=_chat_response(content="", done_reason="stop")) as post:
        generate = controller.build_ollama_generate_fn(base_url="http://127.0.0.1:11434", model_tag="qwen3:4b")
        assert generate("list the files") == ""

    assert post.call_count == 1


def test_builder_generation_survives_an_unreachable_ollama() -> None:
    with mock.patch("requests.post", side_effect=OSError("connection refused")):
        generate = controller.build_ollama_generate_fn(base_url="http://127.0.0.1:11434", model_tag="qwen3:4b")
        assert generate("list the files") == ""


def test_build_mode_answer_never_shows_the_models_reasoning_to_the_user() -> None:
    # The beta transcript, verbatim: the Build-mode reply opened with the model's monologue.
    leaked = (
        "Okay, let me process this step by step... Wait, the user might be confused here... "
        "Hmm, that's tricky.\n</think>\n\nI created todo.py and test_todo.py; the tests pass."
    )
    assert controller.builder_user_visible_text(leaked) == "I created todo.py and test_todo.py; the tests pass."

    # A normal answer is passed through untouched...
    assert controller.builder_user_visible_text("I wrote 2 files.") == "I wrote 2 files."
    # ...and a reply that is ONLY reasoning is never blanked into an empty bubble.
    assert controller.builder_user_visible_text("Hmm, let me think.\n</think>\n\n") == "Hmm, let me think.\n</think>\n\n"


def test_scaffold_verification_payload_keeps_the_trust_marker_out_of_arguments() -> None:
    # Regression: the marker used to live in `arguments`, which the executor strips as forgeable,
    # so the compileall step fell back to kernel isolation and failed closed on Windows.
    payload = support.workspace_build_verification_payload(
        target={"language": "python", "root_dir": "tools"}
    )
    assert payload is not None
    assert payload["trusted_local_only"] is True
    assert not [key for key in payload["arguments"] if str(key).startswith("_")]


def test_builder_loop_grants_trusted_local_only_to_server_generated_scaffold_payloads() -> None:
    agent = _build_agent()
    seen: list[tuple[str, bool]] = []

    def _execute(payload, **kwargs):
        seen.append((str(payload.get("intent") or ""), bool(kwargs.get("trusted_local_only"))))
        assert "trusted_local_only" not in payload
        return SimpleNamespace(
            handled=True, mode="tool_executed", status="executed", ok=True,
            response_text="ok", details={}, tool_name=str(payload.get("intent") or ""),
        )

    def _plan(**_kwargs):
        return SimpleNamespace(handled=True, stop_after=True, reason="done", next_payload=None)

    controller.run_bounded_builder_loop(
        agent,
        task=SimpleNamespace(task_id="t1"),
        session_id="s1",
        effective_input="build it",
        task_class="build",
        source_context={},
        initial_payloads=[
            {"intent": "workspace.write_file", "arguments": {"path": "a.py", "content": ""}},
            {"intent": "sandbox.run_command", "arguments": {"command": "x"}, "trusted_local_only": True},
        ],
        plan_tool_workflow_fn=_plan,
        execute_tool_intent_fn=_execute,
        trust_initial_payloads=True,
    )

    assert seen == [("workspace.write_file", False), ("sandbox.run_command", True)]


def test_builder_loop_refuses_a_trust_marker_on_a_planner_follow_up() -> None:
    agent = _build_agent()
    seen: list[bool] = []
    planned = [
        SimpleNamespace(
            handled=True, stop_after=False, reason="",
            next_payload={"intent": "sandbox.run_command", "arguments": {"command": "x"},
                          "trusted_local_only": True},
        ),
        SimpleNamespace(handled=True, stop_after=True, reason="done", next_payload=None),
    ]

    def _execute(payload, **kwargs):
        seen.append(bool(kwargs.get("trusted_local_only")))
        return SimpleNamespace(
            handled=True, mode="tool_executed", status="executed", ok=True,
            response_text="ok", details={}, tool_name=str(payload.get("intent") or ""),
        )

    def _plan(**_kwargs):
        return planned.pop(0)

    controller.run_bounded_builder_loop(
        agent,
        task=SimpleNamespace(task_id="t1"),
        session_id="s1",
        effective_input="build it",
        task_class="build",
        source_context={},
        initial_payloads=[],
        plan_tool_workflow_fn=_plan,
        execute_tool_intent_fn=_execute,
        trust_initial_payloads=True,
    )

    assert seen == [False]


def test_builder_loop_emits_permission_preview_with_exact_approval_payload() -> None:
    agent = _build_agent()
    approval_request = {
        "token": "approval-token",
        "action": "create_file",
        "resources": ["approval-proof.txt"],
        "reversible": True,
    }
    execution = SimpleNamespace(
        handled=True,
        mode="tool_preview",
        status="pending_approval",
        ok=False,
        response_text="Approval required before creating approval-proof.txt.",
        details={"approval_request": approval_request},
        tool_name="workspace.write_file",
    )

    with mock.patch.object(agent, "_emit_runtime_event") as emit:
        steps, _context, stop_reason, failed = controller.run_bounded_builder_loop(
            agent,
            task=SimpleNamespace(task_id="task-approval"),
            session_id="session-approval",
            effective_input="create approval-proof.txt",
            task_class="build",
            source_context={"runtime_event_stream_id": "stream-approval"},
            initial_payloads=[
                {
                    "intent": "workspace.write_file",
                    "arguments": {"path": "approval-proof.txt", "content": "proof"},
                }
            ],
            plan_tool_workflow_fn=lambda **_kwargs: None,
            execute_tool_intent_fn=lambda *_args, **_kwargs: execution,
        )

    assert len(steps) == 1
    assert failed is execution
    assert stop_reason == "tool_preview:pending_approval"
    assert emit.call_args_list[0].kwargs["event_type"] == "tool_selected"
    preview = emit.call_args_list[1].kwargs
    assert preview["event_type"] == "tool_preview"
    assert preview["message"] == "Approval required before creating approval-proof.txt."
    assert preview["tool_name"] == "workspace.write_file"
    assert preview["status"] == "pending_approval"
    assert preview["mode"] == "tool_preview"
    assert preview["ok"] is False
    assert preview["approval_request"]["token"] == "[redacted]"
    assert "approval-token" not in repr(preview)
