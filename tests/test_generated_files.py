"""Files panel: list generated files, the path-allow guard, and the open endpoint's path confinement."""

from __future__ import annotations

import json

import pytest

from core import generated_files as gf
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post


@pytest.fixture
def _root(tmp_path, monkeypatch):
    root = tmp_path / "gen"
    root.mkdir()
    (root / "render.png").write_bytes(b"x" * 100)
    (root / "notes.md").write_text("hi")
    (root / ".hidden.png").write_bytes(b"y")   # dotfile -> skipped
    monkeypatch.setattr(gf, "_default_roots", lambda: [root])   # hermetic: only this root
    return root


def test_list_generated_files_types_and_hidden(_root) -> None:
    listing = gf.list_generated_files()
    by_name = {f["name"]: f for f in listing["files"]}
    assert "render.png" in by_name and "notes.md" in by_name
    assert ".hidden.png" not in by_name
    assert by_name["render.png"]["type"] == "image"
    assert by_name["notes.md"]["type"] == "doc"
    assert by_name["render.png"]["size"] == 100
    assert listing["count"] == 2


def test_path_is_allowed_confines_to_roots(_root, tmp_path) -> None:
    assert gf.path_is_allowed(str(_root / "render.png")) is True
    assert gf.path_is_allowed("/etc/passwd") is False
    assert gf.path_is_allowed(str(tmp_path / "outside.txt")) is False
    assert gf.path_is_allowed(str(_root / ".." / "escape")) is False  # traversal out of the root


def test_open_file_refuses_outside_and_launches_inside(_root, monkeypatch) -> None:
    import subprocess

    calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: calls.append(list(args)))
    assert gf.open_file("/etc/passwd") is False
    assert calls == []                                   # never launched for a disallowed path
    assert gf.open_file(str(_root / "render.png")) is True
    assert calls and calls[-1][0] == "open"


def _rt():
    return RuntimeServices(display_name="VOOL")


def _post(path, body, host="127.0.0.1"):
    return dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                         runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=host)


def _j(response):
    return json.loads(response.body.decode("utf-8"))


def test_files_endpoints(_root, monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: None)
    listing = _j(dispatch_get(path="/api/files", query={}, runtime=_rt(), model_name="vool"))
    assert any(f["name"] == "render.png" for f in listing["files"])
    ok = _post("/api/files/open", {"path": str(_root / "render.png")})
    assert ok.status == 200 and _j(ok)["ok"] is True
    assert _post("/api/files/open", {"path": "/etc/passwd"}).status == 403       # arbitrary path refused
    assert _post("/api/files/open", {"path": str(_root / "x")}, host="10.0.0.9").status == 403  # not owner-local


def test_files_raw_serves_bytes_and_confines(_root) -> None:
    def _raw(p):
        return dispatch_get(path="/api/files/raw", query={"path": [p]}, runtime=_rt(), model_name="vool")

    ok = _raw(str(_root / "render.png"))
    assert ok.status == 200 and ok.content_type == "image/png" and ok.body == b"x" * 100
    assert _raw("/etc/passwd").status == 403   # outside the roots
    assert _raw("").status == 403


def test_record_tags_a_file_with_its_chat_and_prompt(_root, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))   # the index lives under VOOL_HOME/data
    gf.record_generated_file(str(_root / "render.png"), session_id="openclaw:abc", prompt="a corgi astronaut")
    row = next(x for x in gf.list_generated_files()["files"] if x["name"] == "render.png")
    assert row["session_id"] == "openclaw:abc"
    assert row["prompt"] == "a corgi astronaut"
    # An untagged file just has empty chat fields (never crashes).
    other = next(x for x in gf.list_generated_files()["files"] if x["name"] == "notes.md")
    assert other["session_id"] == "" and other["prompt"] == ""


def test_open_reveal_flag_selects_finder_vs_native(_root, monkeypatch) -> None:
    import subprocess

    calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: calls.append(list(args)))
    assert gf.open_file(str(_root / "render.png"), reveal=True) is True
    assert calls[-1][:2] == ["open", "-R"]          # Finder reveal
    assert gf.open_file(str(_root / "render.png"), reveal=False) is True
    assert calls[-1][0] == "open" and "-R" not in calls[-1]   # native app (Preview)
