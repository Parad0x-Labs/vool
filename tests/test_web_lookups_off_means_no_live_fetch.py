"""With Web lookups switched off, nothing goes online: live weather and prices included.

Owner decision 2026-11-12. Measured the same day on the 0.7.8 sweep (fresh profile, switch off):
"what's the weather in Paris right now?" fetched wttr.in and quoted it, "according to wikipedia,
..." was answered from model memory with no word that lookups are off, and "search online: ..."
told the person to set an environment variable.
"""

from __future__ import annotations

import urllib.request
from unittest import mock

import pytest

from core import policy_engine
from core.remote_fetch_policy import WEB_OFF_LOOKUP_TEXT, WEB_OFF_TEXT


def _ask_all(questions: list[str], *, web_on: bool) -> tuple[dict[str, dict], list[str]]:
    from tests.test_mixed_demand_terminal_closure_p0 import _model_stand_in
    from tests.test_turn_attempt_chain import _Harness

    opened: list[str] = []

    def _record(self, request, *args, **kwargs):
        opened.append(str(getattr(request, "full_url", request)))
        raise OSError("network is not reachable in this test")

    answers: dict[str, dict] = {}
    harness = _Harness("web-lookups-off")
    try:
        with mock.patch.object(policy_engine, "local_only_mode", return_value=False), \
                mock.patch.object(policy_engine, "allow_web_fallback", return_value=web_on), \
                mock.patch.object(urllib.request.OpenerDirector, "open", _record), \
                _model_stand_in(harness.agent, "A stand-in answer."):
            for question in questions:
                answers[question] = harness.agent.run_once(
                    question,
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                    session_id_override=harness.session_id,
                )
    finally:
        harness.close()
    public = [url for url in opened if "127.0.0.1" not in url and "localhost" not in url]
    return answers, public


def test_switch_off_answers_live_data_and_lookups_plainly_and_opens_nothing() -> None:
    live = ["what's the weather in Paris right now?", "what's the bitcoin price right now?"]
    lookups = [
        "according to wikipedia, when did the berlin wall fall?",
        "search online: population of iceland",
        "look up the latest news about the olympics",
    ]
    ordinary = ["tell me a short joke", "what is the capital of france"]
    answers, public = _ask_all(live + lookups + ordinary, web_on=False)
    assert public == [], public
    for question in live:
        assert str(answers[question].get("response") or "") == WEB_OFF_TEXT, question
    for question in lookups:
        assert str(answers[question].get("response") or "") == WEB_OFF_LOOKUP_TEXT, question
    # Control: an ordinary question still reaches the model, never a web-off sentence.
    for question in ordinary:
        assert str(answers[question].get("response") or "") == "A stand-in answer.", question


def test_switch_on_does_not_answer_with_the_web_off_sentence() -> None:
    """Control for the test above: the same live ask with the switch on goes to the live lanes
    (whose fetch this test's network refuses), never to the switched-off sentence."""
    question = "what's the weather in Paris right now?"
    answers, _public = _ask_all([question], web_on=True)
    response = str(answers[question].get("response") or "")
    assert response not in (WEB_OFF_TEXT, WEB_OFF_LOOKUP_TEXT)


@pytest.mark.parametrize(
    "runner_name, operation, arguments, fetcher",
    [
        ("_run_weather_subtask", "weather_lookup", {"location": "Paris"},
         "tools.web.web_research.structured_weather_lookup"),
        ("_run_market_subtask", "market_quote", {"asset_key": "bitcoin", "kind": "crypto"},
         "tools.web.web_research._crypto_price_fallback_multi"),
        ("_run_water_subtask", "water_temperature", {"place": "Baltic Sea"},
         "core.fresh_data.water_temperature.water_temperature_lookup"),
    ],
)
def test_every_live_data_runner_refuses_before_fetching_when_the_switch_is_off(
    runner_name: str, operation: str, arguments: dict, fetcher: str
) -> None:
    """The typed lane and the conductor share these runners, so no road reaches a provider."""
    from core.agent_runtime import live_data_runner
    from core.agent_runtime.live_data_plan import LiveDataSubtask, SubtaskLifecycle

    subtask = LiveDataSubtask(
        subtask_id="s1", entity=str(next(iter(arguments.values()))), operation=operation,
        arguments=arguments, required_result_fields=(), tool="weather", tool_intent="web.research",
    )
    runner = getattr(live_data_runner, runner_name)
    with mock.patch.object(policy_engine, "local_only_mode", return_value=False), \
            mock.patch(fetcher, side_effect=AssertionError("fetched with Web lookups off")) as fetch:
        with mock.patch.object(policy_engine, "allow_web_fallback", return_value=False):
            outcome = runner(subtask, timeout_s=1.0)
        assert outcome.state is SubtaskLifecycle.FAILED
        assert outcome.failure_reason.startswith("Live web lookup is off")
        assert fetch.call_count == 0
        # Control: with the switch on, the runner does reach its provider.
        fetch.side_effect = RuntimeError("provider stand-in")
        with mock.patch.object(policy_engine, "allow_web_fallback", return_value=True):
            runner(subtask, timeout_s=1.0)
        assert fetch.call_count == 1


def test_web_tool_reason_is_chat_words_without_an_environment_variable() -> None:
    from core.execution.capabilities import _web_unsupported_reason

    with mock.patch.object(policy_engine, "local_only_mode", return_value=False):
        reason = _web_unsupported_reason()
    assert "VOOL_ENABLE_WEB" not in reason
    assert "Settings, Privacy & Permissions" in reason


# --- the weather phrasings the recognizer used to miss ----------------------------------------
# 0.7.8 sweep 2026-11-12: a place with no preposition ("weather <place> now") and the progressive
# verb ("is it raining in <place>") were not read as weather at all, so the turn reached the model
# with web on or off. Fresh phrasings here, not the sweep's own wording.

_NEW_WEATHER_ASKS = {
    "weather lisbon right now": "lisbon",
    "forecast bergen tomorrow": "bergen",
    "tallinn weather now": "tallinn",
    "what's the san diego weather today?": "san diego",
    "is it snowing in oslo?": "oslo",
    "will it be raining tomorrow in kyiv": "kyiv",
    "is it hailing in graz": "graz",
}
_NOT_WEATHER_ASKS = (
    "weather patterns are complex",
    "weather is nice today",
    "weather has been nice today",
    "is it raining cats and dogs an idiom?",
    "weather apps today",
)


@pytest.mark.parametrize("text, place", sorted(_NEW_WEATHER_ASKS.items()))
def test_prepositionless_and_progressive_weather_asks_are_read_with_their_place(
    text: str, place: str
) -> None:
    from core.agent_runtime.fast_live_info_mode_classifier import (
        _looks_like_live_weather_request,
    )
    from core.live_data_plan import build_live_data_plan

    assert _looks_like_live_weather_request(text.lower())
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    assert plan is not None
    assert [task.arguments.get("location") for task in plan.subtasks] == [place]


@pytest.mark.parametrize("text", _NOT_WEATHER_ASKS)
def test_weather_prose_is_still_not_a_lookup(text: str) -> None:
    from core.agent_runtime.fast_live_info_mode_classifier import (
        _looks_like_live_weather_request,
    )

    assert not _looks_like_live_weather_request(text.lower())


def test_new_weather_phrasings_are_served_when_on_and_refused_plainly_when_off() -> None:
    from tests.test_mixed_demand_terminal_closure_p0 import _model_stand_in
    from tests.test_turn_attempt_chain import _Harness
    from tools.web.web_research import WeatherResult

    asks = ["weather lisbon right now", "is it snowing in oslo?"]
    looked: list[str] = []

    def _stand_in_lookup(location: str, timeout_s: float = 8.0) -> WeatherResult:
        looked.append(location)
        return WeatherResult(
            location=location, place_label=location.title(), condition="Light snow",
            temperature_c=-2.0, feels_like_c=None, humidity_pct=None, wind_kmph=None,
            observed_at="09:00 AM", source_label="stand-in", source_url="https://example.invalid/",
            high_c=0.0, low_c=-4.0,
        )

    def _no_network(self, request, *args, **kwargs):
        raise OSError("network is not reachable in this test")

    results: dict[tuple[bool, str], str] = {}
    for web_on in (True, False):
        harness = _Harness(f"weather-phrasings-{web_on}")
        try:
            with mock.patch.object(policy_engine, "local_only_mode", return_value=False), \
                    mock.patch.object(policy_engine, "allow_web_fallback", return_value=web_on), \
                    mock.patch("tools.web.web_research.structured_weather_lookup", _stand_in_lookup), \
                    mock.patch.object(urllib.request.OpenerDirector, "open", _no_network), \
                    _model_stand_in(harness.agent, "A stand-in answer."):
                for ask in asks:
                    results[(web_on, ask)] = str(
                        harness.agent.run_once(
                            ask,
                            source_context={"surface": "openclaw", "platform": "openclaw"},
                            session_id_override=harness.session_id,
                        ).get("response") or ""
                    )
        finally:
            harness.close()
        if web_on:
            assert looked == ["lisbon", "oslo"]
            looked.clear()
    assert results[(True, "weather lisbon right now")].startswith("Lisbon: Light snow")
    assert results[(True, "is it snowing in oslo?")].startswith("Oslo: Light snow")
    assert looked == [], "a lookup ran with Web lookups off"
    for ask in asks:
        assert results[(False, ask)] == WEB_OFF_TEXT
