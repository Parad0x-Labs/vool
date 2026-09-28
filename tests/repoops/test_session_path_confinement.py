"""A receipt identifier cannot select an arbitrary JSON file on the daemon host."""
from __future__ import annotations

import json

import pytest

from core.repoops import plane
from core.web.api import repoops_api


@pytest.mark.parametrize("key", ["../outside", "sub/../../outside", "/absolute", "..\\outside"])
def test_session_receipt_refuses_path_syntax(tmp_path, monkeypatch, key):
    root = tmp_path / "sessions"
    root.mkdir()
    marker = {"private": "must-not-be-served"}
    (tmp_path / "outside.json").write_text(json.dumps(marker))
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(root))
    assert repoops_api.repo_session_payload(key)["found"] is False


def test_session_receipt_refuses_a_symlink_outside_the_store(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"private": "must-not-be-served"}))
    (root / "rs-linked.json").symlink_to(outside)
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(root))
    assert repoops_api.repo_session_payload("rs-linked")["found"] is False
    assert repoops_api.repo_sessions_payload()["sessions"] == []


def test_valid_legacy_session_identifier_still_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path))
    payload = {"session_key": "rs-seat-1", "stage": "inspect"}
    (tmp_path / "rs-seat-1.json").write_text(json.dumps(payload))
    assert repoops_api.repo_session_payload("rs-seat-1") == {"found": True, "session": payload}


def _session(key):
    return plane.RepoSession(session_key=key, objective="fixture", root="fixture", session_id="s",
                             turn_id="t", turn_key="k", created_at="2026-01-01T00:00:00+00:00")


def test_runtime_cannot_persist_a_path_as_a_session_key(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(root))
    runtime = plane.RepoOpsRuntime()
    assert runtime._persist(_session("../outside")) is False
    assert not (tmp_path / "outside.json").exists()
    assert not (tmp_path / "outside.json.tmp").exists()
    assert not (tmp_path / "outside.json.lock").exists()
    assert runtime._persist(_session("rs-normal-1")) is True
    assert runtime._load("rs-normal-1").session_key == "rs-normal-1"


def test_runtime_rejects_a_stored_record_with_a_different_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path))
    (tmp_path / "rs-normal-1.json").write_text(json.dumps(_session("../outside").to_json()))
    runtime = plane.RepoOpsRuntime()
    assert runtime._load("rs-normal-1") is None
    session, error = runtime._require_fresh("rs-normal-1")
    assert session is None and error is not None


def test_served_receipt_cannot_expose_a_json_outside_the_journal(tmp_path, monkeypatch):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    root = tmp_path / "sessions"
    root.mkdir()
    (tmp_path / "outside.json").write_text(json.dumps({"private": "private-marker"}))
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(root))
    response = dispatch_get(path="/api/repoops/session", query={"id": ["../outside"]},
                            runtime=RuntimeServices(display_name="VOOL"), model_name="fixture",
                            client_host="127.0.0.1")
    assert response.status == 404
    assert b"private-marker" not in response.body


@pytest.mark.parametrize("suffix", [".json", ".json.tmp", ".json.lock"])
def test_persistence_never_follows_a_journal_artifact_symlink(tmp_path, monkeypatch, suffix):
    root = tmp_path / "sessions"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("preserved")
    (root / ("rs-normal-1" + suffix)).symlink_to(outside)
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(root))
    assert plane.RepoOpsRuntime()._persist(_session("rs-normal-1")) is False
    assert outside.read_text() == "preserved"


# --- a lifecycle plugin_id is one path component under plugins/ -------------------------------
# The operator console's lifecycle door and its re-registration read one pack directory named
# by the request's plugin_id (or by a manifest-declared id the discovery row fed back). A pid
# with a separator, a parent step, an absolute prefix or a drive-qualified prefix would name a
# path outside the pack root; both pid-to-path owners must refuse it before any Path join, and
# every legitimate single-component id must keep working.


@pytest.mark.parametrize("pid", [
    "../escape", "..", ".", "a/b", "a\\b", "/abs", "//abs", "plugins/../../x",
    "a/../b", "..\\..\\x", "a\x00b", "C:", "C:pack", "c:\\pack", "server:share", "\\\\server\\share",
])
def test_plugin_lifecycle_refuses_path_syntax_before_any_join(pid, monkeypatch):
    import core.plugin_catalog as catalog

    monkeypatch.setattr(catalog, "plugins_root", lambda: pytest.fail("a traversal-shaped pid reached path construction"))
    result = repoops_api.plugin_lifecycle_action(action="install", plugin_id=pid)
    assert result["ok"] is False
    assert "plugins/" in result["error"]


@pytest.mark.parametrize("pid", ["", "   "])
def test_plugin_lifecycle_blank_ids_still_hit_the_required_check(pid, monkeypatch):
    import core.plugin_catalog as catalog

    monkeypatch.setattr(catalog, "plugins_root", lambda: pytest.fail("reached path construction"))
    result = repoops_api.plugin_lifecycle_action(action="install", plugin_id=pid)
    assert result["ok"] is False
    assert result["error"] == "plugin_id is required"


def test_plugin_lifecycle_reregistration_refuses_path_syntax():
    ok, error = repoops_api._reregister_pack("../escape", "/tmp/nowhere")
    assert ok is False
    assert "plugins/" in error


@pytest.mark.parametrize("pid", ["vool-database", "wallet.tools", "漢字-pack", "a" * 128, "p_9"])
def test_plugin_lifecycle_single_component_ids_reach_the_owner(monkeypatch, tmp_path, pid):
    """Legitimate ids are one path component: they must pass confinement unchanged."""
    import core.plugin_catalog as catalog
    import core.plugin_lifecycle as lifecycle

    calls = []
    base = tmp_path / "packs"

    def fake_install(plugin_id, *, root, source):
        calls.append((plugin_id, None if root is None else str(root)))
        raise lifecycle.LifecycleError("stop-here-after-confinement")

    monkeypatch.setattr(catalog, "plugins_root", lambda: base)
    monkeypatch.setattr(lifecycle, "install", fake_install)
    result = repoops_api.plugin_lifecycle_action(action="install", plugin_id=pid)
    assert result["ok"] is False  # the stub raised after the id passed confinement
    assert len(calls) == 1
    assert calls[0][0] == pid
    assert calls[0][1] == str(base / "plugins" / pid)


def test_plugin_lifecycle_unknown_action_answered_before_confinement():
    result = repoops_api.plugin_lifecycle_action(action="explode", plugin_id="../x")
    assert result["ok"] is False
    assert "unknown action" in result["error"]


def test_plugin_lifecycle_refusals_write_nothing_outside(tmp_path, monkeypatch):
    """A refused id must not leave artifacts: the refusal happens before any directory is made."""
    import core.plugin_catalog as catalog

    monkeypatch.setattr(catalog, "plugins_root", lambda: tmp_path)
    for pid in ["../escape", "a/b", "/abs", "C:pack"]:
        repoops_api.plugin_lifecycle_action(action="install", plugin_id=pid)
        repoops_api._reregister_pack(pid, tmp_path)
    assert list(tmp_path.rglob("*")) == []
