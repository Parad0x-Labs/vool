"""A number ending in "b" in the user's words is a model size only when it reads as one.

Measured 2026-10-19 (browser job form fixture): the random street address "18620b" was read as a
request for an 18,620-billion-parameter model, the turn became an explicit heavy demand no lane could
serve, and the model was never called. "Flat 24b" or "unit 30b" did the same. The served test drives
the real daemon with an ordinary paid-lane pin (a synthetic local OpenAI-compatible service stands in
for the provider; nothing is paid). Model-size requests in the user's words keep working.
"""
from __future__ import annotations

import json
import uuid

import pytest

from core.local_inference_autopilot import _explicit_heavy_requested, _requested_heavy_marker
from tests.test_conversation_intent_served import (  # openrouter: the module-scoped served daemon
    _answer_text,
    _or_certification,
    _or_completion,
    _session,
    _user_text,
    openrouter,
)


@pytest.mark.parametrize(
    "text",
    [
        "Please deliver the parcel to 18620b Harbour Road and confirm the slot.",
        "My flat is 24b, the buzzer is broken, write a note for the courier.",
        "Unit 30b keeps the spare keys; who should I ask for them?",
        "Flat 24b — which model of kettle should I buy?",
    ],
)
def test_addresses_and_unit_numbers_are_not_heavy_demands(text):
    assert _explicit_heavy_requested(user_text=text, source_context={}) is False
    assert _requested_heavy_marker(user_text=text, source_context={}) is None


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        ("run this on a 70b model please", "70b"),
        ("use the 35b heavy local lane for this", "35b"),
        ("a 72 B parameter model would be better", "72b"),
        ("answer with qwen3 32b", "32b"),
        ("switch to llama-3.1-405b for this one", "405b"),
        ("try mixtral-8x22b", "176b"),
    ],
)
def test_model_size_requests_still_count(text, marker):
    assert _explicit_heavy_requested(user_text=text, source_context={}) is True
    assert _requested_heavy_marker(user_text=text, source_context={}) == marker


def test_an_address_turn_reaches_the_pinned_model(openrouter) -> None:
    daemon, service = openrouter.daemon, openrouter.service
    reply = "Note for the courier: please leave the parcel at 18620b Harbour Road with the neighbour."
    service.reply = lambda body: _or_certification(body) or _or_completion(body, content=reply)
    session = _session(f"address-{uuid.uuid4()}")
    before = len(service.inference_requests())
    status, answer = daemon.chat(
        "Write a two-line note asking the courier to leave my parcel at 18620b Harbour Road with the neighbour.",
        session_id=session,
    )
    turn_requests = [
        body for body in service.inference_requests()[before:]
        if "18620b Harbour Road" in _user_text(body) and "sealed local diagnostic" not in json.dumps(body.get("messages") or [])
    ]
    assert status == 200
    assert len(turn_requests) == 1, _answer_text(answer)
    assert _answer_text(answer) == reply
