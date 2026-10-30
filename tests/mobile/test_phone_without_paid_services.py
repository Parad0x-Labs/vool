"""A phone without "Look things up online" asking for the weather, news or a price is told so, never given a guess.

The live-data lane stood down for such a phone without a word, and the model, offered no live tools, answered
from memory. The phone now gets the same answer its tool fence gives: where the owner turns the right on.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.request_trust as rt
from core.agent_runtime import fast_live_info_runtime_flow as flow


@pytest.fixture
def phone(monkeypatch):
    state = {"device": {"device_id": "phone:a"}, "live_mode": "fresh_lookup", "preflight": None}
    monkeypatch.setattr(rt, "remote_turn_device", lambda _sc: state["device"])
    monkeypatch.setattr(flow, "remote_turn_lacks", lambda _sc, grant: grant == rt.REMOTE_GRANT_PAID)
    monkeypatch.setattr(flow, "prepare_live_info_request",
                        lambda *_a, **_k: (state["live_mode"], "q", state["preflight"]))
    return state


AGENT = SimpleNamespace(_fast_path_result=lambda **kw: kw)


def _answer(text="what is the weather in Kaunas?"):
    found = flow.maybe_handle_live_info_fast_path(
        AGENT, text, session_id="s", source_context={}, interpretation=None, response_class=None)
    return found["response"] if found else None


def test_a_live_ask_from_a_phone_without_the_right_is_told_where_to_turn_it_on(phone):
    assert _answer() == rt.REMOTE_GRANT_REFUSAL_TEXT[rt.REMOTE_GRANT_PAID]


def test_a_chat_ask_and_a_caller_that_is_no_phone_are_untouched(phone):
    phone["live_mode"] = ""
    assert _answer("whats an easy pasta dish that kids will actually eat?") is None
    phone["live_mode"], phone["device"] = "fresh_lookup", None
    assert _answer() is None


def test_live_lookups_turned_off_on_the_computer_say_that_first(phone):
    phone["preflight"] = {"response": "Live lookups are turned off."}
    assert _answer() == "Live lookups are turned off."


# ------------------------------------------------------------ the real turn, on the phone and on the computer

from tests.pa_beta_gate.test_served_calendar_notes_workflows import served_env
from tests.pa_beta_gate.test_served_notes_precedence_residuals import _MODEL_STAND_IN, served


def _turn(text, workspace, *, phone_device=None):
    from unittest import mock

    from core.memory_first_router import ModelExecutionDecision
    from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

    decision = ModelExecutionDecision(
        source="model", task_hash="phone-paid", provider_id="phone-paid", provider_name="stand-in",
        model_name="phone-paid", output_text=_MODEL_STAND_IN, confidence=0.9, trust_score=0.9, used_model=True,
    )
    harness = _Harness("sess-phone-paid")
    context = dict(_SOURCE_CONTEXT, workspace=str(workspace), operating_mode="auto")
    if phone_device:
        context.update({rt.OWNER_LOCAL_KEY: False, rt.HTTP_INGRESS_KEY: True, rt.REMOTE_DEVICE_KEY: phone_device})
    try:
        with mock.patch.object(harness.agent.memory_router, "resolve", return_value=decision):
            result = harness.agent.run_once(text, source_context=context, session_id_override=harness.session_id)
    finally:
        harness.close()
    return str(result.get("response") or "")


def test_the_weather_on_a_phone_without_the_right_and_on_the_computer(served, monkeypatch):
    monkeypatch.setattr(rt, "remote_turn_device", lambda sc: {"device_id": "phone:a"} if (sc or {}).get(rt.REMOTE_DEVICE_KEY) else None)
    monkeypatch.setattr(rt, "remote_turn_has_grant", lambda _sc, _grant: False)
    on_phone = _turn("what is the weather in Kaunas?", served.workspace, phone_device="phone:a")
    assert on_phone == rt.REMOTE_GRANT_REFUSAL_TEXT[rt.REMOTE_GRANT_PAID], on_phone
    assert served.searches == []
    on_computer = _turn("what is the weather in Kaunas?", served.workspace)
    assert "Cloudy, 11 C" in on_computer, on_computer
