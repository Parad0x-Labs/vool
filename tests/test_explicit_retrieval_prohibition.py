"""Explicit negative retrieval instructions are vetoes, never positive lookup subtasks.

This family intentionally varies topic, syntax, punctuation, register, and spelling.  The contract
is semantic: a negative retrieval clause is removed from candidate work and retained as a typed
permission veto.  No city, asset, provider, or exact benchmark sentence is special-cased.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.agent_runtime.fast_live_info_price import recover_price_lookup_query
from core.agent_runtime.live_data_plan import build_live_data_plan
from core.execution.planner import should_attempt_tool_intent
from core.execution_requirements import requirements_for
from core.retrieval_constraints import analyze_retrieval_constraints
from core.task_router import classify, looks_like_explicit_lookup_request, looks_like_live_recency_lookup

PHOENIX_REPORTED = (
    'If my lead dev tells me our production server is "on fire" during a heatwave in Phoenix, '
    'and asks me to "kill the zombie processes," what do the terms "on fire" and "zombie '
    'processes" signify as metaphors in system engineering? Do NOT check the temperature in '
    "Phoenix, do NOT fetch server status metrics, and do NOT search live tech news headlines."
)

FALLING_KNIFE_REPORTED = (
    'What does "catching a falling knife" mean in stock trading? Do NOT look up any live stock '
    "prices, tickers, or financial news."
)

ETH_REPORTED = "No web, no live lookup, no tools, no fetching. Tell me the current ETH price."

CLEAN_PARAPHRASES = (
    (
        "During a Phoenix incident review, explain why engineers say a service is on fire. "
        "Do not check Phoenix weather or temperature."
    ),
    (
        "In stock trading, define dead cat bounce. Do not retrieve live prices or market news."
    ),
    (
        "Explain what a zombie process is. Never search for server status or live headlines."
    ),
    "No live data lookup. What is Ethereum worth right now?",
    (
        "Without searching online, explain why saying the market is bleeding is metaphorical."
    ),
)

SLOPPY_VARIANTS = (
    "phoenix prod is on fire lol — dont chek temp/weather there, just explain the metaphor",
    "falling knife meaning? dont lookup live stock prices pls; definition only",
    "no web pls. whats eth worth rn?",
    "dont search financial news or prices — tell me what risk-on means",
    "dont chek live price, just tell me what catching a falling knife means",
    "no search pls — explain what market capitulation means",
)

NEGATIVE_FAMILY = (
    PHOENIX_REPORTED,
    FALLING_KNIFE_REPORTED,
    ETH_REPORTED,
    *CLEAN_PARAPHRASES,
    *SLOPPY_VARIANTS,
)


def _agent_stub() -> SimpleNamespace:
    agent = SimpleNamespace(
        _looks_like_builder_request=lambda _text: False,
        _wants_fresh_info=lambda _text, interpretation: True,
    )
    return agent


@pytest.mark.parametrize("prompt", NEGATIVE_FAMILY)
def test_negative_retrieval_family_never_becomes_live_data_or_a_lookup_route(prompt: str) -> None:
    constraints = analyze_retrieval_constraints(prompt)
    requirements = requirements_for(prompt)

    assert constraints.has_prohibition, prompt
    assert requirements.answer_mode != "LIVE_DATA", prompt
    assert requirements.tools_required is False, prompt
    assert build_live_data_plan(prompt, plan_id="p", attempt_id="a") is None, prompt
    assert looks_like_explicit_lookup_request(prompt) is False, prompt
    assert looks_like_live_recency_lookup(prompt) is False, prompt
    assert live_info_mode(
        _agent_stub(),
        prompt,
        interpretation=SimpleNamespace(topic_hints=["web", "weather", "news"]),
    ) == "", prompt
    assert recover_price_lookup_query(prompt, source_context={}) == "", prompt


def test_reported_phoenix_negative_clause_cannot_create_a_location_or_status_lookup_node() -> None:
    constraints = analyze_retrieval_constraints(PHOENIX_REPORTED)

    assert "Do NOT fetch server status metrics" not in constraints.eligible_text
    assert "Do NOT check the temperature" not in constraints.eligible_text
    assert "what do the terms" in constraints.eligible_text
    assert constraints.prohibited_toolsets == frozenset({"weather", "news"})
    assert build_live_data_plan(PHOENIX_REPORTED, plan_id="p", attempt_id="a") is None


def test_current_value_with_retrieval_forbidden_is_neither_tool_required_nor_inference_allowed() -> None:
    requirements = requirements_for("No live lookup. What is ETH worth right now?")

    assert requirements.answer_mode == "DIRECT"
    assert requirements.tools_required is False
    assert requirements.allowed_toolsets == ()
    assert requirements.inference_allowed is False
    assert requirements.current_information_required is True
    assert "live_data_toolset_prohibited" in requirements.reason_codes


@pytest.mark.parametrize(
    ("prompt", "answer_mode", "toolsets"),
    (
        ("Look up the current weather in Porto.", "LIVE_DATA", ("weather",)),
        ("Fetch the current Bitcoin price.", "LIVE_DATA", ("market_prices",)),
        (
            "Suppose I migrate soon. Look up the latest SQLite release notes online.",
            "GROUNDED",
            ("web_search", "web_fetch"),
        ),
    ),
)
def test_positive_retrieval_controls_remain_enabled(prompt: str, answer_mode: str, toolsets: tuple[str, ...]) -> None:
    requirements = requirements_for(prompt)

    assert requirements.answer_mode == answer_mode
    assert requirements.tools_required is True
    assert requirements.allowed_toolsets == toolsets


def test_adversarial_ordinary_negation_near_miss_still_permits_the_explicit_lookup() -> None:
    prompt = "I'm not sure whether to search, but actually look up the live weather in Porto now."
    constraints = analyze_retrieval_constraints(prompt)
    requirements = requirements_for(prompt)
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")

    assert constraints.has_prohibition is False
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.allowed_toolsets == ("weather",)
    assert plan is not None
    assert [(task.operation, task.entity) for task in plan.subtasks] == [("weather_lookup", "Porto")]


def test_a_scoped_negative_target_does_not_block_an_unrelated_positive_live_request() -> None:
    prompt = "Look up the current weather in Porto, and do not fetch server status metrics."
    requirements = requirements_for(prompt)
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")

    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.allowed_toolsets == ("weather",)
    assert plan is not None
    assert [(task.operation, task.entity) for task in plan.subtasks] == [("weather_lookup", "Porto")]


def test_a_weather_veto_does_not_disable_an_explicit_crypto_price_request() -> None:
    prompt = "Do not check the weather in Porto. Fetch the current Bitcoin price."
    requirements = requirements_for(prompt)
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")

    assert requirements.allowed_toolsets == ("market_prices",)
    assert plan is not None
    assert [(task.operation, task.entity) for task in plan.subtasks] == [("market_quote", "Bitcoin")]


def test_no_web_does_not_disable_an_unrelated_local_file_tool_request() -> None:
    prompt = "No web access. Read file /tmp/example.txt."

    assert should_attempt_tool_intent(prompt, task_class="file_inspection", source_context={}) is True


@pytest.mark.parametrize("prompt", (PHOENIX_REPORTED, FALLING_KNIFE_REPORTED, ETH_REPORTED))
def test_reported_failures_decline_both_live_preemption_paths_without_provider_calls(make_agent, prompt: str) -> None:
    agent = make_agent()
    interpretation = SimpleNamespace(topic_hints=["web", "weather", "news"])
    with (
        mock.patch(
            "tools.web.web_research.structured_weather_lookup",
            side_effect=AssertionError("weather provider must not run"),
        ) as weather,
        mock.patch(
            "tools.web.web_research._crypto_price_fallback_multi",
            side_effect=AssertionError("market provider must not run"),
        ) as crypto,
        mock.patch(
            "tools.web.web_research._market_quote_fallback_multi",
            side_effect=AssertionError("market provider must not run"),
        ) as market,
        mock.patch(
            "core.agent_runtime.fast_live_info_search.WebAdapter.planned_search_query",
            side_effect=AssertionError("web search must not run"),
        ) as search,
    ):
        typed = agent._maybe_answer_live_data_turn(
            effective_input=prompt,
            raw_input=prompt,
            session_id="negative-retrieval",
            source_context={},
        )
        fast = agent._maybe_handle_live_info_fast_path(
            prompt,
            session_id="negative-retrieval",
            source_context={},
            interpretation=interpretation,
        )

    assert typed is None
    assert fast is None
    weather.assert_not_called()
    crypto.assert_not_called()
    market.assert_not_called()
    search.assert_not_called()


@pytest.mark.parametrize("prompt", (PHOENIX_REPORTED, FALLING_KNIFE_REPORTED, ETH_REPORTED))
def test_run_once_reaches_normal_planning_instead_of_typed_live_data(make_agent, prompt: str) -> None:
    agent = make_agent()
    normal_result = {
        "response": "NORMAL_REASONING_REACHED",
        "response_class": "utility_answer",
        "confidence": 0.9,
    }
    with (
        mock.patch.object(agent, "_maybe_answer_conductor_turn", return_value=None),
        mock.patch.object(agent, "_maybe_answer_planned_turn", return_value=normal_result) as planner,
        mock.patch(
            "tools.web.web_research.structured_weather_lookup",
            side_effect=AssertionError("weather provider must not run"),
        ),
        mock.patch(
            "tools.web.web_research._crypto_price_fallback_multi",
            side_effect=AssertionError("market provider must not run"),
        ),
    ):
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )

    assert planner.called
    assert result.get("route_reason") != "live_data_typed_plan"
    # The invariant this test exists for: the prohibition never routes into the typed live-data
    # plan; the turn reaches normal planning and the planner's answer is what ships.
    response = str(result["response"])
    assert response.startswith("NORMAL_REASONING_REACHED"), response
    # The exact-equality assertion this replaces was STALE against the publication law: the
    # demand slot "tell me the current ETH price" needs a tool the turn explicitly forbids, so the
    # slot ends TOOL_FORBIDDEN and the runtime must SAY so visibly rather than drop it (the
    # 2026-08-29 slot-loss defect). A turn whose only slot is the prohibition itself (the reported
    # PHOENIX / FALLING_KNIFE phrasings) has nothing to disclose and ships the sentinel alone.
    from core.agent_runtime.answer_coverage import demand_units
    from core.semantic.producers.lexical import lexical_request_graph
    from core.semantic.request_graph import ConstraintKind

    graph = lexical_request_graph(prompt, turn_id="t")
    forbidden = [c for c in graph.constraints if c.kind in {ConstraintKind.TOOL_FORBIDDEN, ConstraintKind.RETRIEVAL_FORBIDDEN}]
    assert forbidden, "the lexical graph must carry the turn's explicit tool prohibition"
    price_units = [u for u in demand_units(prompt) if "price" in u.text.lower()]
    if price_units:
        assert "could not answer" in response.lower(), response
        assert "explicitly forbids" in response, response
        assert any(u.text.strip(" .") in response for u in price_units), response
    else:
        assert response == "NORMAL_REASONING_REACHED"


def test_explicit_no_tools_blocks_the_general_tool_catalog_even_under_a_research_label() -> None:
    assert should_attempt_tool_intent(ETH_REPORTED, task_class="research", source_context={}) is False
    assert classify(ETH_REPORTED)["task_class"] != "research"
