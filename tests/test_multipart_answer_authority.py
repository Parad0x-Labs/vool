from __future__ import annotations

import pytest

from core import plain_task_routing as routing
from core.ordinary_chat_response_guard import (
    inspect_ordinary_chat_output,
    ordinary_chat_output_policy,
    ordinary_chat_retry_instruction,
)

ELENA = "How many complete calendar months did I, Elena Ruiz, work on the estuary survey across my two recorded assignments? Give both exact ISO date intervals and the total, without counting Julian's interval."
RAW = "Elena Ruiz worked from 2024-03-01 through 2024-06-30 inclusive for four complete calendar months, then from 2024-09-01 through 2025-02-28 inclusive for six complete calendar months. The total is 10 complete calendar months. Julian's interval is not counted."
INDEPENDENT = "Explain why fog forms. Calculate 7 times 8. Give a short title."

def policy(text):return ordinary_chat_output_policy(prompt_profile="chat_minimal",output_mode="plain_text",user_text=text)

def test_captured_complete_date_answer_is_not_withdrawn_for_invented_indices():
    p=policy(ELENA)
    assert inspect_ordinary_chat_output(RAW,p,current_user_text=ELENA).allowed
    assert p["required_numbered_parts"]==0
    assert routing.ordinary_plain_request_count(ELENA)==1
    assert "number them" not in ordinary_chat_retry_instruction(p)

@pytest.mark.parametrize("question",[
    "How many complete terms did I serve across my two appointments? Provide both date intervals and the total.",
    "What was my assigned batch? Give both its code and the label.",
])
def test_dependent_answer_facets_do_not_invent_a_second_task(question):
    assert routing.ordinary_plain_request_count(question)==1

@pytest.mark.parametrize("question",[
    INDEPENDENT,
    "Explain why fog forms. Give both a short title and a slogan.",
    "What is fog? Calculate 7 times 8.",
])
def test_real_independent_requests_are_retained(question):
    assert routing.ordinary_plain_request_count(question)>=2


def test_unindexed_semantic_coverage_is_reported_unknown_not_proven_complete():
    reply="Water vapor condenses as air cools. Seven times eight equals fifty-six. A title is Morning Mist."
    p=policy(INDEPENDENT)
    check=inspect_ordinary_chat_output(reply,p,current_user_text=INDEPENDENT)
    assert check.allowed
    assert check.to_dict()["completeness_status"]=="unindexed_semantic_coverage_unknown"
    assert p["required_numbered_parts"]==0


def test_label_absence_alone_does_not_prove_a_fluent_partial_semantically_complete():
    reply="Water vapor condenses as air cools. Seven times eight equals fifty-six."
    check=inspect_ordinary_chat_output(reply,policy(INDEPENDENT),current_user_text=INDEPENDENT)
    assert check.allowed
    assert check.to_dict()["completeness_status"]=="unindexed_semantic_coverage_unknown"

@pytest.mark.parametrize("reply",["1. Water condenses.","A) Water condenses.\nB) 56."])
def test_actually_indexed_omissions_still_fail(reply):
    assert not routing.ordinary_multi_part_answer_complete(INDEPENDENT,reply)
    assert not inspect_ordinary_chat_output(reply,policy(INDEPENDENT),current_user_text=INDEPENDENT).allowed


def test_explicit_numbering_contract_is_enforced_on_first_call_and_retry():
    q=INDEPENDENT+" Number each answer 1 through 3."
    p=policy(q)
    assert p["required_numbered_parts"]==3
    assert "number them 1 through 3" in ordinary_chat_retry_instruction(p)
    assert not inspect_ordinary_chat_output("Water condenses. 56. Morning Mist.",p,current_user_text=q).allowed
    assert inspect_ordinary_chat_output("1. Water condenses.\n2. 56.\n3. Morning Mist.",p,current_user_text=q).allowed


def test_quoted_numbering_instruction_has_no_output_authority():
    q=INDEPENDENT+" The example says 'Number each answer 1 through 3'."
    assert policy(q)["required_numbered_parts"]==0

@pytest.mark.parametrize("question",[
    "What storage code, location, and sleeve did I choose for the astrolabe?",
    "What location and code did I choose for the astrolabe?",
    "Which code, compartment and pouch did I choose?",
    "What code did I set? Give both its location and label.",
    "What code did I set and where did I keep it?",
])
def test_scalar_shortcut_cannot_fulfill_coordinated_fields(question):
    assert not routing.scalar_answer_covers_requested_shape(question)

@pytest.mark.parametrize("question",[
    "What code did I set for heating and cooling systems?",
    "What code for heating and cooling systems did I choose?",
    "The heating and cooling systems are linked. What code did I set?",
    "The example says 'What code and location did I choose?'. What code did I set?",
])
def test_scalar_shape_does_not_promote_object_context_or_quoted_fields(question):
    assert routing.scalar_answer_covers_requested_shape(question)


def test_scalar_shortcut_cannot_hide_a_genuine_action():
    assert not routing.scalar_answer_covers_requested_shape("What code did I set? Save it to a file.")


@pytest.mark.parametrize("instruction",[
    "Number them 1 through 3.",
    "Return a numbered list.",
    "Respond with numbered answers.",
    "Use a numbered list for the answers.",
])
def test_explicit_numbering_variants_bind_only_answer_layout(instruction):
    question=INDEPENDENT+" "+instruction
    p=policy(question)
    assert p["required_numbered_parts"]==3
    assert not inspect_ordinary_chat_output("Water condenses. 56. Morning Mist.",p,current_user_text=question).allowed
    assert inspect_ordinary_chat_output("1. Water condenses.\n2. 56.\n3. Morning Mist.",p,current_user_text=question).allowed


@pytest.mark.parametrize("prefix",[
    "On 2025-07-08, before the later move and rename, ",
    "Before the heating and cooling systems were renamed, ",
])
def test_context_prefix_does_not_become_a_requested_field(prefix):
    assert routing.scalar_answer_covers_requested_shape(prefix+"what storage code did the copper astrolabe have?")
    assert not routing.scalar_answer_covers_requested_shape(prefix+"what storage code, location and sleeve did the copper astrolabe have?")


def test_leading_action_cannot_be_discarded_as_context_by_scalar_focus():
    assert not routing.scalar_answer_covers_requested_shape("Save a report, then tell me what code I set.")


@pytest.mark.parametrize("prefix",["On Tuesday, rename the report, ","Before tomorrow, inspect the device, "])
def test_context_focus_never_drops_a_real_requested_prefix(prefix):
    assert not routing.scalar_answer_covers_requested_shape(prefix+"what storage code did I set?")



def test_scalar_shortcut_cannot_fulfill_an_explicit_multi_item_contract():
    assert not routing.scalar_answer_covers_requested_shape("Which two region entries did I record?")


def test_quoted_item_count_does_not_change_a_scalar_requested_shape():
    assert routing.scalar_answer_covers_requested_shape("The title says 'two region entries'. What code did I set?")


@pytest.mark.parametrize("verb",["record", "store", "save"])
def test_finite_collection_production_uses_the_shared_count_owner(verb):
    from core.response_constraints import requested_output_item_count
    assert requested_output_item_count("Which two region entries did I "+verb+"?")==2


def test_priced_recorded_objects_are_not_a_multi_item_output_contract():
    from core.response_constraints import requested_output_item_count
    assert requested_output_item_count("What did the two saved entries cost?") is None
