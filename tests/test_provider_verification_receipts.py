import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import provider_verification as pv
from core.turn_model_call_ledger import (
    begin_turn,
    record_provider_call,
    record_provider_call_outcome,
    turn_call_accounting,
)
from tests.chat_page_js_harness import DOM, run_node, script
from tests.test_coding_hive_and_usage_details import response


def receipt(r=None, **kw):
    return pv.build_verification_receipt(call_id='call-unique', request_id='request-unique',
        requested_model='gpt-6-astra', selected_model='gpt-6-astra', provider_id='usepod-byok:gpt-6-astra',
        response=r, timestamp=1000, **kw)


def rich_response():
    r = response()
    r.provider_attested_model = 'gpt-6-astra'
    r.raw_response = {'id': 'provider-request-123', 'model': 'gpt-6-astra', 'provider': 'provider-node',
                      'system_fingerprint': 'fp-123', 'usage': r.usage, 'choices': [{'text': 'answer'}],
                      'verification_status': 'UPSTREAM_VERIFIED', 'authorization': 'never retain me'}
    r.provider_metadata['usepod'] = {'origin': 'https://usepod.ai', 'envelope': {'body_sha256': 'a' * 64},
        'route_policy': {'eligible_prices': [{'route_class': 'marketplace', 'provider': '',
                         'input_microunits_per_million': 800000, 'output_microunits_per_million': 4000000}],
                         'price_source': {'fetched_at': 990}},
        'response_headers': {'x-request-id': 'request-header', 'authorization': 'private',
                             'set-cookie': 'secret', 'x-generation-id': 'gen-123'}}
    r.provider_metadata['receipt']['route'] = {'class': 'marketplace', 'provider_id': 'provider-node', 'compliance': 'compliant', 'verified': True}
    return r


def test_echoed_astra_and_route_compliance_are_only_claims():
    r = receipt(rich_response())
    assert r['verification']['status'] == 'CLAIMED'
    assert not r['verification']['independent_model_proof']
    assert r['requested_model'] == r['returned_model'] == 'gpt-6-astra'
    assert r['provider_request_id'] == 'provider-request-123'
    assert r['route'] == 'marketplace' and r['provider_id'] == 'provider-node'
    assert r['quoted_price']['eligible_prices'][0]['input_microunits_per_million'] == 800000
    assert r['actual_cost']['amount'] is None and r['cost_bound']['amount'] == .0016
    assert r['request_id'] == 'request-unique' and r['timestamp'].endswith('+00:00')
    assert 'never retain me' not in json.dumps(r) and 'secret' not in json.dumps(r)
    assert 'choices' not in r['upstream_metadata']['response']
    assert r['request_payload_sha256'] == 'a' * 64


@pytest.mark.parametrize('status', [pv.VerificationStatus.UPSTREAM_VERIFIED, pv.VerificationStatus.PROVIDER_VERIFIED])
def test_only_registered_verifier_with_bound_current_evidence_can_promote(monkeypatch, status):
    def verify(facts, response):
        assert response.raw_response['id'] == 'provider-request-123'
        return pv.Validation(status, facts['binding_sha256'], 'synthetic-validator',
                             ('evidence:synthetic-proof',), 1001, 'Synthetic control, not a live attestation.')
    monkeypatch.setitem(pv._VERIFIERS, 'usepod-byok', verify)
    r = receipt(rich_response())
    assert r['verification']['status'] == status.value
    assert r['verification']['independent_model_proof'] == (status is pv.VerificationStatus.UPSTREAM_VERIFIED)


@pytest.mark.parametrize('defect', ['stale', 'wrong_binding', 'no_evidence', 'raw_dict', 'exception'])
def test_invalid_or_unverified_proofs_do_not_promote(monkeypatch, defect):
    def verify(facts, response):
        if defect == 'exception': raise ValueError('invalid signature')
        if defect == 'raw_dict': return {'status': 'UPSTREAM_VERIFIED', 'verified': True}
        return pv.Validation(pv.VerificationStatus.UPSTREAM_VERIFIED,
            'wrong' if defect == 'wrong_binding' else facts['binding_sha256'], 'validator',
            () if defect == 'no_evidence' else ('evidence:proof',),
            999 if defect == 'stale' else 1001, 'example')
    monkeypatch.setitem(pv._VERIFIERS, 'usepod-byok', verify)
    assert receipt(rich_response())['verification']['status'] == 'CLAIMED'


def test_response_bytes_and_call_identity_bind_the_evidence():
    a = rich_response()
    b = deepcopy(a)
    b.raw_response['choices'][0]['text'] = 'different answer'
    assert receipt(a)['binding_sha256'] != receipt(b)['binding_sha256']
    b.provider_attested_model = 'different-model'
    assert receipt(b)['model_match'] == 'different_identifier'
    del b.provider_attested_model
    b.raw_response.pop('model')
    assert receipt(b)['returned_model'] is None


def test_generic_openrouter_receipt_preserves_actual_cost_and_unknown_quote():
    r = SimpleNamespace(raw_response={'id': 'gen-123', 'model': 'openai/model', 'provider': 'upstream'},
                        usage={'cost': .0009}, provider_metadata={})
    out = pv.build_verification_receipt(call_id='c', request_id='r', requested_model='auto',
                                       selected_model='openai/model', provider_id='openrouter-byok', response=r)
    assert out['actual_cost'] == {'state': 'reported', 'amount': .0009, 'currency': 'USD'}
    assert out['quoted_price']['state'] == 'not_reported'
    assert out['verification']['status'] == 'CLAIMED'


def test_failed_and_duplicate_call_are_recorded_once_with_unknown_cost(monkeypatch):
    sink = Mock()
    monkeypatch.setattr('core.runtime_task_events.emit_runtime_event', sink)
    ctx = {'request_id': 'verify-failed'}
    begin_turn(ctx)
    call = record_provider_call(ctx, provider_id='direct-provider', model_id='model', cost_class='paid_cloud')
    assert record_provider_call_outcome(ctx, call, outcome='failed')
    assert not record_provider_call_outcome(ctx, call, outcome='completed')
    rows = turn_call_accounting(ctx)['verification_receipts']
    assert len(rows) == 1 and rows[0]['actual_cost']['amount'] is None
    assert rows[0]['outcome'] == 'failed' and rows[0]['returned_model'] is None
    assert sink.call_count == 1


def test_native_chat_script_displays_claim_and_inspectable_receipt():
    r = receipt(rich_response())
    event = {'event_type': 'model_verification_receipt', 'verification_receipt': r, 'model_id': 'gpt-6-astra'}
    output = run_node(DOM + '\n' + script() + '\nout({row:ledgerRow(' + json.dumps(event) + '), details:activityDetailLines(' + json.dumps(event) + ')});')
    assert 'CLAIMED' in output['row']['sub']
    details = dict(output['details'])
    assert details['returned model claim'] == 'gpt-6-astra'
    assert details['actual charge'] == 'not reported by provider'
    assert 'Verified GPT' not in json.dumps(output)


def test_timeout_gets_one_honest_receipt_even_if_late_answer_returns(monkeypatch):
    from core.turn_model_call_ledger import fail_pending_provider_calls
    sink = Mock()
    monkeypatch.setattr('core.runtime_task_events.emit_runtime_event', sink)
    ctx = {'request_id': 'verify-timeout'}
    begin_turn(ctx)
    call = record_provider_call(ctx, provider_id='usepod-byok', model_id='model', cost_class='paid_cloud')
    assert len(fail_pending_provider_calls(ctx, error_class='timeout')) == 1
    assert not fail_pending_provider_calls(ctx, error_class='timeout')
    assert not record_provider_call_outcome(ctx, call, outcome='completed')
    row = turn_call_accounting(ctx)['verification_receipts'][0]
    assert row['outcome'] == 'failed' and row['returned_model'] is None
    assert row['actual_cost']['amount'] is None and sink.call_count == 1


def test_unreadable_metadata_does_not_discard_the_answer():
    r = rich_response()
    r.raw_response['cycle'] = r.raw_response
    result = receipt(r)
    assert result['metadata_state'] == 'unreadable'
    assert result['verification']['status'] == 'CLAIMED'
    assert result['actual_cost']['amount'] is None


def test_generic_header_metadata_is_allowlisted_case_insensitively():
    r = SimpleNamespace(raw_response={'model': 'm'}, provider_metadata={'response_headers': {
        'X-Request-ID': 'upstream-id', 'Authorization': 'secret', 'Set-Cookie': 'private'}})
    result = receipt(r)
    assert result['provider_request_id'] == 'upstream-id'
    assert result['upstream_metadata']['headers'] == {'x-request-id': 'upstream-id'}
    assert 'secret' not in json.dumps(result)


def test_unreadable_response_never_retains_a_promoted_level(monkeypatch):
    def verify(facts, response):
        return pv.Validation(pv.VerificationStatus.UPSTREAM_VERIFIED, facts['binding_sha256'],
                             'synthetic-verifier', ('evidence:synthetic',), 1e12, 'Synthetic')
    monkeypatch.setitem(pv._VERIFIERS, 'usepod-byok', verify)
    r = rich_response()
    r.raw_response['cycle'] = r.raw_response
    out = receipt(r)
    assert out['verification']['status'] == 'CLAIMED'
    assert not out['verification']['independent_model_proof']
