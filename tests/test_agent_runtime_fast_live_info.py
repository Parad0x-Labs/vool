from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime import fast_paths
from core.agent_runtime.fast_live_info import (
    live_info_mode,
    maybe_handle_live_info_fast_path,
    render_live_info_response,
)


def _build_agent() -> SimpleNamespace:
    return SimpleNamespace(
        _looks_like_builder_request=lambda _text: False,
        _wants_fresh_info=lambda _text, interpretation: True,
    )


def test_fast_live_info_compat_exports_stay_available_from_fast_paths() -> None:
    assert fast_paths.maybe_handle_live_info_fast_path is maybe_handle_live_info_fast_path
    assert fast_paths.render_live_info_response is render_live_info_response


def test_live_info_mode_matches_existing_fresh_lookup_behavior() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(topic_hints=["web"])

    assert live_info_mode(agent, "latest telegram bot api updates", interpretation=interpretation) == "fresh_lookup"


def test_live_info_weather_requires_an_actual_current_weather_request() -> None:
    agent = _build_agent()
    weather_hints = SimpleNamespace(topic_hints=["weather"])

    assert live_info_mode(agent, "what is the weather in London today?", interpretation=weather_hints) == "weather"
    assert live_info_mode(agent, "will it rain in Vancouver tomorrow?", interpretation=weather_hints) == "weather"
    assert live_info_mode(
        agent,
        "New question: why might someone enjoy rain on a window?",
        interpretation=weather_hints,
    ) == ""
    assert live_info_mode(agent, "why do windy walks feel invigorating?", interpretation=weather_hints) == ""
