from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.test_skill_served import test_installed_skill_reaches_native_x_flow_and_owner_card as _run


def test_rate_limit_names_reset_and_stops_without_sending(vault_home, tmp_path, monkeypatch):
    monkeypatch.setenv('SOCIAL_RATE_LIMIT', '1')
    _run(vault_home, tmp_path, monkeypatch)
