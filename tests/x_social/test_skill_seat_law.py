"""Review r4 M3: a matched skill may add read-only or proposal tools, never side-effect tools."""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests._toolchain_fixtures import make_plugin, reset_toolchain_state

SIDE_EFFECTS = ('sandbox.run_command', 'workspace.write_file', 'machine.write_file', 'operator.cleanup_temp_files',
                'machine.move_path', 'code.task.open', 'code.task.step', 'email.send', 'email.draft.send',
                'wallet.spend', 'pay.x402', 'repo.push', 'vool-browser.click')
READS = ('operator.find_notes', 'operator.show_note', 'x.feedback.list')
TEXT = 'Run the quartz garden helper workflow for today.'


@pytest.fixture
def side_effect_skill(vault_home, tmp_path, monkeypatch):
    from core.runtime_flags import override
    monkeypatch.setenv('VOOL_PLUGINS_DIR', str(tmp_path / 'plugins'))
    make_plugin(tmp_path / 'plugins', plugin_id='local-skills', skills={'quartz-helper':
        '---\nname: quartz-helper\ndescription: quartz garden helper workflow\n'
        'triggers: [quartz, garden]\nallowed-tools: [' + ', '.join((*SIDE_EFFECTS, *READS)) +
        ']\n---\nRun the declared tools for the quartz garden.\n'})
    reset_toolchain_state()
    with override('plugin_runtime_tools', True):
        yield
    reset_toolchain_state()


def test_skill_declared_side_effect_tools_are_never_seated(side_effect_skill):
    from core.tool_offer_assembly import assemble_tool_offer, matched_skill_tool_intents

    ctx = {'surface': 'desktop', 'platform': 'api', '_owner_local': True, 'operating_mode': 'auto'}
    seats = matched_skill_tool_intents(TEXT, task_class='chat', source_context=ctx)
    assert set(seats) == set(READS), seats
    offer = assemble_tool_offer(user_text=TEXT, task_class='chat', source_context=dict(ctx))
    assert offer.skill_guidance.skills
    assert not set(SIDE_EFFECTS) & set(offer.intents), set(SIDE_EFFECTS) & set(offer.intents)


def test_contract_tiers_decide_which_declared_tools_a_skill_may_seat():
    from core.tool_offer_assembly import _skill_may_seat

    assert not any(_skill_may_seat(intent) for intent in SIDE_EFFECTS)
    social = ('operator.save_note', 'operator.find_notes', 'operator.show_note', 'x.usage', 'x.search',
              'x.trending', 'x.user', 'x.user_posts', 'x.post', 'x.action.propose', 'x.action.status',
              'x.feedback.save', 'x.feedback.list', 'x.results', 'x.replies.unanswered', 'web.search', 'web.fetch')
    assert all(_skill_may_seat(intent) for intent in social)
