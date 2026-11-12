"""A plan for the person's own output is made, not looked up and not read from a calendar.

Measured 2026-11-12 on the frozen v0.8 candidate 28fc78dd through the served chat door (a scripted,
certified model; Web lookups off as on a fresh install): of eight planning asks only "what should i
post each day next week?" was answered.
- "what should i post this week?", "what should i cook this week?", "write me a post for this week",
  "give me a post idea for every day this week" and "draft a newsletter for this month" were refused
  with "That is not mentioned in the records I have from our conversations, so I am not going to
  state one." The period ("this week") read as a request for current world information.
- "plan my meals for this week" and "plan my posts for next week" were answered "No calendar is
  connected yet": the verb "plan" read as the noun "plans".

A period in such a request says when the person's own output happens. Output about what is happening
in the world ("write a post about the news this week") still needs it observed, and every other
freshness word and signal keeps its meaning.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for

OWN_OUTPUT_PLANS = (
    "what should i post this week?",
    "what should i cook this week?",
    "write me a post for this week",
    "give me a post idea for every day this week",
    "draft a newsletter for this month",
    "plan my meals for this week",
    "make me a meal plan for this week",
    "what should i put on instagram this week? i run a small pottery studio",
    "i make leather wallets by hand, what should i post this week?",
)

WORLD_ASKS = (
    "write a post about the news this week",
    "what are the top headlines this week",
    "whats on in vilnius this week?",
    "what should i invest in this week?",
    "what happened in the markets this week?",
    "whats the weather this week",
    "what should i watch this week",
    "write me a post about the latest iphone",
    "what should i post right now about the election",
    "what is the exchange rate this week",
)


@pytest.mark.parametrize("text", OWN_OUTPUT_PLANS)
def test_a_period_in_a_plan_for_own_output_asks_for_nothing_current(text):
    requirements = requirements_for(text)
    assert requirements.answer_mode == "DIRECT", requirements
    assert requirements.current_information_required is False, requirements
    assert requirements.external_evidence_required is False, requirements


@pytest.mark.parametrize("text", WORLD_ASKS)
def test_a_period_in_a_question_about_the_world_still_asks_for_current_information(text):
    requirements = requirements_for(text)
    assert requirements.current_information_required is True, requirements


def test_another_freshness_signal_keeps_its_say_beside_a_plan():
    requirements = requirements_for("what should i post this week about the new iphone release")
    assert requirements.current_information_required is True, requirements
    assert any("release" in code for code in requirements.reason_codes), requirements.reason_codes


@pytest.mark.parametrize("text", [
    "plan my meals for this week",
    "plan my posts for next week",
    "plan a week of posts for my bakery",
    "please plan my workouts for this week",
    "plan out my posts for this week",
])
def test_the_verb_plan_is_not_a_question_about_the_calendar(text, monkeypatch):
    from core.agent_runtime.agent import _no_calendar_reply

    monkeypatch.delenv("VOOL_CALENDAR_PROVIDER", raising=False)
    monkeypatch.setattr("core.operator.calendar_agenda.selected_sources", lambda: [])
    assert _no_calendar_reply(text) is None


@pytest.mark.parametrize("text", [
    "plan my week",
    "plan out my week",
    "plan my day",
    "plan my weekend",
    "what are my plans for this week?",
    "do i have any plans this week?",
    "what is my schedule this week",
])
def test_a_question_about_the_persons_own_time_still_says_no_calendar_is_connected(text, monkeypatch):
    from core.agent_runtime.agent import _no_calendar_reply

    monkeypatch.delenv("VOOL_CALENDAR_PROVIDER", raising=False)
    monkeypatch.setattr("core.operator.calendar_agenda.selected_sources", lambda: [])
    assert str(_no_calendar_reply(text) or "").startswith("No calendar is connected yet")
