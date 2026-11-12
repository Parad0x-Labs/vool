"""Planning asks through the served chat door: answered, never refused, never "Could not be answered".

A real daemon from this checkout, the real /api/chat door, and a scripted model certified by the
production probe. Web lookups stay off, as on a fresh install. The scripted planner makes the measured
split for every context wording, so the runtime, not the model, has to keep the context sentence out
of the plan. The scripted answer is a fixed seven-day plan; the tests read what the door delivers.

Measured before this change on the frozen v0.8 candidate 28fc78dd with this same rig:
- case X11 and its new wordings: no "Could not be answered" any more (the conductor drops the context
  clause since Oct 29), but each turn still spent a planner call to learn it was one request;
- "... this week" and "... this month" plans were refused ("That is not mentioned in the records I
  have from our conversations ..." or "Web lookups are off, so I can't look this up online"), and
  "plan my meals for this week" / "plan my posts for next week" got "No calendar is connected yet".
"""
from __future__ import annotations

import json
import uuid
from urllib.request import Request, urlopen

import pytest

import tests._reader_served_rig as rig

PLANNER_MARK = "You split a user's message into the separate requests"
POST_PLAN = (
    "Monday: a photo of a fresh batch on the drying rack. Tuesday: how one piece is made, start to "
    "finish. Wednesday: a customer favourite and why people like it. Thursday: a short behind-the-"
    "scenes clip. Friday: where to find the stall this weekend. Saturday: a market-day stall photo. "
    "Sunday: thank the people who stopped by."
)

#: (what the person typed, the request, the context sentence the planner splits off). The first is
#: case X11 as measured on qwen3:8b; the rest are new wordings.
CONTEXT_CASES = (
    ("what should i post each day next week? i sell handmade candles at markets",
     "what should i post each day next week?", "i sell handmade candles at markets"),
    ("i sell handmade candles at markets. what should i post each day next week?",
     "what should i post each day next week?", "i sell handmade candles at markets."),
    ("what should i put on instagram this week? i run a small pottery studio",
     "what should i put on instagram this week?", "i run a small pottery studio"),
    ("give me a post idea for every day next week. we bake sourdough for a farmers market",
     "give me a post idea for every day next week.", "we bake sourdough for a farmers market"),
    ("i make leather wallets by hand, what should i post this week?",
     "what should i post this week?", "i make leather wallets by hand"),
    ("any ideas what to post daily next week? my shop sells vintage records",
     "any ideas what to post daily next week?", "my shop sells vintage records"),
)

PLANS = (
    "what should i post this week?",
    "what should i cook this week?",
    "write me a post for this week",
    "give me a post idea for every day this week",
    "draft a newsletter for this month",
    "plan my meals for this week",
    "plan my posts for next week",
    "what should i post each day next week?",
)


def _reply(body: dict) -> str:
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    everything = " ".join(str(m.get("content") or "") for m in messages)
    users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
    last_user = users[-1].strip() if users else ""
    if PLANNER_MARK in everything:
        for typed, request, context in CONTEXT_CASES:
            if typed == last_user:
                return json.dumps([
                    {"request": request, "operation": "factual_explanation", "depends_on": []},
                    {"request": context, "operation": "factual_explanation", "depends_on": []},
                ])
        return "[]"
    if AMBIGUITY_SYSTEM_PROMPT[:80] in everything:
        return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
    if "memory extraction agent" in everything:
        return "[]"
    return POST_PLAN


@pytest.fixture(scope="module")
def drive(tmp_path_factory):
    provider = rig.CapturingProvider(default=POST_PLAN, reply_fn=_reply)
    provider.__enter__()
    daemon = rig.ServedDaemon(tmp_path_factory.mktemp("planning-served") / "home", provider=provider)
    try:
        daemon.start()
        cert = daemon.certify()
        assert cert.get("state") == "verified", cert
        yield daemon, provider
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def _turn(daemon, text: str) -> str:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
            "session_id": rig.canonical_session("planning-" + uuid.uuid4().hex[:8]), "turn_id": uuid.uuid4().hex,
            "model": daemon.model, "model_selection": "sticky", "mode": "ask"}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    parts: list[str] = []
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            try:
                event = json.loads(line) if line else {}
            except ValueError:
                continue
            message = event.get("message") if isinstance(event, dict) else None
            if isinstance(message, dict) and message.get("content"):
                parts.append(str(message["content"]))
    return "".join(parts)


def _answered(answer: str) -> None:
    lowered = answer.lower()
    assert "Saturday: a market-day stall photo" in answer, answer
    for refusal in ("could not be answered", "answer path", "not mentioned in the records",
                    "web lookups are off", "no calendar is connected"):
        assert refusal not in lowered, answer


@pytest.mark.parametrize(("typed", "request_text", "context"), CONTEXT_CASES,
                         ids=["x11-measured", "context-first", "pottery", "sourdough", "comma-joined", "records"])
def test_a_context_sentence_is_never_unanswered_and_buys_no_planner_call(drive, typed, request_text, context):
    daemon, provider = drive
    provider.reset()
    _answered(_turn(daemon, typed))
    assert [p for p in provider.payloads() if PLANNER_MARK in p] == []


@pytest.mark.parametrize("asked", PLANS)
def test_a_plan_for_the_persons_own_output_is_answered(drive, asked):
    daemon, provider = drive
    provider.reset()
    _answered(_turn(daemon, asked))
