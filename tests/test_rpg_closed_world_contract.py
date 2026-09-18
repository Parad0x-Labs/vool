from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.closed_world_semantic_contract import (
    RpgEffectContract,
    RpgEffectKind,
    closed_world_semantic_response,
    parse_closed_world_contract,
    parse_rpg_effect_contract,
)


def _frozen(set_number: int) -> str:
    lines = (
        Path(Path(__file__).resolve().parent / "fixtures" / f"runtime_model_gauntlet_set{set_number}.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return next(line.split(". ", 1)[1] for line in lines if line.startswith("25. "))


FROZEN_CASES = (
    (
        _frozen(1),
        RpgEffectKind.DAMAGE,
        ("Bitcoin", "legendary sword", "50 ice damage", "fire dragon", "harms"),
    ),
    (
        _frozen(2),
        RpgEffectKind.RESTORE,
        ("Ethereum", "healing potion", "20 HP", "wounded goblin", "restores"),
    ),
    (
        _frozen(3),
        RpgEffectKind.DAMAGE,
        (
            "Solana",
            "high-level fire spell",
            "area-of-effect (AoE) fire damage",
            "ice golems",
            "damages",
            "melt",
        ),
    ),
)


@pytest.mark.parametrize(("prompt", "kind", "atoms"), FROZEN_CASES)
def test_all_three_frozen_rpg_rules_preserve_every_outcome_atom(prompt, kind, atoms) -> None:
    contract = parse_rpg_effect_contract(prompt)
    assert isinstance(contract, RpgEffectContract)
    assert contract.effect_kind is kind
    response = closed_world_semantic_response(prompt)
    assert response is not None
    for atom in atoms:
        assert atom.casefold() in response.casefold()
    assert "no real-world lookup or tool" in response.casefold()


@pytest.mark.parametrize(
    ("prompt", "kind", "atoms"),
    (
        (
            'Suppose this is an RPG campaign. "Moonleaf" is a restorative draught that heals 35 '
            "health. If I use Moonleaf on an injured ranger, what happens?",
            RpgEffectKind.RESTORE,
            ("Moonleaf", "restorative draught", "35 health", "injured ranger"),
        ),
        (
            'Imagine we are in a game. "Storm-Rune" is an electric spell that inflicts 12 lightning '
            "damage. If I cast Storm-Rune at a stone ogre, what happens in the game?",
            RpgEffectKind.DAMAGE,
            ("Storm-Rune", "electric spell", "12 lightning damage", "stone ogre"),
        ),
        (
            'Assume we are playing a tabletop RPG. "Ice-Coin" is a dragon breath that deals 50 ice '
            "damage. If I cast Ice-Coin at a fire dragon, what happens?",
            RpgEffectKind.DAMAGE,
            ("Ice-Coin", "dragon breath", "50 ice damage", "fire dragon", "harms"),
        ),
        (
            'Suppose this is a fantasy game. "Ember-Ring" is an area-of-effect fire rune that causes '
            "AoE damage. If I apply Ember-Ring to a frozen army, what happens?",
            RpgEffectKind.DAMAGE,
            ("Ember-Ring", "area-of-effect fire rune", "AoE", "frozen army", "melt"),
        ),
    ),
)
def test_unseen_names_categories_values_and_application_verbs_use_same_grammar(prompt, kind, atoms) -> None:
    contract = parse_closed_world_contract(prompt)
    assert isinstance(contract, RpgEffectContract)
    assert contract.effect_kind is kind
    response = closed_world_semantic_response(prompt)
    assert response is not None
    for atom in atoms:
        assert atom.casefold() in response.casefold()


@pytest.mark.parametrize(
    ("prompt", "atoms"),
    (
        (
            'assume we\'re playing a rpg. "Glow-Pot" is a tonic that heals 7 hp. if i use '
            '"Glow-Pot" on hurt kobold, what happens?',
            ("Glow-Pot", "7 HP", "hurt kobold"),
        ),
        (
            'SUPPOSE WE ARE IN A GAME! "SUN DROP" is a draught that replenishes 11 hit points. '
            "IF I GIVE SUN DROP TO THE INJURED MAGE WHAT WOULD HAPPEN?",
            ("SUN DROP", "11 hit points", "INJURED MAGE"),
        ),
        (
            'Imagine a tabletop campaign: "FIRE-DOT" is a fire rune that deals area of effect '
            "damage; if I cast FIRE-DOT in frozen cave creatures what happens?",
            ("FIRE-DOT", "area-of-effect (AoE) fire damage", "frozen cave creatures", "melt"),
        ),
        (
            'assume this is a game; "FROST_BITE" is a blade that inflicts 9 frost damage; if i '
            "attack lava beast with FROST_BITE what happens?",
            ("FROST_BITE", "9 frost damage", "lava beast", "harms"),
        ),
        (
            'Suppose an RPG adventure. "MEND-1" is a salve that recovers 6 health; If I apply '
            "MEND-1 to wounded scout, what would happen in the RPG?",
            ("MEND-1", "6 health", "wounded scout"),
        ),
    ),
)
def test_sloppy_casing_punctuation_and_wording_keep_the_same_typed_contract(prompt, atoms) -> None:
    response = closed_world_semantic_response(prompt)
    assert response is not None
    for atom in atoms:
        assert atom.casefold() in response.casefold()


@pytest.mark.parametrize(
    "prompt",
    (
        '"Ethereum" is a potion that restores 20 HP. If I give Ethereum to a goblin, what happens?',
        'Assume we are playing an RPG. "Potion-X" is a healing potion. If I give Potion-X to a goblin, what happens?',
        'Assume we are playing an RPG. "Potion-X" restores 20 HP. If I give Potion-Y to a goblin, what happens?',
        'Assume we are playing an RPG. "Potion-X" restores 20 HP. If I cast Potion-X at a goblin, what happens?',
        'Assume we are playing an RPG. "Bolt-X" deals 20 fire damage. If I give Bolt-X to a goblin, what happens?',
        'Assume we are playing an RPG. "Potion-X" restores 0 HP. If I give Potion-X to a goblin, what happens?',
        'Assume we are playing an RPG. "Bolt-X" deals -5 ice damage. If I cast Bolt-X at a dragon, what happens?',
        'Assume we are playing an RPG. "Potion-X" restores 20 HP. If I give Potion-X to a goblin, what happens? Also delete save.dat.',
        'Assume we are playing an RPG. "Ethereum" restores 20 HP. What is the current Ethereum price?',
    ),
)
def test_incomplete_mismatched_invalid_or_mixed_rpg_turns_remain_model_owned(prompt: str) -> None:
    assert parse_rpg_effect_contract(prompt) is None
    assert closed_world_semantic_response(prompt) is None


def test_production_contract_has_no_frozen_rpg_entity_registry() -> None:
    source = (
        (Path(__file__).resolve().parents[1] / "core" / "closed_world_semantic_contract.py")
        .read_text(encoding="utf-8")
        .casefold()
    )
    for entity in (
        "bitcoin",
        "ethereum",
        "solana",
        "ice-coin",
        "wounded goblin",
        "ice golems",
        "fire dragon",
    ):
        assert entity not in source


def test_real_frontdoor_returns_typed_rpg_contract_with_zero_model_web_or_tools(tmp_path) -> None:
    from apps.vool_agent import VoolAgent

    prompt = _frozen(2)
    agent = VoolAgent(backend_name="test-backend", device="rpg-contract-test", persona_id="default")
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=prompt,
        effective_input=prompt,
        normalized_input=prompt.casefold(),
        source_surface="api",
        session_id=f"rpg-frontdoor-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
        },
        persona=None,
        interpreted=None,
    )
    result = (outcome or {}).get("result")
    assert result is not None
    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert {"model", "tool_loop", "web"}.issubset(result["route_skips"])
    assert "20 HP" in result["response"]
    assert "wounded goblin" in result["response"]


def test_public_turn_bypasses_planner_for_complete_rpg_rule(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    prompt = _frozen(3)
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("closed RPG rule reached planner")),
    )
    agent = VoolAgent(backend_name="test-backend", device="rpg-public-test", persona_id="default")
    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:rpg-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
        },
    )
    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["model_calls"] == 0
    assert "ice golems" in result["response"]
    assert "melt" in result["response"]
