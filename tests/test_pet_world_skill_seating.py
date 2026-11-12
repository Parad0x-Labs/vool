"""The pet and world skills sit at the table only when the ask is about a pet or a world.

They carry trigger words and no task family, so a plain creative turn ("write an X article")
keeps its room for its own skill, while the asks the Pet Yard points people to still seat them.
"""
from __future__ import annotations

import pytest

from core.native_skill_library import select_native_skills

SEATED = ("create-pet", "create-world")


@pytest.fixture()
def iso_home(tmp_path, monkeypatch):
    """Isolated VOOL_HOME so operator config never touches the real home."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    from core import native_skill_library

    native_skill_library.reset_contract_caches()
    yield home
    native_skill_library.reset_contract_caches()


def _seated(user_text: str, task_class: str = "creative_ideation") -> set[str]:
    selection = select_native_skills(task_class=task_class, user_text=user_text)
    return {skill.id for skill in selection.selected} & set(SEATED)


@pytest.mark.parametrize("ask", [
    "create a pet for my toybox",
    "Can you create a pet for me?",
    "make me a pet that looks like a fox",
    "design a pet for the Pet Yard",
    "make a pet I can import into Pet Yard",
    "Draw me a pixel pet cat",
    "make a .voolpet pack of a moth",
])
def test_pet_asks_seat_the_pet_skill(iso_home, ask: str) -> None:
    assert "create-pet" in _seated(ask), ask


@pytest.mark.parametrize("ask", [
    "build a world with a lake",
    "make a world pack with a race course",
    "build a park for my pets",
    "make a .voolworld file with a pond",
])
def test_world_asks_seat_the_world_skill(iso_home, ask: str) -> None:
    assert "create-world" in _seated(ask), ask


@pytest.mark.parametrize("task_class", ["creative_ideation", "general", "coding"])
def test_a_pet_ask_seats_the_pet_skill_whatever_the_class(iso_home, task_class: str) -> None:
    assert "create-pet" in _seated("create a pet for my toybox", task_class)


@pytest.mark.parametrize("ask", ["write an X article", "write a poem about autumn", "write a poem about my pet"])
def test_other_creative_asks_leave_both_seats_free(iso_home, ask: str) -> None:
    assert _seated(ask) == set(), ask
