"""A FAILED weather lookup defers to the reasoning lane; a provider's empty answer does not.

`_weather_search_one` leaves one tagged placeholder per place whether the retrieval broke or the
provider answered with nothing, and a bare note list cannot tell the caller which of the two an
empty return is -- a transport failure is swallowed into [] inside `web_research` before this
lane ever sees it. That is why the lane reads `WebAdapter.search_query_with_status`: the status
says whether the retrieval FAILED (every engine errored, the budget died, or the call raised)
or a provider ANSWERED with nothing. Only the failed lookup's placeholder carries
`_weather_lookup_failed`, and `build_live_info_response_result` reads that marker: a weather
turn whose every note is a placeholder AND at least one is a failed lookup returns None, the
same defer the fresh_lookup mode already makes, so the reasoning lane runs and the grounding
gate withholds a reading nothing retrieved. A provider that answered with nothing keeps the
rendered "no current conditions found" path, and so does a failed city in a table where other
cities succeeded -- the wording pinned by tests/test_weather_multi_city_extraction.py.

`record_live_info_observation` is pinned here too: a `_weather_no_result` placeholder is not a
retrieved row, so it never counts as one and never mints observation support.

The search seam (`WebAdapter.search_query_with_status` as imported by fast_live_info_search)
is the system boundary under test; every function downstream of it is the real code.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from core.agent_runtime.fast_live_info_rendering import render_live_info_response
from core.agent_runtime.fast_live_info_runtime_dispatch import build_live_info_response_result
from core.agent_runtime.fast_live_info_runtime_flow import record_live_info_observation
from core.agent_runtime.fast_live_info_search import live_info_search_notes
from core.observation_evidence import usable_observations

SEARCH_SEAM = "core.agent_runtime.fast_live_info_search.WebAdapter.search_query_with_status"


class _DispatchAgent:
    """The agent surface `build_live_info_response_result` reads.

    Rendering delegates to the real renderer so the published wording in these assertions is
    the production wording, not a copy of it. The result envelope is a plain dict: these tests
    read the response text and the defer, not the audit machinery the real envelope carries.
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


def _dispatch(agent: _DispatchAgent, *, user_input: str, query: str, notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    return build_live_info_response_result(
        agent,
        session_id="sess-weather-defer",
        user_input=user_input,
        query=query,
        live_mode="weather",
        notes=notes,
        source_context=None,
        interpretation=None,
        response_class=None,
    )


def _raising_search(query_text: str, **_kwargs: object) -> tuple[list[dict[str, Any]], str]:
    raise RuntimeError("search stack unreachable")


def _failed_search(query_text: str, **_kwargs: object) -> tuple[list[dict[str, Any]], str]:
    """The swallowed-transport shape: every engine errored inside `web_research`, the note
    list is [] and only the status still says the retrieval broke."""
    return [], "failed"


def _empty_search(query_text: str, **_kwargs: object) -> tuple[list[dict[str, Any]], str]:
    return [], "empty"


def _search_where(failed: tuple[str, ...], empty: tuple[str, ...]):
    """One seam answer per city: "failed" for `failed`, "empty" for `empty`, a real note otherwise."""

    def _search(query_text: str, **_kwargs: object) -> tuple[list[dict[str, Any]], str]:
        city = query_text.replace("weather in ", "").strip()
        if city in failed:
            return [], "failed"
        if city in empty:
            return [], "empty"
        return [{
            "result_title": f"{city.title()} weather",
            "result_url": f"https://example.test/{city}",
            "origin_domain": "example.test",
            "summary": f"{city.title()}: Rain, 16 C",
        }], "ok"

    return _search


def test_a_city_whose_search_raised_leaves_a_placeholder_marked_failed() -> None:
    notes = _weather_notes("weather in valencia", _raising_search)
    assert notes == [
        {"_weather_location": "valencia", "_weather_no_result": True, "_weather_lookup_failed": True}
    ]


def test_a_city_whose_transport_failure_was_swallowed_is_marked_failed_by_the_status() -> None:
    """The dead-internet shape the served rig actually produces: `web_research` swallows the
    fetch errors, the note list comes back [], and only the status carries the failure."""

    notes = _weather_notes("what is the weather in genoa right now", _failed_search)
    assert notes == [
        {"_weather_location": "genoa", "_weather_no_result": True, "_weather_lookup_failed": True}
    ]
    assert _dispatch(
        _DispatchAgent(), user_input="what is the weather in genoa right now",
        query="what is the weather in genoa right now", notes=notes,
    ) is None


def test_a_city_the_provider_answered_with_nothing_keeps_todays_placeholder_exactly() -> None:
    notes = _weather_notes("what is the weather in osaka right now", _empty_search)
    assert notes == [{"_weather_location": "osaka", "_weather_no_result": True}]


def test_a_failed_single_city_lookup_defers_to_the_reasoning_lane() -> None:
    """THE fix: a weather turn whose lookup failed must not publish a reading of the place.

    At base the fast path rendered the failed placeholder as "no current conditions found" and
    the turn never reached the model. It must defer exactly like the fresh_lookup mode does.
    """

    for query in ("weather in valencia", "what is the weather in osaka right now"):
        notes = _weather_notes(query, _raising_search)
        assert _dispatch(_DispatchAgent(), user_input=query, query=query, notes=notes) is None, query


def test_a_provider_empty_single_city_lookup_does_not_defer() -> None:
    """The same zero-note shape WITHOUT the failure marker is the provider's own "nothing here":
    it keeps today's answer, so the defer cannot be keyed on note count alone."""

    query = "what is the weather in osaka right now"
    notes = _weather_notes(query, _empty_search)
    result = _dispatch(_DispatchAgent(), user_input=query, query=query, notes=notes)
    assert result is not None
    assert "no current conditions found" in result["response"]


def test_a_failed_city_in_a_table_where_another_succeeded_keeps_the_row_wording() -> None:
    """The partial table: the city that raised keeps the pinned "no current conditions found"
    row (tests/test_weather_multi_city_extraction.py), and the turn does not defer, because one
    city's real notes make the lookup usable."""

    query = "weather in utrecht and dresden"
    notes = _weather_notes(query, _search_where(failed=("dresden",), empty=()))
    result = _dispatch(_DispatchAgent(), user_input=query, query=query, notes=notes)
    assert result is not None
    assert "Utrecht" in result["response"] and "Rain, 16 C" in result["response"]
    assert "no current conditions found" in result["response"]


def test_no_usable_notes_with_any_failed_lookup_defers_even_mixed_with_provider_empty() -> None:
    """One city raised, one was answered with nothing: no note is usable and at least one lookup
    failed, so the turn defers -- the failure marker decides, not the note count."""

    query = "weather in nairobi and lima"
    notes = _weather_notes(query, _search_where(failed=("nairobi",), empty=("lima",)))
    assert _dispatch(_DispatchAgent(), user_input=query, query=query, notes=notes) is None


def test_every_city_raising_defers() -> None:
    query = "weather in nairobi and lima"
    notes = _weather_notes(query, _raising_search)
    assert all(note.get("_weather_lookup_failed") for note in notes)
    assert _dispatch(_DispatchAgent(), user_input=query, query=query, notes=notes) is None


def test_every_city_provider_empty_renders_the_table_without_deferring() -> None:
    """All-empty with no failure marker anywhere is every provider saying "nothing": the table
    keeps today's per-city wording and the fast path answers."""

    query = "weather in nairobi and lima"
    notes = _weather_notes(query, _empty_search)
    assert not any(note.get("_weather_lookup_failed") for note in notes)
    result = _dispatch(_DispatchAgent(), user_input=query, query=query, notes=notes)
    assert result is not None
    assert "no current conditions found" in result["response"]


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
            [{"_weather_location": "taipei", "_weather_no_result": True, "_weather_lookup_failed": True}],
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
