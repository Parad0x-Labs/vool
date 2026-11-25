"""A judge bound on a thread for one request is never used by another request on that thread.

CI on 95fae99a0: a pooled thread kept the judge of a turn it had served, and a later call outside any turn (a tool
offer assembled with the network off) asked that turn's model: a lookup of 127.0.0.1.
"""
from __future__ import annotations

from core import intent_arbiter
from core.semantic.semantic_admissions import bound_request_context


def _judge(asked: list[str]):
    return lambda prompt: asked.append(prompt) or '{"answer": "yes"}'


def test_a_judge_bound_for_one_request_is_not_used_by_the_next() -> None:
    asked: list[str] = []
    with bound_request_context("req-a"):
        intent_arbiter.bind_turn_judge({}, _judge(asked))
        assert intent_arbiter._bound_judge({}) is not None
    with bound_request_context("req-b"):
        assert intent_arbiter._bound_judge({}) is None
    assert intent_arbiter._bound_judge({}) is None
    intent_arbiter.bind_turn_judge({}, None)


def test_within_its_own_request_the_judge_answers() -> None:
    asked: list[str] = []
    with bound_request_context("req-c"):
        intent_arbiter.bind_turn_judge({}, _judge(asked))
        assert intent_arbiter._judged_for_the_turn("is this a test?", {}) == "yes"
        intent_arbiter.bind_turn_judge({}, None)
    assert asked == ["is this a test?"]
