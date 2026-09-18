"""Project API: create/list/delete a folder-project, bind a chat to it, and the owner-local guards."""

from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _post(path, body, *, host="127.0.0.1", headers=None):
    return dispatch_post(
        path=path, body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=host,
    )


def _get(path):
    return dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool")


def _j(response):
    return json.loads(response.body.decode("utf-8"))


def test_create_list_bind_delete_roundtrip(tmp_path) -> None:
    folder = tmp_path / "acme-app"
    folder.mkdir()

    # Empty to start.
    assert _j(_get("/api/projects"))["projects"] == []

    # Create.
    res = _post("/api/projects", {"name": "Acme", "root": str(folder)})
    assert res.status == 200
    project = _j(res)["project"]
    assert project["name"] == "Acme" and project["id"].startswith("proj_")

    # Listed.
    listed = _j(_get("/api/projects"))["projects"]
    assert [p["id"] for p in listed] == [project["id"]]

    # Bind a fresh chat (no transcript yet) to it.
    bind = _post("/api/chat/session", {"session_id": "openclaw:deadbeef01", "project_id": project["id"]})
    assert bind.status == 200
    assert _j(bind)["meta"]["project_id"] == project["id"]

    # Unbind.
    unbind = _post("/api/chat/session", {"session_id": "openclaw:deadbeef01", "project_id": ""})
    assert unbind.status == 200
    assert _j(unbind)["meta"].get("project_id", "") == ""

    # Delete the project.
    dele = _post("/api/projects/delete", {"id": project["id"]})
    assert dele.status == 200 and _j(dele)["ok"] is True
    assert _j(_get("/api/projects"))["projects"] == []


def test_create_rejects_bad_root_and_unknown_fields(tmp_path) -> None:
    assert _post("/api/projects", {"root": str(tmp_path / "nope")}).status == 400  # missing folder
    assert _post("/api/projects", {"name": "x"}).status == 400  # no root
    assert _post("/api/projects", {"root": str(tmp_path), "junk": 1}).status == 400  # unknown field


def test_bind_rejects_unknown_project(tmp_path) -> None:
    res = _post("/api/chat/session", {"session_id": "openclaw:abc", "project_id": "proj_doesnotexist"})
    assert res.status == 404


def test_owner_local_and_origin_guards(tmp_path) -> None:
    folder = tmp_path / "repo"
    folder.mkdir()
    # Non-loopback peer cannot create a project.
    assert _post("/api/projects", {"root": str(folder)}, host="10.0.0.5").status == 403
    # Cross-origin is refused.
    assert _post(
        "/api/projects", {"root": str(folder)},
        headers={"content-type": "application/json", "origin": "http://evil.example"},
    ).status == 403
    # Non-JSON content-type is refused.
    assert _post("/api/projects", {"root": str(folder)}, headers={"content-type": "text/plain"}).status == 415
