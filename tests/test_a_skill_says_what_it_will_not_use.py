"""A skill that loads but will not do part of what its author wrote says so, in the app and not only in the
command-line tool: settings VOOL does not read, a timer, a tool list that adds nothing. And a package
whose details are refused says which field and what to write instead."""
from __future__ import annotations

import json

import pytest

from core import skill_tools
from core.registry.artifact import metadata
from core.registry.codec import RegistryError

BODY = ("Read the meeting notes the user gives you. List each decision, who owns it and when it is due. "
        "Ask before sending anything anywhere.\n")


def _skill(tmp_path, extra: str = "") -> str:
    folder = tmp_path / "meeting-notes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\nname: meeting-notes\ndescription: Turn meeting notes into decisions\n" + extra + "---\n\n" + BODY,
        encoding="utf-8")
    return str(folder / "SKILL.md")


def test_a_skill_that_uses_everything_has_nothing_to_say(tmp_path):
    verdict = skill_tools.validate_skill(_skill(tmp_path, "triggers: [meeting notes, decisions list]\n"))
    assert verdict["status"] == "ok" and verdict["not_used"] == []


def test_settings_vool_does_not_read_and_a_timer_are_named_while_the_skill_still_loads(tmp_path):
    verdict = skill_tools.validate_skill(_skill(tmp_path, "model: big-one\ncolor: blue\nschedule: every morning\n"))
    assert verdict["status"] == "ok", verdict["problems"]
    said = " ".join(verdict["not_used"])
    assert '"schedule" does nothing: a skill never runs by itself.' in said
    assert '"color", "model" are settings VOOL does not read, so they change nothing.' in said
    assert len(verdict["not_used"]) == 2


def test_one_unread_setting_is_said_in_the_singular(tmp_path):
    verdict = skill_tools.validate_skill(_skill(tmp_path, "temperature: 0.2\n"))
    assert verdict["not_used"] == ['"temperature" is a setting VOOL does not read, so it changes nothing.']


def test_an_imported_skill_is_told_before_turn_on_that_its_tool_list_adds_nothing(tmp_path):
    from core.agent_runtime.fast_paths_skill import _import_preview

    markdown = ("---\nname: meeting-notes\ndescription: Turn meeting notes into decisions\n"
                "allowed-tools: [workspace.read_file]\ncron: 0 9 * * *\n---\n\n" + BODY)
    shown = _import_preview(markdown, "a" * 64)
    line = next(part for part in shown.splitlines() if part.startswith("**What I will not use:**"))
    assert '"cron" does nothing: a skill never runs by itself.' in line
    assert "The tools it lists are not added: it was not written here in VOOL" in line
    plain = _import_preview("---\nname: meeting-notes\ndescription: Turn meeting notes into decisions\n---\n\n" + BODY, "a" * 64)
    assert "What I will not use" not in plain, "nothing to say when everything in the file is used"


def test_a_setting_name_the_author_wrote_cannot_carry_markup_into_the_sentence():
    said = skill_tools.not_used({"x](https://e.example) **VOOL says**": 1})
    assert said and "](" not in said[0] and "*" not in said[0] and "https://" not in said[0]


def test_the_import_result_carries_the_same_sentences(tmp_path, monkeypatch):
    """Staging a zip, the door every import goes through, hands the sentences to whoever shows the result."""
    from core import skill_package
    from core.plugin_catalog import invalidate_storage_listing

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins"))
    invalidate_storage_listing()
    text = ("---\nname: meeting-notes\ndescription: Turn meeting notes into decisions\nrepeat: daily\n---\n\n" + BODY)
    raw = skill_package._zip_bytes([("meeting-notes/SKILL.md", text.encode("utf-8"))])
    result = skill_package.import_package(raw, scope="global", source_name="meeting-notes.zip")
    assert result["status"] == "ok" and result["staged"] is True, result
    assert result["not_used"] == ['"repeat" does nothing: a skill never runs by itself. It is used only when a message asks for it.']
    invalidate_storage_listing()


GOOD = {"id": "alice/notes", "kind": "skill", "version": "1.0.0", "summary": "Turn meeting notes into decisions",
        "category": "productivity", "subcategory": "meetings", "license": "MIT",
        "source": {"upload_id": "1" * 32, "namespace": "alice"}}


@pytest.mark.parametrize("change,message,words", [
    (lambda v: v.pop("summary"), "invalid package metadata fields", "Missing: summary."),
    (lambda v: v.update(tags=["x"], descripton="y"), "invalid package metadata fields", "Not known, so take out: descripton, tags."),
    (lambda v: v.update(title="x" * 61), "invalid title", "1 to 60 characters"),
    (lambda v: v.update(id="Alice/Notes"), "invalid stable package id", "alice/meeting-notes"),
    (lambda v: v.update(id="official/notes"), "reserved publisher namespace", "your own public name"),
    (lambda v: v.update(version="v1"), "invalid package kind or version", "1.0.0"),
    (lambda v: v.update(summary=""), "invalid summary", "1 to 500 characters"),
    (lambda v: v.update(release_notes="a\nb"), "invalid release notes", "no line breaks"),
])
def test_refused_package_details_keep_their_short_reason_and_add_what_to_write(change, message, words):
    value = json.loads(json.dumps(GOOD))
    change(value)
    with pytest.raises(RegistryError) as refused:
        metadata(value)
    assert str(refused.value) == message, "pages and tools match on the short reason; it must not change"
    assert words in refused.value.fix


def test_the_author_tool_prints_the_fix_with_the_reason(tmp_path, capsys):
    from tools.community_skills import main

    bad = dict(GOOD, title="x" * 61)
    (tmp_path / "metadata.json").write_text(json.dumps(bad), encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    assert main(["init", str(tmp_path / "package"), "--metadata", str(tmp_path / "metadata.json"),
                 "--license-file", str(tmp_path / "LICENSE")]) == 1
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert printed["status"] == "refused" and printed["reason"] == "invalid title"
    assert "1 to 60 characters" in printed["fix"]
