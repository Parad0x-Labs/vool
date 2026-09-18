"""Wave-3 contracts: fresh evidence is typed, scoped, and partial results survive.

Every provider is injected.  These tests make no network calls; the production adapters are
tested at their JSON boundary so offline CI is deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest import mock

from core.conductor.planner import ProposedClause, build_plan_from_clauses, plan_conductor_turn
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.fresh_data.fx import (
    FrankfurterFxProvider,
    FxQuote,
    FxQuoteStatus,
    resolve_fx_quote,
)
from core.fresh_data.places import (
    PlaceResult,
    PlaceSearchReport,
    PlaceSearchStatus,
    parse_place_search_request,
)
from core.fresh_data.research import (
    ResearchFieldEvidence,
    ResearchFieldStatus,
    compile_research_coverage,
    parse_structured_research_request,
)
from core.weather_result_contract import WeatherResult
from tests.conductor_product import compose_product

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _web_enabled():
    return mock.patch("core.policy_engine.allow_web_fallback", return_value=True)


def _fx_json(_url, _timeout, _headers):
    return {"date": "2026-08-12", "base": "TRY", "quote": "EUR", "rate": 0.025}


def test_frankfurter_contract_keeps_direction_decimal_source_and_times() -> None:
    provider = FrankfurterFxProvider(fetch_json=_fx_json, now=lambda: NOW)
    quote = provider.quote("try", "eur")

    assert quote.status is FxQuoteStatus.AVAILABLE
    assert quote.direction == "TRY_to_EUR"
    assert quote.rate == Decimal("0.025")
    assert quote.convert(Decimal("1000")) == Decimal("25.000")
    assert quote.observed_at.startswith("2026-08-12")
    assert quote.retrieved_at == NOW.isoformat()
    assert "Frankfurter" in quote.source
    assert quote.source_url.startswith("https://api.frankfurter.dev/v2/rate/")


def test_stale_fx_evidence_is_never_promoted_to_available() -> None:
    provider = FrankfurterFxProvider(
        fetch_json=lambda *_args: {"date": "2026-01-01", "rate": "0.025"},
        now=lambda: NOW,
        stale_after_seconds=48 * 60 * 60,
    )
    quote = resolve_fx_quote("TRY", "EUR", providers=(provider,))
    assert quote.status is FxQuoteStatus.STALE
    assert not quote.available
    assert quote.rate == Decimal("0.025")  # retained for diagnostics, never usable for arithmetic
    try:
        quote.convert(Decimal("100"))
    except ValueError as exc:
        assert "no usable" in str(exc)
    else:  # pragma: no cover - a stale quote becoming usable is a critical regression
        raise AssertionError("stale quote was used")


class _StaticFx:
    def __init__(self, name: str, rate: str):
        self.name = name
        self.rate = Decimal(rate)

    def quote(self, base, quote, *, timeout_s=8.0):
        return FxQuote(
            base=base,
            quote=quote,
            status=FxQuoteStatus.AVAILABLE,
            rate=self.rate,
            observed_at=NOW.isoformat(),
            retrieved_at=NOW.isoformat(),
            source=self.name,
        )


def test_conflicting_fx_sources_return_no_rate() -> None:
    quote = resolve_fx_quote(
        "TRY",
        "EUR",
        providers=(_StaticFx("one", "0.025"), _StaticFx("two", "0.030")),
        max_relative_disagreement=Decimal("0.02"),
    )
    assert quote.status is FxQuoteStatus.CONFLICTING
    assert quote.rate is None
    assert quote.compared_sources == ("one", "two")


def test_fx_provider_failure_is_explicit_and_contains_no_fallback_number() -> None:
    provider = FrankfurterFxProvider(
        fetch_json=mock.Mock(side_effect=OSError("offline")),
        now=lambda: NOW,
    )
    quote = provider.quote("TRY", "EUR")
    assert quote.status is FxQuoteStatus.UNAVAILABLE
    assert quote.rate is None
    assert "offline" in quote.failure_reason


def test_place_parser_is_domain_scoped_not_a_workspace_catch_all() -> None:
    assert parse_place_search_request("Find tyre repair near Vilnius") is not None
    reported = parse_place_search_request("where can I change my tyres in Vilnius")
    assert reported is not None
    assert reported.service == "change my tyres"
    assert reported.location == "Vilnius"
    assert parse_place_search_request("inspect the provider implementation") is None
    assert parse_place_search_request("shut off the overflowing sink") is None


def test_current_bare_currency_pair_is_a_rate_lookup() -> None:
    from core.currency_intent import fx_rate_lookup_intent

    request = fx_rate_lookup_intent("What is EUR to USD today")

    assert request is not None
    assert request.base.code == "EUR"
    assert request.quote.code == "USD"
    assert request.asked_for_current is True


def test_research_coverage_keeps_verified_fields_and_names_missing_ones() -> None:
    request = parse_structured_research_request(
        "Research the Volkswagen Passat and tell me its model, engine, and colour"
    )
    assert request is not None
    report = compile_research_coverage(
        request.subject,
        request.fields,
        (
            ResearchFieldEvidence("model", "Passat B8", "manufacturer archive"),
            ResearchFieldEvidence("engine", "2.0 TDI", "vehicle listing"),
        ),
        retrieved_at=NOW.isoformat(),
    )
    assert report.verified_count == 2
    assert report.unavailable_count == 1
    by_field = {item.field: item for item in report.fields}
    assert by_field["model"].status is ResearchFieldStatus.VERIFIED
    assert by_field["colour"].status is ResearchFieldStatus.UNAVAILABLE
    assert "Passat B8" in by_field["model"].value


def test_reported_vw_question_compiles_to_field_complete_research_without_a_planner_call() -> None:
    prompt = "What is the most sold VW model, what engine and what colour?"
    request = parse_structured_research_request(prompt)

    assert request is not None
    assert request.subject == "the most sold VW"
    assert request.fields == ("model", "engine", "colour")
    planner = mock.Mock(side_effect=AssertionError("deterministic research must not call a model"))
    plan = plan_conductor_turn(prompt, ask_model=planner, plan_id="vw-research")
    assert plan is not None
    assert [node.operation for node in plan.nodes] == ["structured_research"]
    planner.assert_not_called()


def test_research_field_spelling_alias_does_not_create_a_false_missing_field() -> None:
    report = compile_research_coverage(
        "Volkswagen Passat",
        ("colour",),
        (ResearchFieldEvidence("color", "blue", "verified listing"),),
        retrieved_at=NOW.isoformat(),
    )
    assert report.verified_count == 1
    assert report.fields[0].field == "colour"
    assert report.fields[0].value == "blue"


MIXED = (
    "Get the current TRY/EUR exchange rate, get current weather for Vilnius, "
    "and find tyre repair near Vilnius."
)
MIXED_PLAN = json.dumps(
    [
        {
            "request": "Get the current TRY/EUR exchange rate",
            "operation": "fx_quote",
            "depends_on": [],
        },
        {
            "request": "get current weather for Vilnius",
            "operation": "weather_lookup",
            "depends_on": [],
        },
        {
            "request": "find tyre repair near Vilnius",
            "operation": "place_search",
            "depends_on": [],
        },
    ]
)


class _PlaceProvider:
    name = "test places"

    def search(self, request, *, timeout_s=8.0):
        return PlaceSearchReport(
            service=request.service,
            location=request.location,
            status=PlaceSearchStatus.AVAILABLE,
            results=(
                PlaceResult(
                    name="Tyre Hub",
                    service=request.service,
                    location=request.location,
                    address="10 Test Street, Vilnius",
                    source=self.name,
                ),
            ),
            source=self.name,
            retrieved_at=NOW.isoformat(),
        )


def _weather(location, **_kwargs):
    return WeatherResult(
        location=location,
        place_label=location.title(),
        condition="Clear",
        temperature_c=21.0,
        feels_like_c=21.0,
        humidity_pct=50.0,
        wind_kmph=5.0,
        observed_at=NOW.isoformat(),
        source_label="weather test",
        source_url="https://weather.invalid",
    )


def test_smoke_fx_weather_tyres_use_three_explicit_effect_compatible_capabilities() -> None:
    plan = plan_conductor_turn(MIXED, ask_model=lambda _s, _p: MIXED_PLAN, plan_id="mixed")
    assert plan is not None
    assert {node.operation for node in plan.nodes} == {
        "fx_quote",
        "weather_lookup",
        "place_search",
    }
    assert not any(node.operation == "workspace_investigation" for node in plan.nodes)

    tool = mock.Mock(side_effect=AssertionError("workspace tool must not be called"))
    with (
        _web_enabled(),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather),
    ):
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={
                    "allow_remote_fetch": True,
                    "fx_provider": _StaticFx("test fx", "0.025"),
                    "place_provider": _PlaceProvider(),
                },
                run_tool_intent=tool,
            ),
        )
    answer = compose_product(plan, outcomes)
    assert answer.complete and answer.unserved_count == 0
    assert "TRY/EUR" in answer.text and "0.025" in answer.text
    assert "Vilnius" in answer.text and "21.0" in answer.text
    assert "Tyre Hub" in answer.text
    tool.assert_not_called()


def test_conductor_fx_uses_trusted_app_surface_default_without_explicit_flag() -> None:
    request = "Get the current TRY/EUR exchange rate"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "fx_quote", ()),),
        original_request=request,
        plan_id="fx-default-live",
    )
    fetcher = mock.Mock(return_value={"date": "2026-08-12", "rate": "0.025"})
    with _web_enabled():
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={
                    "surface": "channel",
                    "platform": "openclaw",
                    "fx_fetch_json": fetcher,
                    "fx_now": lambda: NOW,
                }
            ),
        )
    assert outcomes[0].succeeded
    assert outcomes[0].result["status"] == "available"
    assert outcomes[0].result["rate"] == "0.025"
    fetcher.assert_called_once()


def test_default_place_search_no_results_is_honest_and_never_calls_workspace_search() -> None:
    clauses = (
        ProposedClause(0, "find tyre repair near Vilnius", "place_search", ()),
    )
    plan = build_plan_from_clauses(
        clauses,
        original_request="find tyre repair near Vilnius",
        plan_id="place",
    )
    tool = mock.Mock(side_effect=AssertionError("workspace search is not a place provider"))
    with (
        _web_enabled(),
        mock.patch(
            "retrieval.web_adapter.WebAdapter.search_query",
            return_value=[],
        ) as search_query,
    ):
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={"allow_remote_fetch": True},
                run_tool_intent=tool,
            ),
        )
    answer = compose_product(plan, outcomes)
    assert outcomes[0].succeeded
    assert outcomes[0].result["status"] == "no_results"
    assert "no sourced place candidates" in answer.text
    assert "No local result was invented" in answer.text
    search_query.assert_called_once()
    tool.assert_not_called()


def test_trusted_app_surface_uses_default_sourced_place_discovery_without_explicit_flag() -> None:
    request = "find tyre repair near Vilnius"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "place_search", ()),),
        original_request=request,
        plan_id="place-default-live",
    )
    note = {
        "result_title": "Vilnius Tyre Service",
        "result_url": "https://tyres.example/repair",
        "summary": "Tyre repair service in Vilnius. Call to confirm opening hours.",
        "source_label": "searxng",
        "search_provider": "searxng",
    }
    with (
        _web_enabled(),
        mock.patch(
            "retrieval.web_adapter.WebAdapter.search_query",
            return_value=[note],
        ) as search_query,
    ):
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={"surface": "channel", "platform": "openclaw"}
            ),
        )
    answer = compose_product(plan, outcomes)
    assert outcomes[0].succeeded
    assert outcomes[0].result["status"] == "available"
    assert outcomes[0].result["results"][0]["address"] == ""
    assert outcomes[0].result["results"][0]["evidence_excerpt"].startswith(
        "Tyre repair service"
    )
    assert "Vilnius Tyre Service" in answer.text
    assert "https://tyres.example/repair" in answer.text
    assert "not guaranteed-current business records" in answer.text
    search_query.assert_called_once_with(
        "tyre repair near Vilnius",
        limit=5,
        source_label="place.search",
        total_budget_s=8.0,
    )


class _PartialResearch:
    name = "test research"

    def research_fields(self, subject, fields, *, timeout_s=8.0):
        return (
            ResearchFieldEvidence("model", "Passat B8", self.name),
            ResearchFieldEvidence("engine", "2.0 TDI", self.name),
        )


def test_vw_model_engine_colour_partial_research_is_not_discarded() -> None:
    request = "Research the Volkswagen Passat and tell me its model, engine, and colour"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "structured_research", ()),),
        original_request=request,
        plan_id="vw",
    )
    with _web_enabled():
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={
                    "allow_remote_fetch": True,
                    "research_field_provider": _PartialResearch(),
                }
            ),
        )
    answer = compose_product(plan, outcomes)
    assert outcomes[0].succeeded and answer.complete
    assert "model: Passat B8" in answer.text
    assert "engine: 2.0 TDI" in answer.text
    assert "colour: unavailable" in answer.text


def test_research_provider_failure_marks_every_field_unavailable_without_losing_coverage() -> None:
    request = "Research the Volkswagen Passat and tell me its model, engine, and colour"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "structured_research", ()),),
        original_request=request,
        plan_id="vw-offline",
    )
    provider = mock.Mock()
    provider.research_fields.side_effect = OSError("offline")
    with _web_enabled():
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={"allow_remote_fetch": True, "research_field_provider": provider}
            ),
        )
    fields = outcomes[0].result["fields"]
    assert outcomes[0].succeeded and len(fields) == 3
    assert all(item["status"] == "unavailable" for item in fields)
    assert all("provider failed" in item["failure_reason"] for item in fields)


def test_builtin_candidate_web_notes_are_not_promoted_to_verified_research_fields() -> None:
    request = "Research the Volkswagen Passat and tell me its model, engine, and colour"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "structured_research", ()),),
        original_request=request,
        plan_id="vw-no-authoritative-provider",
    )
    with _web_enabled():
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={"surface": "channel", "platform": "openclaw"}
            ),
        )
    fields = outcomes[0].result["fields"]
    assert all(item["status"] == "unavailable" for item in fields)
    assert all("candidate-only notes" in item["failure_reason"] for item in fields)


def test_tool_prohibition_prevents_fx_provider_use_and_explains_verification_boundary() -> None:
    request = "Get the current TRY/EUR exchange rate without using the web"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "fx_quote", ()),),
        original_request=request,
        plan_id="offline-fx",
    )
    provider = mock.Mock()
    provider.quote.side_effect = AssertionError("provider must not run")
    with _web_enabled():
        outcomes = run_conductor_plan(
            plan,
            context=NodeContext(
                source_context={"allow_remote_fetch": True, "fx_provider": provider}
            ),
        )
    answer = compose_product(plan, outcomes)
    assert outcomes[0].succeeded
    assert outcomes[0].result["rate"] is None
    assert "verification requires retrieval" in answer.text
    assert "forbids" in answer.text
    provider.quote.assert_not_called()


def test_single_currency_frontdoor_uses_opted_in_public_fx_source() -> None:
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fetcher = mock.Mock(return_value={"date": "2026-08-12", "rate": "0.025"})
    with _web_enabled():
        reply = _currency_reply(
            "1000 TRY to EUR",
            session_id="fresh-data-test",
            source_context={
                "conversation_history": [],
                "allow_remote_fetch": True,
                "fx_fetch_json": fetcher,
                "fx_now": lambda: NOW,
            },
        )
    assert reply is not None
    assert reply["grounded"] == "live_rate"
    assert "25 EUR" in reply["response"]
    assert "2026-08-12" in reply["response"]
    fetcher.assert_called_once()


def test_single_currency_frontdoor_uses_trusted_app_surface_default() -> None:
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fetcher = mock.Mock(return_value={"date": "2026-08-12", "rate": "0.025"})
    with _web_enabled():
        reply = _currency_reply(
            "1000 TRY to EUR",
            session_id="fresh-data-trusted-surface",
            source_context={
                "surface": "channel",
                "platform": "openclaw",
                "conversation_history": [],
                "fx_fetch_json": fetcher,
                "fx_now": lambda: NOW,
            },
        )
    assert reply is not None
    assert reply["grounded"] == "live_rate"
    assert "25 EUR" in reply["response"]
    fetcher.assert_called_once()


def test_single_currency_frontdoor_obeys_no_tools_without_calling_configured_source() -> None:
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fetcher = mock.Mock(side_effect=AssertionError("retrieval prohibition was ignored"))
    with _web_enabled():
        reply = _currency_reply(
            "Get current TRY/EUR exchange rate without using the web",
            session_id="fresh-data-test",
            source_context={
                "conversation_history": [],
                "allow_remote_fetch": True,
                "fx_fetch_json": fetcher,
            },
        )
    assert reply is not None
    assert reply["grounded"] == "retrieval_prohibited_declined"
    assert "requires external retrieval" in reply["response"]
    assert "forbidden term" not in reply["response"].lower()
    fetcher.assert_not_called()


def test_user_supplied_fx_rate_never_triggers_external_retrieval() -> None:
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fetcher = mock.Mock(
        side_effect=AssertionError("user supplied a rate; retrieval is unnecessary")
    )
    reply = _currency_reply(
        "1000 TRY to EUR at 0.025",
        session_id="fresh-data-test",
        source_context={
            "conversation_history": [],
            "allow_remote_fetch": True,
            "fx_fetch_json": fetcher,
        },
    )
    assert reply is not None
    assert reply["grounded"] == "user_supplied_rate"
    assert "25 EUR" in reply["response"]
    fetcher.assert_not_called()


def test_runtime_web_policy_can_disable_fx_even_when_the_surface_requests_remote_fetch() -> None:
    from core.agent_runtime.turn_frontdoor import _currency_reply

    fetcher = mock.Mock(side_effect=AssertionError("runtime web policy was bypassed"))
    with mock.patch("core.policy_engine.allow_web_fallback", return_value=False):
        reply = _currency_reply(
            "1000 TRY to EUR",
            session_id="fresh-data-test",
            source_context={
                "conversation_history": [],
                "allow_remote_fetch": True,
                "fx_fetch_json": fetcher,
            },
        )
    assert reply is not None
    assert reply["grounded"] == "retrieval_disabled_declined"
    assert "disabled by the runtime policy" in reply["response"]
    fetcher.assert_not_called()


def test_configured_place_provider_has_no_authority_without_turn_retrieval_permission() -> None:
    request = "find tyre repair near Vilnius"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "place_search", ()),),
        original_request=request,
        plan_id="place-policy",
    )
    provider = mock.Mock()
    provider.search.side_effect = AssertionError("configuration was mistaken for authority")
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(
            source_context={"allow_remote_fetch": False, "place_provider": provider}
        ),
    )
    assert outcomes[0].succeeded
    assert outcomes[0].result["status"] == "unavailable"
    assert "not enabled" in outcomes[0].result["failure_reason"]
    provider.search.assert_not_called()
