"""A skill removed from the Skill Codex stops being offered on a device that installed it, within a day and
without the owner pressing Browse, once they turned the daily check on. Off, it makes no request at all."""
import hashlib
import json

import pytest
from starlette.testclient import TestClient

from core import plugin_catalog, plugin_lifecycle
from core.registry import client as registry_client
from core.registry import recall
from core.registry.client import Client
from core.registry.trust import TrustedCatalog
from core.web.api.addon_api import handle_addon_post
from registry_service.api import create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, trust_fixture

OWN_PAGE = {"Content-Type": "application/json", "Host": "127.0.0.1:8123", "Origin": "http://127.0.0.1:8123"}
DAY, HOUR = 24 * 3600, 3600


@pytest.fixture
def device(tmp_path, monkeypatch):
    """A device with one enabled Skill Codex skill, and the service it came from."""
    runtime_home = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(runtime_home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(runtime_home / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins-home"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    plugin_lifecycle.reset_for_tests()
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    server_trust = TrustedCatalog(root, tmp_path / "server" / "trust", now=NOW)
    server_trust.accept(catalog_fixture(raw, policy, keys), now=NOW)
    store = Store(tmp_path / "server" / "accounts")
    store.publish(server_trust, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    app = create_app(store, Auth(store, "https://registry.test", []), server_trust, TARGET, clock=lambda: NOW)
    calls, down, replay, seen = [], [], [], []
    with TestClient(app, base_url="https://registry.test") as http:
        def request(url, *, limit):
            calls.append(url)
            if down:
                raise OSError("the service cannot be reached")
            if replay:
                return replay[0]
            response = http.get(url)
            assert response.status_code == 200, response.text
            if not seen:
                seen.append(response.content)
            return response.content

        def connect():
            return Client(root, TARGET, origin="https://registry.test", request=request, now=NOW)

        # The check builds its own client exactly as Browse does; only where it connects is the test's.
        monkeypatch.setattr(registry_client, "configured_client", connect)
        first = connect()
        first.refresh(approved=True, now=NOW)
        preview = first.prepare("alice/echo", "1.0.0", approved=True, now=NOW)
        plugin_id = first.accept("alice/echo", "1.0.0", preview["review_id"], accepted=True, enable=False, now=NOW)["plugin_id"]
        first.close()
        plugin_lifecycle.enable(plugin_id)
        plugin_catalog.set_plugin_enabled(plugin_id, True)
        assert plugin_lifecycle.is_available(plugin_id)
        calls.clear()

        def remove_on_the_service(sequence=2):
            server_trust.accept(catalog_fixture(raw, policy, keys, sequence=sequence,
                                                revoked=[hashlib.sha256(raw).hexdigest()]), now=NOW)

        yield {"plugin_id": plugin_id, "calls": calls, "down": down, "remove": remove_on_the_service, "home": runtime_home,
               "replay": replay, "first_list": seen[0]}
    server_trust.close()
    plugin_lifecycle.reset_for_tests()


# What one daily check asks for: the signed list, then the signed badge document, so a withdrawn badge
# also leaves within the day. Both are the same public files for everyone and carry nothing about the person.
PUBLIC_READS = ["https://registry.test/registry/api/snapshot", "https://registry.test/registry/api/badges"]


def offered(plugin_id):
    from core.tool_offer_assembly import loaded_skills

    return any(skill.plugin_id == plugin_id for skill in loaded_skills())


def test_with_no_choice_saved_the_check_is_on_and_a_removal_arrives(device):
    assert recall.status() == {"status": "ok", "enabled": True, "installs": 1, "last_checked": None, "last_failed": None}
    assert device["calls"] == [], "reading the choice makes no request"
    device["remove"]()
    assert recall.check(now=NOW + 60) == {"status": "ok", "removed_listed": 1}
    assert device["calls"] == PUBLIC_READS and not plugin_lifecycle.is_available(device["plugin_id"])


def test_turned_off_it_asks_nothing_and_a_removal_does_not_arrive(device):
    recall.set_enabled(False)
    device["remove"]()
    assert recall.status() == {"status": "ok", "enabled": False, "installs": 1, "last_checked": None, "last_failed": None}
    assert recall.check(now=NOW + DAY) == {"status": "off"}
    assert device["calls"] == []
    assert plugin_lifecycle.is_available(device["plugin_id"]) and offered(device["plugin_id"])


def test_turned_on_from_the_add_ons_page_a_removal_arrives_with_the_next_daily_check(device):
    status, body = handle_addon_post({"action": "registry_recall", "enabled": True}, OWN_PAGE, "127.0.0.1")
    assert status == 200 and body["enabled"] is True and body["installs"] == 1
    assert device["calls"] == [], "saving the choice makes no request"
    device["remove"]()
    assert plugin_lifecycle.is_available(device["plugin_id"]) and offered(device["plugin_id"])

    assert recall.check(now=NOW + 60) == {"status": "ok", "removed_listed": 1}
    assert device["calls"] == PUBLIC_READS, "the public list and the public badge document, nothing else"
    assert not plugin_lifecycle.is_available(device["plugin_id"]) and not offered(device["plugin_id"])
    row = next(row for row in plugin_catalog.read_plugin_catalog()["plugins"] if row["id"] == device["plugin_id"])
    assert row["enabled"] is False and row["registry_available"] is False
    with pytest.raises(plugin_lifecycle.LifecycleError, match="revoked"):
        plugin_lifecycle.enable(device["plugin_id"])

    # Once a day: nothing more is asked until a day has gone by.
    assert recall.check(now=NOW + 60 + DAY - 1) == {"status": "not_due"}
    assert device["calls"] == PUBLIC_READS
    status, body = handle_addon_post({"action": "registry_recall"}, OWN_PAGE, "127.0.0.1")
    assert status == 200 and body["last_checked"] == NOW + 60 and body["last_failed"] is None


def test_a_check_that_cannot_finish_changes_nothing_and_is_tried_again_later(device, monkeypatch):
    monkeypatch.setattr(recall, "RETRY", 600)  # the signed list in this test lives one hour
    recall.set_enabled(True)
    device["remove"]()
    device["down"].append(True)
    assert recall.check(now=NOW + 60) == {"status": "failed"}
    assert plugin_lifecycle.is_available(device["plugin_id"]), "what was installed keeps working from the last verified list"
    assert recall.status()["last_failed"] == NOW + 60 and recall.status()["last_checked"] is None
    assert recall.check(now=NOW + 60 + 599) == {"status": "not_due"}
    assert len(device["calls"]) == 1
    device["down"].clear()
    assert recall.check(now=NOW + 60 + 600)["status"] == "ok"
    assert not plugin_lifecycle.is_available(device["plugin_id"])
    assert recall.status()["last_failed"] is None and recall.status()["last_checked"] == NOW + 660


def test_a_list_from_before_the_removal_is_refused_and_the_removal_stays_in_force(device, monkeypatch):
    monkeypatch.setattr(recall, "INTERVAL", 600)
    recall.set_enabled(True)
    device["remove"]()
    assert recall.check(now=NOW + 60)["status"] == "ok"
    assert not plugin_lifecycle.is_available(device["plugin_id"])
    # The service, or someone in its place, now answers with the signed list from before the removal.
    device["replay"].append(device["first_list"])
    assert recall.check(now=NOW + 60 + 600) == {"status": "failed"}
    assert not plugin_lifecycle.is_available(device["plugin_id"]) and not offered(device["plugin_id"])
    assert recall.status()["last_checked"] == NOW + 60, "a refused list is not counted as a check"


def test_nothing_from_the_skill_codex_installed_means_no_request(device):
    recall.set_enabled(True)
    plugin_lifecycle.uninstall(device["plugin_id"])
    assert recall.status()["installs"] == 0
    assert recall.check(now=NOW + DAY) == {"status": "nothing_installed"}
    assert device["calls"] == []


def test_a_saved_choice_that_cannot_be_read_counts_as_off(device):
    recall.set_enabled(True)
    (device["home"] / "config" / "registry_recall.json").write_text("{not json")
    assert recall.status()["enabled"] is False
    assert recall.check(now=NOW + DAY) == {"status": "off"}
    assert device["calls"] == []


def test_a_clock_set_back_does_not_silence_the_check(device):
    path = device["home"] / "config" / "registry_recall.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": True, "last_checked": NOW + 400 * DAY}))
    device["remove"]()
    assert recall.check(now=NOW + 60)["status"] == "ok"
    assert device["calls"] == PUBLIC_READS and not plugin_lifecycle.is_available(device["plugin_id"])


@pytest.mark.parametrize("body,headers,host,status", [
    ({"action": "registry_recall", "enabled": "yes"}, OWN_PAGE, "127.0.0.1", 400),
    ({"action": "registry_recall", "enabled": True, "url": "https://elsewhere.test"}, OWN_PAGE, "127.0.0.1", 400),
    ({"action": "registry_recall", "enabled": True}, {**OWN_PAGE, "Origin": "https://vool.dev"}, "127.0.0.1", 403),
    ({"action": "registry_recall", "enabled": True}, OWN_PAGE, "203.0.113.9", 403),
])
def test_only_the_owners_own_page_can_change_the_choice(device, body, headers, host, status):
    recall.set_enabled(False)
    assert handle_addon_post(body, headers, host)[0] == status
    assert recall.status()["enabled"] is False


def test_the_background_pass_waits_first_and_stops_when_told(monkeypatch):
    import threading

    seen, stop = [], threading.Event()
    monkeypatch.setattr(recall, "FIRST_DELAY", 0.01)
    monkeypatch.setattr(recall, "RETRY", 0.01)
    monkeypatch.setattr(recall, "check", lambda: seen.append(1) or (len(seen) >= 3 and stop.set()) or {"status": "off"})
    thread = recall.start(stop)
    thread.join(timeout=5)
    assert not thread.is_alive() and len(seen) >= 3 and thread.daemon is True
