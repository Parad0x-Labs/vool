from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import fake_x
from tests.x_social.retention_scan import scan_home
from tests.x_social.test_cards import _cards, _context, _propose


@pytest.mark.parametrize('operation', ['forget', 'reset'])
def test_card_rating_stays_in_its_store_and_erases_from_the_full_home(fake_x, vault_home, operation):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    marker = 'Granite canoe wording felt cramped.'
    def feedback(body):
        reply = dispatch_post(path='/api/x/feedback', body=body, headers={'Content-Type': 'application/json'},
            runtime=RuntimeServices(display_name='test'), model_name='fixture', client_host='127.0.0.1',
            workspace_root_provider=lambda: '.')
        assert reply.status == 200, reply.body
        return json.loads(reply.body)
    assert _propose(_context()).ok
    [card] = _cards()[1]['cards']
    saved = feedback({'action': 'save', 'action_id': card['id'], 'rating': 7, 'comment': marker})
    before = scan_home(vault_home, marker)
    assert before['matches'] == [{'kind': 'file', 'path': 'config/x_feedback.json'}], before
    assert feedback({'action': operation, 'scope': 'global', 'id': saved['id']})['ok']
    after = scan_home(vault_home, marker)
    out = Path(os.environ['SOCIAL_SHOTS']); out.mkdir(parents=True, exist_ok=True)
    (out / ('card-retention-' + operation + '.json')).write_text(json.dumps({'before': before, 'after': after}, indent=2))
    assert not after['matches'], after


def test_later_chat_rating_does_not_copy_existing_card_feedback_into_recorder(fake_x, vault_home):
    from core import x_feedback
    from core.runtime_execution_tools import execute_runtime_tool
    from core.turn_contract import TurnRequest

    marker = 'Granite canoe wording felt cramped.'
    assert x_feedback.save('global', 7, 'The first card draft.', comment=marker)['ok']
    ctx = {'surface': 'desktop', '_owner_local': True, 'operating_mode': 'auto',
           'workspace_root': str(vault_home), 'runtime_session_id': 'later-rating'}
    ctx['turn_request'] = TurnRequest.from_ingress(user_text='9/10 A separate caption.',
        source_context=ctx, session_id='later-rating', request_id='later-rating', turn_id='later-rating')
    saved = execute_runtime_tool('x.feedback.save', {'rating': 9, 'text': 'A separate caption.'}, source_context=ctx)
    assert saved.ok, saved
    scanned = scan_home(vault_home, marker)
    assert scanned['matches'] == [{'kind': 'file', 'path': 'config/x_feedback.json'}], scanned
    assert x_feedback.forget('global')['ok']
    assert not scan_home(vault_home, marker)['matches']
