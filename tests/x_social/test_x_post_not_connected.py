"""R9 recheck (X05, X06, X13): with X not connected, a post ask says nothing was posted and hands back
the draft, and a trending ask with "do not post" in it still gets the connect place.

Measured on qwen3:8b: "can you post a tweet for me saying thanks ..." got a draft and an offer to post,
"put this on X for me: ..." got "I'll help you share that on X. Let me know the text you'd like to
post." and "show me whats trending but absolutely do not post anything" got "I can't access live
data". None said X isn't connected or where to connect it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._credential_intelligence_support import isolated_home

X05 = 'can you post a tweet for me saying thanks to everyone who came to the meetup tonight'
X06 = 'put this on X for me: just shipped my first app, feeling proud'
X13 = 'show me whats trending but absolutely do not post anything'
PLACE = 'Settings > API Keys > X (Twitter)'
CLAIMS = ('posted it', 'i posted', "i'll post", 'i will post', 'will go out', 'went out', 'is live',
          'scheduled', 'has been shared', "i'll share", 'bearer', 'token', 'oauth', 'credential')
CATALOGS = Path(__file__).resolve().parents[2] / 'core' / 'i18n' / 'catalogs'


def _run(ask: str, **extra) -> str:
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name='test-backend', device='channel-test', persona_id='default')
    result = agent.run_once(ask, source_context={'surface': 'desktop', 'platform': 'api', **extra})
    return str(result.get('response') or '')


def _says_nothing_was_posted(text: str) -> None:
    assert text.startswith('X is not connected, so nothing was posted.'), text
    assert PLACE in text and 'Connect X' in text, text
    lowered = text.lower()
    assert not [word for word in CLAIMS if word in lowered], text


@pytest.mark.parametrize(('ask', 'draft'), [
    (X05, 'Thanks to everyone who came to the meetup tonight!'),
    (X06, 'just shipped my first app, feeling proud'),
])
def test_a_post_ask_without_x_says_nothing_was_posted_and_gives_the_draft(isolated_home, ask, draft):
    from core.x_api import connected

    assert not connected()
    text = _run(ask)
    _says_nothing_was_posted(text)
    assert text.endswith('\n\n' + draft), text
    assert 'draft you can copy and post yourself' in text, text


def test_a_post_ask_with_no_words_asks_what_it_should_say(isolated_home):
    text = _run('post a tweet for me')
    _says_nothing_was_posted(text)
    assert 'Tell me what the post should say' in text, text


def test_a_trending_ask_that_says_do_not_post_still_gets_the_connect_place(isolated_home):
    from core.x_connect_hint import not_connected_text

    assert _run(X13) == not_connected_text('en')


def test_the_post_reply_speaks_the_app_language(isolated_home):
    text = _run(X06, ui_locale='de')
    assert text.startswith('X ist nicht verbunden, deshalb wurde nichts gepostet.'), text
    assert 'Einstellungen > API-Schlüssel > X (ehemals Twitter)' in text and 'X verbinden' in text, text
    assert text.endswith('just shipped my first app, feeling proud'), text


def test_drafting_negated_posting_and_a_connected_x_are_left_alone(monkeypatch):
    from core import x_connect_hint as hint

    for ask in ('write me something sweet to put on X for my best friends birthday',
                'draft a short post saying our shop is closed this monday',
                'help me write a short post about my weekend hiking trip, nothing cringe',
                'whatever happened to that post i asked you about yesterday, did it go out',
                "dont post this on X, just tell me if it sounds ok: hello world",
                'what should i post each day next week? i sell handmade candles at markets',
                'what is the post office opening time', 'send this file to my laptop'):
        assert not hint.asks_to_post_on_x(ask), ask
    for ask in ('whats trending in fashion this fall', 'show me whats trending in my city'):
        assert not hint.asks_for_x_read(ask), ask
    assert hint.asks_for_x_read(X13) and not hint.asks_to_post_on_x(X13)
    assert hint.asks_to_post_on_x(X05) and hint.asks_to_post_on_x(X06)

    class Agent:
        def _fast_path_result(self, **kwargs):
            return dict(kwargs)

    assert hint.reply_if_not_connected(Agent(), X05, session_id='s', source_context={}) is not None
    monkeypatch.setattr(hint, 'x_connected', lambda: True)
    assert hint.reply_if_not_connected(Agent(), X05, session_id='s', source_context={}) is None


def test_the_post_replies_are_in_all_17_catalogs():
    from core.i18n.catalog import placeholders_of, source_catalog_sha256
    from core.x_connect_hint import X_POST_ASK_KEY, X_POST_DRAFT_KEY

    files = sorted(CATALOGS.glob('*.json'))
    assert len(files) == 17
    en = json.loads((CATALOGS / 'en.json').read_text())
    assert 'source_catalog_sha256' not in en
    for key in (X_POST_DRAFT_KEY, X_POST_ASK_KEY):
        source = en['messages'][key]
        for path in files:
            data = json.loads(path.read_text())
            text = data['messages'][key]
            assert placeholders_of(text) == placeholders_of(source) == {'settings', 'keys', 'x_row', 'connect'}
            if path.stem != 'en':
                assert text != source, (path.stem, key)
                assert data['source_catalog_sha256'] == source_catalog_sha256(), path.stem
