"""Legacy upgrades preserve recall without recreating a persisted forget barrier."""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.memory import revocation
from core.vool_memory import VoolMemory
from tests.test_memory_schema_forward_compat import _make_old_db


def test_legacy_upgrade_is_durable_and_idempotent(tmp_path):
    db = tmp_path / 'data/memory/vool_memory.db'
    _make_old_db(db, content='the gateway port is 18789')
    for _ in range(2):
        memory = VoolMemory(db_path=db)
        try:
            assert memory.node_get('n1').content == 'the gateway port is 18789'
            assert revocation.snapshot('legacy', tmp_path) == ()
        finally:
            memory.close()
    ledger = json.loads((tmp_path / '.forget-authority/ledger.json').read_text())
    assert ledger == {'version': 2, 'operations': {}}


@pytest.mark.parametrize('witness', ('dependencies.json', 'lease-orphan', 'ledger.json'))
def test_initialization_never_repairs_unavailable_authority(tmp_path, witness):
    db = tmp_path / 'data/memory/vool_memory.db'
    _make_old_db(db)
    directory = tmp_path / '.forget-authority'
    directory.mkdir()
    path = directory / witness
    payload = b'armed\n' if witness.startswith('lease-') else b'broken authority'
    path.write_bytes(payload)
    with pytest.raises(RuntimeError):
        VoolMemory(db_path=db)
    assert path.read_bytes() == payload
    assert revocation.authorize_payload('the gateway port is 18789', 'legacy', tmp_path) is False


@pytest.mark.parametrize('table', ('memory_revocations', 'memory_erasure_operations'))
def test_legacy_store_with_recorded_erasure_cannot_get_empty_authority(tmp_path, table):
    db = tmp_path / 'data/memory/vool_memory.db'
    _make_old_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE ' + table + ' (token TEXT)')
        conn.execute('INSERT INTO ' + table + ' VALUES (?)', ('18789',))
    with pytest.raises(RuntimeError, match='recorded erasure'):
        VoolMemory(db_path=db)
    assert not (tmp_path / '.forget-authority/ledger.json').exists()
