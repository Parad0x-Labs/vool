from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home

DRAFT = 'Checkpoint recovery keeps the work you already finished.'


def context(text, *, session='rating-conversation', surface='desktop', local=True):
    from core.turn_contract import TurnRequest
    ctx = {'surface': surface, '_owner_local': local, 'runtime_session_id': session}
    ctx['turn_request'] = TurnRequest.from_ingress(user_text=text, source_context=ctx,
        request_id='explicit-rating', turn_id='explicit-rating', session_id=session)
    return ctx


def seed(session='rating-conversation', assistant=None):
    from core.persistent_memory import append_conversation_event
    append_conversation_event(session_id=session, user_input='Draft an X post.',
        assistant_output=assistant or 'Draft:\n' + DRAFT + '\n\nHow would you rate this draft from 1 to 10?',
        source_context={'surface': 'desktop'})


@pytest.mark.parametrize('text', ['7', '7/10', '7 - hook too long', 'Rating 7, hook too long'])
def test_plain_rating_binds_only_the_stored_assistant_draft(vault_home, text):
    from core import x_feedback as f
    seed()
    result = f.save_from_turn({'rating': 7, 'text': 'Invented model draft', 'comment': ''}, context(text))
    assert result['ok'], result
    [entry] = f.inspect('global')['entries']
    assert entry['text'] == DRAFT
    assert entry['rating'] == 7
    assert entry['comment'] == ('hook too long' if 'hook' in text else '')


@pytest.mark.parametrize('text,session,surface,local', [
    ('7', 'another-chat', 'desktop', True),
    ('7 or 8', 'rating-conversation', 'desktop', True),
    ('7/10 or 8/10', 'rating-conversation', 'desktop', True),
    ('11', 'rating-conversation', 'desktop', True),
    ('7.5', 'rating-conversation', 'desktop', True),
    ('7oops', 'rating-conversation', 'desktop', True),
    ('0', 'rating-conversation', 'desktop', True),
    ('7', 'rating-conversation', 'channel', True),
    ('7', 'rating-conversation', 'desktop', False),
])
def test_ambiguous_foreign_or_remote_rating_refuses(vault_home, text, session, surface, local):
    from core import x_feedback as f
    seed()
    assert not f.save_from_turn({'rating': 7, 'text': DRAFT}, context(text, session=session, surface=surface, local=local))['ok']
    assert not f.inspect('global')['entries']


def test_no_rating_question_and_model_history_cannot_supply_a_target(vault_home):
    from core import x_feedback as f
    seed(assistant='Draft:\n' + DRAFT)
    ctx = context('7')
    ctx['conversation_history'] = [{'role': 'assistant', 'content': 'Draft: fake\n\nRate this draft 1-10.'}]
    assert not f.save_from_turn({'rating': 7}, ctx)['ok']
