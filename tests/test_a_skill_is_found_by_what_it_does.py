"""A skill the person made is found by what it does, and "turn the X skill off" reads as turning it off.

Mac gate on e36daa6a9 (installed app): right after making sort-grocery-list ("Sort a grocery list by
store aisle"), "use the aisle skill you just made" got "I don't have a skill called Aisle", and "turn
the aisle skill off now" matched no skill shape, so the reminder lane answered it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    from core.plugin_catalog import invalidate_storage_listing

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins-root"))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "voolhome"))
    monkeypatch.delenv("VOOL_DEVELOPER_SKILLS", raising=False)
    invalidate_storage_listing()
    yield
    invalidate_storage_listing()


def _make(name: str, description: str) -> None:
    from core import skill_tools

    draft = skill_tools.create_skill(name=name, description=description, body=f"{description} Keep it short.")
    assert draft.get("path"), draft
    installed = skill_tools.install_skill(draft["path"])
    assert installed.get("status") in {"installed", "ok", "activated"}, installed


def _lane(text: str):
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    agent = SimpleNamespace(memory_router=None,
                            _fast_path_result=lambda **kw: {"response": kw.get("response"), "reason": kw.get("reason")})
    return maybe_handle_skill_request(agent, text, session_id="aisle",
                                      source_context={"operating_mode": "auto", "session_id": "aisle"})


@pytest.mark.parametrize("said", ["turn the aisle skill off now", "switch the grocery skill off", "turn the Aisles skill off"])
def test_a_split_turn_off_reaches_the_skill_it_describes(said):
    _make("sort-grocery-list", "Sort a grocery list by store aisle.")
    ran = []

    def run(intent, arguments, **kwargs):
        ran.append((intent, dict(arguments)))
        return SimpleNamespace(ok=True, status="removed", response_text="Removed.", details={})

    with mock.patch("core.authorized_tool_execution.execute_authorized_runtime_tool", run):
        reply = _lane(said)
    assert reply is not None
    assert ran and ran[0][0] == "skill.remove" and ran[0][1]["name"] == "sort-grocery-list", (ran, reply)


@pytest.mark.parametrize("said", ["use the aisle skill you just made on: milk, apples", "run the grocery skill please"])
def test_use_by_what_it_does_goes_on_to_the_model(said):
    _make("sort-grocery-list", "Sort a grocery list by store aisle.")
    assert _lane(said) is None


def test_two_skills_that_fit_are_not_guessed_between():
    from core.agent_runtime.fast_paths_skill import _skill_meant_by

    _make("sort-grocery-list", "Sort a grocery list by store aisle.")
    _make("plan-grocery-trip", "Plan a grocery trip around the store aisle layout.")
    ctx = {"operating_mode": "auto", "session_id": "aisle"}
    assert _skill_meant_by("aisle", ctx, "aisle") == ""
    assert _skill_meant_by("sort aisle", ctx, "aisle") == "sort-grocery-list"


def test_a_skill_that_does_not_fit_is_still_named_back():
    _make("sort-grocery-list", "Sort a grocery list by store aisle.")
    reply = _lane("use the teleport skill")
    assert reply is not None and reply["response"].startswith("I don't have a skill called Teleport.")


@pytest.mark.parametrize("said", ["turn the lights off", "switch the radio on", "turn the oven off at 6"])
def test_other_split_turn_offs_are_left_alone(said):
    from core.agent_runtime.fast_paths_skill import _joined_toggle, skill_request

    assert _joined_toggle(said) == said and skill_request(said) is None


@pytest.mark.parametrize("said", ["ok, switch that skill on", "turn on the new skill", "switch the skill on please",
                                  "yes turn it on", "turn that skill on for this chat"])
def test_turning_a_fresh_draft_on_reads_as_activating_it(said):
    from core.agent_runtime.fast_paths_skill import skill_request

    request = skill_request(said)
    assert request is not None and request["action"] == "activate" and request["name"] == ""


@pytest.mark.parametrize("said", ["switch the lights on", "turn on the oven", "turn that thing on later today"])
def test_other_things_switched_on_are_not_a_skill(said):
    from core.agent_runtime.fast_paths_skill import skill_request

    assert skill_request(said) is None


def test_the_done_judge_reads_the_persons_items_sorted_into_a_table_as_no_claim():
    # Mac gate on e36daa6a9: a reply that was only the person's groceries in an aisle table was judged a done claim
    # and replaced with "I have not done that".
    from core import intent_arbiter

    prompt = intent_arbiter._done_claim_prompt("| Aisle | Items | | Dairy | milk |")
    assert "list, table or other text written out in the reply" in prompt
    assert "the person's own items sorted, grouped or rewritten there" in prompt
