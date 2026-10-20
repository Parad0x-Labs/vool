from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home


def test_rating_is_explicit_scoped_bounded_and_never_automatically_a_voice_sample(vault_home):
    from core import x_feedback as f, x_voice_profile as v

    row = f.save('project:one', 10, 'A clear release note.', comment='Keep this concise.')
    assert row['ok']
    assert v.inspect('project:one')['samples'] == []
    assert 'clear release' in f.summary('project:one')
    assert f.summary('project:two') == ''
    assert len(f.summary('project:one')) <= f.MAX_PROMPT_CHARS
    assert f.use_as_voice('project:one', row['id'])['ok']
    assert len(v.inspect('project:one')['samples']) == 1
    assert f.use_as_voice('project:one', row['id'])['ok']
    assert len(v.inspect('project:one')['samples']) == 1
    assert f.forget('project:one', row['id'])['ok']
    assert v.inspect('project:one')['samples'] == []
    assert f.inspect('project:one')['entries'] == []


@pytest.mark.parametrize('rating', [0, 11, True, 9.5, '9'])
def test_invalid_ratings_do_not_write(vault_home, rating):
    from core import x_feedback as f

    assert not f.save('global', rating, 'A clear release note.')['ok']
    assert f.inspect('global')['entries'] == []


def test_pause_restart_reset_and_prompt_integration(vault_home):
    from core import x_feedback as f, x_voice_profile as v

    f.save('global', 3, 'Plain release note.', comment='Too vague about the change.')
    assert 'Too vague' in v.voice_segment_for_turn('Write an X post about our release.')
    assert f.set_paused('global', True)['ok']
    assert f.summary('global') == ''
    assert v.voice_segment_for_turn('Write an X post about our release.') == ''
    assert f.set_paused('global', False)['ok']
    assert 'Too vague' in f.summary('global')
    assert f.forget('global')['removed'] == 1
    assert f.summary('global') == ''


def test_model_cannot_invent_user_rating_or_comment(vault_home):
    from core import x_feedback as f
    from core.turn_contract import TurnRequest

    def ctx(text):
        return {'turn_request': TurnRequest.from_ingress(user_text=text, session_id='feedback-chat', request_id='req-f', turn_id='turn-f', source_context={})}

    args = {'rating': 9, 'text': 'A clear release note.', 'comment': 'Concise.'}
    assert not f.save_from_turn(args, {})['ok']
    assert not f.save_from_turn(args, {'turn_request': {'user_text': '9/10 Concise.'}})['ok']
    assert not f.save_from_turn(args, ctx('Could you rate this?'))['ok']
    assert not f.save_from_turn(args, ctx('9/10 A clear release note.'))['ok']
    assert f.save_from_turn(args, ctx('9/10 A clear release note. Concise.'))['ok']
    assert not f.save_from_turn(args, ctx('9/10 or 2/10 A clear release note. Concise.'))['ok']


def test_corrupt_store_refuses_and_does_not_overwrite(vault_home):
    from core import x_feedback as f
    from core.runtime_paths import active_vool_home

    path = active_vool_home() / f.STORE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{broken')
    assert not f.save('global', 9, 'A clear release note.')['ok']
    assert path.read_text() == '{broken'


def test_forget_removes_file_bytes_and_linked_samples(vault_home):
    from core import x_feedback as f
    from core.runtime_paths import active_vool_home

    row = f.save('global', 9, 'Precise checkpoint release text.', comment='Singular feedback marker.')
    assert f.use_as_voice('global', row['id'])['ok']
    assert f.forget('global')['ok']
    home = active_vool_home()
    for path in (home / 'config').glob('x_*'):
        if path.is_file():
            assert b'Singular feedback marker' not in path.read_bytes()
            assert b'Precise checkpoint release text' not in path.read_bytes()
    assert json.loads((home/f.STORE_FILENAME).read_text())['scopes']['global']['entries'] == []


def test_rating_route_owner_only_and_uses_frozen_action(vault_home, monkeypatch):
    from core import x_actions, x_feedback
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    # A real proposed action from the existing loopback X fixture is covered in the
    # combined served test. This boundary check must refuse before reading any store.
    result = dispatch_post(path='/api/x/feedback', body={'action': 'list'},
                           headers={'Content-Type': 'application/json'},
                           runtime=RuntimeServices(display_name='t'), model_name='fixture',
                           client_host='198.51.100.10', workspace_root_provider=lambda: '.')
    assert result.status == 403
    assert x_feedback.inspect('global')['entries'] == []
    assert x_actions.handle({'action': 'voice'})[0] == 400
