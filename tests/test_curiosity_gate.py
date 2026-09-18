"""Narrow curiosity gate: skip a live web search before high-confidence local/memory/recall/
personal answers; still roam for explicit search/latest/public and genuinely-external questions.
"""
from __future__ import annotations

import pytest

from core.curiosity_gate import (
    looks_like_local_recall,
    should_skip_curiosity_for_local_answer,
    wants_external_research,
    wants_no_research,
)


def _skip(text, *, mission=False, memory=False):
    return should_skip_curiosity_for_local_answer(
        text, active_mission_present=mission, local_memory_hit=memory
    )


@pytest.mark.parametrize("text,mission,memory", [
    # personal / private (family, PII) — skip regardless of local hit
    ("What is my sister's middle name?", False, False),
    ("what is my home address?", False, False),
    # exact recall whose value active mission holds
    ("My launch spend cap is exactly 0.037 SOL. In one short sentence, what is my launch spend cap?", True, False),
    ("What is my launch cap?", True, False),
    # "what is the current mission?" with an active mission present
    ("Active mission: cap 0.05 SOL, domain alice.null. What is the current mission?", True, False),
    # a recall answerable from local memory (retrieval hit)
    ("What is my launch cap?", False, True),
    # explicit "from your own knowledge / do not cite" — answer locally
    ("Explain what a mutex is in one sentence from your own knowledge. Do not cite sources.", False, False),
])
def test_skips_curiosity_for_high_confidence_local_answers(text, mission, memory):
    assert _skip(text, mission=mission, memory=memory) is True


@pytest.mark.parametrize("text", [
    "What is the latest news on Python asyncio?",
    "search the web for the current SOL price",
    "look it up online",
    "what's the most recent version of Python?",
    # a "what is my ..." recall with NO local value and not private -> still allowed to research
    "What is my launch cap?",
    # a general public-knowledge question with no local hit -> allowed
    "how does the TCP three-way handshake work?",
])
def test_does_not_skip_curiosity_when_research_is_warranted(text):
    assert _skip(text) is False


def test_marker_helpers():
    assert wants_external_research("what is the latest news on X") is True
    assert wants_external_research("what is the current mission?") is False  # "current mission" != research
    assert wants_no_research("explain X from your own knowledge, do not cite sources") is True
    assert looks_like_local_recall("what is my launch cap?") is True
    assert looks_like_local_recall("how does TCP work?") is False
