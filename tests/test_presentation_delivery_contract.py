"""Explicit visual requests use the renderer contract, without widening paid authority."""
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from core.memory_first_router import MemoryFirstRouter, _response_constraint_guidance
from core.response_constraints import parse_response_constraint
from tests.test_response_constraint_router import _certified_manifest


@pytest.mark.parametrize("prompt", [
    "Show the portfolio allocations as a chart",
    "Draw the tank volumes as a horizontal bar chart",
])
def test_explicit_chart_guidance_requires_real_chart_payload(prompt):
    guidance = _response_constraint_guidance(parse_response_constraint(prompt))
    assert "fenced chart JSON" in guidance
    assert "no chart renderer" not in guidance
    assert "never invent" in guidance


def test_mermaid_guidance_matches_local_renderer():
    guidance = _response_constraint_guidance(parse_response_constraint("Diagram the shipment route in mermaid"))
    assert "fenced mermaid" in guidance
    assert "renders" in guidance
    assert "shows diagram source" not in guidance
    assert "structure, not completed activity" in guidance
    assert "supplied facts or observed results" in guidance
    assert "without inventing execution or outcomes" in guidance


@pytest.mark.parametrize("cost,expected_calls", [("free_cloud", 2), ("paid_cloud", 0)])
def test_explicit_chart_retry_respects_cloud_cost_class(cost, expected_calls):
    prompt = "Show the tank volumes as a bar chart"
    chart = '```chart\n{"type":"bar","data":{"labels":["A","B"],"datasets":[{"label":"litres","data":[336,624]}]}}\n```'
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Tank A contains 336 litres; tank B contains 624 litres."),
        ModelResponse(output_text=chart),
    ]
    manifest = _certified_manifest(local=False)
    manifest.metadata["cost_class"] = cost
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = ModelRequest(task_kind="conversation", prompt=prompt,
        messages=[{"role": "user", "content": prompt}], metadata={
            "response_constraint": parse_response_constraint(prompt).to_dict(),
            "defer_stream_until_verified": True})
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
         mock.patch("core.memory_first_router.circuit_is_open", return_value=False), \
         mock.patch("core.memory_first_router.is_verified_free_cloud_manifest", return_value=cost == "free_cloud"):
        _, response, error = router._invoke_manifest(manifest=manifest, request=request,
            output_mode="plain_text", task=SimpleNamespace(task_id="explicit-visual"), source_context={})
    assert error is None if expected_calls else error == "paid_call_not_authorized_or_reserved"
    assert adapter.run_text_task.call_count == expected_calls
    if expected_calls == 2:
        assert "```chart" in response.output_text


DEPOT_PROMPT = (
    "Presentation-only comparison. Use these supplied results without recalculating them: "
    "North depot has 144 cartons; South depot has 216 cartons; total inventory is 360 cartons. "
    "Produce one compact table with a total row, one bar chart comparing the two depots, "
    "and a short Mermaid diagram showing Receiving -> Inspection -> North depot / South depot. "
    "Finish with one concise sentence. Preserve the exact numbers. No web search, file writes, "
    "tools, or settings changes. Do not include self-checking narration. If this interface "
    "cannot display a requested visual, say precisely which visual is unavailable."
)
NOVEL_PROMPT = (
    "Use my measurements: batch Amber yielded 18 samples and batch Violet yielded 42 samples. "
    "Present a concise table and a bar chart, then Mermaid showing Collection -> Lab -> Archive. "
    "End with one short sentence. Retain these exact values. Do not browse."
)


@pytest.mark.parametrize("prompt", [DEPOT_PROMPT, NOVEL_PROMPT])
def test_explicit_presentation_overrides_saved_style_for_this_turn(prompt):
    from core.bootstrap_context import _conversation_preference_text

    prefs = SimpleNamespace(humor_percent=25, boundaries_mode="standard",
        profanity_level=0, character_mode="", style_notes="Plain text only; no Markdown.")
    with mock.patch("core.bootstrap_context.load_preferences", return_value=prefs):
        assert "style_notes=" not in _conversation_preference_text(prompt)
        assert "humor=25" in _conversation_preference_text(prompt)
        assert "Plain text only" in _conversation_preference_text("Explain rainfall.")
    assert prefs.style_notes == "Plain text only; no Markdown."


@pytest.mark.parametrize("prompt", [DEPOT_PROMPT, NOVEL_PROMPT])
def test_presentation_budget_is_not_short_chat_ceiling(prompt):
    from core.prompt_normalizer import _generation_profile

    profile = _generation_profile(surface="openclaw", task_kind="conversation",
        output_mode="plain_text", task_class="chat_conversation", user_text=prompt)
    assert profile["max_output_tokens"] >= 1800
    assert profile["output_budget_intent"]["ceiling"] == 0
    assert not profile["stop_sequences"]


def test_presentation_budget_does_not_widen_explicit_word_limit():
    from core.prompt_normalizer import _generation_profile

    profile = _generation_profile(surface="openclaw", task_kind="conversation",
        output_mode="plain_text", task_class="chat_conversation",
        user_text="Compare pears and apples in a table. Use at most 20 words.")
    assert profile["max_output_tokens"] <= 520


@pytest.mark.parametrize("reasoning", ["Planning a depot comparison.", "Determine the laboratory diagram layout."])
def test_reasoning_cannot_be_a_duplicate_final_answer(reasoning):
    from adapters.openai_compatible_adapter import _extract_openai_text

    def payload(content, thought):
        return {"choices": [{"finish_reason": "length", "message": {
            "content": content, "reasoning": thought}}]}

    assert _extract_openai_text(payload(reasoning, reasoning)) == ""
    assert _extract_openai_text(payload(None, reasoning)) == ""
    assert _extract_openai_text(payload("A genuine final answer.", reasoning)) == "A genuine final answer."
    assert _extract_openai_text(payload(reasoning, None)) == reasoning


@pytest.mark.parametrize("prompt", [DEPOT_PROMPT, NOVEL_PROMPT])
def test_supplied_visuals_do_not_require_external_retrieval(prompt):
    from core.execution_requirements import classify_requirements
    requirements = classify_requirements(prompt)
    assert not requirements.current_information_required
    assert not requirements.external_evidence_required


@pytest.mark.parametrize("extra", [
    " What is the current silver price?",
    " Independently verify these numbers with sources.",
    " Also give exact figures for Tesla deliveries this quarter.",
])
def test_preserving_values_does_not_disarm_independent_evidence_demands(extra):
    from core.execution_requirements import classify_requirements
    assert classify_requirements(DEPOT_PROMPT + extra).current_information_required


@pytest.mark.parametrize("prompt", [DEPOT_PROMPT, NOVEL_PROMPT])
def test_all_visual_obligations_survive_local_sentence_limit_and_metadata(prompt):
    from core.response_constraints import (
        check_response_constraint,
        response_constraint_from_metadata,
    )
    constraint = parse_response_constraint(prompt)
    assert constraint is not None
    assert set(constraint.requested_formats) == {"table", "chart", "mermaid"}
    assert constraint.max_sentences is None
    assert constraint.exact_sentences is None
    assert response_constraint_from_metadata({"response_constraint": constraint.to_dict()}) == constraint
    guidance = _response_constraint_guidance(constraint)
    assert "fenced chart JSON" in guidance and "fenced mermaid" in guidance
    table = "| Batch | Samples |\n| --- | --- |\n| Amber | 18 |\n| Violet | 42 |"
    chart = '```chart\n{"type":"bar","data":{"labels":["Amber","Violet"],"datasets":[{"label":"samples","data":[18,42]}]}}\n```'
    diagram = "```mermaid\nflowchart LR\nA[Collection] --> B[Lab] --> C[Archive]\n```"
    for missing in range(3):
        incomplete = "\n\n".join(part for index, part in enumerate((table, chart, diagram)) if index != missing)
        assert "presentation_format" in check_response_constraint(incomplete, constraint).violations
    assert check_response_constraint("\n\n".join((table, chart, diagram)), constraint).compliant


@pytest.mark.parametrize("prompt", [
    DEPOT_PROMPT,
    "Summarize my orchard counts in a table: plums 27, pears 63. No web search or configuration changes.",
])
def test_negative_tool_clause_cannot_select_configuration_work(prompt):
    from core.task_router import classify, model_execution_profile
    classification = classify(prompt)
    assert classification["task_class"] not in {"config", "debugging", "dependency_resolution"}
    assert model_execution_profile(classification["task_class"], chat_surface=True)["output_mode"] == "plain_text"


def test_positive_configuration_task_survives_web_prohibition():
    from core.task_router import classify
    assert classify("Configure my YAML settings. No web search.")["task_class"] == "config"


@pytest.mark.parametrize("prompt", [
    DEPOT_PROMPT,
    "Summarize plums 27 and pears 63 in a table. No browsing, file edits or tools.",
    "No tools. Explain the diagram.",
    "Do not use any tools. Explain photosynthesis.",
])
def test_all_tool_prohibition_reaches_planner_before_any_model_call(prompt):
    from core.agent_runtime.intent_claims import ActionPolicy, turn_action_constraints
    from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin
    from core.retrieval_constraints import analyze_retrieval_constraints

    assert analyze_retrieval_constraints(prompt).forbids_all_tools
    constraints = turn_action_constraints(prompt)
    assert constraints.policy is ActionPolicy.FORBIDDEN
    assert constraints.forbid_commands
    # No facade services exist: reaching checkpoint lookup or planning would fail.
    assert ResearchToolLoopFacadeMixin._maybe_execute_model_tool_intent(
        SimpleNamespace(), task=None, effective_input=prompt, classification={},
        interpretation=None, context_result=None, persona=None, session_id="no-tools",
        source_context={"action_policy": constraints.policy.value}, surface="chat",
    ) is None


@pytest.mark.parametrize("prompt", [
    "Create a local README. No browsing or web tools.",
    "Configure my YAML settings. No web search.",
    "No web search, but use tools to list the local files.",
    "Compare tables and tools for data analysis.",
])
def test_scoped_or_positive_tool_mentions_do_not_forbid_all_actions(prompt):
    from core.agent_runtime.intent_claims import ActionPolicy, turn_action_constraints
    from core.retrieval_constraints import analyze_retrieval_constraints

    assert not analyze_retrieval_constraints(prompt).forbids_all_tools
    assert turn_action_constraints(prompt).policy is ActionPolicy.ALLOWED


@pytest.mark.parametrize("question", [
    "What is the current copper price?",
    "Look up the latest release notes for Rust online.",
])
def test_classifier_keeps_lookup_authority_separate_from_positive_text(question):
    from core.task_router import classify

    assert classify(question)["task_class"] == "research"
    assert classify(question + " No tools or web search.")["task_class"] != "research"


@pytest.mark.parametrize("prompt", [DEPOT_PROMPT, NOVEL_PROMPT])
def test_visual_repair_cannot_invent_a_word_limit(prompt):
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="The supplied values are retained."),
        ModelResponse(output_text="The requested visuals could not be rendered."),
    ]
    manifest = _certified_manifest(local=False)
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = ModelRequest(task_kind="conversation", prompt=prompt,
        max_output_tokens=520, messages=[{"role": "user", "content": prompt}],
        metadata={"response_constraint": parse_response_constraint(prompt).to_dict(),
                  "defer_stream_until_verified": True})
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
         mock.patch("core.memory_first_router.circuit_is_open", return_value=False), \
         mock.patch("core.memory_first_router.is_verified_free_cloud_manifest", return_value=True):
        router._invoke_manifest(manifest=manifest, request=request, output_mode="plain_text",
            task=SimpleNamespace(task_id="visual-budget"), source_context={})
    assert adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.max_output_tokens == request.max_output_tokens


# ---------------------------------------------------------------------------------------------
# Measured on the packaged a651de73 app, 2026-09-11 (diagnostic capture at the adapter boundary,
# owner profile, nvidia/nemotron-3.5-lightning:free): both depot drafts were usable -- table with
# total row, ```mermaid flowchart, one sentence and a grammar-exact chart payload under a ```json
# label -- and the validator rejected them twice per turn because nothing had named the ```chart
# label the renderer reads.  The lab draft did the same in a bare ``` block and wrapped its
# ```mermaid block in a second fence.  The pins below are those drafts.
# ---------------------------------------------------------------------------------------------
from tests.test_presentation_fences import DEPOT_DRAFT, LAB_DRAFT, LAB_RETRY_DRAFT  # noqa: E402


@pytest.mark.parametrize("prompt,draft", [(DEPOT_PROMPT, DEPOT_DRAFT), (NOVEL_PROMPT, LAB_DRAFT)])
def test_captured_usable_drafts_pass_and_ship_canonical_fences(prompt, draft):
    from core.response_constraints import check_response_constraint, enforce_response_constraint

    constraint = parse_response_constraint(prompt)
    check = check_response_constraint(draft, constraint)
    assert check.compliant and check.missing_formats == ()
    applied = enforce_response_constraint(draft, constraint)
    assert applied.compliant and applied.fences_normalized
    assert "```chart\n{\"type\":\"bar\"" in applied.text
    assert "```json" not in applied.text
    assert applied.text.count("```mermaid") == 1 and "```\n```mermaid" not in applied.text
    for value in ("144", "216", "360") if prompt is DEPOT_PROMPT else ("18", "42"):
        assert value in applied.text


def test_bare_json_chart_still_fails_and_the_repair_names_the_missing_chart_label():
    from core.response_constraints import check_response_constraint, formatting_retry_instruction

    constraint = parse_response_constraint(NOVEL_PROMPT)
    check = check_response_constraint(LAB_RETRY_DRAFT, constraint)
    assert not check.compliant and check.missing_formats == ("chart",)
    instruction = formatting_retry_instruction(constraint, missing_formats=check.missing_formats)
    assert instruction.startswith("The previous draft did not satisfy the requested chart")
    assert "```chart" in instruction and "not json" in instruction and "```mermaid" in instruction
    preface = instruction.split("Answer the original", 1)[0]
    assert "requested chart (" in preface
    # Only the missing format is reported as missing; the satisfied table and diagram are not.
    assert "requested table" not in preface and "mermaid:" not in preface and "table:" not in preface


def test_guidance_names_the_exact_fence_labels_for_first_drafts():
    for prompt in (DEPOT_PROMPT, NOVEL_PROMPT):
        guidance = _response_constraint_guidance(parse_response_constraint(prompt))
        assert "opening fence line is exactly ```chart" in guidance
        assert "opening fence line is exactly ```mermaid" in guidance


@pytest.mark.parametrize("prompt,draft", [(DEPOT_PROMPT, DEPOT_DRAFT), (NOVEL_PROMPT, LAB_DRAFT)])
def test_router_accepts_captured_draft_without_a_retry_and_ships_chart_label(prompt, draft):
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [ModelResponse(output_text=draft, finish_reason="stop")]
    manifest = _certified_manifest(local=False)
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = ModelRequest(task_kind="conversation", prompt=prompt, max_output_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
        metadata={"response_constraint": parse_response_constraint(prompt).to_dict(),
                  "defer_stream_until_verified": True})
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
         mock.patch("core.memory_first_router.circuit_is_open", return_value=False), \
         mock.patch("core.memory_first_router.is_verified_free_cloud_manifest", return_value=True):
        _, response, error = router._invoke_manifest(manifest=manifest, request=request,
            output_mode="plain_text", task=SimpleNamespace(task_id="captured-draft"), source_context={})
    assert error is None
    assert adapter.run_text_task.call_count == 1
    assert "```chart\n{\"type\":\"bar\"" in response.output_text
    assert "No usable answer" not in response.output_text
    stage = response.constraint_result
    assert stage["retry_attempted"] is False and stage["compliant"] is True
    assert stage["fences_normalized"] is True and stage["initial_missing_formats"] == []
    assert stage["response_control"]["response_constraint"]["compliant"] is True


def test_router_retry_instruction_names_only_the_missing_format():
    prompt = NOVEL_PROMPT
    complete = LAB_DRAFT
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=LAB_RETRY_DRAFT, finish_reason="stop"),
        ModelResponse(output_text=complete, finish_reason="stop"),
    ]
    manifest = _certified_manifest(local=False)
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = ModelRequest(task_kind="conversation", prompt=prompt, max_output_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
        metadata={"response_constraint": parse_response_constraint(prompt).to_dict(),
                  "defer_stream_until_verified": True})
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
         mock.patch("core.memory_first_router.circuit_is_open", return_value=False), \
         mock.patch("core.memory_first_router.is_verified_free_cloud_manifest", return_value=True):
        _, response, error = router._invoke_manifest(manifest=manifest, request=request,
            output_mode="plain_text", task=SimpleNamespace(task_id="missing-chart"), source_context={})
    assert error is None and adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    instruction = retry_request.messages[-1]["content"]
    assert instruction.startswith("The previous draft did not satisfy the requested chart")
    assert "```chart" in instruction
    assert response.constraint_result["initial_missing_formats"] == ["chart"]
    assert response.constraint_result["retry_succeeded"] is True
    assert "```chart\n" in response.output_text


def test_empty_choices_error_carries_the_provider_error():
    from adapters.openai_compatible_adapter import EmptyProviderResponseError, _extract_openai_text

    payload = {"error": {"message": "Upstream error from Nvidia: Internal server error", "code": 502,
                         "metadata": {"error_type": "provider_unavailable"}}}
    with pytest.raises(EmptyProviderResponseError) as caught:
        _extract_openai_text(payload)
    message = str(caught.value)
    assert "did not include choices" in message
    assert "code 502" in message and "provider_unavailable" in message and "Upstream error from Nvidia" in message
    with pytest.raises(EmptyProviderResponseError) as bare:
        _extract_openai_text({"choices": []})
    assert "Provider error" not in str(bare.value)


@pytest.mark.parametrize("prompt", [DEPOT_PROMPT, NOVEL_PROMPT])
def test_presentation_inclusion_survives_final_turn_scope(prompt):
    from core.agent_runtime.agent import _constraint_is_unit_scoped
    constraint = parse_response_constraint(prompt)
    assert not _constraint_is_unit_scoped(prompt, constraint)


@pytest.mark.parametrize("body", [
    "Receiving --> Inspection --> North depot\nInspection --> South depot",
    "Collection --> Lab --> Archive",
    "%% a comment with no diagram declaration",
    '%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA-->B',
])
def test_mermaid_fence_alone_does_not_satisfy_diagram_delivery(body):
    from core.response_constraints import check_response_constraint
    constraint = parse_response_constraint("Show the process as Mermaid")
    assert check_response_constraint("```mermaid\n" + body + "\n```", constraint).missing_formats == ("mermaid",)
