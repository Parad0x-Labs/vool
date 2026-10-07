"""Shape descriptions never gain literal or runtime-credential authority."""
from types import SimpleNamespace

import pytest

from core.runtime_lane_truth import FACET_KEY, FACET_PROVIDER, runtime_lane_question
from core.web.api.response_control import apply_exact_response_control, exact_response_target

JSON_REQUESTS=[
    'Return exactly one valid JSON object with the sole key "station" and its recorded string value for the willow bench. Do not include prose or Markdown.',
    'Return exactly one complete valid JSON object with the sole key "inspection_label" and its recorded string value for the willow bench. Do not include prose or Markdown.',
    'Respond with exactly a JSON object containing the valid key "label" and its recorded value.',
]

@pytest.mark.parametrize('prompt',JSON_REQUESTS+[
    'Return exactly the table from the saved invoice. No extra text.',
    'Output exactly a CSV row of the recorded values.',
    'Reply exactly one complete XML document with the stored labels.',
])
def test_unquoted_deliverable_description_does_not_bind_literal(prompt):
    assert exact_response_target(prompt)==''
    result={'response':'the actual complete deliverable','fulfillment_outcome':{'fulfillment_status':'failed'}}
    assert apply_exact_response_control(result,prompt)==result

@pytest.mark.parametrize(('prompt','literal'),[
    ('Return exactly "one valid JSON object"','one valid JSON object'),
    ('Output exactly `a CSV row`','a CSV row'),
    ('reply exactly: line one line two','line one line two'),
    ('Respond with exactly PONG','PONG'),
    ('Reply with exactly this word and nothing else: laapitytio','laapitytio'),
])
def test_supplied_literal_authority_is_preserved(prompt,literal):
    assert exact_response_target(prompt)==literal

@pytest.mark.parametrize('prompt',JSON_REQUESTS+[
    'Is the JSON key "enabled" valid in this object?',
    'Is my house key working?',
    'Is the key to this piano working?',
    'Is my subscription token expired?',
])
def test_noncredential_subject_does_not_claim_runtime_key(prompt):
    assert runtime_lane_question(prompt)==''

@pytest.mark.parametrize(('prompt','facet'),[
    ('Is my key working?',FACET_KEY),
    ('Is my API key valid?',FACET_KEY),
    ('Is the OpenRouter key configured?',FACET_KEY),
    ('Does my cloud token work?',FACET_KEY),
    ('Is the provider connected and healthy?',FACET_PROVIDER),
])
def test_genuine_runtime_status_queries_are_preserved(prompt,facet):
    assert runtime_lane_question(prompt)==facet


JSON_OBJECT_PROMPT=JSON_REQUESTS[0]
PIPE_PROMPT='Using the latest museum record for Atlas-6, output exactly code|location|protection with no spaces around the separators, no explanation, and no Markdown.'

@pytest.mark.parametrize('prompt',[JSON_OBJECT_PROMPT,PIPE_PROMPT])
def test_requested_output_owns_wire_shape_in_nonchat_task_class(prompt):
    from core.agent_runtime.runtime_checkpoint_lane_policy import model_routing_profile
    from core.task_router import model_execution_profile
    baseline=model_execution_profile('security_hardening')
    context={'surface':'api','platform':'api'}
    classification,profile=model_routing_profile(SimpleNamespace(_is_chat_truth_surface=lambda _:True),user_input=prompt,classification={'task_class':'security_hardening'},interpretation=SimpleNamespace(as_context=lambda:{}),source_context=context)
    assert profile['output_mode']=='plain_text'
    assert profile['task_kind']==baseline['task_kind']
    assert profile['allow_paid_fallback']==baseline['allow_paid_fallback']
    assert profile['requested_answer_contract']['kind']=='exact_output'

@pytest.mark.parametrize('prompt',[JSON_OBJECT_PROMPT,PIPE_PROMPT])
def test_full_router_rederivation_keeps_requested_output_mode(prompt,monkeypatch):
    from core.memory_first_router import MemoryFirstRouter
    captured={}
    class EndOfProfileProbe(Exception):pass
    def capture_hash(**kwargs):
        captured.update(kwargs)
        raise EndOfProfileProbe
    monkeypatch.setattr('core.memory_first_router.build_task_hash',capture_hash)
    router=object.__new__(MemoryFirstRouter)
    with pytest.raises(EndOfProfileProbe):
        router.resolve(task=SimpleNamespace(task_summary=prompt),classification={'task_class':'security_hardening','planner_style_requested':False},interpretation=SimpleNamespace(normalized_text=prompt),context_result=None,persona=None,force_model=True,surface='api',source_context={'surface':'api'})
    assert captured['output_mode']=='plain_text'

@pytest.mark.parametrize('raw',['The station code is P-29.','{"station": "P-29"','["P-29"]'])
def test_explicit_single_json_object_rejects_invalid_or_wrong_container(raw):
    from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract
    contract=parse_raw_output_contract(JSON_OBJECT_PROMPT)
    assert contract is not None
    application=apply_raw_output_contract(raw,contract)
    assert not application.compliant
    assert any(v in application.violations for v in ('invalid_json','json_not_object'))

def test_valid_user_json_object_preserves_exact_source_bytes():
    from core.raw_output_contract import (
        apply_raw_output_contract,
        parse_raw_output_contract,
        raw_output_contract_from_metadata,
    )
    contract=parse_raw_output_contract(JSON_OBJECT_PROMPT)
    assert contract is not None and getattr(contract,'json_required',False)
    assert getattr(contract,'json_object',False)
    assert raw_output_contract_from_metadata({'raw_output_contract':contract.to_dict()})==contract
    raw='{ "summary" : "User source", "steps" : ["source item"] }'
    app=apply_raw_output_contract(raw,contract)
    assert app.compliant and app.text==raw

def test_json_only_accepts_valid_array_without_reinterpreting_plan_fields():
    from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract
    contract=parse_raw_output_contract('Output JSON only.')
    assert contract is not None
    assert apply_raw_output_contract('["saved", "values"]',contract).compliant
    assert not apply_raw_output_contract('a plain description of saved values',contract).compliant

@pytest.mark.parametrize('prompt',[
    'Explain the sentence "Return exactly one valid JSON object".',
    'Explain why one valid JSON object is sufficient.',
])
def test_quoted_or_descriptive_json_shape_is_not_output_authority(prompt):
    from core.raw_output_contract import parse_raw_output_contract
    assert parse_raw_output_contract(prompt) is None
