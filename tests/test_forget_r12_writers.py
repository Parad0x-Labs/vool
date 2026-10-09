"""Round 12 writer lane: initialization policy and public rewrite admission.

Fresh stories, distinct from the round 7 review repros: a separate process
racing a pending barrier, a restarted writer over lost authority, an append
over a legacy carrier home, a torn routing document, and a healthy control.
"""
import json
import os
import subprocess
import sys

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')

SECRET = 'HARBOR29'
BODY = 'The gate passphrase is HARBOR29.'


def _child(home, code, *args):
    env = dict(os.environ, VOOL_HOME=str(home), VOOL_WORKSPACE_ROOT=str(home / 'workspace'))
    result = subprocess.run([sys.executable, '-B', '-c', code, *map(str, args)],
                            env=env, text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_separate_process_rewrite_cannot_restore_a_pending_value(tmp_path, monkeypatch):
    """A public rewrite from another process refuses a pending token's bytes."""
    from core import runtime_paths
    from core.memory import revocation
    from core.memory.files import conversation_log_path

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    chat = 'ferry-log'
    _ingest(home, chat, [(1760000000, BODY)])
    log = conversation_log_path()
    assert SECRET in log.read_text()
    revocation.begin(chat, SECRET, home)
    before = log.read_bytes()
    outcome = _child(home, '''
import json, sys
from pathlib import Path
from core.memory.files import rewrite_jsonl
rows = [{"session_id": sys.argv[2], "user": sys.argv[3], "assistant": ""}]
try:
    rewrite_jsonl(Path(sys.argv[1]), rows)
    print(json.dumps("written"))
except RuntimeError:
    print(json.dumps("refused"))
''', log, chat, BODY)
    print('SEPARATE_PROCESS_REWRITE', outcome)
    assert outcome == 'refused'
    assert log.read_bytes() == before


def test_restarted_writer_cannot_recreate_authority_over_a_pending_home(tmp_path, monkeypatch):
    """Losing the ledger does not let a restarted ordinary writer reset it."""
    from core import runtime_paths
    from core.memory import revocation

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    carrier = home / 'data' / 'chat_session_meta.json'
    carrier.parent.mkdir(parents=True, exist_ok=True)
    carrier.write_text(json.dumps({'legacy-chat': {'title': BODY}}) + '\n')
    revocation.prepare(home, allow_initialize=True, arm=False)
    revocation.begin('legacy-chat', SECRET, home)
    ledger = home / '.forget-authority' / 'ledger.json'
    assert ledger.exists()
    ledger.unlink()
    outcome = _child(home, '''
import json, sys
from pathlib import Path
from core.memory.files import append_jsonl
try:
    append_jsonl(Path(sys.argv[1]), {"session_id": "legacy-chat", "note": "neutral"})
    print(json.dumps("written"))
except RuntimeError:
    print(json.dumps("refused"))
''', home / 'data' / 'notes.jsonl')
    print('RESTARTED_APPEND_AFTER_LOSS', outcome, 'ledger_recreated', ledger.exists())
    assert outcome == 'refused'
    assert not ledger.exists()


def test_append_never_initializes_authority_over_a_dense_profile_home(tmp_path, monkeypatch):
    """A retained dense profile marks the home as not fresh for any writer."""
    from core import runtime_paths
    from core.memory.files import append_jsonl

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    carrier = home / 'data' / 'operator_dense_profile.json'
    carrier.parent.mkdir(parents=True, exist_ok=True)
    carrier.write_text(json.dumps({'legacy': {'preference': BODY}}) + '\n')
    before = carrier.read_bytes()
    ledger = home / '.forget-authority' / 'ledger.json'
    rejected = False
    try:
        append_jsonl(home / 'data' / 'notes.jsonl', {'session_id': 'fresh', 'note': 'neutral'})
    except RuntimeError:
        rejected = True
    print('DENSE_PROFILE_APPEND', 'rejected', rejected, 'ledger', ledger.exists())
    assert rejected
    assert not ledger.exists()
    assert carrier.read_bytes() == before


def test_corrupt_routing_with_lost_ledger_is_never_reinitialized(tmp_path, monkeypatch):
    """Torn routing proves authority existed; an ordinary rewrite fails closed."""
    from core import runtime_paths
    from core.memory.files import rewrite_jsonl

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    authority = home / '.forget-authority'
    authority.mkdir(parents=True)
    routing = authority / 'dependencies.json'
    routing.write_text('{"version": 1, "scopes": {')
    ledger = authority / 'ledger.json'
    rejected = False
    try:
        rewrite_jsonl(home / 'data' / 'session_summaries.jsonl',
                      [{'session_id': 'any-chat', 'summary': 'neutral'}])
    except RuntimeError:
        rejected = True
    print('CORRUPT_ROUTING_LOST_LEDGER', 'rejected', rejected, 'routing', routing.read_text())
    assert rejected
    assert routing.read_text() == '{"version": 1, "scopes": {'
    assert not ledger.exists()


def test_healthy_forget_completes_and_keeps_other_chats_rows(tmp_path, monkeypatch):
    """Positive control: a healthy forget completes; a neighbor row survives."""
    from core import runtime_paths
    from core.context_namespace import ensure_chat_namespace
    from core.memory.files import load_jsonl, session_summaries_path
    from core.persistent_memory import maybe_handle_memory_command

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    ensure_chat_namespace('harbor-a', grant_current_receipts=False)
    ensure_chat_namespace('harbor-b', grant_current_receipts=False)
    path = session_summaries_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"session_id":"harbor-a","future_note":"HARBOR29","summary":"owner row"}\n'
        '{"session_id":"harbor-b","future_note":"HARBOR29","summary":"neighbor row"}\n',
        encoding='utf-8',
    )
    handled, reply = maybe_handle_memory_command(
        'forget HARBOR29', session_id='harbor-a',
        source_context={'chat_id': 'harbor-a', 'runtime_home': str(home),
                        'client_turn_id': 'writers-control'},
    )
    print('HEALTHY_CONTROL_REPLY', reply)
    assert handled and 'Forget applied' in reply, reply
    raw = [json.loads(line) for line in path.read_text().splitlines()]
    assert raw == [{'session_id': 'harbor-b', 'future_note': 'HARBOR29',
                    'summary': 'neighbor row'}]
    assert load_jsonl(path) == raw
    ledger = json.loads((home / '.forget-authority' / 'ledger.json').read_text())
    assert ledger['operations']
    assert all(row['status'] == 'complete' for row in ledger['operations'].values())
