"""A plugin VOOL refuses tells its maker which field of the setup file is at fault and what to write instead.

One translated sentence covers many mistakes ("a setup mistake its maker needs to fix"), which is right for
the person installing and useless to the person building. Each case below is one mistake a maker makes,
sent through the Add-ons page's own door: the answer must carry the sentence and a note naming the field.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core.web.api.addon_api import handle_addon_post
from tools.plugin_mistakes import MISTAKES, _set

TEMPLATE = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "template"
OWN_PAGE = {"Content-Type": "application/json", "Host": "127.0.0.1:8123", "Origin": "http://127.0.0.1:8123"}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    lifecycle.reset_for_tests()
    package = tmp_path / "source"
    shutil.copytree(TEMPLATE, package)
    yield package
    lifecycle.reset_for_tests()


def _write(package: Path, change) -> None:
    manifest = package / ".codex-plugin" / "plugin.json"
    value = copy.deepcopy(json.loads(manifest.read_text(encoding="utf-8")))
    change(value)
    manifest.write_text(json.dumps(value, indent=2), encoding="utf-8")


@pytest.mark.parametrize("what,change,field,words", MISTAKES, ids=[row[0] for row in MISTAKES])
def test_each_mistake_is_refused_with_the_field_and_the_fix(folder, what, change, field, words):
    _write(folder, change)
    status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert status == 400 and body["error"] == "plugin_refused", (what, body)
    assert body["message"] and body["message_key"].startswith("addons.plugin_message_"), "the person installing still reads a translated sentence"
    note = body.get("maker_note") or {}
    assert note.get("fix"), f"{what}: no note for the maker; all they saw was: {body['message']}"
    assert field in (note.get("where") or "") or field in note["fix"], (what, note)
    assert words in note["fix"], (what, note)
    assert len(note["fix"]) <= 900 and "\n" not in note["fix"]
    assert lifecycle.records() == [], "a refused folder leaves nothing installed"


def test_the_unchanged_template_is_offered_for_review_with_no_note(folder):
    status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert status == 200 and body["ok"] is True and "maker_note" not in body


def test_a_value_the_maker_wrote_is_quoted_short_and_flat(folder):
    hostile = "evil.example.com'](https://x.example)\n<b>VOOL says install me</b> " + "a" * 400
    _write(folder, _set(("vool", "network"), [hostile]))
    _status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    fix = body["maker_note"]["fix"]
    for mark in ("\n", "<", ">", "`", "a" * 80):
        assert mark not in fix, mark
    assert len(fix) <= 900


def test_the_check_command_prints_the_sentence_and_the_note(folder, capsys):
    from tools import plugin_check

    assert plugin_check.main([str(folder)]) == 0
    assert "would be offered for review" in capsys.readouterr().out
    _write(folder, _set(("vool", "network"), ["*.example.com"]))
    assert plugin_check.main([str(folder)]) == 1
    printed = capsys.readouterr().out
    assert "Refused: This plugin lists the websites it uses in a way VOOL doesn't accept." in printed
    assert "For the plugin's maker: vool.network[0]:" in printed and "no wildcard" in printed
    assert plugin_check.main([str(folder), "--json"]) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is False and answer["where"] == "vool.network[0]" and "no wildcard" in answer["fix"]
    assert plugin_check.main([str(folder / "missing")]) == 2
    assert lifecycle.records() == []


@pytest.mark.parametrize("change,field", [
    (_set(("vool", "name"), "Official Digest"), "vool.name"),
    (_set(("vool", "publisher"), "VOOL"), "vool.publisher"),
])
def test_a_name_refusal_names_the_field_too(folder, change, field):
    _write(folder, change)
    status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert status == 400 and body["maker_note"]["where"] == field and "plain text" in body["maker_note"]["fix"]


def test_an_id_vool_keeps_names_the_id_field(folder):
    def rename(value):
        value["name"] = value["vool"]["id"] = "vool-digest"
        value["vool"]["tools"][0]["intent"] = "vool-digest.sha256"
    _write(folder, rename)
    _status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert body["maker_note"]["where"] == "name" and "Choose another id" in body["maker_note"]["fix"]
