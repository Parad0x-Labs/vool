"""`python -m tools.plugin_check` names every problem it can find in one run, not only the first.

An install stops at its first refusal, and so did the check: a maker with a kept-back id, a name
that claims a badge and two bad website entries ran it four times. The first problem stays where
tools already read it ("message", "where", "fix"); "problems" holds all of them in order.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core.web.api.addon_api import handle_addon_post
from tools import plugin_check

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


def _change(package: Path, change) -> None:
    manifest = package / ".codex-plugin" / "plugin.json"
    value = copy.deepcopy(json.loads(manifest.read_text(encoding="utf-8")))
    change(value)
    manifest.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _several_mistakes(value: dict) -> None:
    value["vool"]["name"] = "Editor's Choice digest"            # claims a badge
    value["vool"]["publisher"] = "VOOL"                         # VOOL's own name
    value["vool"]["network"] = ["*.example.com", "api.example.org", "https://files.example.org/x"]
    value["vool"]["homepage"] = "http://example.org"            # not https
    value["vool"]["mcpServers"]["text-digest"]["command"] = "../outside/server"
    value["vool"]["tools"][0]["trust"]["permission_actions"] = ["wallet_spend"]


WANTED = ["vool.name", "vool.publisher", "vool.homepage", "vool.network[0]", "vool.network[2]",
          "vool.mcpServers.text-digest.command", "vool.tools[0].trust.permission_actions"]


def _json(capsys, package) -> tuple[int, dict]:
    code = plugin_check.main([str(package), "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_one_run_names_every_field_to_change(folder, capsys):
    _change(folder, _several_mistakes)
    code, answer = _json(capsys, folder)
    assert code == 1 and answer["ok"] is False
    listed = [item["where"] for item in answer["problems"]]
    assert sorted(listed) == sorted(WANTED), listed
    for item in answer["problems"]:
        assert set(item) == {"message", "where", "fix"}
        assert item["message"] and item["fix"] and len(item["fix"]) <= 900 and "\n" not in item["fix"]
    assert "api.example.org" not in json.dumps(answer["problems"]), "the one good website is not listed as a problem"


def test_the_first_problem_is_the_install_doors_own_and_stays_where_tools_read_it(folder, capsys):
    _change(folder, _several_mistakes)
    status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert status == 400
    _code, answer = _json(capsys, folder)
    assert (answer["message"], answer["where"], answer["fix"]) == (
        body["message"], body["maker_note"]["where"], body["maker_note"]["fix"])
    assert answer["problems"][0] == {key: answer[key] for key in ("message", "where", "fix")}


def test_a_single_mistake_is_listed_once(folder, capsys):
    _change(folder, lambda value: value["vool"].update(network=["*.example.com"]))
    code, answer = _json(capsys, folder)
    assert code == 1 and [item["where"] for item in answer["problems"]] == ["vool.network[0]"]


def test_each_misspelt_field_is_named_by_itself(folder, capsys):
    def misspell(value):
        value["vool"]["homepgae"] = value["vool"].pop("homepage")
        value["vool"]["tools"][0]["handler"]["timeout_seconds"] = 9000
    _change(folder, misspell)
    _code, answer = _json(capsys, folder)
    assert answer["problems"][0] == {key: answer[key] for key in ("message", "where", "fix")}
    text = json.dumps(answer["problems"][1:])
    assert "homepgae" in text and "timeout_seconds" in text
    assert len(answer["problems"]) >= 3


def test_sentences_list_the_rest_after_the_first(folder, capsys):
    _change(folder, _several_mistakes)
    assert plugin_check.main([str(folder)]) == 1
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith("Refused: ")
    assert printed[1].startswith("For the plugin's maker: vool.name: ")
    assert printed[2] == f"{len(WANTED) - 1} more to change:"
    assert sorted(line.split(" ", 1)[1].split(": ", 1)[0] for line in printed[3:]) == sorted(
        where for where in WANTED if where != "vool.name")
    assert all(line.split(". ", 1)[0].isdigit() for line in printed[3:])


def test_a_folder_that_passes_has_no_problem_list_and_the_codes_are_unchanged(folder, capsys):
    code, answer = _json(capsys, folder)
    assert code == 0 and answer["ok"] is True and "problems" not in answer
    assert plugin_check.main([str(folder / "missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert lifecycle.records() == []


def test_listing_problems_never_makes_an_install_pass(folder):
    """The mode that reads on belongs to the check only: the install door still stops at the first refusal."""
    from core.plugin_manifest import ManifestRefusal, manifest_problems, validate_manifest

    _change(folder, _several_mistakes)
    raw = json.loads((folder / ".codex-plugin/plugin.json").read_text())
    assert len(manifest_problems(raw, root=folder)) >= 5
    with pytest.raises(ManifestRefusal):
        validate_manifest(raw, root=folder)
    status, _body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert status == 400 and lifecycle.records() == []


def _faulty_browser_plugin(package: Path) -> None:
    """A browser plugin whose extension breaks four separate rules at once."""
    (package / "ext").mkdir()
    (package / "ext" / "manifest.json").write_text(json.dumps({
        "manifest_version": 2, "name": "Tab notes", "version": "1.0", "update_url": "https://updates.example.org/x",
        "permissions": ["tabs", "nativeMessaging"], "chrome_url_overrides": {"newtab": "start.html"}}), encoding="utf-8")

    def browser(value):
        value["vool"]["kind"] = ["browser"]
        value["vool"]["browser"] = {"extension": "ext"}
        for key in ("tools", "mcpServers"):
            value["vool"].pop(key, None)
        value.pop("tools", None)
    _change(package, browser)


def test_every_browser_extension_rule_that_fails_is_named_in_one_run(folder, capsys):
    _faulty_browser_plugin(folder)
    code, answer = _json(capsys, folder)
    assert code == 1
    assert [item["where"] for item in answer["problems"]] == [
        "manifest.json manifest_version", "manifest.json update_url", "manifest.json permissions",
        "manifest.json chrome_url_overrides / chrome_settings_overrides"]
    assert "nativeMessaging" in answer["problems"][2]["fix"] and "tabs" not in answer["problems"][2]["fix"]


def test_a_faulty_browser_plugin_still_stops_at_its_first_rule_at_install(folder):
    _faulty_browser_plugin(folder)
    status, body = handle_addon_post({"action": "plugin_prepare", "path": str(folder), "update": False}, OWN_PAGE, "127.0.0.1")
    assert status == 400
    assert body["maker_note"]["where"] == "manifest.json manifest_version"
    assert lifecycle.records() == []
