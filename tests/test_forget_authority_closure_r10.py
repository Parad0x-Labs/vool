"""Operation-wide authority, all carrier fields, and reader inventory closure."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')


def forget(home, chat, text):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    handled, reply = maybe_handle_memory_command(
        text, session_id=chat, access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={'chat_id': chat, 'runtime_home': str(home), 'client_turn_id': 'closure-turn'},
    )
    assert handled
    return reply

_WATCH = '''
import json, sys
from core.vool_memory import VoolMemory
memory = VoolMemory(runtime_home=sys.argv[1])
def read():
    value = memory.occurrence_get(sys.argv[2])
    print(json.dumps(value.body if value else None), flush=True)
read()
for line in sys.stdin:
    if line.strip() == 'exit': break
    read()
memory.close()
'''


def _read(process):
    from tests.test_forget_durable_authority_r7 import _read_child_line
    # That helper parses JSON and has a bounded pipe wait.
    return _read_child_line(process)


@pytest.mark.parametrize('failed_home', ['active', 'selected'])
def test_every_partial_home_withholds_existing_process_new_process_and_restart(tmp_path, monkeypatch, failed_home):
    from core import runtime_paths
    from core.context_namespace import ensure_chat_namespace
    from core.vool_memory import VoolMemory

    active = _profile(tmp_path)
    selected = tmp_path / 'selected'
    selected.mkdir()
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', active)
    homes = {'active': active, 'selected': selected}
    chat = 'operation-wide'
    ensure_chat_namespace(chat, grant_current_receipts=False)
    memories = {}
    occurrences = {}
    watchers = {}
    try:
        for name, home in homes.items():
            memory = memories[name] = VoolMemory(runtime_home=home)
            occurrences[name] = memory.occurrence_store(
                chat_scope=chat, role='user', authority='observed-user-statement', body='Locker value MINT84.',
            )
            watchers[name] = subprocess.Popen(
                [sys.executable, '-c', _WATCH, str(home), occurrences[name].occurrence_id],
                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert _read(watchers[name]) == 'Locker value MINT84.'
        original = os.replace

        def denied(src, dst, *args, **kwargs):
            if Path(dst) == homes[failed_home] / '.forget-authority/ledger.json':
                raise OSError('one authority cannot publish')
            return original(src, dst, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(os, 'replace', denied)
            reply = forget(selected, chat, 'forget MINT84')
        assert 'could not be verified' in reply
        assert any(row['status'] == 'pending' for home in homes.values()
                   for row in json.loads((home / '.forget-authority/ledger.json').read_text())['operations'].values())
        for name, home in homes.items():
            assert memories[name].occurrence_get(occurrences[name].occurrence_id) is None
            watchers[name].stdin.write('read\n')
            watchers[name].stdin.flush()
            assert _read(watchers[name]) is None
            child = subprocess.run([sys.executable, '-c', _WATCH, str(home), occurrences[name].occurrence_id],
                                   input='exit\n', text=True, capture_output=True, timeout=20)
            assert child.returncode == 0, child.stderr
            assert json.loads(child.stdout) is None
            memories[name].close()
            memories[name] = VoolMemory(runtime_home=home)
            assert memories[name].occurrence_get(occurrences[name].occurrence_id) is None
    finally:
        for process in watchers.values():
            process.communicate('exit\n', timeout=20)
            assert process.returncode == 0
        for memory in memories.values():
            memory.close()


def test_no_published_barrier_leaves_both_stores_readable_and_reports_nothing_forgotten(tmp_path, monkeypatch):
    from core.context_namespace import ensure_chat_namespace
    from core.vool_memory import VoolMemory

    active = _profile(tmp_path)
    selected = tmp_path / 'selected'
    chat = 'no-publication'
    ensure_chat_namespace(chat, grant_current_receipts=False)
    with VoolMemory(runtime_home=active) as first, VoolMemory(runtime_home=selected) as second:
        sources = [(memory, memory.occurrence_store(chat_scope=chat, role='user',
                    authority='observed-user-statement', body='Locker value MINT84.')) for memory in (first, second)]
        before = [[tuple(row) for row in memory._conn.execute('SELECT * FROM source_occurrences')] for memory in (first, second)]
        ledgers = [(home / '.forget-authority/ledger.json').read_bytes() for home in (active, selected)]
        original = os.replace

        def denied(src, dst, *args, **kwargs):
            if Path(dst).name == 'ledger.json':
                raise OSError('both publications unavailable')
            return original(src, dst, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(os, 'replace', denied)
            reply = forget(selected, chat, 'forget MINT84')
        assert reply.startswith('Nothing was forgotten.'), reply
        assert [[tuple(row) for row in memory._conn.execute('SELECT * FROM source_occurrences')]
                for memory in (first, second)] == before
        assert [(home / '.forget-authority/ledger.json').read_bytes() for home in (active, selected)] == ledgers
        for memory, source in sources:
            assert memory.occurrence_get(source.occurrence_id).body == 'Locker value MINT84.'


@pytest.mark.parametrize('missing', ['ledger.json', 'dependencies.json'])
def test_unavailable_dependent_authority_fails_closed(tmp_path, missing):
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    peer = tmp_path / 'peer'
    with VoolMemory(runtime_home=home) as memory, VoolMemory(runtime_home=peer):
        source = memory.occurrence_store(chat_scope='dependency', role='user',
                                        authority='observed-user-statement', body='The orchard key is silver.')
        operations = revocation.begin_group('dependency', 'MINT84', [home, peer])
        for owner, operation in operations:
            revocation.complete(operation, owner)
        assert memory.occurrence_get(source.occurrence_id) is not None
        path = peer / '.forget-authority' / missing
        path.unlink()
        assert memory.occurrence_get(source.occurrence_id) is None
        child = subprocess.run([sys.executable, '-c', _WATCH, str(home), source.occurrence_id],
                               input='exit\n', text=True, capture_output=True, timeout=20)
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout) is None


def test_block_name_is_erased_and_a_retry_can_finish(tmp_path):
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    chat = 'block-sweep'
    _ingest(home, chat, [(1760000000, 'The orchard key is silver.')])
    with VoolMemory(runtime_home=home) as memory:
        memory.block_write('mint84', 'The orchard key is silver.')
        reply = forget(home, chat, 'forget MINT84')
        assert 'could not be verified' not in reply, reply
        assert memory._conn.execute("SELECT 1 FROM memory_blocks WHERE block_name = 'mint84'").fetchone() is None
        assert 'mint84' not in memory.all_block_names()
        assert memory.blocks_for_prompt(['mint84']) == ''
        assert 'could not be verified' not in forget(home, chat, 'forget MINT84')


def test_future_block_json_carrier_is_gated_and_erased(tmp_path):
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    chat = 'block-future'
    _ingest(home, chat, [(1760000000, 'The orchard key is silver.')])
    with VoolMemory(runtime_home=home) as memory:
        memory.block_write('notes', 'The orchard key is silver.')
        memory._conn.execute('ALTER TABLE memory_blocks ADD COLUMN extension JSON')
        memory._conn.execute('UPDATE memory_blocks SET extension = ?', ('{"\\u006dint84": "retained"}',))
        memory._conn.commit()
        operation = revocation.begin(chat, 'MINT84', home)
        revocation.complete(operation, home)
        assert memory.block_read('notes') is None
        assert memory.all_block_names() == []
        assert memory.blocks_for_prompt(['notes']) == ''
        reply = forget(home, chat, 'forget MINT84')
        assert 'could not be verified' not in reply, reply
        assert memory._conn.execute('SELECT 1 FROM memory_blocks').fetchone() is None


@pytest.mark.parametrize('reader', ['all_block_names', 'node_count', 'occurrence_count', 'occurrence_active_count', 'occurrence_ids_for_batch'])
def test_owner_metadata_readers_withhold_pending_content(tmp_path, reader):
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    chat = 'metadata-readers'
    with VoolMemory(runtime_home=home) as memory:
        memory.block_write('mint84', 'Locker value MINT84.')
        memory.node_store('Locker value MINT84.', [], [], '', [1.0, 0.0])
        memory.occurrence_store(chat_scope=chat, role='user', authority='observed-user-statement',
                                body='Locker value MINT84.', import_batch='batch')
        kwargs = {'chat_scope': chat} if reader in {'occurrence_count', 'occurrence_active_count'} else {}
        args = ('batch',) if reader == 'occurrence_ids_for_batch' else ()
        read = getattr(memory, reader)
        assert read(*args, **kwargs)
        revocation.begin(chat, 'MINT84', home)
        assert not read(*args, **kwargs)


def test_registry_covers_all_memory_modules_and_all_public_apis():
    from core.memory.reader_registry import _audit

    assert _audit(Path(__file__).resolve().parents[1]) == []


@pytest.mark.parametrize('module,owner,name', [
    ('core/memory_receipts.py', None, 'receipt_for_occurrence'),
    ('core/vool_memory.py', 'VoolMemory', 'all_block_names'),
])
def test_registry_rejects_unclassified_and_ungated_public_reader(module, owner, name):
    from core.memory.reader_registry import _audit

    root = Path(__file__).resolve().parents[1]
    original = (root / module).read_text()
    tree = ast.parse(original)
    container = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner) if owner else tree
    container.body.append(ast.parse('def new_public_reader():\n    return "MINT84"\n').body[0])
    errors = _audit(root, source_overrides={module: ast.unparse(tree)})
    assert any('unclassified' in error and module in error for error in errors)
    tree = ast.parse(original)
    container = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner) if owner else tree
    target = next(node for node in container.body if isinstance(node, ast.FunctionDef) and node.name == name)
    target.body = [ast.Return(value=ast.Constant(value='MINT84'))]
    target.decorator_list = []
    errors = _audit(root, source_overrides={module: ast.unparse(tree)})
    assert module + ':' + (owner + '.' if owner else '') + name + ': ungated reader' in errors


def test_unrelated_chat_does_not_depend_on_another_chats_broken_home(tmp_path):
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    broken = tmp_path / 'broken'
    healthy = tmp_path / 'healthy'
    with VoolMemory(runtime_home=home) as memory, VoolMemory(runtime_home=broken), VoolMemory(runtime_home=healthy):
        operations = revocation.begin_group('blocked-chat', 'MINT84', [home, broken])
        for owner, operation in operations:
            revocation.complete(operation, owner)
        (broken / '.forget-authority/ledger.json').write_text('{broken')
        assert not memory.payload_is_current('The orchard key is silver.', chat_id='blocked-chat')
        operations = revocation.begin_group('healthy-chat', 'BLUE92', [home, healthy])
        for owner, operation in operations:
            revocation.complete(operation, owner)
        assert memory.payload_is_current('The orchard key is silver.', chat_id='healthy-chat')
        assert not memory.payload_is_current('The orchard key is silver.')


@pytest.mark.parametrize('reader', ['jsonl', 'metadata', 'short_term', 'paged', 'telemetry'])
def test_low_level_memory_readers_use_the_common_pending_gate(tmp_path, monkeypatch, reader):
    from core import context_retrieval, runtime_paths
    from core.kernel.paged_memory import PagedSession, RecallRefused
    from core.memory import revocation
    from core.memory.carriers import read_session_metadata
    from core.memory.files import load_jsonl, memory_entries_path, rewrite_jsonl
    from core.memory_manager import MemoryManager
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    with VoolMemory(runtime_home=home):
        pass
    body = 'The locker value is MINT84.'
    chat = 'low-level-reader'
    if reader == 'jsonl':
        path = memory_entries_path()
        rewrite_jsonl(path, [{'session_id': chat, 'text': body}])
        def read():
            return load_jsonl(path)
    elif reader == 'metadata':
        path = home / 'data/chat_session_meta.json'
        path.write_text(json.dumps({chat: {'title': body}}))
        def read():
            return read_session_metadata(path)
    elif reader == 'short_term':
        memory = MemoryManager()
        memory.log_short_term('user', body)
        read = memory.get_short_term_context
    elif reader == 'paged':
        pages = PagedSession()
        pages.admit_turn(body, 'Noted.', 1)

        def read():
            try:
                return pages.recall('p0001')
            except RecallRefused:
                return ''
    else:
        context_retrieval.update_retrieval_telemetry(selected_facts=[body])
        read = context_retrieval.get_last_retrieval_telemetry
    assert 'MINT84' in str(read())
    revocation.begin(chat, 'MINT84', home)
    assert 'MINT84' not in str(read())


@pytest.mark.parametrize('state', ['pending', 'withdrawn'])
def test_receipt_write_results_and_rebuild_cannot_recreate_withheld_sources(tmp_path, state):
    from core.memory import revocation
    from core.memory_receipts import rebuild_missing_receipts, write_receipt
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    chat = 'receipt-write-result'
    body = 'My kiln locker code is 7428.'
    _ingest(home, chat, [(1760000000, body)])
    with VoolMemory(runtime_home=home) as memory:
        occurrence = memory.occurrence_search('kiln locker code', chat_scope=chat)[0][0]
        assert write_receipt(memory, occurrence, chat_scope=chat).receipt is not None
        if state == 'pending':
            revocation.begin(chat, '7428', home)
        else:
            assert forget(home, chat, 'Forget the kiln locker code, it is wrong.').startswith('Nothing was removed')
        result = write_receipt(memory, occurrence, chat_scope=chat)
        assert result.status == 'failed'
        assert result.receipt is None
        memory._conn.execute('DELETE FROM memory_receipts')
        memory._conn.commit()
        assert rebuild_missing_receipts(memory, chat) == 0
        assert memory._conn.execute('SELECT COUNT(*) FROM memory_receipts').fetchone()[0] == 0
