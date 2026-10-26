"""Review r4 M1, L2, L3: a chat-typed rating binds a server-derived draft and keeps no receipt text."""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import fake_x
from tests.x_social.retention_scan import scan_home
from tests.x_social.test_plain_ratings import DRAFT, context, seed

RELEASE = 'Shipping VOOL 0.7.4 today, try it'


# -- M1: a typed N/10 rating never takes its draft from the model's arguments --------------

def test_model_cannot_pick_a_substring_of_the_owner_turn_as_the_draft(vault_home):
    from core import x_feedback as f

    turn = context('7/10 for "' + RELEASE + '"', session='m1-chat')
    out = f.save_from_turn({'rating': 7, 'text': 't'}, turn)
    assert not out['ok'], out
    assert f.inspect('global')['entries'] == []


@pytest.mark.parametrize('text', [None, '', RELEASE, '  ' + RELEASE + ' '])
def test_the_server_takes_the_one_quoted_span_from_the_owner_turn(vault_home, text):
    from core import x_feedback as f

    args = {'rating': 7, **({} if text is None else {'text': text})}
    assert f.save_from_turn(args, context('7/10 for "' + RELEASE + '"', session='m1-chat'))['ok']
    [row] = f.inspect('global')['entries']
    assert (row['rating'], row['text']) == (7, RELEASE)


@pytest.mark.parametrize('turn', [
    '7/10 ' + RELEASE,                                   # nothing quoted: no server-derived draft
    '7/10 "' + RELEASE + '" or "Another caption"',        # two spans: ambiguous
    '7/10 "' + RELEASE,                                   # unclosed quote
    '7/10 for “' + RELEASE + '” and "Another caption"',
])
def test_missing_or_ambiguous_quoted_draft_refuses(vault_home, turn):
    from core import x_feedback as f

    assert not f.save_from_turn({'rating': 7, 'text': RELEASE}, context(turn, session='m1-chat'))['ok']
    assert f.inspect('global')['entries'] == []


def test_curly_and_fenced_spans_bind_and_a_different_model_text_refuses(vault_home):
    from core import x_feedback as f

    assert f.save_from_turn({'rating': 8}, context('8/10 “' + RELEASE + '”', session='m1-a'))['ok']
    assert f.save_from_turn({'rating': 6}, context('6/10\n```\n' + RELEASE + '\n```', session='m1-b'))['ok']
    assert not f.save_from_turn({'rating': 5, 'text': 'Shipping VOOL'},
                                context('5/10 "' + RELEASE + '"', session='m1-c'))['ok']
    assert sorted((r['rating'], r['text']) for r in f.inspect('global')['entries']) == [(6, RELEASE), (8, RELEASE)]


# -- L3: a plain reply that starts with a number is not a rating ---------------------------

def test_more_ideas_please_is_not_a_rating(vault_home):
    from core import x_feedback as f

    seed()
    turn = context('10 more ideas please')
    assert f.reply_intents('10 more ideas please', turn) == ()
    assert not f.save_from_turn({'rating': 10}, turn)['ok']
    assert f.inspect('global')['entries'] == []


@pytest.mark.parametrize('text,comment', [
    ('7', ''), ('7/10', ''), ('7 - hook too long', 'hook too long'), ('Rating 7, hook too long', 'hook too long'),
    ('7: hook too long', 'hook too long'), ('7/10 hook too long', 'hook too long'), ('7. hook too long', 'hook too long'),
])
def test_accepted_rating_forms_still_bind(vault_home, text, comment):
    from core import x_feedback as f

    seed()
    assert f.reply_intents(text, context(text)) == ('x.feedback.save',)
    assert f.save_from_turn({'rating': 7}, context(text))['ok']
    [row] = f.inspect('global')['entries']
    assert (row['rating'], row['text'], row['comment']) == (7, DRAFT, comment)


# -- L2: a chat-typed rating's comment stays only in the rating store ----------------------

def test_chat_typed_rating_receipt_keeps_no_comment_or_draft(fake_x, vault_home):
    from core import x_feedback
    from core.runtime_execution_tools import execute_runtime_tool
    from core.turn_contract import TurnRequest

    comment, caption = 'Basalt kettle phrasing ran long.', 'Obsidian lantern caption.'
    ctx = {'surface': 'desktop', '_owner_local': True, 'operating_mode': 'auto',
           'workspace_root': str(vault_home), 'runtime_session_id': 'l2-rating'}
    ctx['turn_request'] = TurnRequest.from_ingress(user_text='9/10 "' + caption + '" ' + comment,
        source_context=ctx, session_id='l2-rating', request_id='l2-rating', turn_id='l2-rating')
    saved = execute_runtime_tool('x.feedback.save', {'rating': 9, 'text': caption, 'comment': comment},
                                 source_context=ctx)
    assert saved.ok, saved
    for needle in (comment, caption):
        assert scan_home(vault_home, needle)['matches'] == [{'kind': 'file', 'path': 'config/x_feedback.json'}]
    assert x_feedback.forget('global')['ok']
    assert not scan_home(vault_home, comment)['matches']


def test_settings_disclosure_uses_the_decided_wording():
    from core.i18n.catalog import catalog_for
    from core.x_social_fragment import render_x_social_fragment

    decided = 'A rating you typed in chat also stays in that chat until you forget the chat message.'
    assert decided in catalog_for('en').text('xs.forget_scope')
    try:
        fragment = render_x_social_fragment(False)
    except TypeError:  # the r4 fragment took no flag
        fragment = render_x_social_fragment()
    assert decided in fragment


# -- r5 Info: on the explicit N/10 path the comment is derived by the server too --------------

@pytest.mark.parametrize('turn,comment', [
    ('7/10 for "' + RELEASE + '", too long', 'too long'),
    ('7/10 "' + RELEASE + '" Great hook.', 'Great hook.'),
    ('7/10 punchy opener "' + RELEASE + '"', 'punchy opener'),
    ('7/10 for "' + RELEASE + '"', ''),
])
def test_explicit_rating_comment_is_the_owner_text_around_the_draft(vault_home, turn, comment):
    from core import x_feedback as f

    assert f.save_from_turn({'rating': 7, 'text': RELEASE}, context(turn, session='info-a'))['ok']
    [row] = f.inspect('global')['entries']
    assert (row['text'], row['comment']) == (RELEASE, comment)


@pytest.mark.parametrize('picked', ['too', 'long', RELEASE, 'Shipping', 'too long, really'])
def test_model_cannot_pick_a_substring_as_the_explicit_comment(vault_home, picked):
    from core import x_feedback as f

    turn = context('7/10 for "' + RELEASE + '", too long', session='info-b')
    assert not f.save_from_turn({'rating': 7, 'text': RELEASE, 'comment': picked}, turn)['ok']
    assert f.save_from_turn({'rating': 7, 'text': RELEASE, 'comment': '  too   long '}, turn)['ok']
    assert [r['comment'] for r in f.inspect('global')['entries']] == ['too long']
