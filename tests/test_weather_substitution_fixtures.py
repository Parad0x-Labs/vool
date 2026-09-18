"""Fixture factories for weather SUBSTITUTION cases -- `place_label` (the wttr.in-resolved
STATION) genuinely diverging from `location` (what the user actually asked for).

Why this exists
----------------
Every existing weather fixture in this suite sets `place_label` equal to `location` (or
`location.title()`). Read directly, one construction site per file, 24 lines across 17 files:

    test_retry_turn_identity_repair.py:70,99
    test_origin_conversation_event_repair.py:58,197
    test_continue_bookkeeping_repair.py:61
    test_conductor_multi_intent.py:68
    test_retry_idempotency_repair.py:265,312
    test_interrupted_subtask_reconciliation_repair.py:117
    test_node_events_reach_the_pollable_stream.py:263
    test_attempt_followup_resolution.py:73,120,141,200,248
    test_canonical_obligation_floor.py:70
    test_live_data_entity_contamination_hotfix.py:538
    test_live_data_runner.py:36
    test_conductor_concurrency.py:194
    test_repair9_mutation_matrix.py:112,244,339
    test_fresh_data_capabilities.py:240
    test_attempt_retry.py:128
    test_live_data_turn_integration.py:50
    test_live_data_concurrency.py:50

`WeatherResult.summary_text` / `answer_text` (core/weather_result_contract.py:43-59) render
`self.place_label` and never read `self.location`. A repair that swaps the rendered subject from
`place_label` (the STATION wttr.in actually resolved to) to `location` (what the user asked for)
is therefore a NO-OP against every fixture above: `place_label == location` on all of them, so the
two code paths produce byte-identical output and reverting the fix would leave all 17 files green.
A fix whose sabotage cannot bite must not land (see AGENT_HANDOVER-adjacent repair discipline).

This module is the missing seam: fixtures built from real, measured wttr.in station
substitutions, where the two strings are genuinely different, so a future repair's sabotage has
something to bite against. It is deliberately NOT itself a test of the repair (there is no repair
yet to test) -- it is infrastructure a future `test_weather_*` file imports.

Two production shapes are covered, both confirmed by reading the real code, not assumed:

  - `WeatherResult` (core/weather_result_contract.py) -- what `structured_weather_lookup` returns
    and what most of the 17 fixtures above construct directly. `place_label` is built from
    `area_name`/`country_name` (the resolved station), `location` is the caller's own input,
    kept as a separate field (tools/web/web_research.py:2264-2280).
  - the runner outcome-result dict shape built by `_run_weather_subtask`
    (core/agent_runtime/live_data_runner.py:118-128) -- forwards `place_label` and DROPS
    `location` entirely. Confirmed by reading that function: its returned dict's keys are
    exactly `condition`, `temperature_c`, `feels_like_c`, `high_c`, `low_c`, `place_label`,
    `source`, `source_url`, `observed_at`. No `location` key anywhere in that dict, nor anywhere
    in `core/agent_runtime/live_data_render.py` (`_weather_row` renders `"City"` from
    `outcome.subtask.entity` -- the REQUESTED name -- never from a `place_label` it does not
    carry).

A third, smaller shape is included because the Antarctica case specifically needs it: the raw
`nearest_area` payload (`areaName`/`country`/`region`/`latitude`/`longitude`) that
`tools/web/web_research._weather_place_corresponds` consumes directly, before any `WeatherResult`
is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _weather_render
from core.weather_result_contract import WeatherResult


@dataclass(frozen=True)
class SubstitutionCase:
    """One real, measured wttr.in request/station-resolution pair.

    `category` is documentation, not behaviour:
      - "near"           -- station is a district/neighbourhood of the requested city, distance
                             measured and small (<=3 km). A future disclosure rule must stay
                             SILENT on these -- see `MEASURED_NEAR_CASES` / `near_case_weather_result`.
      - "near_unmeasured" -- station-name divergence is on the record (cited in
                             `_weather_place_corresponds`'s own docstring), but no km figure was
                             measured for it, so it is not asserted to be within any threshold.
      - "far"            -- station is materially far from the requested place; coordinates known.
      - "no_coords"      -- the offline tzdb coordinate check has nothing to compare against for
                             this request at all (not "checked and found near" -- unreachable),
                             which is exactly the shape of the Antarctica fail-open gap.
    """

    name: str
    location: str
    resolved_area: str
    resolved_country: str = ""
    distance_km: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    category: str = "near"

    @property
    def place_label(self) -> str:
        """Mirrors `structured_weather_lookup`'s own `place` construction
        (tools/web/web_research.py:2264): area name alone, unless a distinct country is present."""
        if self.resolved_country and self.resolved_country.casefold() != self.resolved_area.casefold():
            return f"{self.resolved_area}, {self.resolved_country}"
        return self.resolved_area


# --- the real, measured pairs -------------------------------------------------------------------
# Coordinates are approximate real-world values for the named place, kept only as plausible fixture
# data (self-tested for presence/absence below, never for exact distance math -- this module does
# not re-derive `distance_km` from `latitude`/`longitude`, it carries both as independently
# reported facts, the same way a real wttr.in `nearest_area` payload would).

OSLO_VIKA = SubstitutionCase(
    "oslo_vika", "oslo", "Vika", "Norway",
    distance_km=1.3, latitude=59.9127, longitude=10.7278, category="near",
)
LONDON_STRAND = SubstitutionCase(
    "london_strand", "london", "Strand", "United Kingdom",
    distance_km=1.3, latitude=51.5117, longitude=-0.1180, category="near",
)
WARSAW_POWISLE = SubstitutionCase(
    "warsaw_powisle", "warsaw", "Powisle", "Poland",
    distance_km=2.0, latitude=52.2400, longitude=21.0300, category="near",
)
# Distance not measured in the audit this module is built from -- cited by name only in
# `_weather_place_corresponds`'s own docstring (tools/web/web_research.py:2107-2109) as further
# examples of the same district-vs-city pattern as Warsaw. Kept out of `MEASURED_NEAR_CASES` /
# the "must stay silent" factory on purpose: "near" is a claim about distance, and no distance was
# measured for these two.
MANCHESTER_SEDGLEY_PARK = SubstitutionCase(
    "manchester_sedgley_park", "manchester", "Sedgley Park", "United Kingdom",
    category="near_unmeasured",
)
KRAKOW_KROWODRZA = SubstitutionCase(
    "krakow_krowodrza", "krakow", "Krowodrza", "Poland",
    category="near_unmeasured",
)
NYC_NEW_YORK = SubstitutionCase(
    "nyc_new_york", "nyc", "New York", "United States of America",
    category="near_unmeasured",
)

TOKYO_SHIKINEJIMA = SubstitutionCase(
    "tokyo_shikinejima", "tokyo", "Shikinejima", "Japan",
    distance_km=154.8, latitude=34.3167, longitude=139.2167, category="far",
)

# `_tzdb_coords_for("antarctica")` (tools/web/web_research.py) has no tzdb zone leaf within edit
# distance of "antarctica", so it returns None, and `_weather_place_corresponds` never reaches its
# coordinate check -- it falls straight to `return True` (fail OPEN) regardless of how far the
# resolved area actually is. `latitude`/`longitude` are left None here ON PURPOSE: this case
# models "the offline check cannot be run at all", not "the offline check ran and found a match".
ANTARCTICA_NO_COORDS = SubstitutionCase(
    "antarctica_no_coords", "antarctica", "McMurdo Station", "Antarctica",
    category="no_coords",
)

MEASURED_NEAR_CASES: tuple[SubstitutionCase, ...] = (OSLO_VIKA, LONDON_STRAND, WARSAW_POWISLE)
UNMEASURED_NEAR_CASES: tuple[SubstitutionCase, ...] = (MANCHESTER_SEDGLEY_PARK, KRAKOW_KROWODRZA, NYC_NEW_YORK)
FAR_CASES: tuple[SubstitutionCase, ...] = (TOKYO_SHIKINEJIMA,)
NO_COORDS_CASES: tuple[SubstitutionCase, ...] = (ANTARCTICA_NO_COORDS,)
ALL_CASES: tuple[SubstitutionCase, ...] = (
    MEASURED_NEAR_CASES + UNMEASURED_NEAR_CASES + FAR_CASES + NO_COORDS_CASES
)


# --- factory 1: any substitution pair -> WeatherResult / runner-payload dict --------------------

def weather_result_for(case: SubstitutionCase, **overrides: Any) -> WeatherResult:
    """A `WeatherResult` where `place_label != location`, built from a measured substitution
    pair. Same kwargs and defaults as the 17 existing fixtures (see module docstring) except
    `place_label` is the case's resolved station/area, never `location` itself -- a drop-in
    replacement for `place_label=location`/`location.title()` at any of those call sites."""
    fields: dict[str, Any] = dict(
        location=case.location,
        place_label=case.place_label,
        condition="Clear",
        temperature_c=20.0,
        feels_like_c=19.0,
        humidity_pct=50.0,
        wind_kmph=5.0,
        observed_at="12:00 PM",
        source_label="wttr.in",
        source_url=f"https://wttr.in/{case.location}",
    )
    fields.update(overrides)
    return WeatherResult(**fields)


def runner_payload_for(case: SubstitutionCase, **overrides: Any) -> dict[str, Any]:
    """The `_run_weather_subtask` outcome-result dict shape
    (core/agent_runtime/live_data_runner.py:118-128): forwards `place_label`, carries no
    `location` key at all. `overrides` can still inject a `location` key if a future test wants
    to prove the ABSENCE is a deliberate default rather than something this factory forgot."""
    payload: dict[str, Any] = {
        "condition": "Clear",
        "temperature_c": 20.0,
        "feels_like_c": 19.0,
        "high_c": None,
        "low_c": None,
        "place_label": case.place_label,
        "source": "wttr.in",
        "source_url": f"https://wttr.in/{case.location}",
        "observed_at": "12:00 PM",
    }
    payload.update(overrides)
    return payload


def provider_area_payload_for(case: SubstitutionCase) -> dict[str, Any]:
    """The raw wttr.in `nearest_area` shape consumed inside `_weather_fallback` /
    `structured_weather_lookup` (tools/web/web_research.py) BEFORE `place_label` is built --
    `areaName`/`country`/`region`/`latitude`/`longitude`. Feeds `_weather_place_corresponds`
    directly, which is where the Antarctica fail-open gap actually lives."""
    return {
        "areaName": case.resolved_area,
        "country": case.resolved_country,
        "region": "",
        "latitude": case.latitude,
        "longitude": case.longitude,
    }


# --- factory 2: near/correct pairs that must stay SILENT under a future disclosure rule ---------

def near_case_weather_result(case: SubstitutionCase, **overrides: Any) -> WeatherResult:
    """Same shape as `weather_result_for`, restricted to `MEASURED_NEAR_CASES` (Vika, Strand,
    Powisle) -- a future disclosure rule ("the station wttr.in used is not exactly what you
    asked for") must stay silent on these, because they are the SAME city under a different
    station name, not a materially different place. Raises `ValueError` for anything outside
    `MEASURED_NEAR_CASES`, so a caller cannot accidentally run the "must stay silent" proof
    against a far or unmeasured case."""
    if case not in MEASURED_NEAR_CASES:
        raise ValueError(f"{case.name!r} is not a measured near/correct case (category={case.category!r})")
    return weather_result_for(case, **overrides)


# --- self-test: the factories work ---------------------------------------------------------------
# Everything below asserts on the FIXTURE's own properties (does it diverge, does it carry the
# coordinates it claims to, is it accepted by the real constructors/renderers without raising) --
# never on whether the product currently renders the "right" answer. There is no repair landed yet
# for these fixtures to prove; that proof belongs to the future test that imports this module.


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_weather_result_for_diverges_and_is_accepted_by_the_real_constructor(case: SubstitutionCase) -> None:
    result = weather_result_for(case)
    assert isinstance(result, WeatherResult)
    assert result.location == case.location
    assert result.place_label == case.place_label
    assert result.place_label != result.location, "fixture must diverge -- that is the whole point"
    # Accepted by the real renderers without raising -- their CONTENT is production behaviour
    # under future repair, not this fixture's own correctness, so it is not asserted here.
    assert result.summary_text()
    assert result.answer_text()
    assert result.to_payload()["place_label"] == case.place_label
    assert result.to_payload()["location"] == case.location


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_runner_payload_for_matches_the_production_shape(case: SubstitutionCase) -> None:
    payload = runner_payload_for(case)
    assert set(payload) == {
        "condition", "temperature_c", "feels_like_c", "high_c", "low_c",
        "place_label", "source", "source_url", "observed_at",
    }
    assert "location" not in payload, "live_data_runner.py:118-128 drops location -- the fixture must too"
    assert payload["place_label"] == case.place_label
    assert payload["place_label"] != case.location

    # Accepted by the real `_weather_render` renderer (core/conductor/operations.py:293) without
    # raising -- the exact function the audit named as preferring `result["place_label"]`.
    node = ConductorNode(
        node_id=f"n-{case.name}", operation="weather", request_text=case.location,
        arguments={"entity": case.location.title(), "location": case.location},
    )
    rendered = _weather_render(node, payload)
    assert isinstance(rendered, str) and rendered


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_provider_area_payload_for_is_accepted_by_the_real_correspondence_gate(case: SubstitutionCase) -> None:
    from tools.web.web_research import _weather_place_corresponds

    payload = provider_area_payload_for(case)
    assert payload["areaName"] == case.resolved_area
    assert payload["latitude"] == case.latitude
    assert payload["longitude"] == case.longitude
    # Accepted without raising. The boolean this returns is the production gate's own behaviour
    # (including the Antarctica fail-open gap this module documents) -- not asserted here, per
    # the "fixture properties, not product correctness" rule for this self-test.
    _weather_place_corresponds(
        case.location, payload["areaName"], payload["country"], payload["region"],
        payload["latitude"], payload["longitude"],
    )


def test_tokyo_case_carries_its_coordinates() -> None:
    assert TOKYO_SHIKINEJIMA.latitude is not None
    assert TOKYO_SHIKINEJIMA.longitude is not None
    assert TOKYO_SHIKINEJIMA.distance_km == pytest.approx(154.8)


def test_antarctica_case_carries_no_coordinates() -> None:
    """Models the fail-open gap: no coordinates means the offline check cannot be run, not that
    it ran and found a match."""
    assert ANTARCTICA_NO_COORDS.latitude is None
    assert ANTARCTICA_NO_COORDS.longitude is None
    assert ANTARCTICA_NO_COORDS.distance_km is None


@pytest.mark.parametrize("case", MEASURED_NEAR_CASES, ids=lambda c: c.name)
def test_measured_near_cases_are_genuinely_close(case: SubstitutionCase) -> None:
    """The "must stay silent" claim only holds if these are actually near -- guard the data,
    not just the label."""
    assert case.distance_km is not None
    assert case.distance_km <= 3.0
    assert case.latitude is not None and case.longitude is not None


@pytest.mark.parametrize("case", MEASURED_NEAR_CASES, ids=lambda c: c.name)
def test_near_case_weather_result_still_diverges(case: SubstitutionCase) -> None:
    result = near_case_weather_result(case)
    assert result.place_label != result.location


@pytest.mark.parametrize("case", FAR_CASES + NO_COORDS_CASES + UNMEASURED_NEAR_CASES, ids=lambda c: c.name)
def test_near_case_factory_rejects_anything_outside_measured_near_cases(case: SubstitutionCase) -> None:
    with pytest.raises(ValueError):
        near_case_weather_result(case)


def test_all_cases_have_unique_names() -> None:
    names = [case.name for case in ALL_CASES]
    assert len(names) == len(set(names))


def test_all_cases_genuinely_diverge_by_construction() -> None:
    """Every single case, not just a sample -- this is the property the whole module exists to
    guarantee."""
    for case in ALL_CASES:
        assert case.place_label != case.location, case.name
