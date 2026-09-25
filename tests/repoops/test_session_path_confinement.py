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
