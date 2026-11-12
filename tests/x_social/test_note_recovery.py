from __future__ import annotations

from tests._credential_intelligence_support import isolated_home, vault_home


def test_native_note_arguments_survive_the_real_operator_path(vault_home, tmp_path, monkeypatch):
    from core.execution.operator_tools import execute_operator_tool

    monkeypatch.setenv('VOOL_WORKSPACE_ROOT', str(tmp_path / 'workspace'))
    def call(kind, arguments):
        return execute_operator_tool('operator.' + kind, arguments,
                                     task_id='social-note-' + kind, session_id='social-notes')
    saved = call('save_note', {'text': 'save a note titled "Citrus launch" with: Mandarin rollout checklist'})
    assert saved.ok, saved.response_text
    assert call('save_note', {'text': 'save a note titled "Meeting agenda" with: Venue capacity planning'}).ok
    found = call('find_notes', {'query': 'Mandarin'})
    assert found.ok and found.details['count'] == 1, found
    assert found.details['notes'][0]['title'] == 'Citrus launch'
    assert call('find_notes', {'query': 'unmatched zeppelin'}).details['count'] == 0
    shown = call('show_note', {'title': 'Citrus launch'})
    assert shown.ok and 'Mandarin rollout checklist' in shown.response_text, shown
    assert not call('show_note', {'title': 'Missing note'}).ok
