import hashlib

import pytest
from starlette.testclient import TestClient

from core.registry.codec import RegistryError
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth, Provider
from registry_service.store import Store
from tests.registry.audit_helpers import package, trusted
from tests.registry.helpers import NOW, TARGET


def test_w3_other_browsers_pending_flows_do_not_block_fresh_login(tmp_path):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    with store.connection() as db:
        db.executemany("INSERT INTO oauth VALUES (?,?,?,?,?,?,?)", [
            (hashlib.sha256(str(i).encode()).hexdigest(), "github", "fixture-verifier", "fixture-nonce",
             hashlib.sha256(("other-browser-" + str(i)).encode()).hexdigest(), NOW + 600, "") for i in range(1000)])
    auth = Auth(store, "https://registry.test", [Provider("github", "fixture-client", "fixture-secret")])
    with trusted(raw, tmp_path / "trust.sqlite") as (catalog, *_):
        app = create_app(store, auth, catalog, TARGET, clock=lambda: NOW)
        with TestClient(app, base_url="https://registry.test") as browser:
            response = browser.get(PREFIX + "/auth/github/start", follow_redirects=False)
            assert response.status_code == 303, response.text
            assert "vool_registry_oauth" in response.cookies


def test_w3_capacity_is_bounded_per_browser_and_provider(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    auth = Auth(store, "https://registry.test", [Provider("github", "fixture-client", "fixture-secret"),
                                               Provider("google", "fixture-client", "fixture-secret")])
    _, browser = auth.begin("github", now=NOW)
    for _ in range(7):
        _, reused = auth.begin("github", now=NOW, browser=browser)
        assert reused == browser
    with pytest.raises(RegistryError, match="browser"):
        auth.begin("github", now=NOW, browser=browser)
    assert auth.begin("google", now=NOW, browser=browser)[1] == browser
    assert auth.begin("github", now=NOW)[1] != browser


def test_w3_http_flow_cookie_reuses_binding_and_isolates_other_clients(tmp_path):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    auth = Auth(store, "https://registry.test", [Provider("github", "fixture-client", "fixture-secret"),
                                               Provider("google", "fixture-client", "fixture-secret")])
    with trusted(raw, tmp_path / "trust.sqlite") as (catalog, *_):
        app = create_app(store, auth, catalog, TARGET, clock=lambda: NOW)
        with TestClient(app, base_url="https://registry.test") as browser:
            for _ in range(8):
                assert browser.get(PREFIX + "/auth/github/start", follow_redirects=False).status_code == 303
            assert browser.get(PREFIX + "/auth/github/start", follow_redirects=False).status_code == 400
            assert browser.get(PREFIX + "/auth/google/start", follow_redirects=False).status_code == 303
        with TestClient(app, base_url="https://registry.test") as fresh:
            assert fresh.get(PREFIX + "/auth/github/start", follow_redirects=False).status_code == 303
