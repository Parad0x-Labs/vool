"""Working state through the actual import doors and canonical owners."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.chat_import import store
from core.context_namespace import load_chat_namespace
from core.execution.mcp_bridge import load_mcp_server_configs
from core.user_rules import active_rules
from tests.test_chat_import_migration import _finish
from tests.test_chat_import_migration import app as app
from tests.test_chat_import_served import EXPORT, _call, _conversation, _export_zip, _upload_and_preview
from tests.test_chat_import_served import app as import_app


def _workspace(tmp_path):
    root = tmp_path / 'harbor-project'
    root.mkdir()
    (root / 'AGENTS.md').write_text('Use the harbor_signal convention. Keep a separate release ledger.\n')
    (root / 'MEMORY.md').write_text('The harbor trial uses amber labels.\n')
    (root / 'plan.txt').write_text('Release the amber trial after the tide check.\n')
    (root / 'skills/harbor').mkdir(parents=True)
    (root / 'skills/harbor/SKILL.md').write_text('Review the harbor_signal ledger before a release.\n')
    (root / 'ledger_server.py').write_text("from pathlib import Path\nPath(__file__).with_name('server_started').write_text('started')\n")
    (root / '.cursor').mkdir()
    secret = 'sk-' + 'Q' * 48
    (root / '.cursor/mcp.json').write_text(json.dumps({'mcpServers': {'ledger': {
        'command': 'python3', 'args': [str(root/'ledger_server.py')], 'env': {'API_KEY': secret}}}}))
    (root / 'cron').mkdir()
    (root / 'cron/jobs.json').write_text(json.dumps({'version': 1, 'jobs': [{
        'id': 'tide', 'name': 'Tide check', 'prompt': 'Review the harbor_signal ledger.',
        'schedule': {'kind': 'once', 'run_at': '2031-01-01T09:00:00Z'}}]}))
    return root, secret


def _review(app, preview, root, **extra):
    status, result = _call(app, 'POST', '/api/chat/import/context', body={
        'preview_id': preview['preview_id'], 'ref': '0', 'root': str(root), **extra})
    assert status == 200, result
    return result


def _move(app, preview, reviewed, **options):
    return _finish(app, preview, continuation=True, contexts={'0': {
        'plan_id': reviewed['plan_id'], 'options': options}})


def test_project_rules_files_disabled_tools_drafts_and_paused_tasks_are_reversible(app, tmp_path):
    from core.chat_import.workspace_transfer import _owners
    from core.memory.entries import list_memory_entries
    from core.project_store import get_project
    from core.runtime_paths import active_vool_home

    root, secret = _workspace(tmp_path)
    before = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in root.rglob('*') if path.is_file()}
    rules_before = active_rules()
    source = [_conversation('harbor-fixture', 'Harbor release', [
        ('human', 'Continue from `plan.txt` and check the missing `gone.txt`.', '2026-01-01T09:00:00Z'),
        ('assistant', 'The next step is the tide check.', '2026-01-01T09:00:01Z')])]
    preview = _upload_and_preview(app, _export_zip(source))
    reviewed = _review(app, preview, root, instructions='Continue with `plan.txt` and flag `gone.txt`.')
    assert secret not in json.dumps(reviewed)
    files = {Path(row['path']).name: row['status'] for row in reviewed['context']['files']}
    assert files == {'plan.txt': 'exists', 'gone.txt': 'missing'}
    # Explicit source references are retained even when missing.
    # The review itself has not registered projects, rules or commands.
    assert active_rules() == rules_before
    run = _move(app, preview, reviewed, instructions=True, memories=True, skills=True, tools=True, schedules=True)
    sid = run['first_session_id']
    namespace = load_chat_namespace(sid)
    assert namespace and namespace.project_id
    assert get_project(namespace.project_id)['root'] == str(root)
    assert any('harbor_signal' in rule.text for rule in active_rules(chat_id=sid, project_id=namespace.project_id))
    memories = json.dumps(list_memory_entries(chat_id=sid, limit=30))
    assert 'amber labels' in memories and 'chat-import:' in memories
    assert load_mcp_server_configs() == []
    inventories = {owner: functions[0](run['run_id'], sid) for owner, functions in _owners().items()}
    assert all(inventories[owner] for owner in ('projects', 'rules', 'skills', 'tools', 'schedules'))
    assert not (root / 'server_started').exists()
    for path in active_vool_home().rglob('*'):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200, result
    assert store.continuation_history(sid) == []
    from core.memory.entries import combined_memory_entries

    assert 'amber labels' not in json.dumps(combined_memory_entries())
    assert all(not functions[0](run['run_id'], sid) for functions in _owners().values())
    assert before == {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in root.rglob('*') if path.is_file()}


def test_private_context_cannot_write_memories_or_rules(app, tmp_path):
    root, _secret = _workspace(tmp_path)
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    reviewed = _review(app, preview, root, private=True)
    status, result = _call(app, 'POST', '/api/chat/import/start', body={
        'preview_id': preview['preview_id'], 'chats': ['0'], 'contexts': {'0': {
            'plan_id': reviewed['plan_id'], 'options': {'memories': True}}}})
    assert status == 400 and result['error'] == 'bad_context'
    assert store.imported_history_messages('not-created') == []


def test_reviewed_files_are_model_evidence_without_transcript_opt_in(app, tmp_path):
    from core.persistent_memory import maybe_handle_memory_command
    from core.prompt_normalizer import _reviewed_working_context_messages

    root, secret = _workspace(tmp_path)
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    reviewed = _review(app, preview, root, instructions='Reference `plan.txt` and flag `gone.txt`.')
    run = _finish(app, preview, continuation=False, contexts={'0': {
        'plan_id': reviewed['plan_id'], 'options': {}}})
    sid = run['first_session_id']
    assert store.continuation_history(sid) == []
    messages = _reviewed_working_context_messages({'runtime_session_id': sid})
    assert len(messages) == 1 and messages[0].role == 'context'
    text = messages[0].content
    assert 'plan.txt' in text and 'gone.txt' in text and 'missing' in text
    assert secret not in text and 'review_key' not in text and 'project_id' not in text
    assert 'bread' not in text  # No imported conversation text was attached.
    assert _reviewed_working_context_messages({'runtime_session_id': 'unimported-chat'}) == []
    handled, reply = maybe_handle_memory_command('Forget gone.txt.', session_id=sid)
    assert handled and 'no erasure step' not in reply, reply
    assert 'gone.txt' not in store.reviewed_working_context(sid)
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200, result
    assert store.reviewed_working_context(sid) == ''
    assert _reviewed_working_context_messages({'runtime_session_id': sid}) == []


def test_changed_directory_identity_refuses_the_reviewed_grant(app, tmp_path):
    from core.chat_import import workspace_transfer

    root, _secret = _workspace(tmp_path)
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    reviewed = _review(app, preview, root)
    moved = root.with_name('original-project')
    root.rename(moved)
    root.mkdir()
    try:
        workspace_transfer.apply(reviewed['context'], workspace_transfer.selection(reviewed['context'], {}),
                                 batch_id='synthetic-review', session_id='not-created', source='export',
                                 continuation=True, review_key='synthetic')
    except ValueError as exc:
        assert str(exc) == 'reviewed folder changed'
    else:
        raise AssertionError('a replaced project directory was granted')


def test_source_context_forget_erases_all_owners_and_stays_forgotten_after_undo(app, tmp_path):
    from core.chat_import.workspace_transfer import _owners
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from core.project_store import local_context_required

    root, _secret = _workspace(tmp_path)
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    reviewed = _review(app, preview, root)
    run = _move(app, preview, reviewed, instructions=True, skills=True, tools=True, schedules=True)
    sid = run['first_session_id']
    pid = load_chat_namespace(sid).project_id
    assert local_context_required(pid)
    handled, reply = maybe_handle_memory_command('Forget harbor_signal.', session_id=sid,
        access_policy=resolve_memory_access_policy(chat_id=sid))
    assert handled and 'no erasure step' not in reply, reply
    assert all('harbor_signal' not in json.dumps(functions[0](run['run_id'], sid))
               for functions in _owners().values())
    assert local_context_required(pid)  # Other source context still exists.
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200, result
    assert not local_context_required(pid)
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    status, result = _call(app, 'POST', '/api/chat/import/context', body={
        'preview_id': preview['preview_id'], 'ref': '0', 'root': str(root)})
    assert status == 409 and result['error'] == 'context_withheld'


def test_activated_source_skill_forget_prunes_snapshot_hashes_preserving_new_work(app, tmp_path):
    from core import skill_tools
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    root, _secret = _workspace(tmp_path)
    (root / 'skills/harbor/SKILL.md').write_text('When reviewing a release, check the copper_lantern ledger.\n')
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    reviewed = _review(app, preview, root)
    run = _move(app, preview, reviewed, skills=True)
    sid = run['first_session_id']
    draft = skill_tools.imported_skill_drafts(run['run_id'], sid)[0]
    path = Path(draft['id']) / 'SKILL.md'
    assert 'sha256' not in (path.parent / skill_tools.IMPORTED_MARK).read_text()
    result = skill_tools.install_skill(str(path), scope='chat', keys={'chat': sid})
    assert result['status'] == 'ok', result
    active = Path(result['path'])
    if active.is_dir():
        active = active / 'SKILL.md'
    native = active.read_text().replace('copper_lantern', 'native revised')
    active.write_text(native)
    version = skill_tools._record_version(active.parent, source='edit')
    assert version['version'] == 2
    handled, reply = maybe_handle_memory_command('Forget copper_lantern.', session_id=sid,
        access_policy=resolve_memory_access_policy(chat_id=sid))
    assert handled and 'no erasure step' not in reply, reply
    assert active.read_text() == native
    history = skill_tools._read_history(active.parent)
    assert [v['version'] for v in history['versions']] == [2]
    assert not (active.parent / skill_tools.VERSIONS_DIRNAME / 'v1.md').exists()
    assert skill_tools._record_version(active.parent, source='edit')['version'] == 3


def test_context_projection_keeps_notes_models_and_paths_without_decoding_secret_branches(app, tmp_path):
    from core.chat_import.model import ImportedChat
    from core.chat_import.source_context import _CONFIG, export_context, project_json, review

    root = tmp_path / "moved-folder"
    root.mkdir()
    (root / "entry.txt").write_text("Reference from the previous workstation.")
    secret = 'sk-' + 'R' * 48
    (root / "config.yaml").write_text('model: local-example\nproviders:\n  primary:\n    api_key: '+secret+'\n')
    context = export_context([{"content": {"content_type": "model_editable_context", "model_set_context":
        "The draft needs one final chart."}, "tool_calls": [{"function": {"arguments":
        '{"path":"file:///old/work/entry.txt","env":{"KEY":"'+secret+'"}}'}}]}])
    context['workspace_hint'] = '/old/work'
    chat = ImportedChat(source_format='mapping', source_chat_id='context-record', title='Chart review',
                        context=context)
    result = review(chat, root=str(root))
    assert result['notes'][0]['text'] == 'The draft needs one final chart.'
    assert result['models'] == ['local-example'] and result['providers'] == ['primary']
    assert result['files'] == [{'source':'file:///old/work/entry.txt','path':str(root/'entry.txt'),'status':'exists'}]
    assert secret not in json.dumps(result)
    assert project_json(b'{"providers":{"primary":{"api_key":"not decoded"}},"model":"small-local"}', _CONFIG) == {
        'providers': {'primary': None}, 'model': 'small-local'}
    try:
        project_json(b'{"unknown":"bad\\q"}', _CONFIG)
    except ValueError:
        pass
    else:
        raise AssertionError('malformed ignored configuration was accepted')


def test_shared_schedule_projection_is_read_only_paused_and_excludes_credentials(app, tmp_path):
    import sqlite3

    from core.chat_import.model import ImportedChat
    from core.chat_import.source_context import review

    root = tmp_path / 'assistant-state'
    (root / 'state').mkdir(parents=True)
    database = root / 'state/openclaw.sqlite'
    connection = sqlite3.connect(database)
    connection.execute('PRAGMA user_version=28')
    connection.execute('CREATE TABLE cron_jobs (store_key TEXT,job_id TEXT,job_json TEXT)')
    secret='sk-'+'S'*48
    connection.execute('INSERT INTO cron_jobs VALUES (?,?,?)',(str(root/'cron/jobs.json'),'daily',json.dumps({
        'name':'Daily check','schedule':{'kind':'cron','expr':'0 8 * * *','tz':'Europe/Vilnius'},
        'payload':{'kind':'agentTurn','message':'Review the orchard ledger.'},'env':{'API_KEY':secret}})))
    connection.commit();connection.close()
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    chat=ImportedChat(source_format='local',source_chat_id='daily',title='Orchard')
    result=review(chat,root=str(root))
    assert len(result['schedules'])==1 and result['schedules'][0]['note']=='Review the orchard ledger.'
    assert result['schedules'][0]['schedule']['tz']=='Europe/Vilnius'
    assert secret not in json.dumps(result)
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before


def test_resident_project_work_is_preserved_and_only_later_work_blocks_undo(app, tmp_path):
    from core.context_namespace import ensure_chat_namespace
    from core.persistent_memory import append_conversation_event
    from core.project_store import create_project, get_project, local_context_required

    root, _secret = _workspace(tmp_path)
    ok, project = create_project('Existing project name', str(root))
    assert ok
    resident='openclaw:'+'c'*20
    ensure_chat_namespace(resident,project_id=project['id'])
    append_conversation_event(session_id=resident,user_input='Existing native work',assistant_output='Kept before migration')
    preview=_upload_and_preview(app,_export_zip(EXPORT))
    reviewed=_review(app,preview,root)
    run=_move(app,preview,reviewed,instructions=True)
    assert get_project(project['id'])['name']=='Existing project name'
    assert local_context_required(project['id'])
    status,result=_call(app,'POST','/api/chat/import/undo',body={'batch_id':run['run_id']})
    assert status==200,result
    assert get_project(project['id'])['name']=='Existing project name'
    assert not local_context_required(project['id'])
    preview=_upload_and_preview(app,_export_zip(EXPORT))
    reviewed=_review(app,preview,root)
    run=_move(app,preview,reviewed,instructions=True)
    append_conversation_event(session_id=resident,user_input='Work after source context was added',assistant_output='Retain the new checkpoint')
    status,result=_call(app,'POST','/api/chat/import/undo',body={'batch_id':run['run_id']})
    assert status==409 and result['error']=='batch_in_use'
    assert local_context_required(project['id'])


def test_source_agent_precedence_keeps_identity_and_drops_shadowed_project_files(app, tmp_path, monkeypatch):
    from core.chat_import.local_sources import read
    from core.chat_import.source_context import review
    from tests.test_chat_import_local_sources import _agent_store, _message, _session

    home=tmp_path/'source-home'; profile=home/'.hermes'
    db=_agent_store(profile/'state.db')
    _session(db,'source',1000,title='Source project')
    _message(db,'source','user','Continue the ledger.',1001,uid='source-message');db.close()
    (tmp_path/'snapshots').mkdir()
    (profile/'SOUL.md').write_text('Global identity: use the silver compass.')
    root=tmp_path/'project';root.mkdir()
    (root/'.hermes.md').write_text('Keep the highest_priority checkpoint.')
    (root/'AGENTS.md').write_text('Contradictory lower_priority checkpoint.')
    (root/'SOUL.md').write_text('Wrong project_identity checkpoint.')
    result=read('local_agent',home=home,env={},platform='darwin',work_dir=str(tmp_path/'snapshots'))
    assert result.chats
    plan=review(result.chats[0],root=str(root))
    text=' '.join(row['text'] for row in plan['instructions'])
    assert 'highest_priority' in text and 'silver compass' in text
    assert 'lower_priority' not in text and 'project_identity' not in text
    (root/'.hermes.md').unlink()
    (root/'AGENTS.override.md').write_text('Use the override_priority checkpoint.')
    plan=review(result.chats[0],root=str(root))
    text=' '.join(row['text'] for row in plan['instructions'])
    assert 'override_priority' in text and 'lower_priority' not in text
    (root/'AGENTS.override.md').write_text('')
    plan=review(result.chats[0],root=str(root))
    assert 'lower_priority' not in ' '.join(row['text'] for row in plan['instructions'])


def test_conditional_nested_rules_and_daily_notes_survive_served_import_without_activation(app, tmp_path):
    from core.memory.entries import list_memory_entries
    from core.skill_tools import imported_skill_drafts
    root=tmp_path/'conditional-project';root.mkdir()
    folder=root/'.cursor/rules';folder.mkdir(parents=True)
    (folder/'always.mdc').write_text('---\nalwaysApply: true\n---\nUse the always_marker format.\n')
    (folder/'conditional.mdc').write_text('---\nglobs: "*.py"\nalwaysApply: false\n---\nOnly Python uses conditional_marker.\n')
    nested=root/'ui/.cursor/rules';nested.mkdir(parents=True)
    (nested/'nested.mdc').write_text('---\nalwaysApply: true\n---\nOnly this folder uses nested_marker.\n')
    notes=root/'memory';notes.mkdir()
    (notes/'2026-10-18.md').write_text('The daily_note checkpoint is the amber review.')
    outside=tmp_path/'outside.md';outside.write_text('Do not read escaped_marker.')
    (notes/'2026-10-17.md').symlink_to(outside)
    preview=_upload_and_preview(app,_export_zip(EXPORT));reviewed=_review(app,preview,root)
    plan=reviewed['context'];instructions=' '.join(row['text'] for row in plan['instructions'])
    assert 'always_marker' in instructions
    assert 'conditional_marker' not in instructions and 'nested_marker' not in instructions
    assert len(plan['skills'])==2 and 'conditional_rules_staged' in plan['notices']
    assert 'escaped_marker' not in json.dumps(plan)
    assert 'context_unreadable' in plan['warnings']
    run=_move(app,preview,reviewed,instructions=True,memories=True,skills=True)
    sid=run['first_session_id'];ns=load_chat_namespace(sid)
    text=' '.join(rule.text for rule in active_rules(chat_id=sid,project_id=ns.project_id))
    assert 'always_marker' in text and 'conditional_marker' not in text and 'nested_marker' not in text
    drafts=json.dumps(imported_skill_drafts(run['run_id'],sid))
    assert 'conditional_marker' in drafts and 'nested_marker' in drafts and '*.py' in drafts
    assert 'daily_note' in json.dumps(list_memory_entries(chat_id=sid,limit=30))
    status,result=_call(app,'POST','/api/chat/import/undo',body={'batch_id':run['run_id']})
    assert status==200,result
    assert imported_skill_drafts(run['run_id'],sid)==[]
    assert (notes/'2026-10-18.md').exists() and outside.read_text()=='Do not read escaped_marker.'


def test_other_schedule_partitions_are_visibly_unsupported(app,tmp_path):
    import sqlite3

    from core.chat_import.model import ImportedChat
    from core.chat_import.source_context import review
    root=tmp_path/'partitioned-source';(root/'state').mkdir(parents=True)
    db=sqlite3.connect(root/'state/openclaw.sqlite')
    db.execute('PRAGMA user_version=28')
    db.execute('CREATE TABLE cron_jobs (store_key TEXT,job_id TEXT,job_json TEXT)')
    db.execute('INSERT INTO cron_jobs VALUES (?,?,?)',('another-partition','unread','{"prompt":"not_selected_marker"}'))
    db.commit();db.close()
    plan=review(ImportedChat(source_format='local',source_chat_id='partition',title='Partition'),root=str(root))
    assert 'schedule_partition_unsupported' in plan['warnings']
    assert plan['schedules']==[] and 'not_selected_marker' not in json.dumps(plan)


def test_two_batches_share_a_project_and_undo_preserves_the_other_chats_copies(app,tmp_path):
    from core.chat_import.workspace_transfer import _owners
    from core.project_store import get_project, local_context_required

    root,_secret=_workspace(tmp_path)
    runs=[]
    for index in range(2):
        source=[_conversation('shared-'+str(index),'Shared '+str(index),[
            ('human','Continue the shared harbor ledger.','2026-01-01T09:00:00Z')])]
        preview=_upload_and_preview(app,_export_zip(source));reviewed=_review(app,preview,root)
        runs.append(_move(app,preview,reviewed,instructions=True,skills=True,tools=True,schedules=True))
    first,second=runs
    sids=[run['first_session_id'] for run in runs]
    pid=load_chat_namespace(sids[0]).project_id
    assert load_chat_namespace(sids[1]).project_id==pid
    owners=_owners()
    before={owner:fn[0](second['run_id'],sids[1]) for owner,fn in owners.items()}
    assert all(before[owner] for owner in ('projects','skills','tools','schedules'))
    assert before['rules']==[]  # The canonical project rule is deduplicated.
    assert owners['tools'][0](first['run_id'],sids[0])[0]['id']!=before['tools'][0]['id']
    status,result=_call(app,'POST','/api/chat/import/undo',body={'batch_id':first['run_id']})
    assert status==409 and result['error']=='batch_in_use'
    assert before=={owner:fn[0](second['run_id'],sids[1]) for owner,fn in owners.items()}
    status,result=_call(app,'POST','/api/chat/import/undo',body={'batch_id':second['run_id']})
    assert status==200,result
    assert local_context_required(pid) and get_project(pid)
    assert any('harbor_signal' in rule.text for rule in active_rules(chat_id=sids[0],project_id=pid))
    assert all(not fn[0](second['run_id'],sids[1]) for fn in owners.values())
    status,result=_call(app,'POST','/api/chat/import/undo',body={'batch_id':first['run_id']})
    assert status==200,result
    assert not local_context_required(pid) and get_project(pid) is None
    assert (root/'AGENTS.md').exists() and not (root/'server_started').exists()


def test_saved_resource_overview_omits_credentials_added_after_import(app,tmp_path):
    from core.runtime_paths import active_vool_home
    root,_secret=_workspace(tmp_path)
    preview=_upload_and_preview(app,_export_zip(EXPORT));reviewed=_review(app,preview,root)
    run=_move(app,preview,reviewed,skills=True,tools=True,schedules=True)
    config=active_vool_home()/'mcp_servers.json'
    document=json.loads(config.read_text())
    secret='sk-'+'U'*48
    document['servers'][0]['env']={'API_KEY':secret}
    config.write_text(json.dumps(document))
    status,result=_call(app,'GET','/api/chat/import/batches')
    assert status==200,result
    context=next(batch for batch in result['batches'] if batch['batch_id']==run['run_id'])['contexts'][0]
    assert all(context['resources'][owner] for owner in ('skills','tools','schedules'))
    assert secret not in json.dumps(result) and 'API_KEY' not in json.dumps(result)
    assert 'python3' in context['resources']['tools'][0]['text']


def test_named_pipe_context_file_is_refused_without_waiting_for_a_writer(tmp_path):
    import os
    import signal

    import pytest

    from core.chat_import.source_context import _read

    os.mkfifo(tmp_path/'AGENTS.md')
    def expired(_signal, _frame):
        raise TimeoutError('source context open blocked on a named pipe')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(2)
    try:
        with pytest.raises(ValueError, match='context file limit'):
            _read(tmp_path, 'AGENTS.md')
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
