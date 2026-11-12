from __future__ import annotations

from typing import Any

from retrieval.web_adapter import WebAdapter


def _weather_search_one(location: str) -> list[dict[str, Any]]:
    """Every note found for one place, each tagged with the place it answers.

    A query built from the extracted place name ("weather in kaunas"), never the raw message --
    the raw message is what let a whole sentence become the "location" text before this existed.
    A search that raises or comes back empty still yields one tagged placeholder rather than
    nothing, so the caller can tell "asked, found nothing" apart from "never asked".
    """
    try:
        found = WebAdapter.search_query(
            f"weather in {location}",
            limit=3,
            source_label="duckduckgo.com",
        )
    except Exception:
        found = []
    tagged = [dict(note, _weather_location=location) for note in (found or []) if isinstance(note, dict)]
    return tagged or [{"_weather_location": location, "_weather_no_result": True}]


def _weather_search_notes(query: str) -> list[dict[str, Any]]:
    from tools.web.web_research import _extract_weather_locations

    locations = _extract_weather_locations(query)
    if not locations:
        # No place could be named at all ("weather?", or nothing plausible survived the guard) --
        # the pre-existing single whole-query search, unchanged, so "current location" style
        # requests keep working exactly as they did before this function existed.
        return WebAdapter.search_query(query, limit=3, source_label="duckduckgo.com")
    if len(locations) == 1:
        return _weather_search_one(locations[0])
    # Several independent places named in one request. These are read-only network lookups with
    # no dependency on each other, so running them concurrently is safe -- unlike a planned
    # sub-turn (which drives a whole local-model generation and saturates that lane), a search is
    # bounded I/O and several can be in flight at once. `map` preserves the caller's own order
    # (Kaunas, Tallinn, Warsaw) even though the fetches themselves do not run in that order.
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(locations))) as pool:
            results = list(pool.map(_weather_search_one, locations))
    except Exception:
        results = [_weather_search_one(location) for location in locations]
    all_notes: list[dict[str, Any]] = []
    for notes in results:
        all_notes.extend(notes)
    return all_notes


def live_info_search_notes(
    agent: Any,
    *,
    query: str,
    live_mode: str,
    interpretation: Any,
) -> list[dict[str, Any]]:
    topic_hints = [str(item).strip().lower() for item in getattr(interpretation, "topic_hints", []) or [] if str(item).strip()]
    if live_mode == "weather":
        return _weather_search_notes(query)
    if live_mode == "news":
        return WebAdapter.search_query(
            query,
            limit=3,
            source_label="duckduckgo.com",
        )
    if live_mode == "fresh_lookup":
        quote_notes = agent._try_live_quote_notes(query)
        if quote_notes:
            return quote_notes
    return WebAdapter.planned_search_query(
        query,
        limit=3,
        task_class="research",
        topic_kind="general" if live_mode == "fresh_lookup" else None,
        topic_hints=topic_hints,
        source_label="duckduckgo.com",
    )


def try_live_quote_note(query: str) -> dict[str, Any] | None:
    try:
        from tools.web.web_research import lookup_live_quote

        quote = lookup_live_quote(query, timeout_s=8)
        if quote is None:
            return None
        return quote.to_note()
    except Exception:
        return None


def try_live_quote_notes(query: str) -> list[dict[str, Any]]:
    """Every live quote note a query names -- see `lookup_live_quotes` for why this exists
    alongside the singular `try_live_quote_note` above rather than replacing it."""
    try:
        from tools.web.web_research import lookup_live_quotes

        quotes = lookup_live_quotes(query, timeout_s=8)
        return [quote.to_note() for quote in quotes]
    except Exception:
        return []
