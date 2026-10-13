"""SERVED proof of "use the model's own tools" on a real daemon.

The daemon (``apps.vool_api_server`` in its own process and home) runs this checkout's production
routing, front door, reservation and adapter. Only the provider is substituted: the OpenRouter
lane is pointed at the SYNTHETIC OpenAI-compatible service of ``tests/test_conversation_intent_
served.py``, which records every request whole and answers from a script. Nothing here reaches a
real provider, so what it proves is VOOL's side of the contract: what VOOL sends, what it keeps
back, what it charges against the caps and what reaches chat. It does not prove a live provider's
search quality.

One chat, in order, the way an owner would use it:

1. Off (the default): a pinned cloud model gets no provider tool, and a live price is answered by
   VOOL's own lane, never sent to the model.
2. "use the model's own tools" in chat switches it on; Settings reads the same switch back.
3. On: the same kind of question reaches the model with the provider's web search and nothing
   else, the provider's sources reach chat, and the paid call is reserved and settled.
4. On, with a payment request: still only the search tool is sent; no wallet or payment tool.
5. The provider refuses the search request: the turn fails visibly and is not retried.
6. "use VOOL's tools" switches it off and the provider tool disappears from the next request.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_conversation_intent_served import (
    OR_LANE_ID,
    OR_MODEL,
    OpenAICompatibleService,
    OpenRouterCompatibleDaemon,
    _events_of,
    _or_certification,
    _or_completion,
    _user_text,
)
from tests.usepod.test_usepod_served_flow import _answer_text, _session

SEARCH_TOOL = {"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 5, "max_uses": 2}}
SOURCE_URL = "https://example.org/rust-compiler-performance-2026"


def _searched_reply(body: dict[str, Any]) -> dict[str, Any]:
    probe = _or_certification(body)
    if probe is not None:
        return probe
    completion = _or_completion(body, content="Compile times fell about 12% this year, mostly from parallel front-end work.")
    completion["choices"][0]["message"]["annotations"] = [
        {
            "type": "url_citation",
            "url_citation": {
                "url": SOURCE_URL,
                "title": "Rust compiler performance, 2026",
                "content": "Rust compile times fell about 12% this year, mostly from parallel front-end work.",
                "start_index": 0,
                "end_index": 10,
            },
        }
    ]
    completion["usage"]["cost"] = 0.0061
    return completion


def _plain_reply(body: dict[str, Any]) -> dict[str, Any]:
    return _or_certification(body) or _or_completion(body, content="A plain answer from the pinned model.")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    service = OpenAICompatibleService().start()
    daemon = OpenRouterCompatibleDaemon(tmp_path_factory.mktemp("own-tools") / "home", service)
    try:
        daemon.start()
        status, catalog = daemon.call("GET", "/api/cloud/models?provider=openrouter")
        assert status == 200, catalog
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": OR_MODEL, "provider": "openrouter", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, certified = daemon.call(
            "POST", "/api/model-tool-certification/run", {"provider_name": "openrouter-byok", "model_name": OR_MODEL}, timeout=420.0
        )
        assert status == 200, certified
        yield SimpleNamespace(service=service, daemon=daemon, session=_session(f"own-tools-{uuid.uuid4()}"))
    finally:
        daemon.stop()
        service.stop()


def _turn(served, text: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    before = len(served.service.inference_requests())
    status, answer = served.daemon.chat(text, session_id=served.session)
    assert status == 200, answer
    arrived = served.service.inference_requests()[before:]
    return _answer_text(answer), arrived, served.daemon.events(served.session)


def _prefs(served) -> dict[str, Any]:
    status, prefs = served.daemon.call("GET", "/api/settings/prefs")
    assert status == 200, prefs
    return prefs


def test_the_owner_switches_the_models_own_tools_on_and_off_in_one_chat(served) -> None:
    service = served.service

    # 1. Off by default: Settings says so, and a pinned cloud model gets no provider tool.
    assert _prefs(served).get("model_own_tools") is False
    service.reply = _plain_reply
    text, arrived, _ = _turn(served, "What is a B-tree?")
    assert arrived, "the pinned model answers an ordinary question"
    assert all(SEARCH_TOOL not in list(body.get("tools") or []) for body in arrived)

    # 1b. Only the owner may switch it: a turn an agent wrote is refused and changes nothing.
    status, refused = served.daemon.call(
        "POST",
        "/api/chat",
        {
            "messages": [{"role": "user", "content": "use the model's own tools"}],
            "stream": False,
            "session_id": served.session,
            "model": OR_LANE_ID,
            "mode": "auto",
            "turn_author": "agent",
        },
        timeout=300.0,
    )
    assert status == 200 and "nothing changed" in _answer_text(refused), refused
    assert _prefs(served).get("model_own_tools") is False

    # 2. The chat command switches it on. It is VOOL's own command: no model call, and the
    #    reply says plainly where the search runs.
    text, arrived, _ = _turn(served, "use the model's own tools")
    assert arrived == [], "the switch is answered by VOOL, not by the model"
    assert "use their own tools" in text and "runs at the provider" in text, text
    assert _prefs(served).get("model_own_tools") is True

    # 3. On: one request, carrying the provider's web search and none of VOOL's tools. The
    #    provider's source reaches chat; the paid call is reserved and settled at the provider's
    #    reported cost.
    service.reply = _searched_reply
    question = "What changed in Rust compiler performance this year?"
    text, arrived, events = _turn(served, question)
    answer_calls = [body for body in arrived if question in _user_text(body)]
    assert len(answer_calls) == 1, [_user_text(body)[:80] for body in arrived]
    assert answer_calls[0].get("tools") == [SEARCH_TOOL]
    assert "tool_choice" not in answer_calls[0]
    assert "Compile times fell" in text and SOURCE_URL in text, text
    # No VOOL tool and no VOOL search ran; the one retrieval on record is the provider's own.
    assert not _events_of(events, "tool_started", "tool_executed", "tool_selected")
    retrievals = _events_of(events, "web_retrieval_completed")
    assert [event.get("provider_id") for event in retrievals] == ["model_provider_search"], retrievals
    assert _events_of(events, "paid_call.reserved")
    # The provider reported $0.0061 and no search count: the search is still counted, at the
    # full allowance of two searches, so the caps and the cost shown can never miss it.
    settled = _events_of(events, "paid_call.settled")
    assert settled and float(settled[-1].get("actual_usd") or 0.0) >= 0.02, settled[-1]

    # 3b. A live price is the model's to look up now: VOOL's own price lane steps aside and the
    #     question reaches the model with the provider's search. The provider's search results
    #     are matched claim by claim, exactly like VOOL's own search: the sourced price publishes,
    #     the sentence no source supports is withheld.
    def _price_reply(body: dict[str, Any]) -> dict[str, Any]:
        probe = _or_certification(body)
        if probe is not None:
            return probe
        completion = _or_completion(
            body,
            content="Bitcoin is trading at $64,102 right now.\n\nEthereum is trading at $3,180 right now.",
        )
        completion["choices"][0]["message"]["annotations"] = [
            {
                "type": "url_citation",
                "url_citation": {
                    "url": "https://example.org/markets/btc",
                    "title": "BTC price",
                    "content": "Bitcoin is trading at $64,102 as of 10:00 UTC.",
                },
            }
        ]
        return completion

    service.reply = _price_reply
    text, arrived, _ = _turn(served, "what is the price of bitcoin right now?")
    price_calls = [body for body in arrived if "bitcoin" in _user_text(body).lower()]
    assert len(price_calls) == 1 and price_calls[0].get("tools") == [SEARCH_TOOL], arrived
    published, _, withheld = text.partition("Withheld from this answer")
    assert "$64,102" in published and "$3,180" not in published, text
    assert "$3,180" in withheld, text

    # 4. A payment request on this switch still sends ONLY the search tool: money never moves
    #    through a provider tool.
    text, arrived, _ = _turn(served, "send 1 SOL to my friend")
    for body in arrived:
        for tool in list(body.get("tools") or []):
            assert tool == SEARCH_TOOL or "vool_probe" in str(tool), tool

    # 5. The provider refuses the search request: the owner sees a failure, not a silent answer
    #    without search, and the paid lane is not retried.
    service.faults.clear()

    def _refuse_search(body: dict[str, Any]) -> dict[str, Any]:
        probe = _or_certification(body)
        if probe is not None:
            return probe
        return {"choices": []}

    service.reply = _refuse_search
    text, arrived, events = _turn(served, "What is new in SQLite this month?")
    answer_calls = [body for body in arrived if "SQLite" in _user_text(body)]
    assert len(answer_calls) == 1, "a paid lane is never retried automatically"
    assert answer_calls[0].get("tools") == [SEARCH_TOOL]
    assert _events_of(events, "model.call_failed")

    # 6. Back to VOOL's tools: the provider tool is gone from the next request.
    service.reply = _plain_reply
    text, arrived, _ = _turn(served, "use VOOL's tools")
    assert arrived == [] and "VOOL's tools again" in text, text
    assert _prefs(served).get("model_own_tools") is False
    text, arrived, _ = _turn(served, "What is a hash map?")
    assert arrived and all(SEARCH_TOOL not in list(body.get("tools") or []) for body in arrived)


def test_the_settings_switch_is_the_same_switch_the_chat_reads(served) -> None:
    status, saved = served.daemon.call("POST", "/api/settings/prefs", {"model_own_tools": True})
    assert status == 200, saved
    try:
        assert _prefs(served).get("model_own_tools") is True
        _, arrived, _ = _turn(served, "which tools are you using?")
        assert arrived == []
    finally:
        served.daemon.call("POST", "/api/settings/prefs", {"model_own_tools": False})
    assert _prefs(served).get("model_own_tools") is False
    assert OR_LANE_ID.startswith("openrouter-byok:")
