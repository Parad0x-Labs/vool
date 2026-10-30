"""SERVED: a weather question whose live lookup failed is not answered "not in the records".

Measured on a fresh install with a model set up: "what's the weather in Paris today?" reached
the model, the live weather lookup failed, and the published reply was "That is not mentioned in
the records I have from our conversations, so I am not going to state one." The memory step had
found nothing in the new chat and left its typed records-absence marker on the turn, and the
publication gate read that marker alone as "this turn's support was the chat's records". A live
ask is not a records question: the reply has to say the current information could not be
obtained.

A real daemon (``apps.vool_api_server`` in its own process and home) on the OpenRouter-compatible
lane, pointed at the SYNTHETIC OpenAI-compatible service of ``tests/test_conversation_intent_served``.
The daemon's internet is a dead proxy, so the weather lookup fails the same way every run.

Pinned:
* weather with a model and a failed lookup: the model is called once and the reply is the
  current-information refusal, never the records notice;
* the failure path that must stay: a question about what the user said, in a chat with no records,
  still gets the records notice.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core.unsourced_current_claim import RECORDS_WITHDRAWAL_NOTICE
from tests._web_lookups import switch_web_lookups_on_in_home
from tests.test_conversation_intent_served import (
    OR_MODEL,
    OpenAICompatibleService,
    OpenRouterCompatibleDaemon,
    _answer_text,
    _or_certification,
    _or_completion,
    _session,
    _user_text,
)

DEAD = "http://127.0.0.1:9"
WEATHER = "what's the weather in Paris today?"
WEATHER_REPLY = "It is 18 C and sunny in Paris right now."
RECORDS_QUESTION = "How many tonnes does the yard currently hold according to my notes?"
RECORDS_REPLY = "Your notes say the yard currently holds 12,400 tonnes."
CURRENT_INFORMATION_LEAD = "I can't publish an answer to this: it needed current information"


class OfflineOpenRouterDaemon(OpenRouterCompatibleDaemon):
    """The OpenRouter-compatible daemon with its internet fenced: every lookup fails, loopback answers."""

    def env(self) -> dict[str, str]:
        env = super().env()
        loopback = "127.0.0.1,localhost,::1"
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            env[name] = DEAD
        env["NO_PROXY"] = env["no_proxy"] = loopback
        return env


def _scripted(body: dict) -> dict:
    probe = _or_certification(body)
    if probe is not None:
        return probe
    text = _user_text(body)
    if "according to my notes" in text:
        return _or_completion(body, content=RECORDS_REPLY)
    return _or_completion(body, content=WEATHER_REPLY)


@pytest.fixture(scope="module")
def lane(tmp_path_factory):
    service = OpenAICompatibleService().start()
    service.reply = _scripted
    daemon = OfflineOpenRouterDaemon(tmp_path_factory.mktemp("live-not-records") / "home", service)
    try:
        # A failed live lookup needs the lookup to be allowed first: Web lookups on, as the person sets it.
        switch_web_lookups_on_in_home(daemon.env())
        daemon.start()
        status, catalog = daemon.call("GET", "/api/cloud/models?provider=openrouter")
        assert status == 200, catalog
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": OR_MODEL, "provider": "openrouter", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, certified = daemon.call(
            "POST", "/api/model-tool-certification/run", {"provider_name": "openrouter-byok", "model_name": OR_MODEL}, timeout=420.0
        )
        assert status == 200, certified
        yield SimpleNamespace(service=service, daemon=daemon)
    finally:
        daemon.stop()
        service.stop()


def _turn(lane, text: str) -> tuple[str, list[dict], list[dict]]:
    session = _session(f"live-not-records-{uuid.uuid4()}")
    before = len(lane.service.inference_requests())
    status, answer = lane.daemon.chat(text, session_id=session)
    assert status == 200, answer
    arrived = lane.service.inference_requests()[before:]
    return _answer_text(answer), arrived, lane.daemon.events(session)


def test_weather_with_a_failed_lookup_is_not_answered_from_the_records(lane) -> None:
    reply, arrived, events = _turn(lane, WEATHER)
    assert [_user_text(body) for body in arrived] == [WEATHER], "the model is called once with the question"
    lookups = [event for event in events if event.get("event_type") == "live_data_plan_completed"]
    assert lookups and "0/1 succeeded" in str(lookups[-1].get("message")), lookups
    assert reply != RECORDS_WITHDRAWAL_NOTICE
    assert "records" not in reply.lower(), reply
    assert reply.startswith(CURRENT_INFORMATION_LEAD), reply
    assert WEATHER_REPLY not in reply, "an unobserved reading is never published"


def test_a_question_about_what_was_said_in_a_chat_with_no_records_keeps_the_records_notice(lane) -> None:
    reply, arrived, _events = _turn(lane, RECORDS_QUESTION)
    assert [_user_text(body) for body in arrived] == [RECORDS_QUESTION]
    assert reply == RECORDS_WITHDRAWAL_NOTICE, reply
    assert RECORDS_REPLY not in reply
