"""A two-sentence lesson is saved whole: the fact AND the instruction that follows it.

Memory capture kept "I live in X." and silently dropped "Always give me Y.": its always/never patterns only
accepted the verbs answer, respond, use, remember and be, so "give", "show", "write", "end", "add" and every
other instruction verb fell through. The instruction is the half that matters in a later chat. Authored wording.
"""
from __future__ import annotations

import pytest

from core.memory.learning import extract_memory_candidates


def _texts(text: str) -> list[str]:
    return [c["text"] for c in extract_memory_candidates(text)]


def test_the_fact_and_the_instruction_are_both_kept():
    texts = _texts("I live in Spain. Always give me temperatures in Celsius.")
    assert any("live in Spain" in t for t in texts)
    assert any("temperatures in Celsius" in t for t in texts)


@pytest.mark.parametrize("text", [
    "Never show me prices without the currency.",
    "From now on write dates as day, month, year.",
    "Always end with a one-line summary.",
])
def test_any_standing_instruction_is_kept_as_an_instruction(text):
    candidates = extract_memory_candidates(text)
    assert [c["category"] for c in candidates] == ["instruction"]


@pytest.mark.parametrize("text", [
    "Just this once, give me temperatures in Fahrenheit.",
    "My uncle always gives me socks.",
])
def test_a_one_off_or_someone_elses_habit_is_not_an_instruction(text):
    assert all(c["category"] != "instruction" for c in extract_memory_candidates(text))


@pytest.mark.parametrize("text", [
    "My sister always wants you to answer in French.",
    "My boss never wants me to use bullet points.",
    "Our CFO always prefers totals in euros.",
    "My coach always gives me the plan as a table.",
])
def test_someone_elses_habit_is_never_the_owners_instruction_or_preference(text):
    kinds = [c["category"] for c in extract_memory_candidates(text)]
    assert "instruction" not in kinds and "preference" not in kinds, kinds
