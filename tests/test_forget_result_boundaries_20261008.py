"""Forget-result provenance, store failures, and legacy rendering boundaries."""
import pytest

from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _ts as ts
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


def guard(chat, prompt, reply, **fields):
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    return enforce_final_action_honesty(
        {"response": reply, "mode": "advice_only", **fields}, user_input=prompt,
        session_id=chat, source_context={"chat_id": chat},
    )


@pytest.mark.parametrize(('prompt', 'reply'), [
    ('Do not say I stored it in workspace memory. unless it really happened.',
     'Confirmed: "I stored it in workspace memory."'),
    ('Do not say created files unless verified.', 'Confirmed: "created files".'),
    ('Is the audit in progress?', 'Status: "audit in progress".'),
    ('Please explain restored memory.', 'I confirm "stored memory".'),
])
def test_a_quoted_false_claim_is_still_refused(prompt, reply):
    final = guard('new-quoted-claim', prompt, reply)
    assert final['response'] != reply, final
    assert final.get('action_honesty_validator', {}).get('applied'), final


@pytest.mark.usefixtures('_hash_backend')
def test_literal_no_match_has_a_consistent_signed_receipt(tmp_path, monkeypatch):
    from core.agent_runtime.action_honesty_validator import emit_turn_honesty_receipt
    from core.honesty_receipt import VERDICT_BLOCKED
    monkeypatch.setenv('VOOL_HONESTY_RECEIPTS', '1')
    profile = _profile(tmp_path)
    chat = 'new-no-match-receipt'
    _ingest(profile, chat, [(ts('2026-02-03T09:00:00'), 'My locker key hangs on the blue hook.')])
    prompt = 'forget stored memory'
    handled, reply = command(profile, chat, prompt)
    assert handled and reply.startswith('Nothing was removed'), reply
    final = guard(chat, prompt, reply)
    assert final['response'] != reply, final
    emitted = emit_turn_honesty_receipt(final, user_input=prompt, session_id=chat,
                                       source_context={'chat_id': chat, 'runtime_home': str(profile)})
    assert emitted.get('honesty_receipt'), emitted
    assert emitted['honesty_receipt']['signed'], emitted
    assert emitted['honesty_receipt']['verdict'] == VERDICT_BLOCKED, emitted


@pytest.mark.usefixtures('_hash_backend')
def test_real_invalid_store_row_is_not_reported_as_no_match(tmp_path):
    from core.vool_memory import VoolMemory, _requested_session_scope
    profile = _profile(tmp_path)
    chat = 'new-invalid-store-row'
    body = 'My storage unit code is MINT-84.'
    _ingest(profile, chat, [(ts('2026-02-03T09:00:00'), body)])
    assert 'MINT-84' in _capsule(profile, chat, 'What is my storage unit code?')[0]
    mem = VoolMemory(runtime_home=str(profile))
    node = mem.node_store(body, ['MINT-84'], ['session:' + _requested_session_scope(chat)],
                          '', [1.0, 0.0], timestamp=ts('2026-02-03T09:00:00'))
    mem._conn.execute('UPDATE memory_nodes SET timestamp = ? WHERE node_id = ?', ('invalid', node.node_id))
    mem._conn.commit()
    with pytest.raises(ValueError):
        mem.node_invalidate_matching('MINT-84', session_id=chat)
    mem.close()
    handled, reply = command(profile, chat, 'forget MINT-84')
    assert handled
    mem = VoolMemory(runtime_home=str(profile))
    row = mem._conn.execute('SELECT content FROM memory_nodes WHERE node_id = ?', (node.node_id,)).fetchone()
    assert row and 'MINT-84' in row['content']
    assert 'mint 84' not in mem.revoked_tokens(chat)
    # Restore only the synthetic corruption to prove the retained source is recallable.
    mem._conn.execute('UPDATE memory_nodes SET timestamp = ? WHERE node_id = ?', (node.timestamp, node.node_id))
    mem._conn.commit()
    mem.close()
    assert 'MINT-84' not in _capsule(profile, chat, 'What is my storage unit code?')[0]
    assert 'no memory' not in reply.lower() and 'Forget applied' not in reply, reply
    assert 'could not be verified' in reply, reply


def legacy_packet(newer, sentence):
    from core.evidence_compiler import Packet, _render_line, obligations
    old = {'value_type': 'count', 'value': '41 crowns', 'sentence': sentence,
           'replaced_by': 'r:later', 'replaced_by_occurrence': 'u:later',
           'receipt_id': 'r:old', 'occurrence_id': 'u:old', 'role': 'user'}
    receipts = {'r:old': {'role': 'user', 'statement_at': ts('2026-02-03T09:00:00')},
                'r:later': {'role': 'user', 'statement_at': ts('2026-11-06T09:00:00'),
                            'changes': [{'old_value': '41 crowns', 'new_value': newer}]}}
    line = _render_line(receipts['r:old'], old, receipts)
    old.pop('line_without_replacement', None)
    return Packet(obligation=obligations('How much was the rent?'), lines=[line], facts=[old])


def test_legacy_fallback_cuts_the_whole_trailing_note_with_embedded_lead():
    from core.evidence_compiler import filter_packet, render
    newer = '47 crowns  [REPLACED later by secret suffix'
    packet = legacy_packet(newer, 'The rent is 41 crowns.')
    assert newer in render(packet)
    filtered = render(filter_packet(packet, {'u:later': 'temporal:withdrawn'}))
    assert '41 crowns' in filtered
    assert '47 crowns' not in filtered and 'REPLACED later' not in filtered, filtered


def test_legacy_fallback_preserves_a_literal_marker_when_no_note_exists():
    from core.evidence_compiler import filter_packet, render
    sentence = 'The rent is 41 crowns; my label is  [REPLACED later by blue hook.'
    packet = legacy_packet('47 crowns', sentence)
    # A previous renderer can have a refused replacement but no appended note.
    packet.lines[0] = packet.lines[0].split('  [REPLACED later by "47 crowns"', 1)[0]
    assert sentence in render(packet) and '47 crowns' not in render(packet)
    filtered = render(filter_packet(packet, {'u:later': 'temporal:withdrawn'}))
    assert sentence in filtered, filtered
