from __future__ import annotations

import re
from typing import Any


def _weather_note_line(location: str, notes_for_city: list[dict[str, Any]]) -> tuple[str, str]:
    """One city's condition text and source line, or an honest "nothing found" pair."""
    primary = next((note for note in notes_for_city if not note.get("_weather_no_result")), None)
    if primary is None:
        return "no current conditions found", ""
    summary = " ".join(str(primary.get("summary") or "").split()).strip()
    url = str(primary.get("result_url") or "").strip()
    domain = str(primary.get("origin_domain") or "").strip()
    if not summary:
        return "I couldn't extract conditions from the results", (f"[{domain or 'source'}]({url})" if url else "")
    # THE SAME GATE THE QUERY-PARSING RENDERER ALREADY APPLIES. It was wired on that road only, and
    # this one reached the identical fabrication by the other. Measured live in the shipped app,
    # 2026-08-18, route `live_info_fast_path`:
    #
    #   "what is the weather in Vilnius right now?"
    #     -> "Weather in Vilnius: Be prepared with the most accurate 10-day forecast for Seattle,
    #         Washington ... Source: [weather.com](https://weather.com/us/washington/city/seattle/tenday)."
    #
    # A wrong place stated with confidence AND a citation pointing at the wrong place. The source
    # is dropped along with the snippet: a link to Seattle under a Vilnius heading is the part that
    # makes the answer look checkable.
    if _snippet_names_another_place(summary, location):
        return "no verified conditions -- the sources found describe a different place", ""
    return summary, (f"[{domain or 'source'}]({url})" if url else "")


def _weather_as_table(order: list[str], grouped: dict[str, list[dict[str, Any]]]) -> str:
    """Several cities' conditions as one table, one row per city, in the order asked."""
    rows = []
    for location in order:
        condition, source = _weather_note_line(location, grouped[location])
        rows.append({"City": location.title(), "Conditions": condition, "Source": source})
    columns = [name for name in ("City", "Conditions", "Source") if any(row.get(name) for row in rows)]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(row.get(name, "") or "-" for name in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


#: A place NAMED inside a snippet, in the two shapes search snippets actually use: a locative
#: ("in Mount Airy", "for Springfield") or a place-comma-region pair ("Mount Airy, NC",
#: "Springfield, Illinois"). Sentence-initial capitals ("Fresh update...", "Hourly forecast...")
#: match neither shape, which is what keeps a bare conditions snippet renderable.
_SNIPPET_PLACE_RE = re.compile(
    r"(?:\b(?:in|for|at|near)\s+(?P<loc>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}))"
    r"|(?:\b(?P<pair>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}),\s+(?:[A-Z]{2}\b|[A-Z][a-z]+))"
)

#: Words the locative pattern may capture that are not places.
_SNIPPET_PLACE_STOPWORDS = frozenset(
    {"today", "detail", "hourly", "current", "the", "your", "this", "real", "time"}
)


def _snippet_names_another_place(summary: str, location: str) -> bool:
    """Whether the snippet names a place that is NOT the requested one.

    Judged on NAMES (`_place_names_correspond`: typo distance, word overlap, diacritics), so
    "Villnius" vs a snippet naming Vilnius still corresponds while Mount Airy never can. The
    wttr payload gate is coordinate-based and fail-open instead -- a snippet's place-name is
    the page's SUBJECT, not a nearby station, so absence of a match here is real evidence.
    """

    from tools.web.web_research import _place_names_correspond

    for match in _SNIPPET_PLACE_RE.finditer(str(summary or "")):
        candidate = (match.group("loc") or match.group("pair") or "").strip()
        if not candidate or candidate.lower() in _SNIPPET_PLACE_STOPWORDS:
            continue
        if not _place_names_correspond(location, candidate):
            return True
    return False


def render_weather_response(*, query: str, notes: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for note in list(notes or []):
        if not isinstance(note, dict):
            continue
        tag = str(note.get("_weather_location") or "").strip()
        if not tag:
            continue
        grouped.setdefault(tag, []).append(note)
        if tag not in order:
            order.append(tag)
    if len(order) > 1:
        return _weather_as_table(order, grouped)
    if len(order) == 1:
        location = order[0]
        condition, source = _weather_note_line(location, grouped[location])
        # The legacy path never prefixes "Weather in X:" when the condition text already names
        # the place ("London, United Kingdom: Cloudy...") -- doing so unconditionally regressed
        # that structured wording into "Weather in London: London, United Kingdom: Cloudy...",
        # caught by the existing test_weather_live_lookup_uses_structured_weather_wording.
        line = condition if location.lower() in condition.lower() else f"Weather in {location.title()}: {condition}"
        if not line.endswith("."):
            line += "."
        if source:
            line += f" Source: {source}."
        return line

    # Legacy path: no per-city tagging on any note (a caller that hasn't gone through the
    # multi-city search, or a direct test call). The location is reconstructed by stripping a few
    # question/weather phrases out of the RAW QUERY -- which works for "weather in London" and
    # fails badly for anything else, because subtracting a handful of phrases from a sentence
    # leaves a sentence, not a place name.
    #
    # Live-release repair (2026-08-07, LIVE FAILURE 2): this is the path that manufactured a
    # pseudo-location from an instruction. "Compare the current weather in 8 European capitals and
    # rank them warmest to coldest." reaches here (the extractor correctly found NO explicit city,
    # so `_weather_search_notes` fell back to one untagged whole-query search), and the strip above
    # turned it into the location "Compare the 8 European capitals and rank them warmest to
    # coldest" -- live-verified to reproduce that exact string. Every containment built into the
    # EXTRACTION layer was bypassed, because this reconstruction never consults it.
    #
    # `_is_plausible_weather_location` is the runtime's existing, already-tested answer to "is this
    # string a usable place name" (no brackets/colon/GMT, <=60 characters, <=6 words, has letters).
    # Reusing it here rather than inventing a second rule is the point: one definition of a usable
    # location, applied wherever a location is produced. When the reconstruction does not survive
    # it, this fails CLOSED -- it reports that no specific location could be determined instead of
    # naming a sentence as a place. Semantic expansion of a request like "8 European capitals" into
    # actual cities is CONDUCTOR's job, not this adapter's; the correct behaviour here is to defer,
    # not to guess.
    # ANVIL review closure (2026-08-07): `_is_plausible_weather_location` alone was not enough. It
    # is a SHAPE predicate (no brackets/colon/GMT, <=60 characters, <=6 words, has letters), and a
    # short instruction passes every one of those arms -- "Rank these cities", "Which is warmer",
    # and "Summarize the weather" all rendered as locations. The second gate reuses the extraction
    # path's own instruction vocabulary via `_has_clause_structure_marker` rather than adding a
    # phrase blacklist here: "these" is a demonstrative determiner, "which"/"is" are a WH-word and
    # an auxiliary, "the" is a determiner -- all already closed-class function words. A legitimate
    # short location ("London", "Paris", "New York", "Rio de Janeiro") contains none of them, so
    # nothing real is lost.
    from tools.web.web_research import _has_clause_structure_marker, _is_plausible_weather_location

    location = re.sub(
        r"\b(?:what\s+is\s+(?:the\s+)?|how\s+is\s+(?:the\s+)?|weather\s+(?:like\s+)?(?:in|for|at)\s+|"
        r"weather\s+in\s+|now\??|right\s+now\??|today\??|current(?:ly)?)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    location = re.sub(r"\bforecast\b", " ", location, flags=re.IGNORECASE)
    location = " ".join(location.split()).strip(" ?.,!") or "your location"
    if location != "your location" and (
        not _is_plausible_weather_location(location) or _has_clause_structure_marker(location)
    ):
        return (
            "I couldn't determine a specific location to look up from that request. "
            "Name the cities you want and I'll fetch current conditions for each."
        )
    primary = dict(next((note for note in list(notes or []) if isinstance(note, dict)), {}))
    summary = " ".join(str(primary.get("summary") or "").split()).strip()
    url = str(primary.get("result_url") or "").strip()
    domain = str(primary.get("origin_domain") or "").strip()
    if summary:
        if location.lower() in summary.lower():
            line = summary
        else:
            # A snippet that NAMES A DIFFERENT PLACE is not this place's weather. Measured live
            # 2026-08-15: after the wttr lookup correctly declined the typo "Villnius", THIS
            # branch pasted a search snippet about Mount Airy, NC under the header "Weather in
            # Villnius:" -- the same fabrication the wttr gate had just stopped, one lane over.
            # The discriminator is deliberately place-NAMING, not place-ABSENCE: a bare
            # conditions snippet ("Cloudy.") carries no competing place and stays renderable,
            # because the search that produced it was already scoped to the requested city --
            # that is the legacy behaviour two release-regression tests pin, and they are right.
            if location != "your location" and _snippet_names_another_place(summary, location):
                return (
                    f"I couldn't verify current weather for {location!r} -- the sources I found "
                    "describe a different place. Check the spelling and I'll look again."
                )
            # The location's own casing is preserved -- .title() here turned "Rio de Janeiro"
            # into "Rio De Janeiro" and two release tests caught it before commit.
            line = f"Weather in {location}: {summary}"
    else:
        line = f"I searched for weather in {location} but couldn't extract conditions from the results."
    if url:
        line += f" Source: [{domain or 'source'}]({url})."
    return line
