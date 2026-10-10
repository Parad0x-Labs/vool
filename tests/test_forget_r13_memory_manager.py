"""Ordinary memory-manager construction respects whole-home freshness."""
import json

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')


@pytest.mark.parametrize('carrier_name', [
    'user_heuristics.jsonl',
    'operator_dense_profile.json',
    'chat_session_meta.json',
    'conversation_log.jsonl',
])
def test_memory_manager_refuses_retained_home_and_initializes_a_fresh_one(
    tmp_path, monkeypatch, carrier_name,
):
    from core import runtime_paths
    from core.memory_manager import MemoryManager

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    carrier = home / 'data' / carrier_name
    carrier.parent.mkdir(parents=True, exist_ok=True)
    retained = json.dumps({
        'session_id': 'pottery-workshop',
        'text': 'The workshop cabinet combination is OCHRE68.',
    }) + '\n'
    carrier.write_text(retained, encoding='utf-8')
    authority = home / '.forget-authority'
    assert not (authority / 'ledger.json').exists()
    with pytest.raises(RuntimeError, match='forget authority is absent for retained memory'):
        MemoryManager()
    assert not (authority / 'ledger.json').exists()
    assert not (authority / 'dependencies.json').exists()
    assert carrier.read_text(encoding='utf-8') == retained

    fresh = tmp_path / 'fresh-manager-home'
    fresh.mkdir()
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', fresh)
    manager = MemoryManager()
    assert manager.short_term == []
    assert (fresh / '.forget-authority/ledger.json').exists()
    manager.log_short_term('user', 'A new workshop starts today.')
    assert manager.get_short_term_context() == 'USER: A new workshop starts today.'
