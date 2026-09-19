from __future__ import annotations

from unittest import mock

import pytest

from apps.vool_agent import ChatTurnResult, ResponseClass, VoolAgent
from core.agent_runtime import response
from core.ordinary_chat_response_guard import (
    ordinary_chat_output_policy,
    prior_turn_literal_hashes,
)
from core.raw_output_contract import parse_raw_output_contract
from tests.test_raw_output_contract import (
    NEW_CLEAN_UNRESTRICTED_CASES,
    NEW_SLOPPY_UNRESTRICTED_CASES,
)


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_turn_result_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    result = agent._turn_result(
        "  hello world  ",
        ResponseClass.GENERIC_CONVERSATION,
        workflow_summary=" step one ",
        debug_origin="test",
        allow_planner_style=True,
    )

    assert result == response.turn_result(
        ChatTurnResult,
        "  hello world  ",
        ResponseClass.GENERIC_CONVERSATION,
        workflow_summary=" step one ",
        debug_origin="test",
        allow_planner_style=True,
    )


def test_shape_user_facing_text_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    turn = ChatTurnResult(text="raw reply", response_class=ResponseClass.GENERIC_CONVERSATION)

    with mock.patch(
        "core.agent_runtime.response.shape_user_facing_text",
        return_value="delegated text",
    ) as shape_user_facing_text:
        result = agent._shape_user_facing_text(turn)

    assert result == "delegated text"
    shape_user_facing_text.assert_called_once_with(agent, turn)


def test_decorate_chat_response_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    turn = ChatTurnResult(text="raw reply", response_class=ResponseClass.GENERIC_CONVERSATION)

    with mock.patch(
        "core.agent_runtime.response.decorate_chat_response",
        return_value="decorated text",
    ) as decorate_chat_response:
        result = agent._decorate_chat_response(
            turn,
            session_id="session-123",
            source_context={"surface": "openclaw"},
            workflow_summary="ignored summary",
            include_hive_footer=False,
        )

    assert result == "decorated text"
    decorate_chat_response.assert_called_once_with(
        agent,
        turn,
        session_id="session-123",
        source_context={"surface": "openclaw"},
        workflow_summary="ignored summary",
        include_hive_footer=False,
    )


def test_final_ui_validation_preserves_typed_same_chat_memory_recall() -> None:
    agent = _build_agent()
    current_user_text = "What exact identifier did I just ask you to remember?"
    source_context = {
        "surface": "openclaw",
        "ordinary_chat_output_policy": ordinary_chat_output_policy(
            prompt_profile="chat_minimal",
            output_mode="plain_text",
            user_text=current_user_text,
            prior_turn_literal_hashes=prior_turn_literal_hashes(
                [
                    {
                        "role": "user",
                        "content": "Remember this exact identifier: KAS-ALPHA-8841.",
                    }
                ],
                current_user_text=current_user_text,
            ),
        ),
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="KAS-ALPHA-8841",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:typed-memory-recall",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert decorated == "KAS-ALPHA-8841"
    assert source_context["response_control"]["final_ui"]["ordinary_chat_output"]["allowed"] is True


def test_strip_planner_leakage_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    payload = '{"summary":"Here\'s what I’d suggest: claim the task","bullets":["post progress","deliver result"]}'

    assert agent._strip_planner_leakage(payload) == response.strip_planner_leakage(agent, payload)


def test_orchestration_failure_text_is_humanized_for_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "coder envelope `coder-1` is not allowed to run `workspace.write_file` because it lacks "
                'capability `workspace.write`.\n\n{"task_envelope":{"task_id":"coder-1","tool_permissions":["workspace.read"]}}'
            ),
            response_class=ResponseClass.TASK_FAILED_USER_SAFE,
        ),
        session_id="openclaw:orchestration-failure",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "permissions did not allow the requested action" in decorated.lower()
    assert "coder envelope" not in decorated.lower()
    assert "task_envelope" not in decorated.lower()
    assert "workspace.write_file" not in decorated.lower()


def test_orchestration_success_text_is_humanized_for_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="queen envelope `queen-1` completed merge.",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:orchestration-success",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == "I finished the bounded multi-step run."


def test_bounded_response_is_validated_after_chat_decoration() -> None:
    agent = _build_agent()
    source_context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "response_constraint": {
            "exact_words": 1,
            "max_words": 1,
            "exact_sentences": None,
            "max_sentences": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="Calm and focused.",
            response_class=ResponseClass.GENERIC_CONVERSATION,
            workflow_summary="A workflow summary that must not reach the constrained reply.",
        ),
        session_id="openclaw:bounded-response",
        source_context=source_context,
        include_hive_footer=True,
    )

    assert decorated == "Calm"
    assert source_context["response_constraint_final"] == {
        "compliant": True,
        "structurally_trimmed": True,
        "violations": [],
        "fallback_applied": False,
    }


def test_raw_output_contract_strips_trailing_scratchpad_after_decoration() -> None:
    agent = _build_agent()
    source_context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "workflow_debug": True,
        "raw_output_contract": {
            "raw_only": True,
            "no_markdown": True,
            "no_internal_thought": True,
            "no_json": False,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "Snow rests on pine\nMoonlight crosses fields\nDawn warms frozen air\n\n"
                "Identify core concept...\nSelect concrete imagery...\nVerify syllable counts..."
            ),
            response_class=ResponseClass.APPROVAL_REQUIRED,
            workflow_summary="This must not be attached.",
        ),
        session_id="openclaw:raw-output",
        source_context=source_context,
        include_hive_footer=True,
    )

    assert decorated == "Snow rests on pine\nMoonlight crosses fields\nDawn warms frozen air"
    assert source_context["response_control"]["raw_output_final"]["actions"] == [
        "trailing_scratchpad_removed"
    ]


def test_ordinary_explanation_keeps_imperative_checklist_without_raw_contract() -> None:
    agent = _build_agent()
    answer = (
        "Use three checks:\n\n"
        "Identify the boundary.\n"
        "Verify the payload.\n"
        "Confirm the receipt."
    )

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=answer,
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:ordinary-checklist",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        include_hive_footer=False,
    )

    assert decorated == answer


@pytest.mark.parametrize(
    ("prompt", "answer"),
    (*NEW_CLEAN_UNRESTRICTED_CASES, *NEW_SLOPPY_UNRESTRICTED_CASES),
)
def test_unrestricted_outputs_cross_the_final_visible_boundary_unchanged(
    prompt: str,
    answer: str,
) -> None:
    agent = _build_agent()
    source_context = {"surface": "openclaw", "platform": "openclaw"}

    contract = parse_raw_output_contract(prompt)
    if contract is not None:
        source_context["raw_output_contract"] = contract.to_dict()
    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=answer,
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id=f"unrestricted:{abs(hash(prompt))}",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert contract is None
    assert "raw_output_contract" not in source_context
    assert decorated == answer


def test_raw_output_contract_rejects_the_payload_and_reports_instead_of_a_silent_empty() -> None:
    """The rejection stands -- the ambiguous JSON payload never ships under the contract -- but
    the turn no longer ends as a silent empty string. Measured on the served surface: a
    1,136-token deliverable rendered as "" with 'Completed task with final response:' and nothing
    after it. A withheld draft now yields the runtime's own report, marked
    `runtime_notice_not_an_answer` so no answer-shape contract is re-applied to it."""

    agent = _build_agent()
    source_context = {
        "raw_output_contract": {
            "raw_only": True,
            "no_markdown": False,
            "no_internal_thought": True,
            "no_json": True,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text='{"answer":"blue","reasoning":"private scratchpad"}',
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="raw-output:reject-json",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert '"blue"' not in decorated
    assert "scratchpad" not in decorated
    assert decorated.strip(), "a withheld draft must yield a report, never a silent empty"
    assert source_context["response_control"]["raw_output_final"]["rejected"] is True
    assert source_context["runtime_notice_not_an_answer"] is True


def test_exact_literal_contract_wins_at_the_final_visible_boundary() -> None:
    agent = _build_agent()
    source_context = {
        "response_constraint": {
            "exact_words": 1,
            "max_words": 1,
            "exact_sentences": None,
            "max_sentences": None,
        },
        "raw_output_contract": {
            "raw_only": True,
            "no_markdown": True,
            "no_internal_thought": False,
            "no_json": False,
            "no_punctuation": True,
            "no_title": False,
            "exact_text": "cobalt",
            "exact_words": 1,
            "exact_sentences": None,
            "exact_lines": None,
            "bullet_count": None,
            "bullet_marker": None,
            "delimiter": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="one word, no punctuation, no markdown, no explanation: cobalt",
            response_class=ResponseClass.GENERIC_CONVERSATION,
            workflow_summary="Must never appear.",
        ),
        session_id="raw-output:exact-literal",
        source_context=source_context,
        include_hive_footer=True,
    )

    assert decorated == "cobalt"
    assert source_context["response_control"]["raw_output_final"]["compliant"] is True


def test_exact_bullet_syntax_is_bound_after_all_chat_decoration() -> None:
    agent = _build_agent()
    source_context = {
        "workflow_debug": True,
        "raw_output_contract": {
            "raw_only": True,
            "no_markdown": False,
            "no_internal_thought": False,
            "no_json": False,
            "no_punctuation": False,
            "no_title": False,
            "exact_text": None,
            "exact_words": None,
            "exact_sentences": None,
            "exact_lines": None,
            "bullet_count": 3,
            "bullet_marker": "*",
            "delimiter": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="- Quartz\n- Feldspar\n- Mica",
            response_class=ResponseClass.APPROVAL_REQUIRED,
            workflow_summary="Must never appear.",
        ),
        session_id="raw-output:bullet-marker",
        source_context=source_context,
        include_hive_footer=True,
    )

    assert decorated == "* Quartz\n* Feldspar\n* Mica"
    assert source_context["response_control"]["raw_output_final"]["actions"] == [
        "bullet_markers_normalized"
    ]


def test_explicit_json_survives_the_final_visible_boundary() -> None:
    agent = _build_agent()
    payload = '{"status":"ready","items":["ash","elm"]}'
    source_context = {
        "raw_output_contract": {
            "raw_only": True,
            "no_markdown": False,
            "no_internal_thought": False,
            "no_json": False,
            "no_punctuation": False,
            "no_title": False,
            "exact_text": None,
            "exact_words": None,
            "exact_sentences": None,
            "exact_lines": None,
            "bullet_count": None,
            "bullet_marker": None,
            "delimiter": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=payload,
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="raw-output:json-negative-control",
        source_context=source_context,
        include_hive_footer=True,
    )

    assert decorated == payload
    assert source_context["response_control"]["raw_output_final"] == {
        "changed": False,
        "rejected": False,
        "compliant": True,
        "violations": [],
        "actions": [],
    }


def test_explicit_markdown_survives_the_final_visible_boundary() -> None:
    agent = _build_agent()
    payload = "```markdown\n# Heading\n\n- item\n```"
    source_context = {
        "raw_output_contract": {
            "raw_only": True,
            "no_markdown": False,
            "no_internal_thought": False,
            "no_json": False,
            "no_punctuation": False,
            "no_title": False,
            "exact_text": None,
            "exact_words": None,
            "exact_sentences": None,
            "exact_lines": None,
            "bullet_count": None,
            "bullet_marker": None,
            "delimiter": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=payload,
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="raw-output:markdown-negative-control",
        source_context=source_context,
        include_hive_footer=True,
    )

    assert decorated == payload


def test_final_constraint_trace_keeps_incomplete_fragment_noncompliant() -> None:
    agent = _build_agent()
    source_context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "response_constraint": {
            "exact_words": 2,
            "max_words": 2,
            "exact_sentences": None,
            "max_sentences": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="Clear and",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:incomplete-fragment",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert decorated == "No answer."
    assert source_context["response_constraint_final"] == {
        "compliant": False,
        "structurally_trimmed": False,
        "violations": ["incomplete_fragment"],
        "fallback_applied": True,
    }


def test_final_constraint_trace_does_not_trim_a_refusal_into_a_fake_answer() -> None:
    agent = _build_agent()
    source_context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "response_constraint": {
            "exact_words": 2,
            "max_words": 2,
            "exact_sentences": None,
            "max_sentences": None,
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="I couldn't produce a complete response within the requested format.",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:refusal-fallback",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert decorated == "No answer."
    assert source_context["response_constraint_final"] == {
        "compliant": False,
        "structurally_trimmed": True,
        "violations": ["non_answer_refusal"],
        "fallback_applied": True,
    }


def test_chat_surface_blocks_image_prompt_output_before_memory_persistence() -> None:
    agent = _build_agent()
    source_context = {
        "ordinary_chat_output_policy": {
            "mode": "ordinary_chat",
            "prompt_profile": "chat_minimal",
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "Subject: a library card. Camera: wide shot, 35mm lens. "
                "Lighting: cinematic blue hour with film grain."
            ),
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="ordinary-chat-image-output",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert decorated == "I couldn't produce a normal chat response for that request. Please try again."
    assert source_context["response_control"]["final_ui"]["fallback_applied"] is True


def test_chat_surface_blocks_clear_unexpected_language_before_memory_persistence() -> None:
    agent = _build_agent()
    source_context = {
        "response_language_policy": {
            "expected_language": "en",
            "reason": "english_user_turn",
        },
    }

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="使用索引可以减少扫描。",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="ordinary-chat-language-output",
        source_context=source_context,
        include_hive_footer=False,
    )

    assert decorated == "I couldn't return that response in English. Please try again."
    assert source_context["response_control"]["final_ui"]["fallback_applied"] is True


def test_capacity_blocked_text_is_humanized_for_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "coder envelope `coder-remote-lane` is blocked by provider-capacity policy: "
                "requires_local_provider.\n\n"
                '{"capacity_state":{"availability_state":"blocked","notes":["requires_local_provider"]}}'
            ),
            response_class=ResponseClass.TASK_FAILED_USER_SAFE,
        ),
        session_id="openclaw:capacity-blocked",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "local execution requirements" in decorated.lower()
    assert "capacity_state" not in decorated.lower()
    assert "requires_local_provider" not in decorated.lower()


def test_routing_payload_is_stripped_from_generic_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                '{"selection_notes":["Queue-depth pressure was applied while scoring provider candidates."],'
                '"rejected_candidates":[{"provider_id":"kimi:k2","reason":"requires_local_provider"}],'
                '"routing_requirements":{"required_locality":"local"}}'
            ),
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:routing-payload",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == "I finished the work and stripped the internal routing details from the reply."


def test_mixed_user_content_survives_orchestration_payload_removal() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "The requested file was written successfully.\n\n"
                '{"selection_notes":["internal provider scoring"],"routing_requirements":{"required_locality":"local"}}'
            ),
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:mixed-routing-payload",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == "The requested file was written successfully."


@pytest.mark.parametrize(
    "fragment",
    [
        '{"task_envelope":{"task_id":"queen-1"}}',
        '{"tool_permissions":["workspace.read"]}',
        '{"model_constraints":{"max_tokens":128}}',
        '{"latency_budget":{"seconds":30}}',
        '{"quality_target":"grounded"}',
        '{"allowed_side_effects":[]}',
        '{"required_receipts":["validation"]}',
        '{"merge_strategy":"winner"}',
        '{"cancellation_policy":"stop"}',
        '{"privacy_class":"local"}',
        '{"routing_requirements":{"required_locality":"local"}}',
        '{"rejected_candidates":[{"provider_id":"kimi:k2"}]}',
        '{"selection_notes":["internal provider scoring"]}',
        '{"provider_capability_truth":{"local":true}}',
        '{"queue_pressure_strategy":"least_busy"}',
        '{"required_locality":"local"}',
        '{"preferred_locality":"local"}',
        '{"preferred_provider_role":"coder"}',
        '{"effective_swarm_size":1}',
        '{"capacity_backoff_applied":true}',
        '{"capacity_backoff_notes":["bounded"]}',
        '{"capacity_blocked":false}',
        '{"capacity_state":{"availability_state":"ready"}}',
        '{"scheduled_children":[]}',
        '{"merged_result":{}}',
        '{"step_results":[]}',
        '{"graph":{}}',
    ],
)
def test_mixed_model_answer_survives_every_orchestration_fragment(fragment: str) -> None:
    agent = _build_agent()
    answer = "4821 * 37 = 178377."

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=f"{answer}\n\n{fragment}",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:mixed-orchestration-fragment",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == answer
    assert not any(marker in decorated.lower() for marker in ("task_envelope", "capacity_state", "routing_requirements"))


def test_mixed_model_answer_survives_envelope_status_fragment() -> None:
    agent = _build_agent()
    answer = "4821 * 37 = 178377."

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=f"{answer}\n\ncoder envelope `coder-1` completed merge.",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:mixed-envelope-status",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == answer


def test_safe_content_before_and_after_inline_orchestration_json_survives() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                'The calculation is 178377. {"task_envelope":{"task_id":"queen-1",'
                '"capacity_state":{"availability_state":"ready"}}} You can use that result.'
            ),
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:inline-orchestration-fragment",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == "The calculation is 178377. You can use that result."


def test_grounded_workspace_search_result_is_not_mistaken_for_routing_leak() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                'Search matches for "provider_capability_truth":\n'
                "- notes.md:1 provider_capability_truth is documented here"
            ),
            response_class=ResponseClass.UTILITY_ANSWER,
        ),
        session_id="openclaw:workspace-search-provider-capability-truth",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated.startswith('Search matches for "provider_capability_truth"')
    assert "stripped the internal routing details" not in decorated.lower()
