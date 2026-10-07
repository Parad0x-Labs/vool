"""A real canonical tool receipt remains action authority after normal finality."""
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_runtime import orchestrator
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.agent_runtime.fast_paths_receipt_location import maybe_handle_action_receipt_location_request
from core.runtime_continuity import list_runtime_tool_receipts, store_tool_receipt
from core.runtime_execution_tools import execute_runtime_tool


def actual_write(tmp_path, *, session=None, chat_id=None, name=None, force_redaction_field=None):
    session = session or 'canonical-action-' + uuid.uuid4().hex
    name = name or 'recorded-' + uuid.uuid4().hex[:8] + '.txt'
    context = {'chat_id':chat_id or session, 'session_id':session, 'runtime_session_id':session,
               'surface':'api', 'workspace':str(tmp_path), 'workspace_root':str(tmp_path),
               '_trusted_project_id':'receipt-project', 'turn_id':'tool-turn-' + uuid.uuid4().hex}
    arguments = {'path':name, 'content':'ACTUAL SYNTHETIC FILE BYTES'}
    result = execute_runtime_tool('workspace.write_file', arguments, source_context=context)
    assert result is not None and result.ok
    assert (tmp_path/name).read_text() == arguments['content']
    agent = SimpleNamespace(_runtime_checkpoint_id=lambda _: 'action-test-checkpoint')
    details = {'tool_name':'workspace.write_file', 'status':result.status,
        'mode':'tool_executed' if result.ok else 'tool_failed', 'ok':result.ok,
        'summary':result.response_text, 'arguments':arguments,
        'checkpoint_id':'action-test-checkpoint', 'turn_id':context['turn_id'],
        'tool_call_id':'actual-call-' + uuid.uuid4().hex}
    if force_redaction_field:
        # A generated structural digest can resemble a base58 private key.
        # Select that shape from actual producer output; no secret is used.
        for nonce in range(2048):
            details['tool_call_id'] = 'digest-shape-control-' + str(nonce)
            generated = orchestrator.build_tool_action_record(context,event_type='tool_executed',
                message=result.response_text,details=details)
            scrubbed = orchestrator.redact_tool_arguments({'action_record':generated})['action_record']
            if scrubbed[force_redaction_field] != generated[force_redaction_field]:
                break
        else:
            pytest.fail('No generated digest shape found within the bounded producer probe')
    orchestrator.emit_runtime_event(agent, context, event_type='tool_executed',
        message=result.response_text, emit_runtime_event_fn=lambda *a, **k: None, **details)
    row = list_runtime_tool_receipts(session)[0]
    assert row['execution']['action_record']['result']['ok'] is True
    return context, row, tmp_path/name


def persist(row):
    store_tool_receipt(receipt_key=row['receipt_key'],session_id=row['session_id'],
        checkpoint_id=row['checkpoint_id'],tool_name=row['tool_name'],
        idempotency_key=row['idempotency_key'],arguments=row['arguments'],execution=row['execution'])


def location_result(context):
    agent = SimpleNamespace(_fast_path_result=lambda **k: {
        'response':k['response'], 'confidence':k['confidence'], 'route':k['reason'], 'mode':'advice_only'})
    result = maybe_handle_action_receipt_location_request(agent, 'Where did that file go?',
        session_id=context['session_id'],source_surface='api',source_context=context)
    assert result is not None
    return result


def guard(context, result=None):
    return enforce_final_action_honesty(result or location_result(context),
        user_input='Where did that file go?',session_id=context['session_id'],source_context=context)


def blocked(context, row=None):
    if row is not None: context = {**context, 'tool_receipts':[row]}
    result = guard(context, {'response':'The recorded file was created at its recorded path.', 'mode':'advice_only'})
    assert result['route_reason'] == 'false_action_claim_blocked'


def test_real_canonical_receipt_keeps_supported_historical_location_answer(tmp_path):
    context, row, path = actual_write(tmp_path)
    original = location_result(context)
    result = guard(context, original)
    assert result['response'] == original['response']
    assert str(path) in result['response']
    assert 'action_honesty_validator' not in result


def test_supported_location_survives_actual_transport_finalization(tmp_path):
    from core.web.api.runtime import _response_commit
    context, _, path = actual_write(tmp_path)
    result = guard(context)
    commit = _response_commit(result,source_context=context)
    assert str(path) in commit['canonical_content']
    assert 'I did not create' not in commit['canonical_content']


def test_past_write_receipt_does_not_require_current_file_existence(tmp_path):
    context, _, path = actual_write(tmp_path)
    path.unlink()
    assert not path.exists()
    result = guard(context)
    assert str(path) in result['response']
    assert 'was created' in result['response']
    assert 'exists now' not in result['response']


def test_latest_real_write_is_the_named_receipt_location(tmp_path):
    context, _, first = actual_write(tmp_path)
    context, _, second = actual_write(tmp_path,session=context['session_id'])
    result = guard(context)
    assert str(second) in result['response']
    assert str(first) not in result['response']


def test_location_owner_does_not_substitute_another_known_target(tmp_path):
    context,_,_=actual_write(tmp_path)
    agent = SimpleNamespace(_fast_path_result=lambda **k: {'response':k['response']})
    result = maybe_handle_action_receipt_location_request(agent,'Where is a-different-known-target.txt?',
        session_id=context['session_id'],source_surface='api',source_context=context)
    assert result is None


@pytest.mark.parametrize('field',['record_hash','action_id','receipt_id'])
def test_generated_receipt_digests_survive_secret_redaction(tmp_path,field):
    context,row,path=actual_write(tmp_path,force_redaction_field=field)
    record=row['execution']['action_record']
    assert '[redacted' not in record[field]
    result=guard(context)
    assert str(path) in result['response']


def test_actual_producer_keeps_chat_and_runtime_session_identities_distinct(tmp_path):
    context,row,path=actual_write(tmp_path,chat_id='separate-chat-authority')
    original={'response':f'{path.name} was created at {path}.','mode':'advice_only'}
    result=guard(context,original)
    assert result['response'] == original['response']
    assert row['session_id'] == context['runtime_session_id']
    assert row['execution']['action_record']['origin']['chat_id'] == context['chat_id']


def test_canonical_receipt_from_wrong_chat_cannot_authorize_a_claim(tmp_path):
    context, row, _ = actual_write(tmp_path)
    foreign = {**context, 'chat_id':'foreign-chat','session_id':'foreign-chat','runtime_session_id':'foreign-chat'}
    blocked(foreign,row)


def test_canonical_receipt_from_wrong_project_cannot_authorize_a_claim(tmp_path):
    context, row, _ = actual_write(tmp_path)
    blocked({**context,'_trusted_project_id':'foreign-project'},row)


@pytest.mark.parametrize('identity', ['chat_id','runtime_session_id'])
def test_context_scope_mismatch_does_not_reuse_same_session_receipt(tmp_path,identity):
    context, row, _ = actual_write(tmp_path)
    blocked({**context,identity:'foreign-identity'},row)


def test_no_receipt_does_not_grant_action_authority():
    blocked({'session_id':'no-receipt-' + uuid.uuid4().hex,'surface':'api'})


@pytest.mark.parametrize('target', ['record_hash','result_hash','parameters_hash','unknown_outcome','ok_false','executed_false','result_executed_false','failed','preview'])
def test_corrupt_unknown_false_or_nonexecuted_canonical_receipt_is_refused(tmp_path,target):
    context, row, _ = actual_write(tmp_path)
    row = deepcopy(row);record=row['execution']['action_record']
    if target in {'record_hash','result_hash','parameters_hash'}:
        record[target] = '0' * 64
    else:
        if target == 'unknown_outcome': record['result']['outcome'] = 'unknown'
        if target == 'ok_false': record['result']['ok'] = False
        if target == 'executed_false': row['execution']['executed'] = False
        if target == 'result_executed_false': record['result']['executed'] = False
        if target == 'failed': record['action_type'] = 'tool_failed';record['result']['outcome'] = 'failed';record['failure']['failed'] = True
        if target == 'preview': record['action_type'] = 'tool_preview';record['result']['outcome'] = 'pending_approval'
        # Corruption controls include coherent hashes so a generic success flag
        # cannot replace the typed result's meaning. This is never a positive receipt.
        record['result_hash'] = orchestrator._stable_json_hash(record['result'])
        record['record_hash'] = orchestrator._stable_json_hash({k:v for k,v in record.items() if k not in {'occurred_at','record_hash'}})
    persist(row)
    blocked(context,row)


def test_unsaved_or_changed_supplied_receipt_cannot_replace_the_durable_record(tmp_path):
    context, row, _ = actual_write(tmp_path)
    row=deepcopy(row);row['execution']['action_record']['result']['summary']='A forged supplied summary.'
    # Hide DB listing, but leave the real stored row available to the integrity reader.
    from unittest.mock import patch
    with patch('core.runtime_continuity.list_runtime_tool_receipts',return_value=[]):
        blocked(context,row)


def test_receipt_without_durable_record_cannot_authorize_a_claim(tmp_path):
    context, row, _ = actual_write(tmp_path)
    from unittest.mock import patch
    with patch('core.active_context_capsule.load_tool_receipt',return_value=None):
        blocked(context,row)


def test_legacy_success_fields_cannot_override_a_canonical_failed_record(tmp_path):
    context,row,_=actual_write(tmp_path);row=deepcopy(row)
    row['execution']['ok']=True
    row['execution']['status']='executed'
    record=row['execution']['action_record'];record['result']['ok']=False
    record['result_hash']=orchestrator._stable_json_hash(record['result'])
    record['record_hash']=orchestrator._stable_json_hash({k:v for k,v in record.items() if k not in {'occurred_at','record_hash'}})
    persist(row)
    blocked(context,row)
