from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import core.agent_runtime.fast_live_info_generic_rendering as fast_live_info_generic_rendering
import core.agent_runtime.fast_live_info_mode_clock_markers as fast_live_info_mode_clock_markers
import core.agent_runtime.fast_live_info_mode_lookup_markers as fast_live_info_mode_lookup_markers
import core.agent_runtime.fast_live_info_mode_news_markers as fast_live_info_mode_news_markers
import core.agent_runtime.fast_live_info_mode_weather_markers as fast_live_info_weather_markers
import core.agent_runtime.fast_live_info_news_rendering as fast_live_info_news_rendering
import core.agent_runtime.fast_live_info_quote_rendering as fast_live_info_quote_rendering
import core.agent_runtime.fast_live_info_runtime_dispatch as fast_live_info_runtime_dispatch
import core.agent_runtime.fast_live_info_runtime_preflight as fast_live_info_runtime_preflight
import core.agent_runtime.fast_live_info_weather_rendering as fast_live_info_weather_rendering
from core.agent_runtime import (
    fast_live_info,
    fast_live_info_mode_classifier,
    fast_live_info_mode_failure,
    fast_live_info_mode_markers,
    fast_live_info_mode_policy,
    fast_live_info_mode_query,
    fast_live_info_mode_recency,
    fast_live_info_mode_rules,
    fast_live_info_price,
    fast_live_info_rendering,
    fast_live_info_router,
    fast_live_info_runtime,
    fast_live_info_runtime_flow,
    fast_live_info_runtime_results,
    fast_live_info_runtime_search,
    fast_live_info_runtime_truth,
    fast_live_info_search,
)


def _build_agent() -> SimpleNamespace:
    return SimpleNamespace(
        _try_live_quote_note=lambda _query: None,
        _looks_like_builder_request=lambda _text: False,
        _wants_fresh_info=lambda _text, interpretation: True,
    )


def test_fast_live_info_facade_reexports_split_modules() -> None:
    assert fast_live_info.maybe_handle_live_info_fast_path is fast_live_info_router.maybe_handle_live_info_fast_path
    assert fast_live_info_router.maybe_handle_live_info_fast_path is fast_live_info_runtime.maybe_handle_live_info_fast_path
    assert fast_live_info.live_info_mode is fast_live_info_router.live_info_mode
    assert fast_live_info_router.live_info_mode is fast_live_info_mode_policy.live_info_mode
    assert fast_live_info_mode_policy.live_info_mode is fast_live_info_mode_rules.live_info_mode
    assert fast_live_info_mode_rules.live_info_mode is fast_live_info_mode_classifier.live_info_mode
    assert fast_live_info_mode_policy.normalize_live_info_query is fast_live_info_mode_rules.normalize_live_info_query
    assert fast_live_info_mode_rules.normalize_live_info_query is fast_live_info_mode_query.normalize_live_info_query
    assert (
        fast_live_info_mode_policy.requires_ultra_fresh_insufficient_evidence
        is fast_live_info_mode_rules.requires_ultra_fresh_insufficient_evidence
    )
    assert (
        fast_live_info_mode_rules.requires_ultra_fresh_insufficient_evidence
        is fast_live_info_mode_recency.requires_ultra_fresh_insufficient_evidence
    )
    assert (
        fast_live_info_mode_policy.ultra_fresh_insufficient_evidence_response
        is fast_live_info_mode_rules.ultra_fresh_insufficient_evidence_response
    )
    assert (
        fast_live_info_mode_rules.ultra_fresh_insufficient_evidence_response
        is fast_live_info_mode_recency.ultra_fresh_insufficient_evidence_response
    )
    assert fast_live_info_mode_policy.live_info_failure_text is fast_live_info_mode_rules.live_info_failure_text
    assert fast_live_info_mode_rules.live_info_failure_text is fast_live_info_mode_failure.live_info_failure_text
    assert fast_live_info_mode_policy._CLOCK_AND_DATE_MARKERS is fast_live_info_mode_markers._CLOCK_AND_DATE_MARKERS
    assert fast_live_info_mode_markers._CLOCK_AND_DATE_MARKERS is fast_live_info_mode_clock_markers._CLOCK_AND_DATE_MARKERS
    assert fast_live_info_mode_policy._WEATHER_MARKERS is fast_live_info_mode_markers._WEATHER_MARKERS
    assert fast_live_info_mode_markers._WEATHER_MARKERS is fast_live_info_weather_markers._WEATHER_MARKERS
    assert fast_live_info_mode_policy._NEWS_MARKERS is fast_live_info_mode_markers._NEWS_MARKERS
    assert fast_live_info_mode_markers._NEWS_MARKERS is fast_live_info_mode_news_markers._NEWS_MARKERS
    assert fast_live_info_mode_policy._LIVE_LOOKUP_HINT_MARKERS is fast_live_info_mode_markers._LIVE_LOOKUP_HINT_MARKERS
    assert fast_live_info_mode_markers._LIVE_LOOKUP_HINT_MARKERS is fast_live_info_mode_lookup_markers._LIVE_LOOKUP_HINT_MARKERS
    assert fast_live_info_mode_policy._FRESH_LOOKUP_MARKERS is fast_live_info_mode_markers._FRESH_LOOKUP_MARKERS
    assert fast_live_info_mode_markers._FRESH_LOOKUP_MARKERS is fast_live_info_mode_lookup_markers._FRESH_LOOKUP_MARKERS
    assert fast_live_info_mode_policy._LATEST_DOMAIN_MARKERS is fast_live_info_mode_markers._LATEST_DOMAIN_MARKERS
    assert fast_live_info_mode_markers._LATEST_DOMAIN_MARKERS is fast_live_info_mode_lookup_markers._LATEST_DOMAIN_MARKERS
    assert fast_live_info_runtime.maybe_handle_live_info_fast_path is fast_live_info_runtime_flow.maybe_handle_live_info_fast_path
    assert fast_live_info_runtime_flow.maybe_handle_live_info_fast_path is fast_live_info_runtime.maybe_handle_live_info_fast_path
    assert fast_live_info_runtime_flow.prepare_live_info_request is fast_live_info_runtime_preflight.prepare_live_info_request
    assert fast_live_info_runtime_flow.build_live_info_response_result is fast_live_info_runtime_dispatch.build_live_info_response_result
    assert fast_live_info_runtime_flow.disabled_live_info_result is fast_live_info_runtime_results.disabled_live_info_result
    assert fast_live_info_runtime_flow.live_info_result is fast_live_info_runtime_results.live_info_result
    assert (
        fast_live_info_runtime_flow.live_info_search_notes_with_fallback
        is fast_live_info_runtime_search.live_info_search_notes_with_fallback
    )
    assert (
        fast_live_info_runtime_flow.should_use_chat_truth_wording
        is fast_live_info_runtime_truth.should_use_chat_truth_wording
    )
    assert (
        fast_live_info_runtime_flow.chat_truth_live_info_result
        is fast_live_info_runtime_truth.chat_truth_live_info_result
    )
    assert fast_live_info.live_info_search_notes is fast_live_info_search.live_info_search_notes
    assert fast_live_info.try_live_quote_note is fast_live_info_search.try_live_quote_note
    assert fast_live_info.render_live_info_response is fast_live_info_rendering.render_live_info_response
    assert fast_live_info_rendering.render_live_info_response is fast_live_info_generic_rendering.render_live_info_response
    assert fast_live_info.render_weather_response is fast_live_info_rendering.render_weather_response
    assert fast_live_info_rendering.render_weather_response is fast_live_info_weather_rendering.render_weather_response
    assert fast_live_info.render_news_response is fast_live_info_rendering.render_news_response
    assert fast_live_info_rendering.render_news_response is fast_live_info_news_rendering.render_news_response
    assert fast_live_info_rendering.first_live_quote is fast_live_info_quote_rendering.first_live_quote
    assert fast_live_info.unresolved_price_lookup_response is fast_live_info_price.unresolved_price_lookup_response


def test_live_info_search_notes_prefers_live_quote_notes_for_fresh_lookup() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(topic_hints=["web"])

    agent._try_live_quote_notes = mock.Mock(  # type: ignore[assignment]
        return_value=[{"live_quote": {"asset_key": "btc", "asset_name": "BTC", "value": 1.0}}],
    )

    with mock.patch(
        "core.agent_runtime.fast_live_info_search.WebAdapter.planned_search_query",
        side_effect=AssertionError("planned search should not run when a quote note is available"),
    ):
        notes = fast_live_info_search.live_info_search_notes(
            agent,
            query="BTC price now",
            live_mode="fresh_lookup",
            interpretation=interpretation,
        )

    assert notes == [{"live_quote": {"asset_key": "btc", "asset_name": "BTC", "value": 1.0}}]


def test_live_info_rendering_and_price_helpers_stay_grounded() -> None:
    notes = [
        {
            "result_title": "Example result",
            "origin_domain": "example.com",
            "summary": "Fresh update from the example domain.",
            "result_url": "https://example.com/result",
        }
    ]

    assert fast_live_info_rendering.render_weather_response(query="weather in London", notes=notes).startswith(
        "Weather in London:"
    )
    assert "Latest coverage on" in fast_live_info_rendering.render_news_response(
        query="latest news on London",
        notes=notes,
    )
    assert fast_live_info_price.extract_price_lookup_subject("What is the price of BTC now?") == "BTC"


def test_price_recovery_does_not_hijack_entity_lookup_prompts() -> None:
    recovered = fast_live_info_price.recover_price_lookup_query(
        "find Tolly solana twitter",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert recovered == ""
