"""Round 13 stores-lane closure: the store, not the caller, owns its authority.

Fresh stories for the storage causes the round 8 review left open: a configured
database read from a separately launched process (a different reader and layout
than the review's), the swarm learning dataset's raw copies under an owned
forget (attributed and neighbour-scoped rows), the public atomic writer as a
restoration route into a profile carrier after completion, and writers aimed at
another home's carrier and database from a restarted client. Each denial story
is red on the unpatched base and green once authority comes from the actual
store; the healthy story (a clean forget completes while a neighbour chat's row
survives) stays green on both.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')


def _forget(home, chat, token, client):
    from core.persistent_memory import maybe_handle_memory_command

    return maybe_handle_memory_command(
        f'forget {token}',
        session_id=chat,
        source_context={'chat_id': chat, 'runtime_home': str(home), 'client_turn_id': client},
    )


def _other_home_client(home, code, *args):
    env = dict(os.environ, VOOL_HOME=str(home), VOOL_WORKSPACE_ROOT=str(home / 'workspace'))
    result = subprocess.run(
        [sys.executable, '-B', '-c', code, *map(str, args)],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return result.stdout.strip().splitlines()[-1]


def test_a_configured_session_reader_in_a_new_process_serves_only_its_owner(tmp_path, monkeypatch):
    """The dialogue store sits directly in its home (no data directory): a second
    process that initializes its own home and configures the SAME database must
    still discover the persisted barrier through the store itself — the session
    summary that names the value stays withheld there too."""
    from core import runtime_paths
    from core.memory import revocation
    from core.vool_memory import VoolMemory
    from storage.db import configure_default_db_path
    from storage.dialogue_memory import update_dialogue_session

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    database = home / 'meadow.sqlite'
    configure_default_db_path(str(database))
    with VoolMemory(runtime_home=home):
        pass
    update_dialogue_session(
        'meadow-keeper',
        last_subject='The meadow gate code is ORCHID42.',
        topic_hints=['meadow gate'],
        last_intent_mode=None,
    )
    revocation.begin('meadow-keeper', 'ORCHID42', home)

    served = _other_home_client(tmp_path / 'restart-client', '''
import json, sys
from core.vool_memory import VoolMemory
with VoolMemory():
    pass
from storage.db import configure_default_db_path
configure_default_db_path(sys.argv[1])
from storage.dialogue_memory import get_dialogue_session
print(json.dumps(get_dialogue_session('meadow-keeper')))
''', database)
    print('CONFIGURED_SESSION_READER_OTHER_PROCESS', served)
    assert 'ORCHID42' not in served


def test_a_completed_forget_holds_the_raw_swarm_store_clean_of_its_scope(tmp_path, monkeypatch):
    """The helper node's dataset is retained memory: after a healthy completed
    forget, no raw row of this chat's scope carries the value anymore, while a
    row attributed to the neighbour chat keeps its own bytes on disk."""
    from core import runtime_paths
    from storage import swarm_memory
    from storage.db import configure_default_db_path, get_connection
    from storage.migrations import run_migrations

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    configure_default_db_path(str(home / 'data/atrium.sqlite'))
    run_migrations()
    _ingest(home, 'atrium-story', [(1761400000, 'The tide table key is FERNEL21.')])
    swarm_memory.save_sniffed_context(
        'atrium-peer', {'session_id': 'atrium-story', 'note': 'The tide table key is FERNEL21.'}, {'answer': 'noted'})
    swarm_memory.save_sniffed_context(
        'lobby-peer', {'session_id': 'atrium-lobby', 'note': 'The lobby door code is CLAM37.'}, {'answer': 'ok'})

    handled, reply = _forget(home, 'atrium-story', 'FERNEL21', 'r13-stores-atrium')
    assert handled and 'Forget applied' in reply and 'could not be verified' not in reply, reply
    with get_connection() as conn:
        raw = [dict(row) for row in conn.execute('SELECT * FROM sniffed_context').fetchall()]
    print('SWARM_RAW_AFTER_COMPLETED_FORGET', reply, raw)
    assert 'FERNEL21' not in str(raw)
    assert 'CLAM37' in str(raw)


def test_a_neighbour_row_and_a_clean_forget_survive_together(tmp_path, monkeypatch):
    """Positive control: a healthy forget still completes, and a helper row that
    belongs to another chat keeps teaching the dataset afterwards."""
    from core import runtime_paths
    from storage import swarm_memory
    from storage.db import configure_default_db_path
    from storage.migrations import run_migrations

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    configure_default_db_path(str(home / 'data/ridge.sqlite'))
    run_migrations()
    sentence = 'The summit ledger number is JUNIPER33.'
    _ingest(home, 'ridge-story', [(1761500000, sentence)])
    swarm_memory.save_sniffed_context('ridge-peer', {'summary': sentence}, {'answer': 'kept'})
    swarm_memory.save_sniffed_context('far-peer', {'session_id': 'ridge-other', 'summary': 'a rain gauge note'}, {'answer': 'ok'})

    handled, reply = _forget(home, 'ridge-story', 'JUNIPER33', 'r13-stores-ridge')
    assert handled and 'Forget applied' in reply and 'could not be verified' not in reply, reply
    served = swarm_memory.get_recent_contexts()
    assert 'JUNIPER33' not in str(served)
    assert 'rain gauge' in str(served)


def test_the_public_atomic_writer_refuses_a_restored_heuristic_row(tmp_path, monkeypatch):
    """After a forget is completed and verified, the public atomic writer is not
    a side door: restoring a heuristics row that names the value is refused and
    the carrier keeps its erased bytes."""
    from core import runtime_paths
    from core.memory.files import user_heuristics_path, write_text_atomic

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    body = 'The drying shed rule is BASIL09.'
    _ingest(home, 'stillroom-story', [(1761600000, body)])
    handled, reply = _forget(home, 'stillroom-story', 'BASIL09', 'r13-stores-stillroom')
    assert handled and 'Forget applied' in reply and 'could not be verified' not in reply, reply

    carrier = user_heuristics_path()
    before = carrier.read_text(encoding='utf-8')
    refused = False
    try:
        write_text_atomic(carrier, json.dumps({'session_id': 'stillroom-story', 'signal': body}) + '\n')
    except RuntimeError:
        refused = True
    print('ATOMIC_HEURISTICS_RESTORE', refused, carrier.read_text(encoding='utf-8'))
    assert refused
    assert 'BASIL09' not in carrier.read_text(encoding='utf-8')
    assert carrier.read_text(encoding='utf-8') == before


def test_a_foreign_home_append_never_reaches_an_erased_carrier(tmp_path, monkeypatch):
    """A completed barrier lives in the carrier's own home: a restarted client in
    a fresh home appending to this home's summary carrier is refused, whatever
    its own home's authority says."""
    from core import runtime_paths
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    with VoolMemory(runtime_home=home):
        pass
    operation = revocation.begin('summit-story', 'MOSSDALE7', home)
    revocation.complete(operation, home)
    carrier = home / 'data/session_summaries.jsonl'
    carrier.write_text('')

    returned = _other_home_client(tmp_path / 'append-client', '''
import json, sys
from pathlib import Path
from core.memory import files
try:
    files.append_jsonl(Path(sys.argv[1]), {'session_id': 'summit-story', 'summary': 'The spoil heap code is MOSSDALE7.'})
    print(json.dumps('accepted'))
except RuntimeError:
    print(json.dumps('refused'))
''', carrier)
    raw = carrier.read_text(encoding='utf-8')
    print('FOREIGN_HOME_APPEND', returned, raw)
    assert 'MOSSDALE7' not in raw


def test_a_restarted_feedback_writer_cannot_reenter_a_completed_value(tmp_path, monkeypatch):
    """A completed forget governs the configured database it was published for:
    a restarted client pointing that database writes no feedback row whose
    correction carries the value."""
    from core import runtime_paths
    from core.memory import revocation
    from core.vool_memory import VoolMemory
    from storage.db import configure_default_db_path, get_connection
    from storage.migrations import run_migrations

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    database = home / 'data/feedback.sqlite'
    configure_default_db_path(str(database))
    run_migrations()
    from storage.dialogue_memory import _init_tables

    _init_tables()
    with VoolMemory(runtime_home=home):
        pass
    operation = revocation.begin('cliffwalk-story', 'PETREL44', home)
    revocation.complete(operation, home)

    returned = _other_home_client(tmp_path / 'feedback-client', '''
import json, sys
from storage.db import configure_default_db_path
configure_default_db_path(sys.argv[1])
from storage.dialogue_memory import record_response_feedback
try:
    record_response_feedback('cliffwalk-story', feedback_type='correction',
                             user_correction='The cliffwalk marker is PETREL44.')
    print(json.dumps('accepted'))
except RuntimeError:
    print(json.dumps('refused'))
''', database)
    with get_connection() as conn:
        raw = [dict(row) for row in conn.execute(
            "SELECT * FROM response_feedback WHERE session_id = 'cliffwalk-story'").fetchall()]
    print('RESTARTED_FEEDBACK_WRITER', returned, raw)
    assert 'PETREL44' not in str(raw)
