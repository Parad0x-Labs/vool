import hashlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from core import plugin_catalog, plugin_lifecycle
from core.registry.artifact import inspect
from core.registry.client import Client
from core.registry.codec import RegistryError
from core.registry.trust import TrustedCatalog
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, trust_fixture


def test_anonymous_download_local_review_install_enable_tamper_and_revoke(tmp_path, monkeypatch):
    runtime_home = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(runtime_home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(runtime_home / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins-home"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    plugin_lifecycle.reset_for_tests()
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    server_trust = TrustedCatalog(root, tmp_path / "server" / "trust", now=NOW)
    snapshot = catalog_fixture(raw, policy, keys)
    server_trust.accept(snapshot, now=NOW)
    store = Store(tmp_path / "server" / "accounts")
    store.publish(server_trust, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    app = create_app(store, Auth(store, "https://registry.test", []), server_trust, TARGET, clock=lambda: NOW)
    calls = []
    with TestClient(app, base_url="https://registry.test") as http:
        def request(url, *, limit):
            calls.append(url)
            response = http.get(url)
            assert response.status_code == 200, response.text
            assert len(response.content) <= limit
            return response.content

        client = Client(root, TARGET, origin="https://registry.test", request=request, now=NOW)
        with pytest.raises(RegistryError, match="consent"):
            client.refresh(approved=False, now=NOW)
        assert calls == []
        rows = client.refresh(approved=True, now=NOW)["entries"]
        assert rows[0]["id"] == "alice/echo" and rows[0]["reviewed"]
        preview = client.prepare("alice/echo", "1.0.0", approved=True, now=NOW)
        assert preview["installed"] is False and preview["instructions"]
        assert not plugin_lifecycle.records()
        with pytest.raises(RegistryError, match="consent"):
            client.accept("alice/echo", "1.0.0", preview["review_id"], accepted=False, enable=True, now=NOW)
        result = client.accept("alice/echo", "1.0.0", preview["review_id"], accepted=True, enable=False, now=NOW)
        plugin_id = result["plugin_id"]
        assert result["enabled"] is False and not plugin_lifecycle.is_available(plugin_id)
        plugin_lifecycle.enable(plugin_id)
        plugin_catalog.set_plugin_enabled(plugin_id, True)
        assert plugin_lifecycle.is_available(plugin_id)
        from core.tool_offer_assembly import loaded_skills

        assert any(skill.plugin_id == plugin_id for skill in loaded_skills())
        from core.plugin_skills import load_skills

        installed = plugin_lifecycle.record_for(plugin_id)
        skills = load_skills(Path(installed.root), plugin_id=plugin_id)
        assert skills[0].body == "Summarize only the provided message."
        file = Path(installed.root) / "skills/echo/SKILL.md"
        file.chmod(0o644)
        file.write_text("---\nname: echo\ndescription: altered\n---\nChanged after approval\n")
        assert not plugin_lifecycle.is_available(plugin_id)
        assert all(skill.plugin_id != plugin_id for skill in loaded_skills())
        file.write_bytes(inspect(raw)["files"]["skills/echo/SKILL.md"])
        file.chmod(0o444)
        assert plugin_lifecycle.is_available(plugin_id)
        revoked = catalog_fixture(raw, policy, keys, sequence=2, revoked=[hashlib.sha256(raw).hexdigest()])
        server_trust.accept(revoked, now=NOW)
        client.refresh(approved=True, now=NOW)
        assert not plugin_lifecycle.is_available(plugin_id)
        assert all(skill.plugin_id != plugin_id for skill in loaded_skills())
        row = next(row for row in plugin_catalog.read_plugin_catalog()["plugins"] if row["id"] == plugin_id)
        assert row["enabled"] is False and row["registry_available"] is False
        with pytest.raises(plugin_lifecycle.LifecycleError, match="revoked"):
            plugin_lifecycle.enable(plugin_id)
        assert store.stats("alice/echo")["download_requests"] == 1
        client.close()
    server_trust.close()


def test_website_cannot_call_local_install_or_select_fetch_url():
    from core.web.api.addon_api import handle_addon_post

    headers = {"Content-Type": "application/json", "Host": "127.0.0.1:8123", "Origin": "https://vool.dev"}
    assert handle_addon_post({"action": "registry_refresh", "approved": True}, headers, "127.0.0.1")[0] == 403
    headers["Origin"] = "http://127.0.0.1:8123"
    assert handle_addon_post({"action": "registry_prepare", "approved": True, "url": "https://evil.test"}, headers,
                             "127.0.0.1")[0] == 400
