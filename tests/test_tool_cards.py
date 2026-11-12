"""Tool cards: a tool says once when a person needs it, and the core routes to it from that.

What this protects (DESIGN: /mnt/project-files/modular-tools-20261029/DESIGN.md):

- every builtin card is well formed and cards a real builtin tool (no orphan cards);
- a card can never carry or change authority: unknown fields (approval, permissions, network) are
  refused, and a plugin contract with a card has exactly the trust of the same tool without one;
- a plugin tool with a card is offered on a person's ordinary words, not only on its intent
  token, the moment it is registered and never after it is removed;
- card routing is owner-local only, on by default, and the ``tools.card_routing`` flag turns it off;
- a ``not_for`` near miss vetoes its card.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import tool_card_router, tool_cards
from core.tool_cards import ToolCardError, parse_card

_CARD = {
    "use_when": "The person wants to know when their next bin collection is.",
    "examples": [
        "when are the bins collected",
        "which bin goes out this week",
        "is it recycling or general waste tomorrow",
        "cuándo pasa el camión de la basura",
    ],
    "not_for": ["how do I recycle batteries"],
}

_TOOL = {
    "intent": "bin-day.next_collection",
    "description": "Report the next household waste collection day and bin type for the saved address.",
    "handler": {"kind": "subprocess", "entry": "bin/run"},
    "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
    "side_effect_class": "read_only",
    "approval_requirement": "none",
}


def _pack(*, card: dict | None) -> dict:
    tool = dict(_TOOL)
    if card is not None:
        tool["card"] = card
    return {"name": "bin-day", "version": "1.0.0", "runtime": {"contract_version": 1}, "tools": [tool]}


@pytest.fixture
def card_flag(monkeypatch: pytest.MonkeyPatch):
    from core import policy_engine

    monkeypatch.setenv("VOOL_TOOL_CARD_ROUTING", "1")
    policy_engine.load(force_reload=True)
    tool_card_router.reset()
    yield
    monkeypatch.delenv("VOOL_TOOL_CARD_ROUTING", raising=False)
    policy_engine.load(force_reload=True)
    tool_card_router.reset()


@pytest.fixture
def bin_pack():
    """The pack as an installed, verified and enabled plugin registers it: supported contracts."""
    from core import capability_graph, tool_registry
    from core.plugin_tools import parse_manifest

    tool_registry.reset()
    contracts = parse_manifest(_pack(card=_CARD), path=Path("/x/bin-day/.codex-plugin/plugin.json")).contracts
    tool_registry.register_all(contracts)
    try:
        yield
    finally:
        tool_registry.reset()
        capability_graph.reset()
        capability_graph.init_graph()
        capability_graph.bootstrap_from_registry()


def test_every_builtin_card_parses_and_cards_a_real_builtin_tool() -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    tool_cards.reset()
    cards = tool_cards.builtin_cards()
    assert cards, "no builtin cards were read from core/tool_card_data"
    builtins = runtime_tool_contract_map()
    orphans = sorted(intent for intent in cards if intent not in builtins)
    assert not orphans, f"cards for tools that do not exist: {orphans}"


@pytest.mark.parametrize("field", ["approval_requirement", "permission_actions", "network", "side_effect_class", "credentials"])
def test_a_card_that_tries_to_carry_trust_is_refused(field: str) -> None:
    raw = dict(_CARD, **{field: "none"})
    with pytest.raises(ToolCardError, match="unknown field"):
        parse_card(raw, owner="bin-day.next_collection")


def test_a_card_cannot_name_the_same_ask_as_example_and_near_miss() -> None:
    raw = dict(_CARD, not_for=["When are the bins collected"])
    with pytest.raises(ToolCardError, match="both an example and not_for"):
        parse_card(raw, owner="bin-day.next_collection")


def test_a_card_changes_nothing_a_gate_reads() -> None:
    from core.plugin_tools import parse_manifest

    path = Path("/x/bin-day/.codex-plugin/plugin.json")
    with_card = parse_manifest(_pack(card=_CARD), path=path).contracts[0]
    without = parse_manifest(_pack(card=None), path=path).contracts[0]
    assert with_card.card is not None and without.card is None
    for attr in ("side_effect_class", "approval_requirement", "permission_actions", "supported",
                 "model_visible", "handler", "json_schema", "source", "claim", "mutation"):
        assert getattr(with_card, attr) == getattr(without, attr), attr


def test_a_bad_plugin_card_fails_the_manifest_loudly() -> None:
    from core.plugin_tools import PluginManifestError, parse_manifest

    with pytest.raises(PluginManifestError, match="card.examples"):
        parse_manifest(_pack(card=dict(_CARD, examples=["one"])), path=Path("/x/bin-day/.codex-plugin/plugin.json"))


def test_a_builtin_card_file_never_describes_a_plugin_tool() -> None:
    from core.plugin_tools import parse_manifest

    contract = parse_manifest(_pack(card=None), path=Path("/x/bin-day/.codex-plugin/plugin.json")).contracts[0]
    assert tool_cards.card_for(contract) is None


def test_an_installed_plugin_tool_is_offered_on_ordinary_words(tmp_path: Path, monkeypatch, card_flag) -> None:
    """Through the REAL lifecycle (install, verify, enable) and the real offer builder: a person
    asking in their own words gets the plugin's tool, with no router edit and no intent token."""
    from core import capability_graph, plugin_tools, tool_registry
    from core.capability_graph import model_visible_specs
    from core.runtime_flags import override
    from tests._toolchain_fixtures import make_plugin, reset_toolchain_state

    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    tool = dict(_TOOL, card=_CARD, handler={"kind": "subprocess", "entry": "bin/run"})
    make_plugin(tmp_path, plugin_id="bin-day", tools=[tool], admit=True)
    reset_toolchain_state()
    try:
        tool_registry.reset()
        plugin_tools.reset_roots()
        with override("plugin_runtime_tools", True):
            plugin_tools.load_all(tmp_path)
            capability_graph.reset()
            capability_graph.init_graph()
            capability_graph.bootstrap_from_registry()
            ask = "which bin goes out this week?"
            owner = {str(s.get("intent")) for s in model_visible_specs(user_text=ask, source_context={"surface": "desktop"})}
            remote_ctx = {"surface": "desktop", "_owner_local": False}
            remote = {str(s.get("intent")) for s in model_visible_specs(user_text=ask, source_context=remote_ctx)}
            from core import policy_engine

            monkeypatch.setenv("VOOL_TOOL_CARD_ROUTING", "0")
            policy_engine.load(force_reload=True)
            remote_without_cards = {
                str(s.get("intent")) for s in model_visible_specs(user_text=ask, source_context=remote_ctx)
            }
        assert "bin-day.next_collection" in owner, sorted(owner)
        # A remote turn's offer is exactly what it was without cards: cards add nothing there.
        assert remote == remote_without_cards
    finally:
        reset_toolchain_state()


def test_a_removed_plugin_tool_is_never_routed_again(bin_pack, card_flag) -> None:
    from core import tool_registry

    assert tool_card_router.routed_intents("when are the bins collected", owner_local=True)
    tool_registry.unregister("bin-day.next_collection")
    assert not any(
        m.intent == "bin-day.next_collection"
        for m in tool_card_router.routed_intents("when are the bins collected", owner_local=True)
    )


def test_card_routing_is_owner_local_only_and_can_be_switched_off(bin_pack, monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.delenv("VOOL_TOOL_CARD_ROUTING", raising=False)
    policy_engine.load(force_reload=True)
    try:
        assert tool_card_router.routed_intents("when are the bins collected", owner_local=True), "on by default"
        assert tool_card_router.routed_intents("when are the bins collected", owner_local=False) == ()
        monkeypatch.setenv("VOOL_TOOL_CARD_ROUTING", "0")
        policy_engine.load(force_reload=True)
        assert tool_card_router.routed_intents("when are the bins collected", owner_local=True) == ()
    finally:
        monkeypatch.delenv("VOOL_TOOL_CARD_ROUTING", raising=False)
        policy_engine.load(force_reload=True)


def test_a_near_miss_vetoes_its_card(bin_pack) -> None:
    hits = tool_card_router.match_cards("how do I recycle batteries", semantic="off")
    assert all(m.intent != "bin-day.next_collection" for m in hits)


def test_the_card_releases_a_tool_less_chat_turn_only_for_the_owner(bin_pack, card_flag) -> None:
    from core.execution_requirements import requirements_for

    owner = requirements_for("which bin goes out this week?", source_context={"surface": "desktop"})
    assert owner.tools_required and "tool_card_request" in owner.reason_codes, owner
    remote = requirements_for(
        "which bin goes out this week?", source_context={"surface": "desktop", "_owner_local": False}
    )
    assert "tool_card_request" not in remote.reason_codes, remote


def test_a_plugin_that_is_only_present_on_disk_is_never_routed(tmp_path: Path, monkeypatch, card_flag) -> None:
    """Present is not installed: an unverified pack registers as an explained absence, and its
    card must not put it in front of the model."""
    from core import capability_graph, plugin_tools, tool_registry
    from core.runtime_flags import override

    manifest = tmp_path / "plugins" / "bin-day" / ".codex-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(json.dumps(_pack(card=_CARD)), encoding="utf-8")
    tool_registry.reset()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    try:
        with override("plugin_runtime_tools", True):
            plugin_tools.load_all(tmp_path)
            assert tool_registry.tool_for_intent("bin-day.next_collection") is not None
            assert tool_card_router.routed_intents("when are the bins collected", owner_local=True) == ()
    finally:
        tool_registry.reset()
        capability_graph.reset()
        capability_graph.init_graph()
        capability_graph.bootstrap_from_registry()
