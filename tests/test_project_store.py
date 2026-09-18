"""Server-side project registry: folder validation, idempotent-by-path ids, list/get/delete, unbind."""

from __future__ import annotations

import os

import pytest

from core import project_store as ps
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def test_create_validates_and_persists(tmp_path) -> None:
    folder = tmp_path / "my-repo"
    folder.mkdir()
    ok, proj = ps.create_project("My Repo", str(folder))
    assert ok is True
    assert proj["name"] == "My Repo"
    assert proj["root"] == os.path.realpath(str(folder))
    assert proj["id"].startswith("proj_")
    assert proj["exists"] is True
    assert ps.get_project(proj["id"])["root"] == proj["root"]
    assert ps.project_root(proj["id"]) == os.path.realpath(str(folder))
    assert [p["id"] for p in ps.list_projects()] == [proj["id"]]


def test_create_is_idempotent_by_real_path(tmp_path) -> None:
    folder = tmp_path / "repo"
    folder.mkdir()
    ok1, p1 = ps.create_project("First", str(folder))
    ok2, p2 = ps.create_project("Renamed", str(folder) + "/")  # trailing slash = same folder
    assert ok1 and ok2
    assert p1["id"] == p2["id"]  # same folder -> one project, not two
    assert p2["name"] == "Renamed"  # name refreshed
    assert len(ps.list_projects()) == 1


def test_create_rejects_missing_and_non_dir(tmp_path) -> None:
    ok, err = ps.create_project("x", str(tmp_path / "nope"))
    assert ok is False and "does not exist" in err
    afile = tmp_path / "a.txt"
    afile.write_text("hi")
    ok2, err2 = ps.create_project("x", str(afile))
    assert ok2 is False and "must be a folder" in err2
    ok3, err3 = ps.create_project("x", "   ")
    assert ok3 is False and "required" in err3
    ok4, err4 = ps.create_project("x", "/")
    assert ok4 is False and "whole filesystem" in err4


def test_name_defaults_to_folder_basename(tmp_path) -> None:
    folder = tmp_path / "cool-project"
    folder.mkdir()
    ok, proj = ps.create_project("   ", str(folder))
    assert ok and proj["name"] == "cool-project"


def test_delete_forgets_without_touching_the_folder(tmp_path) -> None:
    folder = tmp_path / "repo"
    folder.mkdir()
    _, proj = ps.create_project("R", str(folder))
    assert ps.delete_project(proj["id"]) is True
    assert ps.get_project(proj["id"]) is None
    assert folder.is_dir()  # the real folder is untouched
    assert ps.delete_project(proj["id"]) is False  # already gone


def test_project_root_none_when_folder_removed(tmp_path) -> None:
    folder = tmp_path / "temp-repo"
    folder.mkdir()
    _, proj = ps.create_project("R", str(folder))
    folder.rmdir()
    assert ps.project_root(proj["id"]) is None  # gone -> no root
    assert ps.list_projects()[0]["exists"] is False  # surfaced as missing, not crashing


def test_set_and_clear_project_emoji(tmp_path) -> None:
    folder = tmp_path / "repo"
    folder.mkdir()
    _, proj = ps.create_project("R", str(folder))
    assert proj["emoji"] == ""                                       # new project starts with no emoji
    assert ps.set_project_emoji(proj["id"], "🚀") is True
    assert next(p for p in ps.list_projects() if p["id"] == proj["id"])["emoji"] == "🚀"
    assert ps.create_project("R", str(folder))[1]["emoji"] == "🚀"    # re-adding the folder keeps it
    assert ps.set_project_emoji(proj["id"], "") is True              # clearable
    assert ps.get_project(proj["id"])["emoji"] == ""
    assert ps.set_project_emoji("proj_nope", "🔥") is False           # unknown id -> no-op


def test_set_and_clear_project_color(tmp_path) -> None:
    folder = tmp_path / "repo"
    folder.mkdir()
    _, proj = ps.create_project("R", str(folder))
    assert proj["color"] == ""                                        # new project starts with no colour
    assert ps.set_project_color(proj["id"], "#E5484D") is True
    assert ps.get_project(proj["id"])["color"] == "#e5484d"           # normalized to lowercase #rrggbb
    assert ps.create_project("R", str(folder))[1]["color"] == "#e5484d"  # re-adding keeps it
    assert ps.set_project_color(proj["id"], "red") is True            # invalid -> clears (no arbitrary CSS)
    assert ps.get_project(proj["id"])["color"] == ""
    assert ps.set_project_color("proj_nope", "#123456") is False      # unknown id -> no-op
    assert ps.normalize_color("#ABC") == "" and ps.normalize_color(" #Aa11Bb ") == "#aa11bb"
