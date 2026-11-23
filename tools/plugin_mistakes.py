"""The mistakes a plugin's maker makes most, each as one change to the example plugin's setup file.

One table, read by two readers: the test that proves every mistake is refused with the field and the
fix named, and the generator that writes the same answers into the creators' reference
(``python -m tools.creator_docs --write``). It lives beside the tools because the reference is built
from a checkout, and a tool that builds docs should not need the test folder to run.
"""
from __future__ import annotations

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
