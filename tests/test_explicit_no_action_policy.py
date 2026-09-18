from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime.fast_paths_machine import looks_like_safe_machine_write_request
from core.agent_runtime.intent_claims import ActionPolicy, action_policy_for_text
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import OperatingMode, PermissionAction, PermissionDecision, PermissionEffect
from core.tool_intent_executor import execute_tool_intent

NO_WRITE_PROMPT = (
    "I am thinking out loud: why do people organize Downloads folders? "
    "Do not list, open, or change anything."
)
NO_ACTION_PROMPT = "Do not take action: what makes workspace search useful?"


def _agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_explicit_no_action_language_becomes_typed_forbidden_policy() -> None:
    assert action_policy_for_text(NO_ACTION_PROMPT) is ActionPolicy.FORBIDDEN
    assert action_policy_for_text(NO_WRITE_PROMPT) is ActionPolicy.FORBIDDEN
    assert action_policy_for_text(
        "Do not run a search. Conceptually, why are indexes useful?"
    ) is ActionPolicy.FORBIDDEN
    assert action_policy_for_text("search this workspace for the router") is ActionPolicy.ALLOWED


def test_bounded_create_request_is_not_mistaken_for_a_no_action_turn() -> None:
    assert action_policy_for_text(
        "Create exactly three files: a.txt, b.txt, c.txt. Do not create anything else."
    ) is ActionPolicy.ALLOWED


def test_negated_change_verb_is_not_a_safe_machine_write_request() -> None:
    assert not looks_like_safe_machine_write_request(NO_WRITE_PROMPT)
    assert looks_like_safe_machine_write_request("create a notes.txt file in Downloads with text: hello")


def test_run_once_overrides_caller_policy_with_server_derived_no_action_policy() -> None:
    agent = _agent()
    seen: dict[str, object] = {}

    def capture_frontdoor(**kwargs):
        seen.update(dict(kwargs["source_context"]))
        return {"result": {"response": "conversation continues", "success": True}}

    with mock.patch.object(agent, "_handle_turn_frontdoor", side_effect=capture_frontdoor):
        result = agent.run_once(
            NO_ACTION_PROMPT,
            session_id_override="no-action-run-once",
            source_context={"surface": "api", "action_policy": "allowed"},
        )

    assert result["response"] == "conversation continues"
    assert seen["action_policy"] == ActionPolicy.FORBIDDEN.value


def test_run_once_returns_redacted_turn_receipts_to_the_calling_context() -> None:
    agent = _agent()
    caller_context: dict[str, object] = {"surface": "api"}

    def capture_frontdoor(**kwargs):
        turn_context = kwargs["source_context"]
        turn_context["context_manifest_id"] = "context-manifest-test-001"
        turn_context["context_manifest_trace_id"] = "trace-test-001"
        turn_context["provider_manifest_links"] = [
            {
                "context_manifest_id": "context-manifest-test-001",
                "context_manifest_trace_id": "trace-test-001",
                "provider_manifest_id": "provider-manifest-test-002",
                "payload_hash": "a" * 64,
                "provider_id": "ollama",
                "model_id": "qwen2.5:7b",
            }
        ]
        return {"result": {"response": "conversation continues", "success": True}}

    with mock.patch.object(agent, "_handle_turn_frontdoor", side_effect=capture_frontdoor):
        agent.run_once(
            NO_ACTION_PROMPT,
            session_id_override="no-action-receipt-propagation",
            source_context=caller_context,
        )

    assert caller_context["context_manifest_id"] == "context-manifest-test-001"
    assert caller_context["context_manifest_trace_id"] == "trace-test-001"
    assert caller_context["provider_manifest_links"] == [
        {
            "context_manifest_id": "context-manifest-test-001",
            "context_manifest_trace_id": "trace-test-001",
            "provider_manifest_id": "provider-manifest-test-002",
            "payload_hash": "a" * 64,
            "provider_id": "ollama",
            "model_id": "qwen2.5:7b",
        }
    ]


def test_frontdoor_skips_action_capable_machine_fast_paths_when_forbidden() -> None:
    agent = _agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8, topic_hints=())
    blocked = (
        "_maybe_handle_direct_machine_download_request",
        "_maybe_handle_direct_machine_write_request",
        "_maybe_handle_safe_machine_write_guard",
        "_maybe_handle_direct_machine_read_request",
        "_maybe_handle_direct_workspace_runtime_request",
    )
    patches = [mock.patch.object(agent, name, side_effect=AssertionError(name)) for name in blocked]
    with patches[0], patches[1], patches[2], patches[3], patches[4], mock.patch(
        "core.agent_runtime.turn_frontdoor._maybe_arbitrate_intent",
        side_effect=AssertionError("intent arbiter"),
    ), mock.patch(
        "core.agent_runtime.fast_paths_media.maybe_handle_image_generation",
        side_effect=AssertionError("image generation"),
    ):
        result = agent._handle_turn_frontdoor(
            raw_user_input=NO_WRITE_PROMPT,
            effective_input=NO_WRITE_PROMPT,
            normalized_input=NO_WRITE_PROMPT,
            source_surface="api",
            session_id="no-action-frontdoor",
            source_context={"surface": "api", "action_policy": ActionPolicy.FORBIDDEN.value},
            persona=mock.sentinel.persona,
            interpreted=interpreted,
        )

    assert result == {"result": None}


def test_frontdoor_skips_cloud_actions_hive_and_builder_when_forbidden() -> None:
    agent = _agent()
    interpreted = SimpleNamespace(understanding_confidence=0.8, topic_hints=())
    with mock.patch(
        "core.agent_runtime.fast_command_surface.maybe_handle_cloud_switch_intent",
        side_effect=AssertionError("cloud switch"),
    ), mock.patch(
        "core.agent_runtime.fast_command_surface.maybe_handle_catalog_refresh_intent",
        side_effect=AssertionError("catalog refresh"),
    ), mock.patch(
        "core.agent_runtime.fast_command_surface.maybe_handle_connection_test_intent",
        side_effect=AssertionError("connection test"),
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_frontdoor",
        side_effect=AssertionError("hive frontdoor"),
    ), mock.patch.object(
        agent,
        "_maybe_handle_web0_builder_fast_path",
        side_effect=AssertionError("web0 builder"),
    ):
        result = agent._handle_turn_frontdoor(
            raw_user_input="Do not take action: refresh the cloud catalog.",
            effective_input="Do not take action: refresh the cloud catalog.",
            normalized_input="Do not take action: refresh the cloud catalog.",
            source_surface="api",
            session_id="no-action-cloud-actions",
            source_context={"surface": "api", "action_policy": ActionPolicy.FORBIDDEN.value},
            persona=mock.sentinel.persona,
            interpreted=interpreted,
        )

    assert result == {"result": None}


def test_tool_boundary_rejects_model_proposal_before_approval_creation() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    with mock.patch("core.tool_intent_executor.decide_tool_call") as decide_tool_call:
        result = execute_tool_intent(
            {"intent": "web.search", "arguments": {"query": "workspace search"}},
            task_id="no-action-task",
            session_id="no-action-session",
            source_context={"surface": "api", "action_policy": ActionPolicy.FORBIDDEN.value},
            hive_activity_tracker=tracker,
        )

    assert result.handled is True
    assert result.ok is False
    assert result.status == "blocked_by_action_policy"
    assert result.mode == "tool_failed"
    assert result.details["executed"] is False
    decide_tool_call.assert_not_called()


def test_tool_loop_does_not_construct_a_model_tool_payload_when_forbidden() -> None:
    agent = _agent()

    result = agent._maybe_execute_model_tool_intent(
        task=mock.sentinel.task,
        effective_input=NO_ACTION_PROMPT,
        classification={},
        interpretation=mock.sentinel.interpretation,
        context_result=mock.sentinel.context_result,
        persona=mock.sentinel.persona,
        session_id="no-action-tool-loop",
        source_context={"action_policy": ActionPolicy.FORBIDDEN.value},
        surface="api",
    )

    assert result is None


def test_operator_dispatch_is_not_attempted_when_turn_forbids_actions() -> None:
    from core.agent_runtime.turn_dispatch import prepare_turn_task_bundle

    agent = _agent()
    operator = SimpleNamespace(kind="run_command")
    with mock.patch.object(agent, "_resolve_runtime_task") as resolve_task, mock.patch.object(
        agent, "_update_runtime_checkpoint_context"
    ), mock.patch.object(agent, "_update_task_class"), mock.patch.object(
        agent, "_emit_runtime_event"
    ), mock.patch.object(agent, "_maybe_handle_hive_create_confirmation", return_value=None), mock.patch.object(
        agent, "_extract_hive_topic_create_draft", return_value=None
    ), mock.patch.object(agent, "_maybe_handle_hive_topic_mutation_request", return_value=None), mock.patch.object(
        agent, "_maybe_handle_hive_topic_create_request", return_value=None
    ), mock.patch.object(agent, "_maybe_run_builder_controller", return_value=None) as builder:
        task = SimpleNamespace(task_id="blocked-op-task", task_summary="blocked", environment_os="", environment_shell="", environment_runtime="", environment_version_hint="")
        resolve_task.return_value = task
        dispatch = mock.Mock(side_effect=AssertionError("forbidden operator must not dispatch"))
        result = prepare_turn_task_bundle(
            agent,
            effective_input="Run sudo rm -rf /, then explain data permanence. Do not search the web.",
            user_input="Run sudo rm -rf /, then explain data permanence. Do not search the web.",
            session_id="blocked-op-session",
            source_context={"surface": "api", "action_policy": ActionPolicy.FORBIDDEN.value},
            interpreted=SimpleNamespace(as_context=lambda: {}),
            classify_fn=lambda *_args, **_kwargs: {"task_class": "multi_part_qa"},
            parse_channel_post_intent_fn=lambda _text: (None, ""),
            dispatch_outbound_post_intent_fn=mock.Mock(),
            parse_operator_action_intent_fn=lambda _text: operator,
            dispatch_operator_action_fn=dispatch,
        )

    assert result["task"] is task
    dispatch.assert_not_called()
    builder.assert_not_called()


def test_no_action_policy_disables_adaptive_research_before_any_remote_fetch() -> None:
    agent = _agent()
    with mock.patch.object(agent.curiosity, "adaptive_research") as adaptive_research:
        result = agent._collect_adaptive_research(
            task_id="no-action-research",
            query_text=NO_ACTION_PROMPT,
            classification={},
            interpretation=mock.sentinel.interpretation,
            source_context={"action_policy": ActionPolicy.FORBIDDEN.value},
        )

    assert result.enabled is False
    assert result.reason == "action_policy_forbidden"
    adaptive_research.assert_not_called()


def test_explicit_tool_request_keeps_the_existing_approval_path() -> None:
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    # The gate is unconditional now, so there is nothing left to switch on: the old
    # `mode_policy_is_active` patch that used to force it is gone with the seam it controlled.
    with mock.patch(
        "core.tool_intent_executor.decide_tool_call",
        return_value=PermissionDecision(
            effect=PermissionEffect.REQUIRE_APPROVAL,
            mode=OperatingMode.MANUAL,
            actions=(PermissionAction.USE_BROWSER,),
            reason="Manual mode requires approval for this exact action.",
            approval_request={"approval_id": "approval-1", "affected_resources": []},
        ),
    ):
        result = execute_tool_intent(
            {"intent": "web.search", "arguments": {"query": "workspace search"}},
            task_id="ordinary-action-task",
            session_id="ordinary-action-session",
            source_context={"surface": "api", "action_policy": ActionPolicy.ALLOWED.value},
            hive_activity_tracker=tracker,
        )

    assert result.status == "pending_approval"
    assert result.mode == "tool_preview"
