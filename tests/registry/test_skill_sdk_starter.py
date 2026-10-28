"""The two-command start for skill authors: `new` scaffolds, `try` proves the picking."""
import json
import re
import shutil
from pathlib import Path

import pytest

from core.plugin_skills import parse_skill
from core.registry.categories import is_listed
from tools.community_skills import main

PANTRY_SKILL = """---
name: pantry-meals
description: Turn the ingredients the user already has, like rice, beans or pasta, into meals
  they can cook this week, plus a shopping list of only the missing items.
triggers:
  - plan meals from my pantry
  - shopping list from what I have
license: MIT
---

# Pantry meals

1. Use only the ingredients the user lists in the current request.
2. Suggest meals that use those ingredients and state unknowns plainly.
3. Add a shopping list of only the items the meals still need.
"""


@pytest.fixture
def person_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _last_json(capsys):
    out = capsys.readouterr().out.splitlines()
    assert out, "no output printed"
    return out, json.loads(out[-1])


def _scaffold(tmp_path, capsys, *extra):
    folder = tmp_path / "draft"
    argv = ["new", str(folder), "--name", "pantry-meals", *extra]
    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["status"] == "created"
    assert "vool-community try" in payload["next"]
    return folder


def test_new_writes_the_three_files_and_a_complete_scaffold(tmp_path, capsys):
    folder = _scaffold(tmp_path, capsys, "--author", "Ada Lovelace")
    skill = parse_skill(folder / "SKILL.md")
    assert skill is not None and skill.name == "pantry-meals"
    assert skill.description.strip()
    assert len(skill.triggers) == 2
    assert all(str(t).strip() for t in skill.triggers)
    licence = (folder / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence and "Copyright (c)" in licence and "Ada Lovelace" in licence
    info = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert info["id"] == "your-namespace/pantry-meals"
    assert info["kind"] == "skill" and info["version"] == "1.0.0"
    assert is_listed(info["category"], info["subcategory"])
    assert info["languages"] == ["en"]
    assert re.fullmatch(r"[0-9a-f]{32}", info["source"]["upload_id"])
    assert info["source"]["namespace"] == "your-namespace"
    assert "license" in info and "summary" in info


def test_new_filled_then_init_and_lint_passes(tmp_path, capsys):
    folder = _scaffold(tmp_path, capsys)
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    info = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    info["summary"] = ("Turn the ingredients the user already has into meals for the week "
                       "and a shopping list of the missing items.")
    (folder / "metadata.json").write_text(json.dumps(info), encoding="utf-8")
    package = tmp_path / "package-draft"
    assert main(["init", str(package), "--metadata", str(folder / "metadata.json"),
                 "--license-file", str(folder / "LICENSE")]) == 0
    shutil.copyfile(folder / "SKILL.md", package / "skills" / "pantry-meals" / "SKILL.md")
    assert main(["lint", str(package), "--scratch", str(tmp_path / "scratch")]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["status"] == "preflight_passed" and not report["findings"]


def test_new_refuses_existing_non_empty_folder(tmp_path, capsys):
    folder = tmp_path / "draft"
    folder.mkdir()
    (folder / "keep.txt").write_text("mine", encoding="utf-8")
    assert main(["new", str(folder), "--name", "pantry-meals"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["status"] == "refused"
    assert sorted(p.name for p in folder.iterdir()) == ["keep.txt"]


def test_new_refuses_bad_name_and_reserved_namespace(tmp_path, capsys):
    for argv in (["new", str(tmp_path / "a"), "--name", "Bad_Name"],
                 ["new", str(tmp_path / "b"), "--name", "pantry-meals", "--namespace", "vool"],
                 ["new", str(tmp_path / "c"), "--name", "pantry-meals", "--namespace", "official"]):
        assert main(argv) == 1
        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == 1 and json.loads(lines[0])["status"] == "refused"
        assert not Path(argv[2]).exists()


def test_new_into_existing_empty_folder_and_default_author(tmp_path, capsys):
    folder = tmp_path / "draft"
    folder.mkdir()
    assert main(["new", str(folder), "--name", "quiet-recipe-box"]) == 0
    capsys.readouterr()
    licence = (folder / "LICENSE").read_text(encoding="utf-8")
    assert "the author" in licence and "Copyright (c)" in licence


def test_try_passes_three_picks_and_two_not_picks(tmp_path, capsys, person_home):
    folder = tmp_path / "pantry"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    argv = ["try", str(folder)]
    for ask in ("plan meals from my pantry",
                "make a shopping list from what I have",
                "what can I cook with rice and beans"):
        argv += ["--pick", ask]
    for ask in ("write a haiku about saturn", "balance my budget spreadsheet"):
        argv += ["--not-pick", ask]
    before = sorted(p.name for p in person_home.iterdir())
    assert main(argv) == 0
    out, summary = _last_json(capsys)
    assert summary["status"] == "passed"
    assert summary["picks"] == 3 and summary["pick_failures"] == 0
    assert summary["not_picks"] == 2 and summary["not_pick_failures"] == 0
    assert len(out) == 6  # one line per ask plus the summary
    assert any(line.startswith("ok   pick") and "plan meals from my pantry" in line for line in out)
    assert any(line.startswith("ok   not-pick") and "haiku" in line for line in out)
    assert sorted(p.name for p in person_home.iterdir()) == before


def test_try_fails_when_a_not_pick_names_the_skill(tmp_path, capsys, person_home):
    folder = tmp_path / "pantry"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    argv = ["try", str(folder), "--pick", "plan meals from my pantry",
            "--not-pick", "use the pantry meals skill please"]
    assert main(argv) == 1
    out, summary = _last_json(capsys)
    assert summary["status"] == "failed"
    failing = [line for line in out if line.startswith("FAIL not-pick")]
    assert len(failing) == 1 and "pantry meals" in failing[0]
    assert "name word" in failing[0]


def test_try_refuses_an_empty_description_without_installing(tmp_path, capsys, person_home):
    folder = tmp_path / "pantry"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PANTRY_SKILL.replace(
        "description: Turn the ingredients the user already has, like rice, beans or pasta, into meals\n"
        "  they can cook this week, plus a shopping list of only the missing items.",
        'description: ""'), encoding="utf-8")
    before = sorted(p.name for p in person_home.iterdir())
    assert main(["try", str(folder), "--pick", "plan meals from my pantry"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out and "description is empty" in out[-1] and "not installed" in out[-1]
    assert sorted(p.name for p in person_home.iterdir()) == before


def test_try_restores_the_callers_environment(tmp_path, capsys, person_home, monkeypatch):
    sentinel = tmp_path / "sentinel-plugins"
    sentinel.mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(sentinel))
    folder = tmp_path / "pantry"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    assert main(["try", str(folder), "--pick", "plan meals from my pantry"]) == 0
    capsys.readouterr()
    import os

    assert os.environ["HOME"] == str(person_home)
    assert os.environ["VOOL_PLUGINS_DIR"] == str(sentinel)
    assert list(sentinel.iterdir()) == []
    assert not list((tmp_path).glob("pantry-*/**"))


def test_try_fails_when_a_pick_never_matches(tmp_path, capsys, person_home):
    folder = tmp_path / "pantry"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    argv = ["try", str(folder), "--pick", "plan meals from my pantry",
            "--pick", "file my taxes with the accountant"]
    assert main(argv) == 1
    out, summary = _last_json(capsys)
    assert summary["status"] == "failed" and summary["pick_failures"] == 1
    assert any(line.startswith("FAIL pick") and "file my taxes" in line for line in out)


def test_try_refuses_a_folder_without_a_skill(tmp_path, capsys, person_home):
    folder = tmp_path / "empty"
    folder.mkdir()
    assert main(["try", str(folder), "--pick", "anything at all"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out and "SKILL.md" in out[-1] and "not installed" in out[-1]
