"""Owner-reported chat flows; external services are replaced at their I/O boundary."""
import pytest

from core.entity_ambiguity import single_plain_know_question
from core.input_normalizer import normalize_user_text
from core.runtime_lane_truth import runtime_lane_question


@pytest.mark.parametrize('text', [
    'are you suing cloud model or local llm for this?',
    'which model answered this?',
    'are you running locally or in the cloud right now?',
])
def test_runtime_status_is_not_an_entity_ambiguity_probe(text):
    assert runtime_lane_question(text)
    assert not single_plain_know_question(text)

@pytest.mark.parametrize('literal', ['usepod.ai', 'docs.vendor.co.uk', 'example.education', 'https://example.ai/a?b=c'])
def test_addresses_survive_before_routing(literal):
    assert literal in normalize_user_text('please fetch ' + literal + ' and summarize it').normalized_text


def test_real_entity_question_keeps_ambiguity_check():
    assert single_plain_know_question('What is the population of Springfield?')

from core.conductor import operations as op
from core.live_data_continuation import continuation_inheritance
from core.runtime_continuity import remember_live_data_obligation

ALLOCATION = ('i have 1 btc i wanto sell it and convert to euros, how mcuh euros i will have? '
              'also then i want to split it in 3 parts and buy gold solver and bnb coin how mcuh of each i wll havE?')
CORRECTION = 'i ment Gold, silver and BNB coin'


def test_allocation_clarification_keeps_the_sum_conversion_and_three_parts():
    remember_live_data_obligation('allocation-demo', operation='allocation', slots=['gold solver', 'bnb'], request_text=ALLOCATION, absorbed_text=ALLOCATION)
    context = {'session_id': 'allocation-demo', 'conversation_history': [{'role': 'user', 'content': ALLOCATION}]}
    corrected, kind = continuation_inheritance(CORRECTION, source_context=context)
    assert kind == 'clarification', corrected
    roles = op.allocation_purchase_roles(corrected)
    assert roles.quantity == 1 and roles.parts == 3 and roles.valuation_currency == 'EUR'
    assert [t.role.key for t in roles.targets] == ['gold', 'silver', 'binancecoin']
    assert not roles.problem
    remember_live_data_obligation('allocation-demo', operation='allocation', slots=['gold', 'silver', 'bnb'], request_text=corrected, absorbed_text=CORRECTION)
    context['conversation_history'].append({'role': 'user', 'content': CORRECTION})
    assert continuation_inheritance('ok so answer original question in full?', source_context=context)[0] == corrected
    context['conversation_history'].append({'role': 'user', 'content': 'write a poem about the sea'})
    assert not continuation_inheritance(CORRECTION, source_context=context)[0]


def test_unresolved_target_never_guesses_silver():
    roles = op.allocation_purchase_roles(ALLOCATION)
    assert roles.problem
    assert [t.text for t in roles.targets] == ['gold solver', 'bnb']


def test_explicit_website_summary_acquires_retrieval():
    from core.task_router import looks_like_explicit_lookup_request
    assert looks_like_explicit_lookup_request(normalize_user_text('fetch me summary from usepod.ai website - what is it built for?').normalized_text)
    assert looks_like_explicit_lookup_request('read example.education and summarize it')
    assert not looks_like_explicit_lookup_request('Do not browse. Explain how to summarize a website in Python.')


def test_allocation_executes_conversion_and_each_target_without_a_model(monkeypatch):
    from decimal import Decimal

    from core.conductor import compose_answer, plan_conductor_turn, run_conductor_plan
    from core.conductor.product_decision import ExecutionReport, reduce_execution_report
    from core.conductor.registry import NodeContext
    from core.fresh_data.fx import FxQuote, FxQuoteStatus
    from core.live_data_plan import SubtaskLifecycle, SubtaskOutcome
    prices = {'bitcoin': 60000, 'gold': 3000, 'silver': 30, 'binancecoin': 600}
    def market(subtask, timeout_s=None):
        key = subtask.arguments.get('asset_key') or subtask.entity
        return SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.SUCCEEDED, result={'asset_key': key, 'price': prices[key], 'currency': 'USD', 'source': 'fixture', 'retrieved_at': '2026-09-16T00:00:00Z'})
    class FX:
        name = 'fixture'
        def quote(self, base, quote, timeout_s=None):
            assert (base, quote) == ('USD', 'EUR')
            return FxQuote(base=base, quote=quote, status=FxQuoteStatus.AVAILABLE, rate=Decimal('.9'), observed_at='2026-09-16T00:00:00Z', retrieved_at='2026-09-16T00:00:00Z', source=self.name)
    monkeypatch.setattr('core.agent_runtime.live_data_runner._run_market_subtask', market)
    monkeypatch.setattr('core.conductor.fresh_data_operations._configured_fx_providers', lambda ctx: (FX(),))
    monkeypatch.setattr('core.conductor.fresh_data_operations._runtime_retrieval_allowed', lambda ctx: True)
    def no_model(*a, **kw):
        pytest.fail('This fully specified calculation needs no model')
    text = 'I have 1 btc and want to convert to euros, then split it in 3 parts and buy gold, silver and bnb coin. How much of each?'
    plan = plan_conductor_turn(text, ask_model=no_model, propose_semantics=no_model)
    assert plan is not None
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=no_model, timeout_s=5), plan_deadline_s=20)
    decision = reduce_execution_report(ExecutionReport(bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes), planned_node_ids=tuple(n.node_id for n in plan.nodes), requirement_nodes=dict(plan.requirement_nodes)))
    output = compose_answer(plan, outcomes, decision).text
    assert decision.disposition.value == 'fulfilled', output
    assert '54,000' in output or '54000' in output, output
    assert '18,000' in output or '18000' in output, output
    assert '6.666' in output and '666.66' in output and '33.333' in output, output
    assert 'EUR' in output


def test_unusable_model_output_cannot_complete_or_verify_the_turn(make_agent, context_result_factory, monkeypatch):
    from types import SimpleNamespace

    from core.memory_first_router import ModelExecutionDecision
    from core.proof_projection import _fulfilment_gaps
    from tests.test_agent_runtime_turn_reasoning import _configure_grounded_turn_agent
    agent = make_agent()
    task, classification, interpreted, persona = _configure_grounded_turn_agent(agent, context_result=context_result_factory())
    agent.memory_router.resolve.return_value = ModelExecutionDecision(source='provider', task_hash='empty-output', provider_id='fixture:model', used_model=True, output_text='', confidence=.8, trust_score=.8)
    monkeypatch.setattr('core.agent_runtime.agent.orchestrate_parent_task', lambda **kw: None)
    monkeypatch.setattr('core.agent_runtime.agent.ingest_media_evidence', lambda **kw: [])
    monkeypatch.setattr('core.agent_runtime.agent.build_media_context_snippets', lambda *a, **kw: [])
    monkeypatch.setattr('core.agent_runtime.agent.build_plan', lambda **kw: SimpleNamespace(confidence=.7))
    monkeypatch.setattr('core.agent_runtime.agent.should_use_planner_renderer', lambda **kw: False)
    monkeypatch.setattr('core.agent_runtime.agent.feedback_engine.evaluate_outcome', lambda *a, **kw: SimpleNamespace(is_success=False, is_durable=False))
    monkeypatch.setattr('core.agent_runtime.agent.feedback_engine.apply', lambda *a, **kw: None)
    context = {'surface': 'openclaw', 'platform': 'openclaw', '_execution_identity': {}}
    agent._execute_grounded_turn(task=task, effective_input='Explain recursion.', classification=classification, interpreted=interpreted, persona=persona, session_id='empty-output-demo', source_context=context)
    assert context['response_control']['fulfillment_outcome']['fulfillment_status'] == 'failed'
    events = [call.kwargs for call in agent._emit_runtime_event.call_args_list]
    assert any(e.get('event_type') == 'task_failed' for e in events)
    assert not any(e.get('event_type') == 'task_completed' for e in events)
    assert _fulfilment_gaps(events, {'canonical_content': 'degraded answer'})


def test_outer_chat_status_does_not_consult_a_model(make_agent, monkeypatch):
    agent = make_agent()
    calls = []
    def no_model(*a, **kw):
        calls.append(1)
        raise AssertionError('runtime status tried a model')
    monkeypatch.setattr(agent.memory_router, 'resolve', no_model)
    monkeypatch.setattr('core.agent_runtime.turn_planner_hook.build_conductor_ask_model', lambda *a, **kw: no_model)
    result = agent.run_once('are you suing cloud model or local llm for this?', session_id_override='status-outer-demo', source_context={'surface': 'api', '_owner_local': True})
    assert "answered by VOOL's runtime directly" in result.get('response', '')
    assert not calls


def test_website_evidence_enters_the_answering_call(make_agent, context_result_factory):
    from tests.test_retrieval_precedes_current_answer_synthesis import SOURCE_SENTINEL, TurnRecorder, _configure, _drive
    agent = make_agent()
    recorder = TurnRecorder(rows=[{'summary': 'Website purpose: ' + SOURCE_SENTINEL, 'result_title': 'Provider overview', 'result_url': 'https://example.education/', 'origin_domain': 'example.education', 'source_type': 'web_derived'}])
    task, classification, interpreted, persona = _configure(agent, recorder, context_result_factory(local_candidates=[], retrieval_confidence_score=0))
    request = normalize_user_text('fetch me summary from example.education website - what is it built for?').normalized_text
    _drive(agent, task, classification, interpreted, persona, request=request)
    assert recorder.order.index('retrieval') < recorder.order.index('model_call')
    assert SOURCE_SENTINEL in str(recorder.model_calls)


def test_crypto_valuation_refuses_missing_or_wrong_currency_evidence():
    from core.conductor.registry import NodeContext
    roles = op._asset_valuation_request('how much is 2 eth in GBP')
    context = NodeContext(dependency_results={'q:ethereum': {'asset_key': 'ethereum', 'price': 1000, 'currency': 'USD'}, 'fx': {'base': 'EUR', 'quote': 'GBP', 'rate': '.8', 'status': 'available'}})
    with pytest.raises(ValueError, match='USD/GBP'):
        op._asset_valuation_run(roles, context)
    context.dependency_results['fx']['base'] = 'USD'
    assert op._asset_valuation_run(roles, context)['values']['step_1'] == 1600


def test_outer_chat_allocation_resumes_after_target_correction(make_agent, monkeypatch):
    from core.runtime_continuity import recall_live_data_obligation
    from tests.test_shared_contracts_referent_allocation import QUOTES, _fake_market, _Fx
    # This journey checks continuity; numerical conversion is checked above with a
    # separate directed FX fixture. Keep this variant in the quote currency.
    monkeypatch.setitem(QUOTES, 'silver', 30)
    monkeypatch.setitem(QUOTES, 'binancecoin', 600)
    monkeypatch.setattr('core.agent_runtime.live_data_runner._run_market_subtask', _fake_market)
    monkeypatch.setattr('core.conductor.fresh_data_operations._configured_fx_providers', lambda ctx: (_Fx(),))
    monkeypatch.setattr('core.conductor.fresh_data_operations._runtime_retrieval_allowed', lambda ctx: True)
    agent = make_agent()
    calls = []
    def no_model(*a, **kw):
        calls.append(1)
        raise AssertionError('allocation invoked a model')
    monkeypatch.setattr(agent.memory_router, 'resolve', no_model)
    monkeypatch.setattr('core.agent_runtime.turn_planner_hook.build_conductor_ask_model', lambda *a, **kw: no_model)
    first = 'I have 1 btc, split it in 3 parts and buy gold solver and bnb coin. How much of each?'
    history = []
    context = {'surface': 'api', '_owner_local': True, 'allow_remote_fetch': True, 'conversation_history': history}
    result = agent.run_once(first, session_id_override='allocation-outer-demo', source_context=dict(context))
    assert '3 equal parts' in result['response'], result['response']
    saved = recall_live_data_obligation('allocation-outer-demo')
    assert saved and saved['operation'] == 'allocation'
    history.append({'role': 'user', 'content': first})
    result = agent.run_once(CORRECTION, session_id_override='allocation-outer-demo', source_context=dict(context))
    assert 'silver' in result['response'].lower(), result['response']
    assert 'could not be answered' not in result['response'].lower(), result['response']
    assert not calls
