"""The card template an author copies, run through the same validator as every real card.

What this protects:

- ``docs/guides/templates/tool-card.example.json`` stays a card the core accepts: it reads as
  JSON and passes ``core.tool_cards.parse_card``, the validator builtin cards and plugin
  manifests already go through, so an author who copies it starts from a working card;
- the template also loads inside a full plugin manifest (``core.plugin_tools.parse_manifest``)
  and the tool contract that comes back carries the card, which is exactly where an author
  pastes it;
- every builtin card still passes that same validator and names a builtin tool that exists;
- the checker itself turns red on a deliberately broken card, so a green run means something.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import tool_cards
from core.tool_cards import ToolCardError, parse_card

#: The template the guide hands to card authors: a bare card object for a fictional plugin.
TEMPLATE = (
    Path(__file__).resolve().parents[1] / "docs" / "guides" / "templates" / "tool-card.example.json"
)

_TOOL = {
    "intent": "tide-tables.next_tide",
    "description": "Report the tide times and heights for a place, today or on a named date.",
    "handler": {"kind": "subprocess", "entry": "bin/run"},
    "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
    "side_effect_class": "read_only",
    "approval_requirement": "none",
}


def _pack(*, card: dict) -> dict:
    tool = dict(_TOOL, card=card)
    return {"name": "tide-tables", "version": "1.0.0", "runtime": {"contract_version": 1}, "tools": [tool]}


def test_the_template_is_json_and_passes_the_real_card_validator() -> None:
    try:
        raw = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        pytest.fail(f"{TEMPLATE.name} could not be read as JSON: {exc}")
    try:
        parse_card(raw, owner="tide-tables.next_tide")
    except ToolCardError as exc:
        pytest.fail(f"{TEMPLATE.name} is not a card the core accepts: {exc}")


def test_every_builtin_card_passes_the_real_validator_and_names_a_real_tool() -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    tool_cards.reset()
    try:
        cards = tool_cards.builtin_cards()
    except ToolCardError as exc:
        pytest.fail(f"a builtin card file under core/tool_card_data failed the card validator: {exc}")
    assert cards, "no builtin cards were read from core/tool_card_data"
    builtins = runtime_tool_contract_map()
    orphans = sorted(intent for intent in cards if intent not in builtins)
    assert not orphans, f"builtin cards name tools that do not exist: {', '.join(orphans)}"


def test_the_template_is_paste_ready_inside_a_full_plugin_manifest() -> None:
    from core.plugin_tools import PluginManifestError, parse_manifest

    card = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    try:
        contract = parse_manifest(
            _pack(card=card), path=Path("/x/tide-tables/.codex-plugin/plugin.json")
        ).contracts[0]
    except PluginManifestError as exc:
        pytest.fail(f"{TEMPLATE.name} was rejected inside a plugin manifest: {exc}")
    assert contract.card is not None, (
        f"the manifest loaded but the tool carries no card from {TEMPLATE.name}"
    )
    assert contract.card.examples == tuple(card["examples"]), (
        f"the card that reached the tool contract is not the one in {TEMPLATE.name}"
    )


def test_the_checker_rejects_a_deliberately_broken_template_variant() -> None:
    card = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    broken = dict(card, examples=[card["examples"][0]])
    with pytest.raises(ToolCardError, match=r"card\.examples"):
        parse_card(broken, owner="tide-tables.next_tide")
