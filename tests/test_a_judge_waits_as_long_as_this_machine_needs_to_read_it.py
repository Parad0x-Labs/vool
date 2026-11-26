"""A judgement waits as long as this machine was measured to need for reading its prompt, within a ceiling.

CI Intel smoke on f7800a1ad (qwen3:8b on the runner's CPU): the runner read 5.4 prompt tokens a second. The Reminders
pick's own question, about 480 tokens with nothing cached, needed about 90 s against its 60 s (15 s scaled for a CPU);
it timed out, the wrong pick of a recall question stood, and its tool loop ran past the 900 s smoke step. Ollama's
answer counts the tokens it read and how long that took, and the next judgement's wait is sized from it.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, local_compute_budget

_SLOW = {"prompt_eval_count": 54, "prompt_eval_duration": 9_962_000_000, "eval_count": 7, "eval_duration": 2_497_000_000}
_FAST = {"prompt_eval_count": 600, "prompt_eval_duration": 300_000_000, "eval_count": 7, "eval_duration": 70_000_000}


@pytest.fixture
def calls(monkeypatch):
    import requests

    seen: list[float] = []
    bodies: list[dict] = []

    class Answer:
        def __init__(self, body: dict) -> None:
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"answer": "no"}'}, **self.body}

    def post(*_a, **k):
        seen.append(k["timeout"][1])
        return Answer(bodies.pop(0) if bodies else {})

    monkeypatch.setattr(local_compute_budget, "local_inference_is_cpu_only", lambda: True)
    monkeypatch.setattr(intent_arbiter, "arbiter_enabled", lambda: True)
    monkeypatch.setattr(intent_arbiter, "_JUDGE_SPEED", {})
    monkeypatch.setattr(requests, "post", post)
    return seen, bodies


def _ask(prompt: str, model: str = "qwen3:8b") -> None:
    intent_arbiter.arbitrate_followup("", "", "", prompt=prompt, model=model, patient=True,
                                      parse=lambda content: "no")


def test_a_slow_machine_gives_the_next_long_prompt_the_time_it_needs(calls):
    seen, bodies = calls
    bodies.append(_SLOW)
    _ask("Is the sky green? yes or no")
    long_prompt = "Which one is the message? " * 55  # about 1,430 characters, near 480 tokens
    _ask(long_prompt)
    budget = intent_arbiter._TIMEOUT_S * local_compute_budget.CPU_ONLY_FACTOR
    assert seen[0] == budget  # nothing measured yet: the budget as it was
    assert seen[1] > 90 > budget
    assert seen[1] <= intent_arbiter._JUDGE_LONGEST_WAIT_S


def test_a_fast_machine_keeps_the_budget(calls):
    seen, bodies = calls
    bodies.append(_FAST)
    _ask("Is the sky green? yes or no")
    _ask("Which one is the message? " * 55)
    assert seen[1] == intent_arbiter._TIMEOUT_S * local_compute_budget.CPU_ONLY_FACTOR


def test_the_wait_never_passes_the_ceiling(calls):
    seen, bodies = calls
    bodies.append(_SLOW)
    _ask("Is the sky green? yes or no")
    _ask("x" * 20_000)
    assert seen[1] == intent_arbiter._JUDGE_LONGEST_WAIT_S


def test_a_prompt_served_from_the_cache_measures_nothing(calls):
    seen, bodies = calls
    bodies.append({"prompt_eval_count": 3, "prompt_eval_duration": 9_000_000_000, "eval_count": 7,
                   "eval_duration": 2_000_000_000})
    _ask("Is the sky green? yes or no")
    _ask("Which one is the message? " * 55)
    assert seen[1] == intent_arbiter._TIMEOUT_S * local_compute_budget.CPU_ONLY_FACTOR


def test_each_model_keeps_its_own_speed(calls):
    seen, bodies = calls
    bodies.append(_SLOW)
    _ask("Is the sky green? yes or no", model="qwen3:8b")
    _ask("Which one is the message? " * 55, model="gemma3:4b")
    assert seen[1] == intent_arbiter._TIMEOUT_S * local_compute_budget.CPU_ONLY_FACTOR
