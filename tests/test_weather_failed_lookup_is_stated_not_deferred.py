"""A failed weather lookup is stated by the weather lane; it never defers to the model.

`_weather_search_one` leaves one tagged placeholder per place whether the search raised or came
back empty. The full 0.7.7 train deferred an all-placeholder turn with a failed lookup to the
reasoning lane; the v0.8 line chose the other way on 2026-11-12: with web lookups on, the weather
lane itself states the failed reading ("Weather in Paris: no current conditions found.",
tests/test_live_question_is_not_answered_from_the_records.py), publishes no reading of the place,
and the model is never asked to fill the gap. These tests pin that contract one level down, on
the real dispatch and renderer.

`record_live_info_observation` is pinned here too: a `_weather_no_result` placeholder is not a
retrieved row, so it never counts as one and never mints observation support.

The search seam (`WebAdapter.search_query` as imported by fast_live_info_search) is the system
boundary under test; every function downstream of it is the real code.
"""

from __future__ import annotations

import re
from typing import Any
from unittest import mock

from core.agent_runtime.fast_live_info_rendering import render_live_info_response
from core.agent_runtime.fast_live_info_runtime_dispatch import build_live_info_response_result
from core.agent_runtime.fast_live_info_runtime_flow import record_live_info_observation
from core.agent_runtime.fast_live_info_search import live_info_search_notes
from core.observation_evidence import usable_observations

SEARCH_SEAM = "core.agent_runtime.fast_live_info_search.WebAdapter.search_query"
_READING = re.compile(r"-?\d+\s*(?:°|C\b|F\b)")


class _DispatchAgent:
    """The agent surface `build_live_info_response_result` reads.

    Rendering delegates to the real renderer so the published wording in these assertions is
    the production wording, not a copy of it. The result envelope is a plain dict: these tests
    read the response text, not the audit machinery the real envelope carries.
    """

    def _unresolved_price_lookup_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str | None:
        return None

    def _looks_like_grounded_price_lookup(self, query: str) -> bool:
        return False

    def _live_info_failure_text(self, *, query: str, mode: str) -> str:
        return "the live lookup did not produce an answer"

    def _render_live_info_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str:
        return render_live_info_response(query=query, notes=notes, mode=mode)

    def _is_chat_truth_surface(self, source_context: dict[str, object] | None) -> bool:
        return False

    def _first_live_quote(self, notes: list[dict[str, Any]]) -> Any:
        return None

    def _fast_path_result(
        self,
        *,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict[str, Any]:
        return {"response": response, "confidence": confidence, "reason": reason}


def _weather_notes(query: str, search: Any) -> list[dict[str, Any]]:
    with mock.patch(SEARCH_SEAM, side_effect=search):
        return live_info_search_notes(object(), query=query, live_mode="weather", interpretation=object())


def _dispatch(query: str, notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    return build_live_info_response_result(
        _DispatchAgent(),
        session_id="sess-weather-stated",
        user_input=query,
        query=query,
        live_mode="weather",
        notes=notes,
        source_context=None,
        interpretation=None,
        response_class=None,
    )


def _raising_search(query_text: str, **_kwargs: object) -> list[dict[str, Any]]:
    raise RuntimeError("search stack unreachable")


def _empty_search(query_text: str, **_kwargs: object) -> list[dict[str, Any]]:
    return []


def _search_where(failed: tuple[str, ...], empty: tuple[str, ...]):
    """One seam answer per city: raises for `failed`, [] for `empty`, a real note otherwise."""

    def _search(query_text: str, **_kwargs: object) -> list[dict[str, Any]]:
        city = query_text.replace("weather in ", "").strip()
        if city in failed:
            raise RuntimeError(f"{city} lookup broke")
        if city in empty:
            return []
        return [{
            "result_title": f"{city.title()} weather",
            "result_url": f"https://example.test/{city}",
            "origin_domain": "example.test",
            "summary": f"{city.title()}: Rain, 16 C",
        }]

    return _search


def _stated(query: str, notes: list[dict[str, Any]], places: tuple[str, ...]) -> str:
    result = _dispatch(query, notes)
    assert result is not None, f"the weather lane deferred {query!r} instead of stating it"
    reply = str(result["response"])
    assert "no current conditions found" in reply, reply
    for place in places:
        assert place in reply.lower(), (place, reply)
    assert not _READING.search(reply), f"a reading nobody retrieved was published: {reply}"
    return reply


def test_a_failed_single_city_lookup_is_stated_by_the_weather_lane() -> None:
    for query, place in (("weather in valencia", "valencia"), ("what is the weather in osaka right now", "osaka")):
        notes = _weather_notes(query, _raising_search)
        assert notes == [{"_weather_location": place, "_weather_no_result": True}], notes
        _stated(query, notes, (place,))


def test_a_lookup_that_came_back_empty_is_stated_the_same_way() -> None:
    query = "what is the weather in osaka right now"
    _stated(query, _weather_notes(query, _empty_search), ("osaka",))


def test_a_failed_city_in_a_table_where_another_succeeded_keeps_the_row_wording() -> None:
    """The partial table: the city that raised keeps the pinned "no current conditions found"
    row (tests/test_weather_multi_city_extraction.py), next to the city that answered."""

    query = "weather in utrecht and dresden"
    notes = _weather_notes(query, _search_where(failed=("dresden",), empty=()))
    result = _dispatch(query, notes)
    assert result is not None
    assert "Utrecht" in result["response"] and "Rain, 16 C" in result["response"]
    assert "no current conditions found" in result["response"]


def test_every_city_failing_is_stated_city_by_city() -> None:
    query = "weather in nairobi and lima"
    _stated(query, _weather_notes(query, _raising_search), ("nairobi", "lima"))


def test_one_city_failing_and_one_empty_is_stated_city_by_city() -> None:
    query = "weather in nairobi and lima"
    notes = _weather_notes(query, _search_where(failed=("nairobi",), empty=("lima",)))
    _stated(query, notes, ("nairobi", "lima"))


def test_record_live_info_observation_never_counts_a_placeholder_as_a_retrieved_result() -> None:
    """A `_weather_no_result` placeholder is the lane saying a place came back empty, not a row
    it retrieved: ok=False, status no_results, and nothing it carries mints observation support."""

    captured: dict[str, Any] = {}

    def _capture(source_context: dict[str, Any], *, entries: Any) -> int:
        captured["entries"] = list(entries)
        return 0

    context: dict[str, Any] = {"session_id": "sess-obs", "cancel_turn_id": "turn-obs-1"}
    with mock.patch("core.grounding_lifecycle.record_typed_observations", side_effect=_capture):
        recorded = record_live_info_observation(
            context,
            [{"_weather_location": "taipei", "_weather_no_result": True}],
        )
    assert recorded is True
    observation = context["runtime_tool_observations"][-1]
    assert observation["ok"] is False
    assert observation["status"] == "no_results"
    assert observation["response_preview"] == "live-info lookup returned no results"
    assert usable_observations(captured["entries"]) == [], "a placeholder must mint nothing"


def test_record_live_info_observation_counts_only_the_real_rows() -> None:
    captured: dict[str, Any] = {}

    def _capture(source_context: dict[str, Any], *, entries: Any) -> int:
        captured["entries"] = list(entries)
        return 0

    real = {
        "summary": "Kyoto: Rain, 16 C",
        "origin_domain": "example.test",
        "result_url": "https://example.test/kyoto",
        "source_type": "web_derived",
    }
    placeholder = {"_weather_location": "osaka", "_weather_no_result": True}
    context: dict[str, Any] = {"session_id": "sess-obs", "cancel_turn_id": "turn-obs-2"}
    with mock.patch("core.grounding_lifecycle.record_typed_observations", side_effect=_capture):
        record_live_info_observation(context, [real, placeholder])
    observation = context["runtime_tool_observations"][-1]
    assert observation["ok"] is True
    assert observation["response_preview"] == "1 live-info result(s) retrieved"
    assert placeholder not in captured["entries"]
    assert real in captured["entries"]
