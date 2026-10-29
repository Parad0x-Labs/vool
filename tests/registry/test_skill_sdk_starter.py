"""The two-command start for skill authors: `new` scaffolds, `try` proves the picking."""
import json
import re
import shutil
from pathlib import Path

import pytest

from core.plugin_skills import parse_skill
from core.registry.categories import is_listed
from tools.community_skills import STARTER_MARKER, main, metadata_path

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
    info = json.loads(metadata_path(folder).read_text(encoding="utf-8"))
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
    info = json.loads(metadata_path(folder).read_text(encoding="utf-8"))
    info["summary"] = ("Turn the ingredients the user already has into meals for the week "
                       "and a shopping list of the missing items.")
    metadata_path(folder).write_text(json.dumps(info), encoding="utf-8")
    package = tmp_path / "package-draft"
    assert main(["init", str(package), "--metadata", str(metadata_path(folder)),
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
    asks = [line for line in out[:-1] if not line.startswith("warn ")]
    assert len(asks) == 5  # one line per ask, then the summary
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
    assert "names the skill" in failing[0]


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


# --- outsider fixes 2026-10-29: importable layout, zip, starter text, reasons, warnings ---

def _filled(tmp_path, capsys, body=PANTRY_SKILL):
    folder = _scaffold(tmp_path, capsys)
    (folder / "SKILL.md").write_text(body, encoding="utf-8")
    return folder


def test_new_keeps_metadata_beside_the_folder_so_the_folder_imports_as_made(tmp_path, capsys):
    folder = _scaffold(tmp_path, capsys)
    assert sorted(p.name for p in folder.iterdir()) == ["LICENSE", "SKILL.md"]
    assert metadata_path(folder) == tmp_path / "draft.metadata.json" and metadata_path(folder).is_file()
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    import zipfile

    from core.skill_package import read_package

    archive = tmp_path / "by-hand.zip"
    with zipfile.ZipFile(archive, "w") as handle:  # the guide's `zip -r` of the folder, as made
        for path in folder.iterdir():
            handle.write(path, "draft/" + path.name)
    assert read_package(archive.read_bytes())["kind"] == "skill"


def test_new_refuses_when_the_metadata_file_beside_it_exists(tmp_path, capsys):
    (tmp_path / "draft.metadata.json").write_text("{}", encoding="utf-8")
    assert main(["new", str(tmp_path / "draft"), "--name", "pantry-meals"]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "refused"
    assert not (tmp_path / "draft").exists()


def test_zip_writes_an_importable_zip_and_names_what_it_left_out(tmp_path, capsys):
    folder = _filled(tmp_path, capsys)
    (folder / "README.md").write_text("Pantry meals.\n", encoding="utf-8")
    (folder / "notes.txt").write_text("scratch\n", encoding="utf-8")
    assert main(["zip", str(folder)]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["status"] == "created" and report["files"] == ["SKILL.md", "LICENSE", "README.md"]
    assert report["left_out"] == ["notes.txt"] and "import this skill" in report["next"]
    from core.skill_package import read_package

    package = read_package(Path(report["zip"]).read_bytes())
    assert package["kind"] == "skill" and package["skills"][0]["slug"] == "pantry-meals"
    assert main(["zip", str(folder)]) == 1  # never replaces a zip already there
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "refused"


def test_untouched_starter_text_is_refused_by_try_zip_and_lint(tmp_path, capsys, person_home):
    folder = _scaffold(tmp_path, capsys)
    assert STARTER_MARKER in (folder / "SKILL.md").read_text(encoding="utf-8")
    before = sorted(p.name for p in person_home.iterdir())
    assert main(["try", str(folder), "--pick", "plan meals from my pantry"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "not installed" in out[0] and "starter text" in out[0]
    assert sorted(p.name for p in person_home.iterdir()) == before
    assert main(["zip", str(folder)]) == 1
    assert "starter text" in json.loads(capsys.readouterr().out.splitlines()[-1])["reason"]
    # lint: a filled summary but the starter SKILL.md -> a plain blocking finding
    info = json.loads(metadata_path(folder).read_text(encoding="utf-8"))
    info["summary"] = "Turn the ingredients the user has into meals and a shopping list."
    metadata_path(folder).write_text(json.dumps(info), encoding="utf-8")
    package = tmp_path / "package-draft"
    assert main(["init", str(package), "--metadata", str(metadata_path(folder)),
                 "--license-file", str(folder / "LICENSE")]) == 0
    shutil.copyfile(folder / "SKILL.md", package / "skills" / "pantry-meals" / "SKILL.md")
    capsys.readouterr()
    assert main(["lint", str(package), "--scratch", str(tmp_path / "scratch")]) == 1
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["status"] == "blocked"
    assert any(f["check"] == "starter-text" and "starter text" in f["reason"] for f in report["findings"])


def test_lint_blocks_a_starter_summary_and_init_refuses_it(tmp_path, capsys):
    folder = _filled(tmp_path, capsys)
    assert main(["init", str(tmp_path / "pkg"), "--metadata", str(metadata_path(folder)),
                 "--license-file", str(folder / "LICENSE")]) == 1
    assert "starter text" in json.loads(capsys.readouterr().out.splitlines()[-1])["reason"]
    info = json.loads(metadata_path(folder).read_text(encoding="utf-8"))
    starter_summary = info["summary"]
    info["summary"] = "Meals from the pantry."
    metadata_path(folder).write_text(json.dumps(info), encoding="utf-8")
    package = tmp_path / "pkg"
    assert main(["init", str(package), "--metadata", str(metadata_path(folder)),
                 "--license-file", str(folder / "LICENSE")]) == 0
    shutil.copyfile(folder / "SKILL.md", package / "skills" / "pantry-meals" / "SKILL.md")
    registry = json.loads((package / "registry.json").read_text(encoding="utf-8"))
    registry["summary"] = starter_summary  # edited back by hand after init
    (package / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    capsys.readouterr()
    assert main(["lint", str(package), "--scratch", str(tmp_path / "scratch")]) == 1
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert any(f["path"] == "registry.json summary" for f in report["findings"])


def test_lint_needs_notice_says_where_to_add_the_need(tmp_path, capsys):
    folder = _filled(tmp_path, capsys, PANTRY_SKILL + "4. Check the user's wallet before buying.\n")
    info = json.loads(metadata_path(folder).read_text(encoding="utf-8"))
    info["summary"] = "Meals from the pantry."
    metadata_path(folder).write_text(json.dumps(info), encoding="utf-8")
    package = tmp_path / "pkg"
    assert main(["init", str(package), "--metadata", str(metadata_path(folder)),
                 "--license-file", str(folder / "LICENSE")]) == 0
    shutil.copyfile(folder / "SKILL.md", package / "skills" / "pantry-meals" / "SKILL.md")
    capsys.readouterr()
    assert main(["lint", str(package), "--scratch", str(tmp_path / "scratch")]) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    reason = report["needs_notices"][0]["reason"]
    assert '"wallet"' in reason and "metadata.json" in reason and '"needs"' in reason


PHOTO_SKILL = """---
name: photo-tidy
description: Sort the file names of exported files and folders into dated albums.
triggers:
  - rename my exported files
  - what do they want from me
  - what are they asking
---

1. Sort the names the user lists into albums by date.
"""


def test_try_reasons_name_folded_words_name_words_and_misses(tmp_path, capsys, person_home, monkeypatch):
    from core import plugin_skills

    monkeypatch.delattr(plugin_skills, "match_reasons", raising=False)
    folder = tmp_path / "photo-tidy"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PHOTO_SKILL, encoding="utf-8")
    argv = ["try", str(folder), "--pick", "rename my exported files", "--pick", "plan a birthday party",
            "--not-pick", "the photo is blurry", "--not-pick", "tidy files and folders"]
    assert main(argv) == 1
    out = capsys.readouterr().out.splitlines()
    warns = [line for line in out if line.startswith("warn ")]
    assert any('"what do they want from me" has no words left' in line for line in warns)
    assert any('"what are they asking" comes down to the one word "ask"' in line for line in warns)
    assert any('name words "photo", "tidy"' in line for line in warns)
    miss = next(line for line in out if line.startswith("FAIL pick"))
    assert "not picked: no trigger matched; shares no words with the skill" in miss
    photo = next(line for line in out if "photo is blurry" in line)
    assert "word from the skill's name \"photo\"" in photo and "shared words" not in photo
    files = next(line for line in out if "tidy files and folders" in line)
    assert 'shared words "files", "folders"' in files


def test_try_uses_the_pickers_own_reasons_when_it_has_them(tmp_path, capsys, person_home, monkeypatch):
    from core import plugin_skills

    calls = []

    def match_reasons(skills, user_text):
        calls.append(user_text)
        return {skill.name: ["picker said so"] for skill in skills}

    monkeypatch.setattr(plugin_skills, "match_reasons", match_reasons, raising=False)
    folder = tmp_path / "pantry"
    folder.mkdir()
    (folder / "SKILL.md").write_text(PANTRY_SKILL, encoding="utf-8")
    assert main(["try", str(folder), "--pick", "plan meals from my pantry",
                 "--not-pick", "use the pantry meals skill please"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert any(line.startswith("FAIL not-pick") and "picked by picker said so" in line for line in out)
    assert calls == ["use the pantry meals skill please"]
