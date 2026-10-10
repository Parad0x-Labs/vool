"""Round 12 reader-lane closure: fresh stories, different readers/processes/homes.

Each story drives a public read of retained memory that the round 7 review never
touched: the swarm learning dataset (this process and a separately launched one),
the adaptive lexicon, the cold-page measurements, per-chat dialogue scoping with
a retry that lifts the barrier, and the registry's booking of the storage stores.
Every story is red on the unpatched base and green once the shared pending-forget
authority governs these reads; the healthy paths (other chats, other scopes,
clean rows) keep serving.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')

CODE = 'TULIP77'
SENTENCE = 'The garden shed combination is TULIP77.'


def _say_forget(home, chat):
    from core.persistent_memory import maybe_handle_memory_command

    return maybe_handle_memory_command(
        f'forget {CODE}',
        session_id=chat,
        source_context={'chat_id': chat, 'runtime_home': str(home), 'client_turn_id': 'r12-readers'},
    )


def _storage_home(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import active_default_db_path, configure_default_db_path
    from storage.migrations import run_migrations

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    configure_default_db_path(str(home / 'data/story.sqlite'))
    run_migrations()
    return home, active_default_db_path()


def _separate_reader(home, db_path, code):
    env = dict(os.environ, VOOL_HOME=str(home), VOOL_WORKSPACE_ROOT=str(home / 'workspace'))
    result = subprocess.run(
        [sys.executable, '-B', '-c', code, str(db_path)],
        env=env, text=True, capture_output=True, timeout=25,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return result.stdout.strip().splitlines()[-1]


def test_swarm_learning_dataset_withholds_a_forgotten_combination_everywhere(tmp_path, monkeypatch):
    """The helper node's saved prompts are retained memory in a store the erasure
    sweep never visits: after a healthy completed forget, the raw rows survive but
    no public dataset read — here or in a separately launched process — serves the
    forgotten combination, while the clean neighbour row still teaches."""
    from storage import swarm_memory

    home, db_path = _storage_home(tmp_path, monkeypatch)
    _ingest(home, 'swarm-story', [(1761000000, SENTENCE)])
    swarm_memory.save_sniffed_context('peer-north', {'summary': SENTENCE}, {'output_text': 'acknowledged'})
    swarm_memory.save_sniffed_context('peer-south', {'summary': 'a weather note for the ridge'}, {'output_text': 'noted'})
    handled, reply = _say_forget(home, 'swarm-story')
    assert handled and 'Forget applied' in reply and 'could not be verified' not in reply, reply

    from storage.db import get_connection

    with get_connection() as conn:
        raw = [dict(row) for row in conn.execute('SELECT prompt_json, result_json FROM sniffed_context').fetchall()]
    assert not any(CODE in str(row) for row in raw), 'a completed forget leaves no raw dataset copy'

    served = swarm_memory.get_recent_contexts()
    assert CODE not in str(served)
    assert any('weather note' in str(row) for row in served)
    assert CODE not in str(swarm_memory.search_recent_contexts('garden shed combination'))

    restarted = _separate_reader(home, db_path, '''
import json, sys
from storage.db import configure_default_db_path
configure_default_db_path(sys.argv[1])
from storage.swarm_memory import get_recent_contexts
print(json.dumps(get_recent_contexts()))
''')
    assert CODE not in restarted and 'weather note' in restarted


def test_adaptive_lexicon_no_longer_teaches_a_forgotten_term(tmp_path, monkeypatch):
    """A learned spelling is retained memory: while its chat's barrier holds, the
    lexicon reader of that chat serves nothing; a different chat still learns."""
    from storage import dialogue_memory as dm

    home, _ = _storage_home(tmp_path, monkeypatch)
    dm.upsert_lexicon_term(CODE, CODE, scope='global')
    assert dm.session_lexicon('lexicon-story') == {CODE.lower(): CODE.lower()}

    from core.memory import revocation

    revocation.begin('lexicon-story', CODE, home)
    assert dm.session_lexicon('lexicon-story') == {}
    assert dm.session_lexicon('a-different-chat') == {CODE.lower(): CODE.lower()}


def test_cold_page_accessors_measure_only_what_the_authority_serves(tmp_path, monkeypatch):
    """The page property, its header and its length are public reads of admitted
    bytes: under a pending barrier they serve and measure nothing, and only the
    handle and stamp remain as a pointer."""
    from core import runtime_paths
    from core.kernel.paged_memory import PagedSession
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    with VoolMemory(runtime_home=home):
        pass
    pages = PagedSession()
    secret_page = pages.admit_turn(SENTENCE, 'Written down.', 1)
    plain_page = pages.admit_turn('water the ferns twice', 'done', 2)
    assert CODE in secret_page.text and secret_page.chars > 0

    revocation.begin('paged-story', CODE, home)
    assert secret_page.text == '' and plain_page.text == ''
    assert secret_page.chars == 0 and plain_page.chars == 0
    assert CODE not in secret_page.header()
    assert 'withheld by the memory authority' in secret_page.header()
    assert plain_page.header().endswith(':: (withheld by the memory authority)')


def test_dialogue_rows_wait_out_their_own_chats_barrier_and_return_when_it_lifts(tmp_path, monkeypatch):
    """A blocked erasure withholds only the owning chat's turns; the neighbour
    chat keeps serving throughout, and once the block lifts a retry completes the
    operation and physically removes the rows."""
    from storage import dialogue_memory as dm
    from storage.db import get_connection

    home, _ = _storage_home(tmp_path, monkeypatch)
    _ingest(home, 'garden-a', [(1761000000, SENTENCE)])
    for chat, text in (('garden-a', SENTENCE), ('garden-b', 'water the ferns twice')):
        dm.record_dialogue_turn(
            chat, raw_input=text, normalized_input=text, reconstructed_input=text,
            topic_hints=[], reference_targets=[], understanding_confidence=1.0, quality_flags=[],
        )
    with get_connection() as conn:
        conn.execute(
            "CREATE TRIGGER deny_story_erasure BEFORE DELETE ON dialogue_turns"
            " BEGIN SELECT RAISE(ABORT, 'story storage unavailable'); END;"
        )
        conn.commit()

    handled, reply = _say_forget(home, 'garden-a')
    assert handled and 'could not be verified' in reply, reply
    ledger = json.loads((home / '.forget-authority/ledger.json').read_text())
    assert any(row['status'] == 'pending' for row in ledger['operations'].values())
    assert dm.recent_dialogue_turns('garden-a') == []
    neighbour = dm.recent_dialogue_turns('garden-b')
    assert [row['raw_input'] for row in neighbour] == ['water the ferns twice']

    with get_connection() as conn:
        conn.execute('DROP TRIGGER deny_story_erasure')
        conn.commit()
    handled, reply = _say_forget(home, 'garden-a')
    assert handled and 'Forget applied' in reply, reply
    ledger = json.loads((home / '.forget-authority/ledger.json').read_text())
    assert all(row['status'] == 'complete' for row in ledger['operations'].values())
    assert dm.recent_dialogue_turns('garden-a') == []
    with get_connection() as conn:
        remaining = conn.execute("SELECT raw_input FROM dialogue_turns WHERE session_id = 'garden-a'").fetchall()
    assert remaining == []
    assert [row['raw_input'] for row in dm.recent_dialogue_turns('garden-b')] == ['water the ferns twice']


def test_registry_books_the_storage_memory_stores_by_their_serving_behavior():
    """The registry inventories the storage stores and calls their content-serving
    functions readers, so a clean audit can never again depend on a storage reader
    being quietly booked as something else."""
    import core.memory.revocation as revocation
    from core.memory.reader_registry import _CLASSIFICATIONS, _audit

    root = Path(revocation.__file__).resolve().parents[2]
    assert _audit(root) == []
    assert 'storage/dialogue_memory.py' in _CLASSIFICATIONS
    assert 'storage/swarm_memory.py' in _CLASSIFICATIONS
    for name in (
        'get_dialogue_session', 'recent_archived_dialogue_topics', 'recent_dialogue_turns',
        'recent_dialogue_turns_any', 'recent_feedback', 'session_lexicon',
    ):
        assert _CLASSIFICATIONS['storage/dialogue_memory.py'][name] == 'reader', name
    for name in ('get_recent_contexts', 'search_recent_contexts'):
        assert _CLASSIFICATIONS['storage/swarm_memory.py'][name] == 'reader', name
