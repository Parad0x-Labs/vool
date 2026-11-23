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

TEMPLATE = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "template"
OWN_PAGE = {"Content-Type": "application/json", "Host": "127.0.0.1:8123", "Origin": "http://127.0.0.1:8123"}
SUBPROCESS = {"kind": "subprocess", "entry": "bin/server"}


def _tool(value):
    return value["vool"]["tools"][0]


def _set(path, item):
    def change(value):
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = item
    return change


def _key_slot(env):
    return _set(("vool", "credentials"), {"main": {"label": "Service key", "help_url": "https://example.org/keys", "env": env}})


def _both(*changes):
    def change(value):
        for one in changes:
            one(value)
    return change


# (what the maker did, the change, the field the note must name, words the note must hold)
MISTAKES = [
    ("a wildcard host", _set(("vool", "network"), ["*.example.com"]), "vool.network[0]", "no wildcard"),
    ("an address in numbers", _set(("vool", "network"), ["203.0.113.7"]), "vool.network[0]", "host name"),
    ("a local name", _set(("vool", "network"), ["printer.local"]), "vool.network[0]", "public services only"),
    ("capital letters in a host", _set(("vool", "network"), ["API.example.com"]), "vool.network[0]", "lower case"),
    ("a host with no dot", _set(("vool", "network"), ["intranet"]), "vool.network[0]", "at least one dot"),
    ("a full address where a host belongs", _set(("vool", "network"), ["https://api.example.com/v1"]), "vool.network[0]", "no https://"),
    ("the same host twice", _set(("vool", "network"), ["api.example.com", "api.example.com"]), "vool.network", "once"),
    ("a homepage without https", _set(("vool", "homepage"), "http://example.org"), "vool.homepage", "https://"),
    ("the owner's model key name", _key_slot("OPENAI_API_KEY"), "vool.credentials.main.env", "ACME_API_KEY"),
    ("a VOOL setting as the key name", _key_slot("VOOL_HOME"), "vool.credentials.main.env", "kept back"),
    ("a tool not named after the plugin", lambda v: _tool(v).__setitem__("intent", "lookup"), "vool.tools[0].intent", "text-digest-example.<tool>"),
    ("capital letters in a tool name", lambda v: _tool(v).__setitem__("intent", "text-digest-example.SHA256"), "vool.tools[0].intent", "lowercase"),
    ("a field VOOL does not know", lambda v: _tool(v).__setitem__("timeout", 10), "vool.tools[0]", "unknown field timeout"),
    ("a description left out", lambda v: _tool(v).pop("description"), "vool.tools[0]", "missing description"),
    ("a spending class", lambda v: _tool(v)["trust"].__setitem__("side_effect_class", "wallet_spend"), "vool.tools[0].trust.side_effect_class", "read_only"),
    ("a wallet permission", lambda v: _tool(v)["trust"].__setitem__("permission_actions", ["wallet_spend"]), "vool.tools[0].trust.permission_actions", "paid_services"),
    ("a tool named like a payment", lambda v: _tool(v).__setitem__("intent", "text-digest-example.payment"), "vool.tools[0].intent", "money action"),
    ("an auto-start switch", _set(("vool", "auto_start"), True), "manifest.vool", "auto_start"),
    ("a wallet block", _set(("wallet",), {"limit": 5}), "manifest.wallet", "holds no wallet"),
    ("the owner's keys as a binding", _set(("credential_bindings",), ["llm.cloud.openai"]), "credential_bindings[0]", "its own key slots"),
    ("a server command outside the folder", _set(("vool", "mcpServers", "text-digest", "command"), "/usr/bin/python3"), "vool.mcpServers.text-digest.command", "inside the plugin folder"),
    ("an installer as the server command", _set(("vool", "mcpServers", "text-digest", "command"), "npx"), "vool.mcpServers.text-digest.command", "no installer"),
    ("a fixed value in a server's env", _both(_key_slot("ACME_API_KEY"), _set(("vool", "mcpServers", "text-digest", "env"), {"ACME_API_KEY": "abc"})), "vool.mcpServers.text-digest.env", "${NAME}"),
    ("a variable in a server argument", _set(("vool", "mcpServers", "text-digest", "args"), ["--key=${ACME_API_KEY}"]), "vool.mcpServers.text-digest.args", "through its env"),
    ("a server the tool names but nobody declared", lambda v: _tool(v)["handler"].__setitem__("server", "other"), "vool.tools[0].handler", "vool.mcpServers"),
    ("tools without the tools kind", _set(("vool", "kind"), ["scanner"]), "vool", ""),
    ("the same kind twice", _set(("vool", "kind"), ["tools", "tools"]), "vool.kind", "once"),
    ("an id that differs from the name", _set(("vool", "id"), "another-id"), "vool.id / vool.version", "must equal"),
    ("a version that differs", _set(("vool", "version"), "2.0.0"), "vool.id / vool.version", "must equal"),
    ("a program path that climbs out", lambda v: _tool(v).__setitem__("handler", {"kind": "subprocess", "entry": "../tool"}), "vool.tools[0].handler.entry", "inside the plugin folder"),
    ("internet for a tool with no hosts listed", lambda v: _tool(v).__setitem__("handler", {**SUBPROCESS, "network": True}), "vool.tools[0].handler.network", "vool.network is empty"),
    ("a key the plugin never declared", lambda v: _tool(v).__setitem__("handler", {**SUBPROCESS, "env_allowlist": ["ACME_API_KEY"]}), "vool.tools[0].handler.env_allowlist", "vool.credentials"),
    ("a paid service with a path", _both(_set(("vool", "network"), ["pay.example.com"]), _set(("vool", "paid_services"), ["https://pay.example.com/v1"])), "vool.paid_services[0]", "https://host only"),
    ("a paid service the plugin does not reach", _set(("vool", "paid_services"), ["https://pay.example.com"]), "vool.paid_services[0]", "vool.network"),
]


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
