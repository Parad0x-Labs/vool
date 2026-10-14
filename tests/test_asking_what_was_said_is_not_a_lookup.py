"""Asking what was said in this conversation is answered from the conversation, not a live lane.

Measured on 9adff83 with the pack 2 patches applied (2026-10-06), with no model in the loop:

* "what did I tell you about the latest Bitcoin news?" was classified `research` and the turn
  answered from the live web lane ("I checked the live web lane for ...").
* "What did I tell you about the weather at my wedding?" was served by the weather lane as the
  weather at a place called "My Wedding".
* "remind me what I told you about my landlord" was parsed as a reminder to schedule.
* Asked to pick a category for a recollection the rules left `unknown`, the local model fallback
  could choose `research`, which buys the web lane and a paid arm.

A lookup asked beside the recollection keeps its lane. The phrasings below are written for this file.
"""
from __future__ import annotations

import pytest

import core.task_router as task_router
from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.execution_requirements import classify_requirements
from core.operator.parser import parse_operator_action_intent
from core.task_router import classify

RECOLLECTIONS = (
    "what did I tell you about the latest Bitcoin news?",
    "What did I tell you about the current price of my Tesla shares?",
    "What did I say about the news this morning?",
    "what did I tell you about my research project?",
    "What did I tell you about the weather at my wedding?",
    "what was it I told you about the weather in Oslo?",
    "Did I ever tell you about my trip to Japan?",
    "Earlier I told you something about Kubernetes, what was it?",
    "do you remember what I said about the stock market today?",
    "search our chat for what I said about the Lisbon trip",
)

LOOKUPS = (
    "what is the latest news about Bitcoin?",
    "what's the Bitcoin price right now?",
    "what is the weather in Oslo right now?",
    "who is the current CEO of OpenAI?",
    "what happened in the news today?",
    "search the web for what I told you about the Tesla stock",
    "I said, what's the weather in Oslo right now?",
    "I told you I'm flying to Berlin tomorrow. What is the weather in Berlin tomorrow?",
    "what did I tell you about Lisbon, and what's the latest news from Lisbon?",
)


def _class(text: str) -> str:
    return classify(text, {"skip_model_classification": True})["task_class"]


@pytest.mark.parametrize("text", RECOLLECTIONS)
def test_a_recollection_is_not_classified_as_research(text: str) -> None:
    assert _class(text) == "chat_conversation", text


@pytest.mark.parametrize("text", RECOLLECTIONS)
def test_a_recollection_needs_no_current_information(text: str) -> None:
    req = classify_requirements(text)
    assert not req.current_information_required, (text, req.reason_codes)
    assert req.allowed_toolsets == (), (text, req.allowed_toolsets)


@pytest.mark.parametrize("text", RECOLLECTIONS)
def test_the_live_info_lane_does_not_claim_a_recollection(text: str) -> None:
    assert live_info_mode(None, text, interpretation=None) == "", text


def test_a_recollection_does_not_reach_the_model_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_router, "_classify_via_model", lambda _text: "research")
    verdict = classify("what did I tell you about Bitcoin?", {"source_context": {"surface": "desktop"}})
    assert verdict["task_class"] == "chat_conversation"


@pytest.mark.parametrize("text", LOOKUPS)
def test_a_lookup_keeps_its_live_lane(text: str) -> None:
    assert classify_requirements(text).current_information_required, text


@pytest.mark.parametrize(
    "text",
    (
        "what is the weather in Oslo right now?",
        "I told you I'm flying to Berlin tomorrow. What is the weather in Berlin tomorrow?",
    ),
)
def test_a_weather_lookup_beside_a_recollection_keeps_the_weather_lane(text: str) -> None:
    assert live_info_mode(None, text, interpretation=None) == "weather", text


def test_asking_to_be_reminded_what_was_said_is_not_a_reminder() -> None:
    assert parse_operator_action_intent("remind me what I told you about my landlord") is None


@pytest.mark.parametrize(
    "text",
    (
        "remind me to call my sister tomorrow at 9",
        "remind me tomorrow at 9 what I told you about the rent",
    ),
)
def test_a_reminder_with_a_time_is_still_a_reminder(text: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "schedule_reminder", text
