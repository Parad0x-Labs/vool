"""Requested answer shape survives the actual routing and presentation owners."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import ChatTurnResult, ResponseClass
from core.agent_runtime import memory_runtime, response
from core.agent_runtime.runtime_checkpoint_lane_policy import model_routing_profile
from core.model_output_contracts import validate_contract
from core.prompt_normalizer import normalize_prompt
from core.raw_output_contract import parse_raw_output_contract
from core.reasoning_engine import build_plan, explicit_planner_style_requested
from core.runtime_task_outcome import terminal_fulfillment_outcome
from core.task_router import classify

FULL = "Restate the full numbered ceramic kiln preparation checklist you proposed, with every stage."
SOURCE = "Kiln preparation checklist:\n" + "\n".join(f"{i}. Check the independent condition {i}; retain exception {i}." for i in range(1,32))

def route(prompt):
    with mock.patch("core.task_router._classify_via_model", side_effect=AssertionError("offline chat routing must not classify with a model")):
        classification=classify(prompt,context={"source_surface":"api","source_platform":"api"})
        return model_routing_profile(SimpleNamespace(_is_chat_truth_surface=lambda context: True),user_input=prompt,classification=classification,interpretation=SimpleNamespace(as_context=lambda: {}),source_context={"surface":"api","platform":"api"})

def request(prompt):
    classification,profile=route(prompt)
    context=SimpleNamespace(local_candidates=[],swarm_metadata=[],retrieval_confidence_score=.9,assembled_context=lambda:SOURCE,context_snippets=lambda:[],report=SimpleNamespace(retrieval_confidence=.9,total_tokens_used=lambda:500,to_dict=lambda:{"external_evidence_attachments":[]}))
    normalized=normalize_prompt(task=SimpleNamespace(task_id="contract-test",task_summary=prompt),classification=classification,interpretation=SimpleNamespace(raw_text=prompt,normalized_text=prompt,topic_hints=[],understanding_confidence=.9),context_result=context,persona=SimpleNamespace(persona_id="default",display_name="VOOL",tone="direct"),output_mode=profile["output_mode"],task_kind=profile["task_kind"],trace_id="contract-test",surface="api",source_context={"surface":"api","platform":"api","conversation_history":[{"role":"assistant","content":SOURCE}]})
    return profile,normalized

@pytest.mark.parametrize("prompt",[FULL,"Repeat every numbered checklist stage from our earlier conversation.","What was the full checklist you suggested before?"])
def test_historical_complete_source_uses_its_answer_budget(prompt):
    profile,normalized=request(prompt)
    assert profile["output_mode"]=="plain_text"
    assert normalized.max_output_tokens==2048
    assert profile["requested_answer_contract"]["kind"]=="historical_restatement"

@pytest.mark.parametrize("prompt",["Give me a new step-by-step ceramic kiln preparation plan.","Make me an execution checklist for a new release."])
def test_fresh_planning_keeps_existing_finite_provider_budget(prompt):
    profile,normalized=request(prompt)
    assert explicit_planner_style_requested(prompt)
    assert profile["output_mode"]=="action_plan"
    assert normalized.max_output_tokens==320


def test_explicit_summary_does_not_become_a_plan():
    profile,normalized=request("Summarize the checklist you proposed in exactly three sentences.")
    assert profile["output_mode"]=="plain_text"
    assert not explicit_planner_style_requested("Summarize the checklist you proposed in exactly three sentences.")
    assert normalized.max_output_tokens < 2048


def test_nine_valid_structured_information_steps_are_not_silently_dropped():
    payload={"summary":"Complete information","steps":[f"Condition {i}" for i in range(1,10)]}
    validated=validate_contract("action_plan",json.dumps(payload))
    assert validated.ok
    assert all(f"Condition {i}" in validated.normalized_text for i in range(1,10))
    assert len(validated.structured_output["steps"])==9


def test_non_list_plan_steps_are_a_contract_failure():
    assert not validate_contract("action_plan",'{"summary":"Invalid plan","steps":"one instruction"}').ok


def test_existing_abstract_plan_budget_is_not_expanded():
    plan=build_plan(SimpleNamespace(),{"task_class":"unknown"},{"model_candidates":[{"summary":"Bounded candidate","validation_state":"valid","resolution_pattern":[f"step-{i}" for i in range(31)]}]},SimpleNamespace())
    assert len(plan.abstract_steps)==8
    assert plan.safe_actions==[]
    assert not plan.writes_workspace


def test_user_json_with_plan_shaped_keys_stays_exact(make_agent):
    prompt='Output JSON only: reproduce the complete saved checklist object with summary and steps.'
    profile,_=request(prompt)
    assert profile["output_mode"]=="plain_text"
    raw='{ "summary" : "Saved source", "steps" : ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"] }'
    contract=parse_raw_output_contract(prompt)
    assert contract is not None
    source={"surface":"api","platform":"api","raw_output_contract":contract.to_dict()}
    delivered=make_agent()._decorate_chat_response(ChatTurnResult(raw,ResponseClass.GENERIC_CONVERSATION),session_id="exact-json",source_context=source,include_hive_footer=False)
    assert delivered==raw


def test_generic_generated_scaffold_is_detected_under_a_heading(make_agent):
    raw="Kiln preparation checklist:\n\n- review problem\n- choose safe next step\n- validate result"
    assert response.contains_generic_planner_scaffold(make_agent(),raw)


def test_contract_failed_final_model_text_is_not_an_answer():
    decision=SimpleNamespace(source="provider_execution",used_model=True,validation_state="contract_failed",output_text="unvalidated raw reply",structured_output=None)
    assert memory_runtime.chat_surface_model_final_text(decision)==""


def test_valid_final_model_information_is_preserved():
    decision=SimpleNamespace(source="provider_execution",used_model=True,validation_state="valid",output_text=SOURCE,structured_output=None)
    assert memory_runtime.chat_surface_model_final_text(decision)==SOURCE


def test_exact_requested_table_and_ordinary_headings_survive(make_agent):
    table="| Stage | Exception |\n|---|---|\n| 30 | Leave 4 mm |\n| 31 | Wait 72 hours |"
    contract=parse_raw_output_contract("Output only the exact table and nothing else.")
    source={"surface":"api","platform":"api"}
    if contract: source["raw_output_contract"]=contract.to_dict()
    assert make_agent()._decorate_chat_response(ChatTurnResult(table,ResponseClass.GENERIC_CONVERSATION),session_id="exact-table",source_context=source,include_hive_footer=False)==table


def test_internal_tool_payload_is_still_refused_even_under_json_only(make_agent):
    contract=parse_raw_output_contract("Output JSON only.")
    raw='{"tool":"workspace.write_file","arguments":{"path":"test.txt","content":"fake"}}'
    source={"surface":"api","platform":"api","raw_output_contract":contract.to_dict()}
    delivered=make_agent()._decorate_chat_response(ChatTurnResult(raw,ResponseClass.GENERIC_CONVERSATION),session_id="internal-json",source_context=source,include_hive_footer=False)
    assert raw!=delivered
    assert "workspace.write_file" not in delivered


def _grounded_final(make_agent, context_result_factory, raw):
    from core.memory_first_router import ModelExecutionDecision
    from tests.test_agent_runtime_turn_reasoning import _configure_grounded_turn_agent
    agent=make_agent()
    task,classification,interpreted,persona=_configure_grounded_turn_agent(agent,context_result=context_result_factory(retrieval_confidence_score=.9))
    prompt="Give me a new step-by-step ceramic kiln preparation plan."
    validation=validate_contract("action_plan",raw)
    agent.memory_router.resolve.return_value=ModelExecutionDecision(source="provider_execution",task_hash="contract-render",used_model=True,provider_id="offline:reader",output_text=validation.normalized_text,structured_output=validation.structured_output,validation_state="valid" if validation.ok else "contract_failed",trust_score=.8)
    agent._turn_result=type(agent)._turn_result.__get__(agent,type(agent))
    agent._decorate_chat_response=type(agent)._decorate_chat_response.__get__(agent,type(agent))
    agent._model_routing_profile=lambda **kwargs: model_routing_profile(agent,**kwargs)
    source={"surface":"api","platform":"api","allow_remote_fetch":False}
    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task",return_value=None), mock.patch("core.agent_runtime.agent.ingest_media_evidence",return_value=[]), mock.patch("core.agent_runtime.agent.build_media_context_snippets",return_value=[]), mock.patch("core.agent_runtime.agent.render_response",wraps=__import__("core.reasoning_engine",fromlist=["render_response"]).render_response) as renderer, mock.patch("core.agent_runtime.agent.feedback_engine.evaluate_outcome",return_value=SimpleNamespace(is_success=False,is_durable=False)),mock.patch("core.agent_runtime.agent.feedback_engine.apply",return_value=None):
        result=agent._execute_grounded_turn(task=task,effective_input=prompt,classification={"task_class":"unknown"},interpreted=interpreted,persona=persona,session_id="contract-render",source_context=source)
    return result,source,renderer.call_count


def test_answer_bearing_contract_failure_cannot_render_a_fulfilled_scaffold(make_agent,context_result_factory):
    result,source,calls=_grounded_final(make_agent,context_result_factory,"Kiln preparation checklist: full plain-text source against a JSON contract")
    outcome=terminal_fulfillment_outcome(result,source_context=source)
    assert outcome.fulfillment_status.value=="failed"
    assert outcome.failure_stage=="output_validation"
    assert "model_output_contract_failed" in outcome.failure_codes
    assert calls==0
    assert "review problem" not in result["response"]


def test_valid_nine_step_plan_wording_is_the_canonical_chat_answer(make_agent,context_result_factory):
    raw=json.dumps({"summary":"Nine condition procedure","steps":[f"Retain distinct condition {i}." for i in range(1,10)]})
    result,source,calls=_grounded_final(make_agent,context_result_factory,raw)
    assert all(f"condition {i}." in result["response"] for i in range(1,10))
    assert calls==0
    assert terminal_fulfillment_outcome(result,source_context=source).fulfillment_status.value=="fulfilled"



def test_prior_plan_intro_does_not_suppress_a_requested_new_plan():
    prompt="Earlier you proposed a checklist. Make me a new step-by-step plan for the changed constraints."
    profile,normalized=request(prompt)
    assert explicit_planner_style_requested(prompt)
    assert profile["output_mode"]=="action_plan"
    assert normalized.max_output_tokens==320


def test_source_heading_and_generic_words_remain_data(make_agent):
    source={"surface":"api","platform":"api"}
    model_routing_profile(SimpleNamespace(_is_chat_truth_surface=lambda ctx:True),user_input=FULL,classification={"task_class":"unknown"},interpretation=SimpleNamespace(as_context=lambda:{}),source_context=source)
    raw="Original heading:\n\n- review problem\n- choose safe next step\n- validate result"
    delivered=make_agent()._decorate_chat_response(ChatTurnResult(raw,ResponseClass.GENERIC_CONVERSATION),session_id="source-generic",source_context=source,include_hive_footer=False)
    assert delivered==raw


def test_untyped_source_format_claim_cannot_silence_the_scaffold_verdict(make_agent):
    source={"surface":"api","_requested_answer_contract":{"preserve_source_format":True}}
    raw="Fallback heading:\n\n- review problem\n- choose safe next step\n- validate result"
    make_agent()._decorate_chat_response(ChatTurnResult(raw,ResponseClass.GENERIC_CONVERSATION),session_id="untyped-source-format",source_context=source,include_hive_footer=False)
    assert terminal_fulfillment_outcome({"response":"safe runtime notice"},source_context=source).fulfillment_status.value=="failed"


def test_quoted_reference_phrase_remains_an_exact_literal():
    contract=parse_raw_output_contract('Return only "the exact table" and nothing else.')
    assert contract is not None and contract.exact_text=="the exact table"


def test_legitimate_json_data_is_not_an_implicit_runtime_envelope(make_agent):
    raw='{"summary":"Original data","steps":["a","b","c","d","e","f","g","h","i"],"version":"v3"}'
    delivered=make_agent()._decorate_chat_response(ChatTurnResult(raw,ResponseClass.GENERIC_CONVERSATION),session_id="json-data",source_context={"surface":"api"},include_hive_footer=False)
    assert delivered==raw



def _provider_completion_reply(raw, mode, reason, reported_tokens):
    from adapters.base_adapter import ModelRequest, ModelResponse
    from core.memory_first_router import MemoryFirstRouter
    from tests.test_response_constraint_router import _manifest
    adapter=mock.Mock()
    model_response=ModelResponse(output_text=raw,output_mode=mode,finish_reason=reason,usage={} if reported_tokens is None else {"completion_tokens":reported_tokens},effective_max_output_tokens=2048)
    adapter.run_text_task.return_value=model_response
    adapter.run_structured_task.return_value=model_response
    router=MemoryFirstRouter(registry=mock.Mock());router.registry.build_adapter.return_value=adapter
    req=ModelRequest(task_kind="conversation",prompt="Explain the retained procedure.",messages=[{"role":"user","content":"Explain the retained procedure."}],output_mode=mode,max_output_tokens=320,metadata={"defer_stream_until_verified":True})
    source={}
    with mock.patch("core.memory_first_router.should_probe_health",return_value=False),mock.patch("core.memory_first_router.circuit_is_open",return_value=False):
        _,reply,error=router._invoke_manifest(manifest=_manifest(local=False),request=req,output_mode=mode,task=SimpleNamespace(task_id="completion-final-only"),source_context=source)
    assert error is None and reply is not None
    assert adapter.run_text_task.call_count + adapter.run_structured_task.call_count==1
    assert not reply.constraint_result["response_control"]["retry_attempted"]
    return reply,source


@pytest.mark.parametrize(("mode","raw"),[("plain_text","The first retained condition is complete."),("json_object",'{"summary":"Complete first section","steps":["First condition"]}')])
def test_provider_length_is_not_fulfilled_even_when_final_text_was_syntactically_valid(mode,raw):
    assert validate_contract(mode,raw).ok
    reply,source=_provider_completion_reply(raw,mode,"length",17)
    completion=reply.constraint_result["response_control"]["provider_completion"]["final"]
    assert completion["finish_reason"]=="length" and completion["provider_reported_limit"]
    assert completion["max_output_tokens"]==2048 and not completion["at_output_limit"]
    assert terminal_fulfillment_outcome({"response":reply.output_text},source_context=source).fulfillment_status.value!="fulfilled"


@pytest.mark.parametrize(("mode","raw"),[("plain_text","All retained conditions are complete."),("action_plan",'{"summary":"Three complete conditions","steps":["Condition one","Condition two","Condition three"]}')])
def test_product_intent_320_is_not_mistaken_for_attested_wire_2048_exhaustion(mode,raw):
    reply,source=_provider_completion_reply(raw,mode,"stop",1000)
    completion=reply.constraint_result["response_control"]["provider_completion"]["final"]
    assert completion["finish_reason"]=="stop"
    assert completion["output_tokens"]==1000 and completion["max_output_tokens"]==2048
    assert not completion["incomplete"] and not completion["at_output_limit"]
    assert terminal_fulfillment_outcome({"response":reply.output_text},source_context=source).fulfillment_status.value=="fulfilled"


def test_missing_finish_and_token_telemetry_stays_unknown():
    reply,source=_provider_completion_reply("Complete retained condition.","plain_text","",None)
    completion=reply.constraint_result["response_control"]["provider_completion"]["final"]
    assert completion["finish_reason"]=="" and completion["output_tokens"] is None
    assert not completion["provider_reported_limit"]
