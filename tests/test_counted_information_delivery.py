"""A finite requested collection must outrank implicit brevity, without granting actions."""
from __future__ import annotations
from types import SimpleNamespace
from unittest import mock
import pytest
from adapters.base_adapter import ModelRequest,ModelResponse
from core import response_constraints
from core.memory_first_router import MemoryFirstRouter
from core.ordinary_chat_response_guard import ordinary_chat_output_policy,inspect_ordinary_chat_output,constrain_ordinary_chat_output
from tests.test_requested_answer_delivery_contract import request
from tests.test_response_constraint_router import _manifest

PROMPT="What would you include in a seven-item observatory maintenance kit?"
RAW='Seven items in the observatory maintenance kit:\n1. Two brass clamps measuring 120 mm by 20 mm.\n2. A lens brush with a soft-fibre tip.\n3. A box of stainless M6 bolts.\n4. A tub of neutral mounting grease.\n5. Instead of a mains extension cable, a 12 V battery pack.\n6. A printed inspection card asking, "Are both side clamps tight?"\n7. A labelled tin for spare M6 washers.'

@pytest.mark.parametrize(("prompt","count"),[
    (PROMPT,7),
    ("What seven items did you list in the maintenance kit earlier?",7),
    ("Which seven items did you recommend for the maintenance kit before?",7),
    ("Which seven components did you suggest for the maintenance kit before?",7),
    ("Give me a 9-part set for a studio.",9),
    ("Give me a 7-item maintenance kit.",7),
    ("Suggest a twelve-item set for a studio.",12),
    ("Which tools would you include in a twenty-item set?",20),
])
def test_finite_output_cardinality_has_one_bounded_owner(prompt,count):
    resolve=getattr(response_constraints,"requested_output_item_count",lambda text:None)
    assert resolve(prompt)==count

@pytest.mark.parametrize("prompt",[
    'Explain the phrase "What would you include in a seven-item kit?".',
    "What does a seven-item kit cost?",
    "Summarize the seven-item kit in one sentence.",
    "The seven-item kit is heavy.",
    "Give me a 21-item kit.",
    "Give me a zero-item kit.",
    'Explain the message "What seven items did you list in the kit earlier?".',
    "Describe the seven-item kit price.",
    "Give me a seven-word title.",
    "Give me the price of a seven-item kit.",
    "Give me a description of the seven-item kit.",
    "What does the seven-item kit you listed cost?",
])
def test_source_quantity_quotes_and_out_of_bound_counts_do_not_bind_output_cardinality(prompt):
    resolve=getattr(response_constraints,"requested_output_item_count",lambda text:None)
    assert resolve(prompt) is None


def test_counted_information_survives_the_actual_ordinary_trimmer():
    policy=ordinary_chat_output_policy(prompt_profile="chat_minimal",output_mode="plain_text",user_text=PROMPT)
    assert constrain_ordinary_chat_output(RAW,policy)==RAW
    assert inspect_ordinary_chat_output(RAW,policy,current_user_text=PROMPT).allowed


def test_counted_information_is_projected_into_generation_intent():
    profile,normalized=request(PROMPT)
    assert profile["requested_answer_contract"].get("requested_items")==7
    assert profile["output_mode"]=="plain_text"
    assert 320<normalized.max_output_tokens<=2048
    assert normalized.metadata["generation_profile"]["adaptive_length"] is False


def test_first_complete_reader_reply_is_not_retried_or_clipped():
    adapter=mock.Mock();adapter.run_text_task.side_effect=lambda *args,**kwargs:ModelResponse(output_text=RAW,output_mode="plain_text",finish_reason="stop",usage={"completion_tokens":100},effective_max_output_tokens=2048)
    router=MemoryFirstRouter(registry=mock.Mock());router.registry.build_adapter.return_value=adapter
    policy=ordinary_chat_output_policy(prompt_profile="chat_minimal",output_mode="plain_text",user_text=PROMPT)
    req=ModelRequest(task_kind="conversation",prompt=PROMPT,messages=[{"role":"user","content":PROMPT}],output_mode="plain_text",max_output_tokens=320,metadata={"ordinary_chat_output_policy":policy,"defer_stream_until_verified":True})
    with mock.patch("core.memory_first_router.should_probe_health",return_value=False),mock.patch("core.final_answer_authorship.precall_author_verdict",return_value=None),mock.patch("core.memory_first_router.circuit_is_open",return_value=False):
        _,reply,error=router._invoke_manifest(manifest=_manifest(local=True),request=req,output_mode="plain_text",task=SimpleNamespace(task_id="counted-info"),source_context={})
    assert error is None and reply is not None
    assert reply.output_text==RAW
    assert adapter.run_text_task.call_count==1


def test_explicit_short_summary_remains_short():
    prompt="Summarize the seven-item kit in exactly three words."
    policy=ordinary_chat_output_policy(prompt_profile="chat_minimal",output_mode="plain_text",user_text=prompt)
    assert policy["max_words"]=="3"
    profile,normalized=request(prompt)
    assert profile["output_mode"]=="plain_text"
    assert normalized.max_output_tokens<320


def test_fresh_action_plan_budget_remains_320():
    profile,normalized=request("Give me a new seven-step execution checklist for a release.")
    assert profile["output_mode"]=="action_plan"
    assert normalized.max_output_tokens==320
