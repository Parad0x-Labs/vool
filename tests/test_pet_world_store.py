from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from core.pet_world.model import YardError
from storage import pet_world_store as store
from storage.db import get_connection
from tests._pet_world_support import home as home


def test_persistence_and_concurrent_compare_and_swap(home):
    first = store.load()
    assert first['fresh'] and not first['read_only']
    yard = first['yard']
    yard['name'] = 'Quiet grove'
    raw = json.dumps(yard)
    def write(_):
        try:
            return store.save(raw)['yard']['revision']
        except YardError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [1, 409]
    assert store.load()['yard']['name'] == 'Quiet grove'
    assert not store.load()['fresh']


def test_corruption_is_kept_and_explicit_reset_archives_it(home):
    saved = store.save(json.dumps(store.load()['yard']))
    raw = '{"damaged":true}'
    with closing(get_connection()) as conn:
        conn.execute('UPDATE pet_yard_v1 SET document=?', (raw,))
        conn.commit()
    broken = store.load()
    assert broken['notice'] == 'corrupt' and broken['read_only']
    with pytest.raises(YardError, match='corrupt'):
        store.save(json.dumps(broken['yard']))
    with pytest.raises(YardError, match='conflict'):
        store.reset(1, saved['stamp'], True)
    reset = store.reset(1, broken['stamp'], True)
    assert reset['yard']['revision'] == 2 and not reset['notice']
    with closing(get_connection()) as conn:
        assert conn.execute('SELECT document FROM pet_yard_backups_v1').fetchone()[0] == raw


def test_real_transaction_failure_preserves_previous_copy(home):
    saved = store.save(json.dumps(store.load()['yard']))
    with closing(get_connection()) as conn:
        conn.execute("CREATE TRIGGER yard_write_failure BEFORE UPDATE ON pet_yard_v1 BEGIN SELECT RAISE(ABORT,'disk write refused'); END")
        conn.commit()
    changed = saved['yard']
    changed['name'] = 'New name'
    with pytest.raises(YardError, match='unavailable'):
        store.save(json.dumps(changed))
    assert store.load()['yard']['name'] == ''
    with pytest.raises(YardError):
        store.reset(1, saved['stamp'], True)
    with closing(get_connection()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM pet_yard_backups_v1').fetchone()[0] == 0


def test_oversized_corrupt_row_is_preserved(home):
    saved = store.save(json.dumps(store.load()['yard']))
    with closing(get_connection()) as conn:
        conn.execute('UPDATE pet_yard_v1 SET document=?', ('x'*300000,))
        conn.commit()
    broken = store.load()
    assert broken['read_only'] and broken['notice'] == 'corrupt'
    store.reset(saved['yard']['revision'], broken['stamp'], True)
    with closing(get_connection()) as conn:
        assert conn.execute('SELECT length(document) FROM pet_yard_backups_v1').fetchone()[0] == 300000
