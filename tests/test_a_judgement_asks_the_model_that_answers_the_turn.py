"""A judgement of a turn is asked of the model that answers the turn, never of whichever larger model is loaded.

Gate iMac on a5403e091: another app had qwen3.5:9b loaded in the shared Ollama. The keep judge took the largest
loaded model, so a qwen3:8b chat's judgements went to qwen3.5:9b, and "my cousin Darius has a cat named Murka" was
answered "Okay, I won't keep that." The keep-wanted question asked whether the person wanted it KEPT, and a fact told
plainly, with no ask either way, read as "no"."""
from __future__ import annotations

import pytest

from core import intent_arbiter


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("VOOL_KEEP_JUDGE_MODEL", raising=False)
    monkeypatch.setattr(intent_arbiter, "_local_models_disabled", lambda: False)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _bind(turn_model: str, answers: list[str], asked: list[str]):
    def judge(prompt: str) -> str:
        asked.append(prompt)
        return answers.pop(0)

    judge.turn_model = lambda: (turn_model, "general")
    intent_arbiter.bind_turn_judge({}, judge)


def _loaded(monkeypatch, *names: str) -> None:
    monkeypatch.setenv("VOOL_LOADED_OLLAMA_MODELS", ",".join(names))


def test_the_turns_own_loaded_model_is_asked_not_the_largest_loaded(monkeypatch):
    _loaded(monkeypatch, "qwen3.5:9b", "qwen3:8b")
    _bind("qwen3:8b", [], [])
    assert intent_arbiter._keep_judge_model() == "qwen3.5:9b"  # what the machine has, which is not the turn's
    assert intent_arbiter._judge_model_for({}) == "qwen3:8b"


def test_a_turn_model_not_loaded_yet_is_asked_through_the_turn_judge_not_another_loaded_one(monkeypatch):
    _loaded(monkeypatch, "qwen3.5:9b")
    asked: list[str] = []
    _bind("qwen3:8b", ['{"answer": "no"}'], asked)
    assert intent_arbiter._judge_model_for({}) == ""
    calls: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", lambda *a, **k: calls.append(k.get("model")))
    assert intent_arbiter.arbitrate_keep_wanted("my cousin Darius has a cat named Murka", source_context={}) == "yes"
    assert calls == [] and len(asked) == 1


def test_a_tag_written_without_latest_is_the_same_model(monkeypatch):
    _loaded(monkeypatch, "llama3.1:latest")
    _bind("llama3.1", [], [])
    assert intent_arbiter._judge_model_for({}) == "llama3.1"


def test_a_turn_with_no_judge_of_its_own_keeps_the_loaded_model(monkeypatch):
    _loaded(monkeypatch, "qwen3:8b")
    intent_arbiter.bind_turn_judge({}, None)
    assert intent_arbiter._judge_model_for({}) == "qwen3:8b"


def test_an_owner_pinned_judge_model_still_wins(monkeypatch):
    _loaded(monkeypatch, "qwen3:8b")
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:14b")
    _bind("qwen3:8b", [], [])
    assert intent_arbiter._judge_model_for({}) == "qwen3:14b"


@pytest.mark.parametrize(("refused", "wanted"), [('{"answer": "no"}', "yes"), ('{"answer": "yes"}', "no"),
                                                 ("maybe", None)])
def test_keep_wanted_asks_only_whether_the_keep_was_refused(monkeypatch, refused, wanted):
    _loaded(monkeypatch, "qwen3:8b")
    _bind("qwen3:8b", [], [])
    seen: dict[str, object] = {}

    def followup(*_a, **k):
        seen.update(k)
        return k["parse"](refused)

    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", followup)
    assert intent_arbiter.arbitrate_keep_wanted("mi hermana vive en Kaunas", source_context={}) == wanted
    assert seen["model"] == "qwen3:8b"
    prompt = str(seen["prompt"])
    assert "NOT to remember" in prompt and "without asking either way" in prompt
