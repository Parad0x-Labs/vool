"""Installed guidance supplies available seats, never permissions or unknown tools."""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests._toolchain_fixtures import make_plugin, reset_toolchain_state

TOOLS = ('x.usage', 'x.search', 'x.trending', 'x.user', 'x.user_posts', 'x.post',
         'x.action.propose', 'x.action.status', 'x.feedback.save', 'x.feedback.list',
         'x.results', 'operator.save_note', 'operator.find_notes', 'operator.show_note')
TEXT = 'Run the orchard social-manager workflow for today.'


@pytest.fixture
def installed(vault_home, tmp_path, monkeypatch):
    from core import policy_engine
    from core.runtime_flags import override
    monkeypatch.setenv('VOOL_PLUGINS_DIR', str(tmp_path / 'plugins'))
    make_plugin(tmp_path / 'plugins', plugin_id='local-skills', skills={'social-manager':
        '---\nname: social-manager\ndescription: orchard social-manager workflow\n'
        'triggers: [orchard, social-manager]\nallowed-tools: [' + ', '.join(TOOLS) +
        ']\n---\nUse the declared X and note tools; propose changes for approval.\n'})
    reset_toolchain_state()
    config = vault_home / 'config' / 'default_policy.yaml'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('x:\n  trending_enabled: true\n')
    policy_engine.load(force_reload=True)
    with override('plugin_runtime_tools', True):
        yield
    reset_toolchain_state()


def test_installed_skill_seats_each_available_tool_and_admits_its_workflow(installed):
    from core.execution.planner import plan_tool_workflow, should_attempt_tool_intent
    from core.tool_offer_assembly import assemble_tool_offer
    from tests.test_x_api_reads import _connect
    _connect()
    ctx = {'surface': 'desktop', 'platform': 'api', '_owner_local': True, 'operating_mode': 'manual'}
    offer = assemble_tool_offer(user_text=TEXT, task_class='chat', source_context=ctx)
    assert offer.skill_guidance.skills
    assert set(TOOLS) <= set(offer.intents), set(TOOLS) - set(offer.intents)
    assert should_attempt_tool_intent(TEXT, task_class='chat', source_context=ctx)
    decision = plan_tool_workflow(user_text='Read this X post for orchard social-manager https://x.com/user/status/556', task_class='research', executed_steps=[], source_context=ctx)
    assert not decision.handled, decision
    assert not should_attempt_tool_intent('Do not use tools. ' + TEXT, task_class='chat', source_context=ctx)
    bounded = assemble_tool_offer(user_text=TEXT, task_class='chat', max_candidates=8)
    assert len(bounded.specs) <= 8


def test_disconnected_skill_does_not_seat_remote_x_tools(installed):
    from core.tool_offer_assembly import assemble_tool_offer
    offer = assemble_tool_offer(user_text=TEXT, task_class='chat')
    assert not {'x.search', 'x.user', 'x.post', 'x.results', 'x.action.propose'} & set(offer.intents)
    assert 'x.feedback.list' in offer.intents


def test_optional_tools_report_unavailable_and_typos_still_refuse(installed, tmp_path):
    from core.skill_tools import install_skill, validate_skill
    draft = tmp_path / 'portable-social' / 'SKILL.md'
    draft.parent.mkdir()
    draft.write_text('---\nname: portable-social\ndescription: seasonal citrus workflow\nallowed-tools: [x.search, x.results]\n---\nRead permitted citrus context.\n')
    verdict = validate_skill(str(draft))
    assert verdict['status'] == 'ok', verdict
    assert set(verdict['unavailable_tools']) == {'x.search', 'x.results'}
    installed = install_skill(str(draft))
    assert installed['status'] == 'ok', installed
    assert set(installed['unavailable_tools']) == {'x.search', 'x.results'}
    draft.write_text(draft.read_text().replace('x.results', 'x.resutls'))
    refused = validate_skill(str(draft))
    assert refused['status'] == 'invalid'
    assert refused['unknown_tools'] == ['x.resutls']
