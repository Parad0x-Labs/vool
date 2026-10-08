"""Forget revocation binds every carrier and the completed delivery snapshot."""
import inspect
import sqlite3
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from tests.lifecycle.test_forget_revocation_law_20260929 import (
    _dialogue_item_texts,
    _forget,
    _policy,
    _transcript_text,
    _turn,
    fresh_profile,
)
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile


def ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


@pytest.mark.usefixtures('_hash_backend')
def test_forget_during_delivery_snapshot_blocks_built_content(tmp_path, monkeypatch):
    from core.memory.entries import resolve_memory_access_policy
    profile = _profile(tmp_path)
    chat = 'delivery-snapshot'
    _ingest(profile, chat, [(ts('2026-02-03T09:00:00'), 'My storage unit code is MINT-84.')])
    assert 'MINT-84' in _capsule(profile, chat, 'What is my storage unit code?')[0]
    original = cr._chat_revocations
    happened = []

    def read_then_forget(*args, **kwargs):
        tokens = original(*args, **kwargs)
        # Candidate has one delivery read. Baseline has an initial and a delivery read.
        happened.append(tokens)
        expected = 1 if 'check_revocations' in inspect.signature(cr._packet_only_injection).parameters else 2
        if len(happened) == expected:
            cr.forget_session_memory(chat, 'MINT-84', access_policy=resolve_memory_access_policy(chat_id=chat),
                                     source_context={'chat_id': chat, 'runtime_home': str(profile)})
            assert 'mint 84' in cr.revoked_tokens_for_chat(chat)
        return tokens

    monkeypatch.setattr(cr, '_chat_revocations', read_then_forget)
    capsule, telemetry = _capsule(profile, chat, 'What is my storage unit code?')
    assert happened
    assert 'MINT-84' not in capsule, (capsule, telemetry)


def deny_ledger_reads(monkeypatch):
    original = cr._open_memory
    faults = []

    def faulty_open():
        mem = original()
        def authorize(action, table, *rest):
            if action == sqlite3.SQLITE_READ and table == 'memory_revocations':
                faults.append(table)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        mem._conn.set_authorizer(authorize)
        return mem

    monkeypatch.setattr(cr, '_open_memory', faulty_open)
    return faults


@pytest.mark.parametrize('carrier', ['tiered', 'bootstrap', 'kernel'])
def test_completed_real_forget_stays_erased_when_carrier_ledger_select_fails(fresh_profile, monkeypatch, carrier):
    from core.evidence_kernel.revocation import without_revoked
    chat = 'carrier-' + carrier
    _policy(chat)
    body = 'The Kestrel ferry leaves at 06:15 on winter mornings.'
    _turn(chat, body)
    _turn(chat, 'I visit the shipyard on Tuesdays.')
    facts = [{'occurrence_id': 'review-copy', 'sentence': body, 'value': '06:15'}]

    def read():
        if carrier == 'tiered':
            return '\n'.join(_dialogue_item_texts(chat))
        if carrier == 'bootstrap':
            return _transcript_text(fresh_profile, chat, 'When does the ferry leave?')
        text, kept = without_revoked(body, facts, chat)
        return text + str(kept)

    assert 'Kestrel' in read(), 'control: carrier did not carry the source'
    _forget(fresh_profile, chat, 'Kestrel ferry')
    assert 'kestrel ferry' in cr.revoked_tokens_for_chat(chat)
    # A rebuilt/replayed derivative remains bound by the durable revocation.
    # The ordinary sweep deletes raw turns, so replay the actual pre-forget body.
    if carrier != 'kernel':
        _turn(chat, body)
    assert 'Kestrel' not in read(), 'control: ordinary completed forget did not bind carrier'
    faults = deny_ledger_reads(monkeypatch)
    result = read()
    assert faults, 'real SQLite ledger SELECT did not hit authorizer fault'
    assert 'Kestrel' not in result and '06:15' not in result, result
