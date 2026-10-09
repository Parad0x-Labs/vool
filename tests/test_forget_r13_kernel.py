"""Round 13 kernel lane: ordinary constructors never bootstrap over retained carriers,
and kernel refusal output carries only serving-gate-authorized content.

Fresh stories (distinct tokens, bodies, scopes and processes from the round 8 review
probes). Red on 2558d23, green on the lane head. No mocks: every case drives the public
constructors, the public assembly API and the real pending-forget authority.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')

TOKEN = 'CINDER34'
BODY = 'My kiln shelf pass is CINDER34. The glaze code is RUST07.'
OTHER = 'The spare glaze sieve is RUST07 and lives by the kiln door.'


def _child(home, code, *args):
    env = dict(os.environ, VOOL_HOME=str(home), VOOL_WORKSPACE_ROOT=str(home / 'workspace'))
    result = subprocess.run([sys.executable, '-B', '-c', code, *map(str, args)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_restarted_semantic_store_refuses_a_legacy_carrier_home_across_processes(tmp_path, monkeypatch):
    """Finding 5, other process + other legacy carrier: a fresh process opening the
    semantic store over a home that retains operator_dense_profile.json must fail
    closed instead of inventing empty authority over the retained row."""
    from core import runtime_paths
    from core.memory.files import load_jsonl
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    carrier = home / 'data/operator_dense_profile.json'
    carrier.parent.mkdir(parents=True, exist_ok=True)
    carrier.write_text(json.dumps({'legacy': {'text': BODY}}) + '\n', encoding='utf-8')
    assert not (home / '.forget-authority/ledger.json').exists()
    outcome = _child(home, '''
import json,sys
from core.vool_memory import VoolMemory
try:
    with VoolMemory(): pass
    print(json.dumps({"opened": True}))
except RuntimeError:
    print(json.dumps({"opened": False}))
''')
    ledger = home / '.forget-authority/ledger.json'
    print('SEMANTIC_RESTART_LEGACY', outcome, ledger.exists())
    assert outcome['opened'] is False, outcome
    assert not ledger.exists(), ledger.read_text() if ledger.exists() else None
    # A genuinely new home still initializes: the policy refuses retained memory,
    # never a fresh start.
    fresh = tmp_path / 'fresh-home'
    fresh.mkdir()
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', fresh)
    with VoolMemory(runtime_home=fresh):
        pass
    assert (fresh / '.forget-authority/ledger.json').exists()


def test_paged_constructor_refuses_a_retained_chat_meta_home_but_initializes_a_new_one(tmp_path, monkeypatch):
    """Finding 5, paged constructor on a different legacy carrier family: PagedSession
    must use the same whole-carrier freshness policy, and a new home still opens."""
    from core import runtime_paths
    from core.kernel.paged_memory import PagedSession

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    carrier = home / 'data/chat_session_meta.json'
    carrier.parent.mkdir(parents=True, exist_ok=True)
    carrier.write_text(json.dumps({'session_id': 'kiln-legacy', 'text': BODY}) + '\n', encoding='utf-8')
    rejected = False
    try:
        PagedSession()
    except RuntimeError:
        rejected = True
    ledger = home / '.forget-authority/ledger.json'
    print('PAGED_LEGACY_BOOTSTRAP', rejected, ledger.exists())
    assert rejected and not ledger.exists()
    fresh = tmp_path / 'fresh-paged-home'
    fresh.mkdir()
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', fresh)
    session = PagedSession()
    assert len(session) == 0
    assert (fresh / '.forget-authority/ledger.json').exists()


def test_undercoverage_names_uncovered_rows_only_through_the_serving_gate(tmp_path, monkeypatch):
    """Finding 7: the kernel refusal exception is public debug output rendered before
    the final gate; every caller-supplied spine string is authorized through the same
    serving gate as a shipped assembly. With the gate open (no barrier) rows render
    verbatim; under a pending barrier the gate withholds everything, so the exception
    keeps only the typed count, the budget and typed placeholders."""
    from core import runtime_paths
    from core.kernel.paged_memory import ContextUndercovered, PagedSession, RecallRefused
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    with VoolMemory(runtime_home=home):
        pass
    pages = PagedSession()
    pages.admit_turn(BODY, 'recorded', 1)
    rows = [{'id': 'shelf-9', 'description': BODY, 'reason': 'pending work'}]
    # Before any barrier: the row passed the serving gate and stays verbatim.
    with pytest.raises((ContextUndercovered, RecallRefused)) as open_gate:
        pages.assemble('continue', ledger_rows=rows, budget_chars=1)
    assert 'shelf-9' in str(open_gate.value) and BODY in str(open_gate.value)
    assert open_gate.value.uncovered[0] == ('shelf-9', BODY)
    revocation.begin('kiln-page', TOKEN, home)
    exposed = None
    try:
        pages.assemble('continue', ledger_rows=rows, budget_chars=1)
    except (ContextUndercovered, RecallRefused) as exc:
        exposed = {'message': str(exc), 'uncovered': getattr(exc, 'uncovered', None)}
    print('UNDERCOVERAGE_GATE', exposed)
    assert exposed is not None
    assert TOKEN not in str(exposed) and 'RUST07' not in str(exposed)
    assert 'shelf-9' not in str(exposed) and BODY not in str(exposed)
    assert exposed['uncovered'][0][0] == '<withheld>'
    assert exposed['uncovered'][0][1] == '<withheld: memory authority>'
    assert exposed['message'].startswith('context assembly refused: budget 1 chars')
    assert '(1 uncovered)' in exposed['message']


def test_healthy_home_forget_still_completes_and_other_chats_rows_survive(tmp_path, monkeypatch):
    """Designed positive control (green on the base by design, like the round 12
    writers control): the stricter constructor policy never refuses a healthy home,
    a healthy forget completes, and the other chat's row survives erasure."""
    from core import runtime_paths
    from core.memory.files import conversation_log_path, load_jsonl
    from core.persistent_memory import maybe_handle_memory_command
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    _ingest(home, 'kiln-main', [(1761300100, BODY)])
    _ingest(home, 'kiln-other', [(1761300200, OTHER)])
    with VoolMemory(runtime_home=home):
        pass
    handled, reply = maybe_handle_memory_command(
        'forget ' + TOKEN, session_id='kiln-main',
        source_context={'chat_id': 'kiln-main', 'runtime_home': str(home), 'client_turn_id': 'r13-kernel-control'})
    print('HEALTHY_CONTROL', reply, load_jsonl(conversation_log_path()))
    assert handled and 'Forget applied' in reply, reply
    surviving = load_jsonl(conversation_log_path())
    assert any('RUST07' in str(row) for row in surviving), surviving
    assert not any('CINDER34' in str(row) for row in surviving), surviving
    with VoolMemory(runtime_home=home) as memory:
        assert memory.blocks_for_prompt(['user_profile']) == ''
