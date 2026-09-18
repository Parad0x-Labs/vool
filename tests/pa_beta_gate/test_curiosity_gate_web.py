"""pa_beta_gate — curiosity/web-gate coverage for correction / style / long-distraction turns
(Codex round 4, fix C). A local-answerable turn must not roam the web unless the user explicitly
asks for search / latest / current-external. Offline, deterministic (no live model).
"""
from __future__ import annotations

import pytest

from core.curiosity_gate import (
    looks_like_correction,
    looks_like_style_task,
    should_skip_curiosity_for_local_answer,
)

pytestmark = [pytest.mark.pa_beta]


def _skip(text, *, mission=False, memory=False):
    return should_skip_curiosity_for_local_answer(text, active_mission_present=mission, local_memory_hit=memory)


@pytest.mark.parametrize("text,mission", [
    # the three corpus prompts that were still firing web calls
    ("Wrong: the cap is not 0.037 SOL anymore. It is 0.05 SOL. Answer only with the current cap.", False),
    ("Answer in short Telegram dev style: alpha vs beta readiness in one sentence.", False),
    ("Remember this mission: cap 0.037 SOL. Update mission: cap 0.05 SOL, domain parad0x.null. What is the current mission?", True),
    # long-distraction must skip even if the mission flag is momentarily absent for its session
    ("Remember this mission: cap 0.037 SOL. Update mission: cap 0.05 SOL. What is the current mission?", False),
    ("First remember: launch cap is 0.037 SOL. Update: launch cap is now 0.05 SOL. What is the current launch cap?", False),
    ("update: my cap is 0.05, what is my cap", False),
    ("Answer in short Telegram dev style: what is Web0 in the VOOL context?", False),
])
def test_local_correction_style_and_distraction_skip_web(text, mission):
    assert _skip(text, mission=mission) is True


@pytest.mark.parametrize("text", [
    "what is the latest Python version?",
    "what is the newest Ollama release?",
    "search the web for the SOL price",
    "Answer in Telegram style: what is the latest Solana news?",
    "browse for current events",
])
def test_explicit_or_genuine_external_still_researches(text):
    assert _skip(text) is False


@pytest.mark.parametrize("text", [
    # must NOT over-skip: an external ask that only looks superficially like a correction/update
    "I updated the readme, whats the weather today",
    "actually is bitcoin up today",
])
def test_false_positive_guards_do_not_over_skip(text):
    assert _skip(text) is False


def test_helper_precision():
    assert looks_like_style_task("Answer in short Telegram dev style: X") is True
    assert looks_like_style_task("tell me about telegram") is False
    assert looks_like_correction("update: cap is now 0.05") is True
    assert looks_like_correction("I updated the readme") is False
