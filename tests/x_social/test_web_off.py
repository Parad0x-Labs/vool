"""With web lookups off, no web lookup tool is offered, and a named one is refused with paste guidance.

G7 of the skills round: a press-pr-manager turn with web off still offered ``web.search``; the model
called it and the turn ended on core's own refusal, so the skill's "Web research is off. Paste the
article text, links or notes..." line never reached the chat, and the refusal said nothing about
what the owner could paste instead.
"""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests._toolchain_fixtures import make_plugin, reset_toolchain_state
from tests.x_social.support import as_vool_browse

TEXT = 'Use press-pr-manager to research recent coverage of checkpoint recovery tools for an Orchard pitch.'
WEB = {'web.search', 'web.research', 'web.fetch'}


@pytest.fixture
def press(vault_home, tmp_path, monkeypatch):
    from core import policy_engine
    from core.runtime_flags import override
    monkeypatch.setenv('VOOL_PLUGINS_DIR', str(tmp_path / 'plugins'))
    monkeypatch.delenv('VOOL_ENABLE_WEB', raising=False)
    make_plugin(tmp_path / 'plugins', plugin_id='local-skills', skills={'press-pr-manager':
        '---\nname: press-pr-manager\ndescription: orchard press pitch research\n'
        'triggers: [press-pr-manager, pitch]\nallowed-tools: [operator.save_note, operator.find_notes, web.search, web.fetch]\n'
        '---\nResearch coverage for the pitch. If web research is off, say "Web research is off. Paste the '
        'article text, links or notes you want me to use, and I will build the pitch from them."\n'})
    as_vool_browse(monkeypatch, 'press-pr-manager')
    reset_toolchain_state()
    policy_engine.load(force_reload=True)
    with override('plugin_runtime_tools', True):
        yield
    reset_toolchain_state()


def test_web_off_offers_no_web_lookup_tool_on_a_skill_turn(press, monkeypatch):
    from core import policy_engine
    from core.tool_offer_assembly import assemble_tool_offer
    ctx = {'surface': 'desktop', 'platform': 'api', '_owner_local': True, 'operating_mode': 'manual'}
    assert not policy_engine.allow_web_fallback()
    offer = assemble_tool_offer(user_text=TEXT, task_class='research', source_context=dict(ctx))
    assert offer.skill_guidance.skills
    assert not WEB & set(offer.intents), offer.intents
    assert 'operator.save_note' in offer.intents
    # The switch, not the skill, is what withholds them.
    monkeypatch.setattr(policy_engine, 'allow_web_fallback', lambda *a, **k: True)
    on = assemble_tool_offer(user_text=TEXT, task_class='research', source_context=dict(ctx))
    assert 'web.search' in on.intents, on.intents


@pytest.mark.parametrize('locale', ['en', 'de', 'ja'])
def test_a_named_web_tool_is_refused_with_paste_guidance_in_the_app_language(isolated_home, locale):
    from unittest import mock

    from core.execution.web_tools import WEB_OFF_REFUSAL_KEY
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.i18n.catalog import catalog_for
    from core.i18n.turn_locale import bind_turn_locale, reset_turn_locale
    from core.tool_intent_executor import execute_tool_intent
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    expected = catalog_for(locale).text(WEB_OFF_REFUSAL_KEY)
    assert expected != WEB_OFF_REFUSAL_KEY
    token = bind_turn_locale(locale)
    try:
        with mock.patch('core.tool_intent_executor.policy_engine.allow_web_fallback', return_value=False):
            result = execute_tool_intent(
                {'intent': 'web.search', 'arguments': {'query': 'checkpoint recovery tools coverage'}},
                task_id='task-g7', session_id='session-g7',
                source_context={'surface': 'desktop', 'platform': 'api'}, hive_activity_tracker=tracker,
            )
    finally:
        reset_turn_locale(token)
    assert result.status == 'disabled' and result.ok is False
    assert result.response_text == expected
    assert result.user_safe_response_text == expected
    assert 'VOOL_ENABLE_WEB=1' in expected
    if locale == 'en':
        assert 'paste the article text, links or notes' in expected
        assert not expected.startswith('No.')
