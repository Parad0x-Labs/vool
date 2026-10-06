"""New guard-boundary cases frozen before any execution on this delivery.

Proof level: actual final response seam, supplied admitted capsule, no provider.
"""
import json
from pathlib import Path
import pytest
from core.agent_runtime.response import _validate_final_chat_output
from core.unsourced_current_claim import inspect_unsourced_current_claim

CASES = json.loads(Path(__file__).with_name('owner_fresh_round2_cases.json').read_text())

@pytest.fixture(autouse=True)
def isolate_telemetry():
    from core.context_retrieval import reset_retrieval_telemetry
    reset_retrieval_telemetry()
    yield
    reset_retrieval_telemetry()

@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_fresh_round2(case):
    context = {'surface': 'openclaw', 'platform': 'openclaw', 'chat_id': 'authority-review',
               'conversation_history': [{'role': 'user', 'content': case['question']}]}
    if case['lines']:
        capsule = '<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n' + '\n'.join(case['lines']) + '\n</retrieved_context>'
        context['admitted_capsule_evidence'] = {
            'text': capsule, 'chat_id': 'foreign-authority-review' if case.get('foreign') else 'authority-review',
            'source': 'canonical_runtime_transcript'}
    actual = _validate_final_chat_output(case['reply'], source_context=context)
    if case['keep']:
        assert actual == case['reply'], {'id': case['id'], 'actual': actual, 'expected': case['reply']}
    else:
        assert case['value'] not in actual, {'id': case['id'], 'actual': actual, 'forbidden': case['value']}


@pytest.mark.parametrize('case', [c for c in CASES if c['lines']], ids=[c['id'] for c in CASES if c['lines']])
def test_record_regressions_at_the_earlier_current_claim_owner(case):
    capsule = '<retrieved_context>\n' + '\n'.join(case['lines']) + '\n</retrieved_context>'
    context = {'chat_id': 'authority-review', 'admitted_capsule_evidence': {
        'text': capsule, 'chat_id': 'foreign-authority-review' if case.get('foreign') else 'authority-review',
        'source': 'canonical_runtime_transcript'}}
    verdict = inspect_unsourced_current_claim(answer=case['reply'], requires_current=True,
        source_context=context, user_turn_text=case['question'])
    assert verdict.unsupported is (not case['keep']), {'id': case['id'], 'verdict': verdict.as_dict()}
