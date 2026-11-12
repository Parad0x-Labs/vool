"""GLM audit R9 (X01, X02, X03): with X not connected, an X read says what to connect and where.

Measured: "whats trending on X right now?" ended on "I can't publish an answer to this: it needed
current information...", and the usage ask on "That is not mentioned in the records I have". Neither
said X isn't connected or where to connect it, and a named X trending tool answered with "set
x.trending_enabled and configure a bearer token". Both paths now give one plain reply naming the
real Settings place (the Settings page's own words) and that drafting still works.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._credential_intelligence_support import isolated_home

ASKS = ('whats trending on X right now?', 'anything blowing up on twitter tonight?',
        'how much of my X api usage have i burned')
ENGINEERING = ('bearer', 'token', 'x.trending', 'trending_enabled', 'runtime', 'api.x.com', 'oauth',
               'credential', 'not certified', 'records i have', "can't publish")
CATALOGS = Path(__file__).resolve().parents[2] / 'core' / 'i18n' / 'catalogs'


def _plain(text: str) -> None:
    lowered = text.lower()
    assert 'X is not connected yet' in text, text
    assert 'Settings > API Keys > X (Twitter)' in text and 'Connect X' in text, text
    assert 'draft posts' in text, text
    assert not [word for word in ENGINEERING if word in lowered], text


@pytest.mark.parametrize('ask', ASKS)
def test_an_x_read_without_x_connected_says_where_to_connect_it(isolated_home, ask):
    from apps.vool_agent import VoolAgent
    from core.x_api import connected

    assert not connected()
    agent = VoolAgent(backend_name='test-backend', device='channel-test', persona_id='default')
    result = agent.run_once(ask, source_context={'surface': 'desktop', 'platform': 'api'})
    _plain(str(result.get('response') or ''))


def test_the_reply_speaks_the_app_language_with_the_settings_page_words(isolated_home):
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name='test-backend', device='channel-test', persona_id='default')
    result = agent.run_once(ASKS[0], source_context={'surface': 'desktop', 'platform': 'api', 'ui_locale': 'de'})
    text = str(result.get('response') or '')
    assert 'Einstellungen > API-Schlüssel > X (ehemals Twitter)' in text and 'X verbinden' in text, text


@pytest.mark.parametrize('intent', ['x.trending', 'x.search', 'x.usage'])
def test_a_named_x_tool_without_x_connected_gives_the_same_plain_reply(isolated_home, intent):
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent
    from core.x_connect_hint import not_connected_text

    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    result = execute_tool_intent({'intent': intent, 'arguments': {'query': 'launch'}}, task_id='task-r9',
                                 session_id='session-r9', source_context={'surface': 'desktop', 'platform': 'api'},
                                 hive_activity_tracker=tracker)
    assert result.ok is False
    assert result.response_text == not_connected_text('en'), result.response_text
    assert result.user_safe_response_text == not_connected_text('en')
    _plain(result.response_text)


def test_the_runtime_tool_door_and_the_local_x_catalog_say_it_too(isolated_home):
    from core.runtime_execution_tools import execute_runtime_tool
    from core.x_chat_tools import run_x_tool

    refused = execute_runtime_tool('x.trending', {}, source_context={'surface': 'desktop', 'platform': 'api'})
    assert refused is not None and not refused.ok
    _plain(refused.response_text)
    catalog = run_x_tool('x.api.catalog', {'query': 'trends'}, {'surface': 'desktop', 'platform': 'api'})
    assert catalog.ok and 'Settings > API Keys > X (Twitter)' in catalog.response_text, catalog.response_text


def test_drafting_and_a_connected_x_are_left_alone(isolated_home, monkeypatch):
    from core import x_connect_hint

    for ask in ('draft a tweet about my launch', 'write a post for X about whats trending',
                'what is happening in x86 assembly', 'what is the latest value of x', 'is the x axis trending up'):
        assert not x_connect_hint.asks_for_x_read(ask), ask
    assert all(x_connect_hint.asks_for_x_read(ask) for ask in ASKS)

    class Agent:
        def _fast_path_result(self, **kwargs):
            return dict(kwargs)

    assert x_connect_hint.reply_if_not_connected(Agent(), ASKS[0], session_id='s', source_context={}) is not None
    monkeypatch.setattr(x_connect_hint, 'x_connected', lambda: True)
    assert x_connect_hint.reply_if_not_connected(Agent(), ASKS[0], session_id='s', source_context={}) is None


def test_campaign_work_that_names_x_is_left_to_the_social_content_manager(isolated_home):
    """A plan or campaign that mentions X keeps the plan, the other platforms and the owner's exact
    line: the social content manager answers it (a publish ask gets its typed unavailable outcome
    and copy-ready text). A bare X read or post ask still gets the plain connect reply."""
    from core import x_connect_hint

    class Agent:
        def _fast_path_result(self, **kwargs):
            return dict(kwargs)

    for ask in ('Manage our campaign and publish the approved post to X and LinkedIn',
                'Check X trends and plan the campaign post around this exact line: `A budget it cannot '
                'talk its way past.`',
                'build a content calendar for X and Bluesky and post the first one on X'):
        assert x_connect_hint.reply_if_not_connected(Agent(), ask, session_id='s', source_context={}) is None, ask
    for ask in (ASKS[0], 'post a tweet for me saying hello world'):
        assert x_connect_hint.reply_if_not_connected(Agent(), ask, session_id='s', source_context={}) is not None, ask


def test_the_reply_is_in_all_17_catalogs_and_names_the_settings_page_words():
    from core.i18n.catalog import placeholders_of, source_catalog_sha256
    from core.x_connect_hint import X_NOT_CONNECTED_KEY

    files = sorted(CATALOGS.glob('*.json'))
    assert len(files) == 17
    source = json.loads((CATALOGS / 'en.json').read_text())['messages'][X_NOT_CONNECTED_KEY]
    for path in files:
        data = json.loads(path.read_text())
        text = data['messages'][X_NOT_CONNECTED_KEY]
        assert placeholders_of(text) == placeholders_of(source) == {'settings', 'keys', 'x_row', 'connect'}
        if path.stem != 'en':
            assert text != source, path.stem
            assert data['source_catalog_sha256'] == source_catalog_sha256(), path.stem
        for key in ('sidebar.settings', 'settings.group.keys.title', 'xsetup.title', 'xsetup.open'):
            assert data['messages'].get(key), (path.stem, key)
    page = (Path(__file__).resolve().parents[2] / 'core' / 'vool_settings_page.py').read_text()
    assert "T('xsetup.title', 'X (Twitter)')" in page and "T('xsetup.open', 'Connect X')" in page
    assert '"title": "API Keys"' in page
