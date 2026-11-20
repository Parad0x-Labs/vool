"""Real capture, credential vault and lifecycle operations; no external service calls."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core.credential_store import get_credential, has_credential
from core.plugin_tools import plugin_scratch_dir
from core.web.api.addon_api import handle_addon_post
from tests.test_plugin_manifest_v1 import manifest


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    root = tmp_path / "source"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(json.dumps(manifest()))
    (root / "LICENSE").write_text("MIT License\n")
    return root


def installed(package, *, update=False):
    review = manager.prepare_local(str(package), update=update)
    return manager.install(review["review_id"], accepted=True, digest=review["digest"])["plugin"]


def test_install_is_verified_disabled_and_not_store_reviewed(package):
    review = manager.prepare_local(str(package))
    assert review["reviewed_by_store"] is False
    assert lifecycle.records() == []
    info = manager.install(review["review_id"], accepted=True, digest=review["digest"])["plugin"]
    assert info["stage"] == "verified" and info["enabled"] is False
    assert not info["reviewed_by_store"]
    assert not lifecycle.record_for(info["id"]).network_consent
    with pytest.raises(lifecycle.LifecycleError, match="consent"):
        lifecycle.enable(info["id"])


def test_consent_acceptance_and_decline_use_one_lifecycle_record(package):
    info = installed(package)
    declined = manager.enable(info["id"], accepted=False, approval_digest=info["approval_digest"])
    assert declined["enabled"] is False and not lifecycle.is_available(info["id"])
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    record = lifecycle.record_for(info["id"])
    assert record.network_consent and lifecycle.is_available(info["id"])
    assert manager.inventory()[0]["enabled"] is True
    manager.disable(info["id"])
    assert not lifecycle.is_available(info["id"])
    with pytest.raises(lifecycle.LifecycleError, match="consent"):
        lifecycle.enable(info["id"])


def test_credentials_are_saved_only_under_the_declared_own_namespace(package):
    info = installed(package)
    value = secrets.token_urlsafe(32)
    result = manager.connect(info["id"], "access", value)
    assert get_credential("plugin.sample-provider.access") == value
    assert result["plugin"]["configured_slots"] == ["access"]
    assert value not in json.dumps(result)
    with pytest.raises(lifecycle.LifecycleError, match="declared"):
        manager.connect(info["id"], "other-provider.access", value)
    assert not has_credential("plugin.other-provider.access")


def test_tampering_invalidates_consent_and_requires_review(package):
    info = installed(package)
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    path = Path(lifecycle.record_for(info["id"]).root) / "LICENSE"
    path.chmod(0o644)
    path.write_text("Changed package bytes\n")
    assert not lifecycle.is_available(info["id"])
    row = manager.inventory()[0]
    assert row["enabled"] is False and "changed" in row["reason"]
    assert not lifecycle.record_for(info["id"]).network_consent


def test_stale_consent_does_not_enable_a_replacement(package):
    info = installed(package)
    (package / "LICENSE").write_text("A changed licence text\n")
    current = installed(package, update=True)
    assert current["approval_digest"] != info["approval_digest"]
    with pytest.raises(lifecycle.LifecycleError, match="changed"):
        manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    assert not lifecycle.is_available(info["id"])


def test_duplicate_identity_preserves_the_existing_package(package):
    info = installed(package)
    digest = lifecycle.record_for(info["id"]).verified_digest
    (package / "LICENSE").write_text("Replacement without update consent\n")
    with pytest.raises(lifecycle.LifecycleError, match="already installed"):
        installed(package)
    assert lifecycle.record_for(info["id"]).verified_digest == digest


def test_update_always_returns_to_verified_disabled(package):
    info = installed(package)
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    (package / "LICENSE").write_text("Updated package\n")
    current = installed(package, update=True)
    assert current["stage"] == "verified" and current["enabled"] is False
    assert not lifecycle.record_for(info["id"]).network_consent


@pytest.mark.parametrize("remove_key", [True, False])
def test_uninstall_disables_first_removes_owned_files_and_preserves_history(package, remove_key):
    info = installed(package)
    manager.connect(info["id"], "access", secrets.token_urlsafe(32))
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    target = Path(lifecycle.record_for(info["id"]).root)
    scratch = plugin_scratch_dir(info["id"])
    scratch.mkdir(parents=True)
    (scratch / "output.txt").write_text("Disposable plugin output")
    manager.uninstall(info["id"], remove_key=remove_key)
    assert not target.exists() and not scratch.exists()
    assert has_credential("plugin.sample-provider.access") is not remove_key
    assert lifecycle.record_for(info["id"]).stage == "uninstalled"
    assert lifecycle.record_for(info["id"]).evidence[-1]["event"] == "uninstalled"
    assert manager.inventory() == []
    again = installed(package)
    assert again["enabled"] is False


def test_bad_manifest_can_still_be_uninstalled_without_leaving_its_key(package):
    info = installed(package)
    manager.connect(info["id"], "access", secrets.token_urlsafe(32))
    target = Path(lifecycle.record_for(info["id"]).root)
    file = target / ".codex-plugin/plugin.json"
    file.chmod(0o644)
    file.write_text("broken")
    assert manager.inventory()[0]["stage"] == "invalid"
    manager.uninstall(info["id"])
    assert not target.exists() and not has_credential("plugin.sample-provider.access")


def test_changed_staging_digest_is_never_installed(package):
    review = manager.prepare_local(str(package))
    with pytest.raises(lifecycle.LifecycleError, match="changed"):
        manager.install(review["review_id"], accepted=True, digest="0" * 64)
    assert lifecycle.records() == []


def test_owner_api_rejects_remote_cross_origin_and_extra_fields(package):
    body = {"action": "plugin_prepare", "path": str(package)}
    headers = {"Content-Type": "application/json", "Host": "127.0.0.1:10000", "Origin": "http://127.0.0.1:10000"}
    assert handle_addon_post(body, headers, "192.0.2.1")[0] == 403
    assert handle_addon_post(body, {**headers, "Origin": "https://elsewhere.example"}, "127.0.0.1")[0] == 403
    assert handle_addon_post({**body, "autostart": True}, headers, "127.0.0.1")[0] == 400
    status, result = handle_addon_post(body, headers, "127.0.0.1")
    assert status == 200 and result["reviewed_by_store"] is False


def test_package_errors_have_plain_owner_visible_messages(package):
    (package / ".codex-plugin/plugin.json").write_text("bad json")
    status, result = handle_addon_post({"action": "plugin_prepare", "path": str(package)},
                                      {"Content-Type": "application/json"}, "127.0.0.1")
    assert status == 400 and "invalid" in result["message"]
    assert result["message_key"].startswith("addons.plugin_message_")
