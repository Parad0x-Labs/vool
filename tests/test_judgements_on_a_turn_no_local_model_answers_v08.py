"""The memory judgements on a turn no local model answers go to the model that answers the turn.

Measured on the real app on the cloud route (the fix mission, 2026-11-18): with local models off, a fact said in
passing was not kept, a keep ask in other words was not kept and a question in other words was not found, because
every judgement asks a loaded local model and there was none.

All names and values are invented for this contract.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import intent_arbiter
from core.agent_runtime import turn_judge

SAID = "the spare trailer hitch lives in the blue crate under the workbench."


@pytest.fixture(autouse=True)
def _no_judge_left_over():
    intent_arbiter.bind_turn_judge({}, None)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _all_eight(context: dict) -> list[str | None]:
    return [
        intent_arbiter.arbitrate_keep_ask("tuck this away", SAID, source_context=context),
        intent_arbiter.arbitrate_restore("put it back", SAID, SAID, source_context=context),
        intent_arbiter.arbitrate_answered("where is the hitch?", SAID, source_context=context),
        intent_arbiter.arbitrate_meaning("where is the hitch?", SAID, source_context=context),
        intent_arbiter.arbitrate_same_thing("where is the hitch?", SAID, source_context=context),
        intent_arbiter.arbitrate_lasting_fact(SAID, source_context=context),
        intent_arbiter.arbitrate_standing_state(SAID, source_context=context),
        intent_arbiter.arbitrate_told_as_fact(SAID, source_context=context),
    ]


def test_with_no_local_judge_and_no_judge_bound_nobody_judges(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    assert _all_eight({}) == [None] * 8


def test_every_judgement_goes_to_the_judge_bound_for_the_turn(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    asked: list[str] = []
    intent_arbiter.bind_turn_judge({}, lambda prompt: asked.append(prompt) or '{"answer": "yes"}')
    assert _all_eight({}) == ["yes"] * 8
    assert len(asked) == 8 and all(SAID in prompt for prompt in asked)
    # The same prompts a local judge is given: nothing about the question changes with who answers it.
    assert asked[5] == intent_arbiter._lasting_fact_prompt(SAID) and asked[4] == intent_arbiter._same_thing_prompt("where is the hitch?", SAID)


def test_a_local_judge_is_asked_first_and_the_bound_judge_is_left_alone(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", lambda *a, **k: "no")
    intent_arbiter.bind_turn_judge({}, lambda prompt: pytest.fail("asked the turn's judge with a local judge loaded"))
    assert _all_eight({}) == ["no"] * 8


@pytest.mark.parametrize("answer", ["I am not sure.", "", None])
def test_an_unreadable_answer_or_a_failed_call_decides_nothing(monkeypatch, answer):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")

    def judge(prompt: str) -> str:
        if answer is None:
            raise RuntimeError("paid_call_refused:cap")
        return answer

    intent_arbiter.bind_turn_judge({}, judge)
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context={}) is None


def test_binding_with_none_takes_the_judge_of_the_turn_before_away(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    intent_arbiter.bind_turn_judge({}, lambda prompt: '{"answer": "yes"}')
    intent_arbiter.bind_turn_judge({}, None)
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context={}) is None


# --- which turn gets a judge ----------------------------------------------------------------------------------


@pytest.fixture
def cloud_turn(monkeypatch):
    """A turn the owner's own cloud model answers, local models off; each condition can be switched."""
    state = {"local_off": True, "owner_local": True, "speaks": True, "private": False, "manifest": object(), "sent": []}
    monkeypatch.setattr(intent_arbiter, "_local_models_disabled", lambda: state["local_off"])
    monkeypatch.setattr("core.request_trust.request_is_owner_local", lambda context: state["owner_local"])
    monkeypatch.setattr("core.request_trust.turn_speaks_for_owner", lambda context, session: state["speaks"])
    monkeypatch.setattr("core.private_chat.turn_is_private", lambda context, session: state["private"])
    monkeypatch.setattr("core.clean_loop.brain.resolve_cloud_manifest", lambda router, context: state["manifest"])
    monkeypatch.setattr(
        "core.clean_loop.brain.make_brain",
        lambda router, manifest, context: lambda messages: state["sent"].append(messages) or '{"answer": "yes"}',
    )
    monkeypatch.delenv(turn_judge.SWITCH_ENV, raising=False)
    return state


AGENT = SimpleNamespace(memory_router=object())


def test_the_owners_cloud_turn_gets_a_judge_that_asks_the_answering_model(cloud_turn, monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    context: dict = {}
    turn_judge.bind_for_turn(AGENT, context, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context=context) == "yes"
    assert cloud_turn["sent"] == [[{"role": "user", "content": intent_arbiter._lasting_fact_prompt(SAID)}]]


@pytest.mark.parametrize("change", [
    {"local_off": False},   # a machine with local models keeps its local judge, or none
    {"owner_local": False},  # a remote or phone turn never spends the owner's key here
    {"speaks": False},      # a turn that is not the owner's
    {"private": True},      # a private chat sends nothing of itself anywhere new
    {"manifest": None},     # no cloud model pinned for the turn or saved for Auto
])
def test_any_other_turn_gets_no_judge(cloud_turn, monkeypatch, change):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    cloud_turn.update(change)
    context: dict = {}
    turn_judge.bind_for_turn(AGENT, context, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context=context) is None
    assert cloud_turn["sent"] == []


@pytest.mark.parametrize("value", ["0", "false", "off", "never"])
def test_the_switch_turns_it_off_and_an_unreadable_value_is_off(cloud_turn, monkeypatch, value):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    monkeypatch.setenv(turn_judge.SWITCH_ENV, value)
    turn_judge.bind_for_turn(AGENT, {}, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context={}) is None and cloud_turn["sent"] == []


def test_a_turn_with_no_router_or_a_broken_lookup_is_the_turn_it_always_was(cloud_turn, monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    turn_judge.bind_for_turn(SimpleNamespace(), {}, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context={}) is None
    monkeypatch.setattr("core.clean_loop.brain.resolve_cloud_manifest", lambda router, context: 1 / 0)
    turn_judge.bind_for_turn(AGENT, {}, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context={}) is None


@pytest.fixture
def local_turn(monkeypatch):
    """A turn a local model answers, with no model loaded yet (first turn, or after Ollama unloaded it when idle)."""
    state = {"owner_local": True, "speaks": True, "manifest": object(), "sent": []}
    monkeypatch.setattr(intent_arbiter, "_local_models_disabled", lambda: False)
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    monkeypatch.setattr("core.request_trust.request_is_owner_local", lambda context: state["owner_local"])
    monkeypatch.setattr("core.request_trust.turn_speaks_for_owner", lambda context, session: state["speaks"])
    monkeypatch.setattr("core.clean_loop.brain.resolve_local_manifest", lambda router, context: state["manifest"])
    monkeypatch.setattr(
        "core.clean_loop.brain.make_brain",
        lambda router, manifest, context: lambda messages: state["sent"].append(messages) or (
            '{"answer": [1]}' if "Reminders:" in messages[-1]["content"] else '{"answer": "yes"}'),
    )
    return state


def test_with_no_model_loaded_yet_the_local_model_that_answers_the_turn_judges(local_turn):
    # Blind audit of 58f60bbf3: with local models on and none loaded, every judgement was None in a few ms, so a
    # reminder asked in Lithuanian got no tool. The judge is now the local model the turn loads to answer anyway.
    context: dict = {}
    turn_judge.bind_for_turn(AGENT, context, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context=context) == "yes"
    assert local_turn["sent"] == [[{"role": "user", "content": intent_arbiter._lasting_fact_prompt(SAID)}]]
    asked = intent_arbiter.arbitrate_plugin_ask("Primink man rytoj 9 val. paskambinti", ["Reminders: ..."],
                                                source_context=context)
    assert asked == (1,) and len(local_turn["sent"]) == 2


@pytest.mark.parametrize("change", [{"owner_local": False}, {"speaks": False}, {"manifest": None}])
def test_a_remote_or_agent_turn_or_no_local_answering_model_gets_no_local_judge(local_turn, change):
    local_turn.update(change)
    context: dict = {}
    turn_judge.bind_for_turn(AGENT, context, "chat-1")
    assert intent_arbiter.arbitrate_lasting_fact(SAID, source_context=context) is None
    assert local_turn["sent"] == []
