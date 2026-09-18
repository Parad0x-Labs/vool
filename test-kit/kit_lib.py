"""Shared helpers for the conversation-truth kit.

Mirrors the seeding idiom of tests/test_a_new_question_never_inherits_the_previous_answer.py
so kit cases and the production test pack stay directly comparable.
"""
from __future__ import annotations

import uuid

from core.live_data_plan import build_live_data_plan
from core.runtime_continuity import remember_live_data_obligation

# The two turns of the recorded incident (request req:http:67536be2ab574acfb57e2de27fcc7a64).
GOLD_QUESTION = "what is gold price now?"
GOLD_ANSWER = (
    "Gold: USD 4,476.60 per troy ounce (24h change: -1.39%). "
    "Source: [Yahoo Finance](https://finance.yahoo.com/quote/GC=F), retrieved 2026-09-04 20:59 UTC."
)
VW_QUESTION = (
    "give me comparision review Vw passat vs vw golf, like how long they been maming it, "
    "total sale, most popuplar regions where sold, engines and so on"
)


def sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def context(session_id: str, *messages: tuple[str, str]) -> dict:
    return {
        "session_id": session_id,
        "runtime_session_id": session_id,
        "conversation_history": [{"role": role, "content": text} for role, text in messages],
    }


def gold_thread(label: str = "gold") -> str:
    session_id = sid(label)
    remember_live_data_obligation(
        session_id, operation="market_quote", slots=["Gold"],
        request_text=GOLD_QUESTION, absorbed_text=GOLD_QUESTION,
    )
    return session_id


def weather_thread(label: str, slots: list[str], request_text: str) -> str:
    session_id = sid(label)
    remember_live_data_obligation(
        session_id, operation="weather_lookup", slots=slots,
        request_text=request_text, absorbed_text=request_text,
    )
    return session_id


def plan_operations(text: str, source: dict) -> list[tuple[str, str]]:
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a", source_context=source)
    if plan is None:
        return []
    return [(task.to_dict().get("operation"), task.to_dict().get("entity")) for task in plan.subtasks]
