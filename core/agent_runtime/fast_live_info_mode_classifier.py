from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from core.agent_runtime.fast_paths_builder import looks_like_builder_request
from core.retrieval_constraints import analyze_retrieval_constraints
from core.stipulated_frame import stipulated_frame_forbids_retrieval
from core.task_router import (
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)

from .fast_live_info_mode_markers import (
    _CLOCK_AND_DATE_MARKERS,
    _FRESH_LOOKUP_MARKERS,
    _LATEST_DOMAIN_MARKERS,
    _LIVE_LOOKUP_HINT_MARKERS,
    _NEWS_MARKERS,
)

# People misspell this word constantly, and a single wrong letter dropped the whole turn out of the
# weather lane. Measured live in the shipped app on 2026-08-18:
#
#   "wheather in vilnius, berlin and rome"   -> route=ordinary_plain_text_chat, the model was asked
#                                               to do it, the model call failed, the user got
#                                               "no current weather results came back"
#   "weather in vilnius, berlin and rome"    -> route=live_data_typed_plan, a full sourced table
#
# One letter. The forecast clause in the original report was a red herring; isolating showed
# "weather ... also forecast for next 3 days" is served fine.
#
# Same reasoning as `core.measurement_medium`, which is edit-distance tolerant because ITS live
# reproduction was "watter": a runtime that serves a correctly-spelled request and drops the typo'd
# one is failing exactly the users who type fastest.
#
# `whether` is deliberately ABSENT -- it is an ordinary English word ("I don't know whether it
# rains"), and matching it would claim turns that are not weather requests at all. `wether` (a
# castrated ram) is likewise excluded; the words that ARE here are not words in English.
_WEATHER_WORD = r"(?:weather|wheather|weater|weathr|wetaher|weahter|wheter|wather)"

_WEATHER_LIVE_REQUEST_RE = re.compile(
    r"\b(?:"
    # "only"/"just"/"specifically" between the subject and the preposition -- "weather only for
    # Berlin and Copenhagen" was measured to miss this pattern entirely (fell through to the
    # model, which hallucinated or stalled) because the original pattern required the preposition
    # immediately after the subject word with no filler allowed.
    rf"(?:{_WEATHER_WORD}|forecast|temperature|humidity|sunrise|sunset)\s+(?:only\s+|just\s+|specifically\s+)?"
    r"(?:in|for|at|like|around|over|today|tomorrow|tonight|now|currently|this\s+(?:morning|afternoon|evening|weekend))"
    # `wether` is admitted HERE and only here: this branch requires the subject word be followed
    # by a preposition and a place, and in that frame ("wether in Rome") the weather reading is
    # unambiguous even though the bare word is a real English noun (a ram) that the vocabulary
    # rightly excludes elsewhere -- "I don't know wether it rains" carries no preposition frame
    # and still matches nothing.
    rf"|(?:{_WEATHER_WORD}|wether)\s+(?:only\s+|just\s+|specifically\s+)?"
    r"(?:in|for|at|like|around|over)\s"
    r"|(?:current|today(?:'s)?|tomorrow(?:'s)?|tonight(?:'s)?)\s+"
    rf"(?:{_WEATHER_WORD}|forecast|temperature|humidity)"
    r"|(?:will\s+it|is\s+it|is\s+there|do\s+we)\s+"
    r"(?:rain|snow|freeze|storm|be\s+windy)"
    r"|(?:rain|snow|wind)\s+(?:in|for|at)\s+"
    r")\b"
    # A labeled section header ("Weather:" followed by a list of places) carries no in/for/at
    # trigger word at all -- the request is a live-weather lookup by section structure, not by
    # preposition. Structural (any weather/forecast word immediately followed by a colon), not a
    # list of specific place names.
    rf"|\b(?:{_WEATHER_WORD}|forecast)\s*:"
    # The PREPOSITION-LESS subject-first form ("weather warsaw and moscow") -- M3B, measured
    # live 2026-08-30: the operator's typo/sloppy phrasings drop the "in", and requiring the
    # preposition dropped the whole turn out of the lane. Structural gates bound the claim
    # (closed classes only -- no noun vocabulary): the weather word may not be function-word-
    # led (a determiner OR preposition before it marks a noun mention, not a request
    # subject), may not be followed by a preposition/temporal filler/auxiliary (a verb
    # phrase after it is prose -- "weather has been nice"), and the tail must carry a LIST
    # connector ("and"/","/"&") -- the shape of a place list, which abstract prose
    # ("weather patterns are complex", pinned by test_weather_live_request_recognizer)
    # never has. Over-claiming past the gates is bounded the way the plan builder already
    # is: a claimed turn that mints zero subtasks falls through to the model, exactly like
    # the SENTINEL G4 shape -- the recognizer, not this regex, decides whether a place was
    # actually named.
    rf"|(?<!the )(?<!a )(?<!an )(?<!for )(?<!of )(?<!in )(?<!on )(?<!at )(?<!with )(?<!about )"
    rf"(?<!from )(?<!by )(?<!to )"
    rf"\b{_WEATHER_WORD}\s+(?!(?:in|for|at|like|around|over|today|tomorrow|tonight|now|currently|only|just|specifically|is|are|was|were|be|been|being|has|have|had|does|did|will|would|can|could|should|must|shall|may|might)\b)"
    rf"[A-Za-z][A-Za-z,]*\s+(?:and|,|&)\s+[A-Za-z]",
    re.IGNORECASE,
)


def _looks_like_live_weather_request(text: str) -> bool:
    """Require a current-weather request, not merely weather-related prose.

    A request for the temperature of WATER is not one of these, whatever else it looks like. The
    provider behind this lane (`wttr.in`) answers conditions of the atmosphere at a place, and
    claiming a water-temperature turn meant taking the body of water as a city and rendering that
    place's AIR reading as the answer -- measured live: "what is the watter temperature in Balctic
    sea?" answered "Balctic Sea: Clear, 15 C ... Source: wttr.in" while the sea surface was near
    18 C. Declining here leaves the turn to lanes that can either find the right measurement or say
    they could not; see `core.measurement_medium`.
    """

    from core.measurement_medium import requests_a_water_temperature

    if requests_a_water_temperature(text):
        return False
    return bool(_WEATHER_LIVE_REQUEST_RE.search(text))


@lru_cache(maxsize=4096)
def _marker_re(marker: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", re.IGNORECASE)


def _names_a_marker(text: str, markers: tuple[str, ...] | frozenset[str]) -> bool:
    """Whether the text uses one of these markers AS A WORD, not as a fragment of another word.

    The test was `marker in lowered` -- a bare substring. `browse` is a live-lookup hint and
    `browser` is a noun that contains it, so measured 2026-08-18:

        "create hive mind task: Task: Standalone VOOL browser integration."
            -> live_info_mode() == "fresh_lookup"

    The live-info lane then claimed a hive-create turn. That false claim was harmless for as long
    as every search provider failed and the lane declined; it became visible the moment a provider
    that actually returns results was added to the chain (d7b73c22), and the lane started ending
    the turn. A capability detector had been acting as an authority gate, masked by the capability
    being broken.

    Boundaries are alphanumeric-only on purpose: a marker like "look it up" or "how much" contains
    spaces and must still match, and `\b` would not help at a space.
    """

    return any(_marker_re(marker).search(text) for marker in markers)


def live_info_mode(agent: Any, text: str, *, interpretation: Any) -> str:
    """Which live-information mode this text asks for, or "" for none.

    `agent` is optional. It was once required for both of the calls below, and that single
    requirement is why this family could not be registered as a per-clause probe in
    `answer_coverage._PROBES` -- so a weather clause was invisible to the claim arbitration, and
    "What is your name? Also what's the weather in Vilnius?" let the identity lane end the turn
    with the weather half unanswered. The builder check never needed the instance (the agent method
    is a bare passthrough to this module-level function), and the hint tail below is unreachable
    without an `interpretation` carrying topic hints. Passing `agent=None, interpretation=None`
    therefore gives the full text-only reading.
    """
    if stipulated_frame_forbids_retrieval(text):
        return ""
    constraints = analyze_retrieval_constraints(text)
    # The subject of a requested poem or story is not a lookup (see `creative_writing_remainder`).
    from core.agent_runtime.grounded_mode import creative_writing_remainder

    lowered = " ".join(creative_writing_remainder(constraints.eligible_text).strip().lower().split())
    if not lowered:
        return ""
    if looks_like_builder_request(lowered):
        return ""
    if _names_a_marker(lowered, _CLOCK_AND_DATE_MARKERS):
        return ""

    # A water-temperature ask is served by the TYPED live-data lane (its plan
    # builder mints water_temperature subtasks; `execution_requirements`
    # classifies it LIVE_DATA). The search-notes fast path below renders search
    # snippets as conditions — the wrong instrument for a sea reading — so this
    # branch DECLINES the fast path ("": no mode) and lets the turn reach the
    # typed lane. Before this branch, water asks fell through to the model,
    # which improvised unsourced prose (measured live, AUD-20260829-003 C4).
    from core.measurement_medium import requests_a_water_temperature

    if requests_a_water_temperature(lowered):
        return ""
    # A conversion between measurement units ("2 ounces of gold in grams") is a different measurement
    # from a price, however many market assets it names -- measured on the packaged build 2026-09-08,
    # where the asset word alone bought a sourced gold quote for a weight question.
    from core.measurement_medium import requests_a_unit_conversion

    if requests_a_unit_conversion(lowered):
        return ""
    # A purchasable-amount derivation ("how much gold would that get me") is the conductor's, never a
    # bare quote: the coverage grain already refuses the quote family this unit, and the lane's own
    # door must read it the same way (measured on the same build: the follow-up got a price, not the
    # amount the previous conversion buys).
    from core.conductor.operations import asks_for_a_purchasable_amount

    if asks_for_a_purchasable_amount(lowered):
        return ""

    # The vocabulary arms read what the message ASKS for -- the request units of the one
    # interpretation, through the requirements authority's own reader -- never the material it
    # describes. Measured served 2026-09-16 (candidate 035dea9b): a design brief listing "view bot
    # status and recent errors" among a proposed admin panel's features read "recent" beside "bot"
    # as a fresh lookup here; this lane retrieved, paid for a wording call and widened the turn to
    # current-information through the retrieval door while the authority itself read DIRECT.
    from core.execution_requirements import asked_text

    # ... and the ASKING UNITS are read through the SAME creative-writing remainder: a poem or
    # story whose subject is weather words is an authoring request, and its asking units name
    # that subject, not a lookup. Composing the two laws (the asked-text reader alone read
    # "write haiku about rain" as a fresh weather lookup and the live-info lane claimed turns
    # the model must answer -- reproduced by the lane's precedence suite, sloppy wordings).
    asked = " ".join(
        creative_writing_remainder(asked_text(constraints.eligible_text)).strip().lower().split()
    ) or lowered
    if _looks_like_live_weather_request(asked) and not constraints.forbids("weather"):
        return "weather"
    if _names_a_marker(asked, _NEWS_MARKERS) and not constraints.forbids("news"):
        return "news"
    if constraints.forbids_candidate(lowered):
        return ""
    if looks_like_live_recency_lookup(asked):
        return "fresh_lookup"
    if looks_like_explicit_lookup_request(asked) or looks_like_public_entity_lookup_request(asked):
        return "fresh_lookup"
    if _names_a_marker(asked, _LIVE_LOOKUP_HINT_MARKERS):
        return "fresh_lookup"
    if _names_a_marker(asked, _FRESH_LOOKUP_MARKERS):
        return "fresh_lookup"
    if _names_a_marker(asked, ("latest", "newest", "recent", "just released")) and any(
        marker in asked for marker in _LATEST_DOMAIN_MARKERS
    ):
        return "fresh_lookup"

    # Topic hints are derived from the original turn, so on a constrained turn they may describe
    # the removed negative clause rather than the remaining request ("do not search live news" ->
    # hints={web, news}).  Every explicit positive retrieval shape above has already had a chance
    # to claim the sanitized text.  Letting a hint resurrect a mode here would turn the prohibited
    # clause back into positive work through a side channel.
    if constraints.has_prohibition:
        return ""

    hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
    if "weather" in hints and _looks_like_live_weather_request(lowered) and not constraints.forbids("weather"):
        return "weather"
    if "news" in hints and not constraints.forbids("news"):
        return "news"
    if (
        "web" in hints
        and not constraints.forbids("fresh_lookup")
        and agent is not None
        and agent._wants_fresh_info(asked, interpretation=interpretation)
    ):
        return "fresh_lookup"
    return ""
