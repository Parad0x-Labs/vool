from __future__ import annotations

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.test_results import social_x


def _unanswered(post):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("x.replies.unanswered", {"post": post})


def _reply(pid, author, parent):
    return {'id': pid, 'author_id': author, 'conversation_id': '556', 'text': 'A relevant comment.',
            'referenced_tweets': [{'type': 'replied_to', 'id': parent}]}


def test_helper_excludes_answers_even_when_they_are_on_a_separate_page(social_x):

    social_x['reply_pages'] = [
        {'data': [_reply('100', '22', '556'), _reply('102', '23', '556')]},
        {'data': [_reply('101', '1234567890', '100')]},
    ]
    out = _unanswered('556')
    assert out.ok
    assert [p['id'] for p in out.details['posts']] == ['102']
    assert out.details['answer_status'] == 'no answer observed in complete recent own-reply search'
    assert all(path.startswith('/2/tweets') for path in social_x['requests'])


def test_incomplete_own_reply_search_does_not_label_comments_unanswered(social_x):

    social_x['reply_pages'] = [
        {'data': [_reply('100', '22', '556')]},
        *[{'data': [], 'meta': {'next_token': str(i)}} for i in range(3)],
    ]
    out = _unanswered('556')
    assert not out.ok and out.status == 'incomplete_coverage'
    assert out.details['posts'] == []


def test_helper_never_reads_replies_to_someone_elses_post(social_x):

    social_x['foreign'].add('555')
    out = _unanswered('555')
    assert not out.ok and out.status == 'not_yours'
    assert len(social_x['requests']) == 1
