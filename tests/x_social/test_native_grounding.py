import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.test_skill_served import test_installed_skill_reaches_native_x_flow_and_owner_card as _run


@pytest.mark.parametrize('invented', [False, True], ids=['all-backed', 'fabricated-sibling'])
def test_native_opportunities_reach_chat_with_only_supported_assertions(vault_home, tmp_path, monkeypatch, invented):
    monkeypatch.setenv('SOCIAL_FAST_CARD', '1')
    if invented:
        monkeypatch.setenv('SOCIAL_FAST_CARD_UNBACKED', '1')
    else:
        monkeypatch.delenv('SOCIAL_FAST_CARD_UNBACKED', raising=False)
    _run(vault_home, tmp_path, monkeypatch)
