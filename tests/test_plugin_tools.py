"""Loading a user's own tools from a plugin manifest.

The manifest format is the one already on disk — `.codex-plugin/plugin.json`, as shipped by both
plugins in `~/Desktop/Vool-skills-plugins` — with `runtime` and `tools` added. A manifest without
those keys must keep loading exactly as it does today, or installing this would orphan the packs a
user already has.

Most of this file is refusals, because a plugin is untrusted input that ends up in the model's tool
catalog with real permissions. Each rule below exists to stop a specific way a pack could be wrong:
claiming a runtime namespace, inventing a security tier, declaring a mutating tool as needing no
approval, or omitting the claim block that makes its answers checkable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import plugin_tools, tool_registry

GOOD_TOOL = {
    "intent": "vool-jira.search_issues",
    "description": "Search Jira issues with a JQL query and return key, summary and status.",
    "handler": {"kind": "subprocess", "entry": "bin/run", "args": ["search_issues"]},
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["jql"],
        "properties": {
            "jql": {"type": "string", "x-vool-kind": "query"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        },
    },
    "side_effect_class": "read_only",
    "approval_requirement": "none",
    "claim": {"target_argument": "jql", "resolved_target_key": "jql", "result_items_key": "issues"},
}


@pytest.fixture(autouse=True)
def _clean():
    tool_registry.reset()
    yield
    tool_registry.reset()


def _manifest(tmp_path: Path, *, tools=None, runtime=..., name="vool-jira") -> Path:
    body = {"name": name, "version": "1.0.0", "description": "d"}
    if tools is not None:
        body["tools"] = tools
        body["runtime"] = {"contract_version": 1} if runtime is ... else runtime
    directory = tmp_path / "plugins" / name / ".codex-plugin"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "plugin.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _tool(**overrides) -> dict:
    spec = json.loads(json.dumps(GOOD_TOOL))
    spec.update(overrides)
    return spec


def _load_tool(spec: dict, plugin_id: str = "vool-jira"):
    return plugin_tools.contract_from_tool(spec, plugin_id=plugin_id, version="1.0.0")


# --------------------------------------------------------------------------------------
# The happy path and backward compatibility
# --------------------------------------------------------------------------------------


def test_a_declared_tool_becomes_a_runtime_contract() -> None:
    contract = _load_tool(_tool())
    assert contract.intent == "vool-jira.search_issues"
    assert contract.source == "plugin:vool-jira"
    assert contract.claim.result_items_key == "issues"
    assert contract.json_schema["required"] == ["jql"]


def test_a_manifest_without_tools_still_loads(tmp_path: Path) -> None:
    """Both installed plugins are exactly this shape. Adding tools must not orphan them."""

    plugin = plugin_tools.load_manifest(_manifest(tmp_path))
    assert plugin.plugin_id == "vool-jira" and plugin.contracts == ()


def test_a_full_manifest_registers_its_tools(tmp_path: Path) -> None:
    _manifest(tmp_path, tools=[_tool()])
    loaded, errors = plugin_tools.load_all(tmp_path)
    assert errors == ()
    assert "vool-jira.search_issues" in tool_registry.registry_map()
    assert loaded[0].contracts


def test_one_bad_pack_does_not_stop_the_others(tmp_path: Path) -> None:
    # Each pack's intent must carry its own name, so the fixtures are renamed together with them.
    _manifest(tmp_path, tools=[_tool(intent="vool-good.search")], name="vool-good")
    _manifest(
        tmp_path,
        tools=[_tool(intent="vool-bad.search", side_effect_class="nonsense")],
        name="vool-bad",
    )
    loaded, errors = plugin_tools.load_all(tmp_path)
    assert [p.plugin_id for p in loaded] == ["vool-good"]
    assert errors and "nonsense" in errors[0]
    assert "vool-good.search" in tool_registry.registry_map()


# --------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------


def test_a_tool_must_be_namespaced_to_its_plugin() -> None:
    with pytest.raises(plugin_tools.PluginManifestError, match="prefixed"):
        _load_tool(_tool(intent="machine.list_directory"))


def test_an_invented_side_effect_class_is_refused() -> None:
    """A plugin must not be able to mint a security tier."""

    with pytest.raises(plugin_tools.PluginManifestError, match="side_effect_class"):
        _load_tool(_tool(side_effect_class="totally_safe"))


def test_a_mutating_tool_cannot_declare_no_approval() -> None:
    with pytest.raises(plugin_tools.PluginManifestError, match="cannot declare"):
        _load_tool(_tool(side_effect_class="network_send", approval_requirement="none"))


def test_a_read_only_tool_cannot_back_an_action_claim() -> None:
    """Otherwise a pack could assert "I sent it" with a tool that only reads."""

    spec = _tool()
    spec["claim"]["asserts_action"] = True
    with pytest.raises(plugin_tools.PluginManifestError, match="asserts_action"):
        _load_tool(spec)


def test_an_annotated_property_requires_a_claim_block() -> None:
    """A tool whose answers cannot be checked must not load silently."""

    spec = _tool()
    del spec["claim"]
    with pytest.raises(plugin_tools.PluginManifestError, match="claim"):
        _load_tool(spec)


def test_an_unsupported_schema_keyword_is_refused_at_load() -> None:
    spec = _tool()
    spec["input_schema"]["properties"]["jql"]["allOf"] = []
    with pytest.raises(plugin_tools.PluginManifestError, match="allOf"):
        _load_tool(spec)


def test_an_unsupported_handler_kind_is_refused() -> None:
    with pytest.raises(plugin_tools.PluginManifestError, match="handler kind"):
        _load_tool(_tool(handler={"kind": "python_entry_point", "entry": "pkg:fn"}))


def test_a_thin_description_is_refused() -> None:
    """The description is what the model reads to decide whether to call the tool."""

    with pytest.raises(plugin_tools.PluginManifestError, match="description"):
        _load_tool(_tool(description="jira"))


def test_tools_without_a_runtime_block_are_refused(tmp_path: Path) -> None:
    path = _manifest(tmp_path, tools=[_tool()], runtime=None)
    with pytest.raises(plugin_tools.PluginManifestError, match="runtime"):
        plugin_tools.load_manifest(path)


def test_a_missing_contract_version_is_not_defaulted(tmp_path: Path) -> None:
    """A manifest written against a different contract must fail loudly, not load half-understood."""

    path = _manifest(tmp_path, tools=[_tool()], runtime={"requires_vool": ">=0.4.0"})
    with pytest.raises(plugin_tools.PluginManifestError, match="contract_version"):
        plugin_tools.load_manifest(path)


def test_a_wrong_contract_version_is_refused(tmp_path: Path) -> None:
    path = _manifest(tmp_path, tools=[_tool()], runtime={"contract_version": 99})
    with pytest.raises(plugin_tools.PluginManifestError, match="contract_version"):
        plugin_tools.load_manifest(path)


def test_a_duplicate_intent_within_one_manifest_is_refused(tmp_path: Path) -> None:
    path = _manifest(tmp_path, tools=[_tool(), _tool()])
    with pytest.raises(plugin_tools.PluginManifestError, match="twice"):
        plugin_tools.load_manifest(path)


def test_unreadable_json_reports_the_file(tmp_path: Path) -> None:
    directory = tmp_path / "plugins" / "broken" / ".codex-plugin"
    directory.mkdir(parents=True)
    path = directory / "plugin.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(plugin_tools.PluginManifestError, match="JSON"):
        plugin_tools.load_manifest(path)


def test_a_pack_registers_atomically(tmp_path: Path) -> None:
    """A half-installed pack offers the model part of a plugin and denies the rest."""

    good = _tool()
    bad = _tool(intent="vool-jira.create_issue", side_effect_class="network_send", approval_requirement="none")
    _manifest(tmp_path, tools=[good, bad])
    loaded, errors = plugin_tools.load_all(tmp_path)
    assert loaded == () and errors
    assert "vool-jira.search_issues" not in tool_registry.registry_map()


# --------------------------------------------------------------------------------------
# Against the real installed repo
# --------------------------------------------------------------------------------------


def test_the_installed_plugin_repo_still_loads_if_present() -> None:
    """Guards the additive promise against the packs actually on this machine."""

    from core.plugin_catalog import plugins_root

    root = plugins_root()
    if root is None:
        pytest.skip("no plugin repo installed on this machine")
    loaded, errors = plugin_tools.load_all(root)
    assert errors == (), f"an installed plugin stopped loading: {errors}"
    assert loaded, "expected at least one installed plugin"
