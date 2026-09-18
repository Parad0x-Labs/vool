from __future__ import annotations

from unittest import mock

import pytest

from core.curiosity_roamer import CuriosityResult
from core.memory_first_router import ModelExecutionDecision


@pytest.fixture(autouse=True)
def _enable_web_for_freshness_suite(enable_web):
    """These tests cover the live web-lookup path, which is opt-in/off by
    default. Enable it for the whole module; tests that assert the disabled
    path still pass because they gate locally (per-call `allow_remote_fetch`
    or their own `allow_web_fallback` patch)."""
    yield


FORBIDDEN_CHAT_WRAPPERS = (
    "workflow:",
    "here's what i'd suggest",
    "real steps completed:",
    "summary_block",
    "action_plan",
)


def test_wants_fresh_info_detects_live_queries_and_ignores_builder_language(make_agent):
    agent = make_agent()

    assert agent._wants_fresh_info("latest telegram bot api updates", interpretation=mock.Mock(topic_hints=["telegram"]))
    assert agent._wants_fresh_info("weather in London today", interpretation=mock.Mock(topic_hints=["weather"]))
    assert agent._wants_fresh_info("check Toly on X", interpretation=mock.Mock(topic_hints=["solana"]))
    assert agent._wants_fresh_info("tell me about stoicism", interpretation=mock.Mock(topic_hints=[])) is False
    assert agent._live_info_mode("latest qwen release notes", interpretation=mock.Mock(topic_hints=["web"])) == "fresh_lookup"
    assert agent._live_info_mode("check Toly on X", interpretation=mock.Mock(topic_hints=["solana"])) == "fresh_lookup"
    assert agent._live_info_mode("tell me about stoicism", interpretation=mock.Mock(topic_hints=[])) == ""
    assert agent._live_info_mode("What's the latest on Iran war?", interpretation=mock.Mock(topic_hints=[])) == "news"
    assert agent._live_info_mode("What happened five minutes ago in global markets?", interpretation=mock.Mock(topic_hints=[])) == "fresh_lookup"
    assert agent._live_info_mode("build a telegram bot from docs and github", interpretation=mock.Mock(topic_hints=["telegram", "github"])) == ""


def test_weather_related_conversation_does_not_enter_live_info_fast_path(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="rain-conversation",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Rain on a window can feel calming because it creates a soft, steady rhythm.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        side_effect=AssertionError("ordinary weather-related conversation must not search the web"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("ordinary weather-related conversation must not plan a web search"),
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "New question: why might someone enjoy rain on a window?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] in {"generic_conversation", "utility_answer"}
    assert "soft, steady rhythm" in result["response"].lower()
    assert agent.memory_router.resolve.called


def test_ordinary_conceptual_question_does_not_start_research(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="gentle-morning-conversation",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Starting with one quiet task can make a morning feel less rushed.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.research_query",
        side_effect=AssertionError("ordinary conversation must not start research"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        side_effect=AssertionError("ordinary conversation must not search the web"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("ordinary conversation must not plan research"),
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "What is one gentle way to make a morning feel less rushed?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] in {"generic_conversation", "utility_answer"}
    assert "quiet task" in result["response"].lower()
    classification = agent.memory_router.resolve.call_args.kwargs["classification"]
    assert classification["task_class"] == "chat_conversation"


def test_latest_telegram_updates_trigger_planned_web_lookup(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are the canonical source for these updates.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ) as planned_search, mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        side_effect=AssertionError("generic web search should not be used for this research query"),
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 1
    assert result["response_class"] == "utility_answer"
    assert "canonical source" in result["response"].lower()
    assert "telegram bot api" in result["response"].lower()
    assert result["model_execution"]["used_model"] is True
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()
    assert agent.memory_router.resolve.called
    assert agent.memory_router.resolve.call_args.kwargs["force_model"] is True
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "grounding observations for this turn" in model_input or "answer only using the search results below" in model_input
    assert "sources" in model_input
    assert "live web results for" not in model_input
    assert "live weather results for" not in model_input


def test_explicit_remote_fetch_false_uses_local_memory_on_trusted_surface(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="web0-local-memory",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "Web0 is Parad0x Labs' local-first/private web stack with .null identity, "
                "durable content, payments, discovery, VOOL agents, and OpenClaw operations."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.research_query",
        side_effect=AssertionError("explicit remote-fetch=false must not run research query"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        side_effect=AssertionError("explicit remote-fetch=false must not run generic web search"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("explicit remote-fetch=false must not run planned web search"),
    ), mock.patch.object(
        agent,
        "_live_info_mode",
        return_value="fresh_lookup",
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "tell me about web0",
            source_context={
                "surface": "api",
                "platform": "openclaw",
                "allow_remote_fetch": False,
            },
        )

    assert result["response_class"] in {"generic_conversation", "utility_answer"}
    lowered = result["response"].lower()
    assert "web0 is parad0x labs" in lowered
    assert "live web results for" not in lowered
    assert "search results" not in lowered
    assert agent.memory_router.resolve.called
    assert agent.memory_router.resolve.call_args.kwargs["source_context"]["allow_remote_fetch"] is False


def test_live_info_without_web_fallback_returns_deterministic_disabled_response(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web-disabled",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="should not be used",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch("core.agent_runtime.agent.policy_engine.allow_web_fallback", return_value=False):
        result = agent.run_once(
            "What is the current BTC price now?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "utility_answer"
    assert "live web lookup is disabled on this runtime" in result["response"].lower()
    assert "can't verify current prices" in result["response"].lower()
    assert "would you like me to attempt" not in result["response"].lower()
    assert agent.memory_router.resolve.call_count == 0


def test_live_info_chat_surface_routes_model_wording_through_chat_research(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web-chat-research",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are still the cleanest source for this question.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "channel", "platform": "openclaw"},
        )

    assert result["model_execution"]["used_model"] is True
    assert agent.memory_router.resolve.called
    classification = agent.memory_router.resolve.call_args.kwargs["classification"]
    assert classification["task_class"] == "chat_research"
    assert classification["planner_style_requested"] is False


def test_ultra_fresh_market_question_returns_insufficient_evidence_without_bluffing(make_agent):
    agent = make_agent()

    with mock.patch.object(
        agent,
        "_live_info_search_notes",
        side_effect=AssertionError("ultra-fresh honesty path should not hit live search"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("ultra-fresh honesty path should not hit planned search"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        side_effect=AssertionError("ultra-fresh honesty path should not hit generic search"),
    ):
        result = agent.run_once(
            "What happened five minutes ago in global markets?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert "can't verify" in lowered or "insufficient evidence" in lowered
    assert "five minutes ago there were no significant events" not in lowered


def test_brent_quote_fast_path_returns_grounded_structured_answer(make_agent):
    agent = make_agent()

    with mock.patch.object(
        agent,
        "_try_live_quote_notes",
        return_value=[{
            "result_title": "Brent crude quote",
            "result_url": "https://finance.yahoo.com/quote/BZ=F",
            "origin_domain": "finance.yahoo.com",
            "summary": "Brent crude: $102.36 USD per barrel | session change: +2.15% | as of 2026-03-17 16:36 UTC",
            "confidence": 0.95,
            "source_profile_label": "Yahoo Finance",
            "page_text": "Brent crude: $102.36 USD per barrel | session change: +2.15% | as of 2026-03-17 16:36 UTC",
            "live_quote": {
                "asset_key": "brent_crude",
                "asset_name": "Brent crude",
                "symbol": "BZ=F",
                "value": 102.36,
                "currency": "USD",
                "as_of": "2026-03-17 16:36 UTC",
                "source_label": "Yahoo Finance",
                "source_url": "https://finance.yahoo.com/quote/BZ=F",
                "kind": "market",
                "unit_label": "per barrel",
                "change_percent": 2.15,
                "change_window": "session",
                "market_cap": None,
                "timestamp_utc": 1773765360,
                "exchange": "NYM",
                "confidence": 0.95,
            },
        }],
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("structured quote path should not fall back to generic planned search"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        side_effect=AssertionError("structured quote path should not use generic search"),
    ):
        result = agent.run_once(
            "Brent crude price now?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "utility_answer"
    lowered = result["response"].lower()
    assert "brent crude is $102.36 usd per barrel" in lowered
    assert "session change: +2.15%" in lowered
    assert "as of 2026-03-17 16:36 utc" in lowered
    assert "[yahoo finance](https://finance.yahoo.com/quote/bz=f)" in lowered
    assert "live web results for" not in lowered
    assert "wikipedia" not in lowered


def test_evaluative_turn_does_not_hit_web_lookup(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="no_cached_or_memory_answer",
            task_hash="evaluative-fast",
            used_model=False,
        )
    )

    with mock.patch("core.agent_runtime.agent.WebAdapter.search_query") as search_query, mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query"
    ) as planned_search:
        result = agent.run_once(
            "you sound weird",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "generic_conversation"
    assert "routing is still too stitched together" in result["response"].lower()
    search_query.assert_not_called()
    planned_search.assert_not_called()
    agent.memory_router.resolve.assert_not_called()
    assert result["model_execution"]["used_model"] is False


def test_help_turn_uses_deterministic_fast_path_when_no_cached_answer(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="no_cached_or_memory_answer",
            task_hash="help-fast",
            used_model=False,
        )
    )

    result = agent.run_once(
        "help",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "utility_answer"
    assert "wired on this runtime" in result["response"].lower()
    agent.memory_router.resolve.assert_not_called()
    assert result["model_execution"]["used_model"] is False


def test_conceptual_system_design_turn_does_not_hit_web_lookup_without_freshness_markers(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="react-watcher",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Start with the file watcher, symlinks, and editor temp-file behavior.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch("core.agent_runtime.agent.WebAdapter.search_query") as search_query, mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query"
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "my react dev server keeps reloading on save; where would you look first?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert "file watcher" in result["response"].lower()
    search_query.assert_not_called()
    planned_search.assert_not_called()


def test_weather_live_lookup_uses_structured_weather_wording(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="weather",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="London looks cloudy with light rain around 11C, based on BBC Weather.",
            confidence=0.83,
            trust_score=0.83,
        )
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        return_value=[
            {
                "summary": "London, United Kingdom: Cloudy with light rain, 11 C (feels like 9 C), humidity 82%, wind 14 km/h. Observed 09:00 AM.",
                "source_label": "wttr.in",
                "origin_domain": "wttr.in",
                "result_title": "wttr.in weather for London",
                "result_url": "https://wttr.in/London",
                "used_browser": False,
            }
        ],
    ) as search_query:
        result = agent.run_once(
            "what is the weather in London today?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert search_query.called
    assert result["response_class"] == "utility_answer"
    lowered = result["response"].lower()
    assert "cloudy with light rain" in lowered
    assert "source: [wttr.in](https://wttr.in/london)" in lowered
    assert "live weather results" not in lowered
    assert "weather in london" not in lowered
    assert "hive:" not in result["response"].lower()
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()


def test_news_live_lookup_uses_structured_headline_wording(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="iran-news",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="This should not be used when structured live news notes are available.",
            confidence=0.83,
            trust_score=0.83,
        )
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        return_value=[
            {
                "summary": "Reuters | 2026-03-17 | Tanker near Strait of Hormuz hit by projectile",
                "origin_domain": "reuters.com",
                "result_title": "Tanker near Strait of Hormuz hit by projectile",
                "result_url": "https://www.reuters.com/world/middle-east/demo",
                "used_browser": False,
            },
            {
                "summary": "Al Jazeera | 2026-03-17 | Regional tensions remain high after new maritime incident",
                "origin_domain": "aljazeera.com",
                "result_title": "Regional tensions remain high after new maritime incident",
                "result_url": "https://www.aljazeera.com/news/demo",
                "used_browser": False,
            },
        ],
    ) as search_query:
        result = agent.run_once(
            "What's the latest on Iran war?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert search_query.called
    assert result["response_class"] == "utility_answer"
    lowered = result["response"].lower()
    assert "latest coverage on iran war" in lowered
    # The source name is an embedded markdown link (short clickable source), not a raw URL.
    assert (
        "2026-03-17 | [reuters](https://www.reuters.com/world/middle-east/demo): "
        "tanker near strait of hormuz hit by projectile" in lowered
    )
    assert "recent developments include" not in lowered
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in lowered


def test_ambiguous_price_lookup_fails_honestly_instead_of_answering_biography(make_agent):
    agent = make_agent()

    with mock.patch.object(
        agent,
        "_live_info_search_notes",
        return_value=[
            {
                "summary": "Seth Price is a New York City-based multi-disciplinary post-conceptual artist.",
                "origin_domain": "wikipedia.org",
                "result_title": "Seth Price",
                "result_url": "https://en.wikipedia.org/wiki/Seth_Price",
            }
        ],
    ):
        result = agent.run_once(
            # WAS "what is Seth price?". That reached the price lane only because `eth price` is a
            # marker and "S-eth price" contains it as a SUBSTRING -- the artist Seth Price was being
            # read as a crypto quote. Fixed 2026-08-18 by matching markers on word boundaries, which
            # is what this module's own comment asks for: "this was never an asset quote (stay out of
            # it entirely and let the reasoning lane answer)". The guard below is unchanged and is
            # the point of the test -- an asset-shaped query whose symbol does not resolve must say
            # so rather than present the biography the search returned.
            "what is eth price?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    # The property this test is NAMED for: the search returned a Wikipedia biography, and the
    # runtime must not present it as the answer to a price question. It fails honestly instead.
    #
    # The assertions used to pin one exact refusal wording ("couldn't map `seth` to a known traded
    # asset"). That wording was reachable only through the substring collision described above, and
    # pinning prose rather than the property is what made this test guard an accident. What is
    # asserted now is the behaviour: it is a utility answer, and none of the biography survives
    # into it.
    assert result["response_class"] == "utility_answer"
    lowered = result["response"].lower()
    for leaked in ("post-conceptual", "multi-disciplinary", "new york city", "artist"):
        assert leaked not in lowered, f"the biography leaked into a price answer: {leaked!r}"
    assert "seth price" not in lowered


def test_a_person_named_price_is_not_a_crypto_quote(make_agent):
    """The other half of the same repair: the substring collision must not come back."""
    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode

    assert live_info_mode(None, "what is Seth price?", interpretation=None) == ""
    assert live_info_mode(None, "what is eth price?", interpretation=None) == "fresh_lookup"


def test_workflow_planner_does_not_hijack_live_info_chat_when_fast_path_has_no_notes(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web-main-lane",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are still the cleanest source for this, and I only found one solid note in this run.",
            confidence=0.82,
            trust_score=0.82,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.plan_tool_workflow",
        side_effect=AssertionError("ordinary live-info chat should not enter the workflow planner"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "generic_conversation"
    assert result["model_execution"]["used_model"] is True
    assert "cleanest source" in result["response"].lower()


def test_ordinary_chat_can_escalate_into_adaptive_research(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="adaptive-research",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Supabase is simpler if you want Postgres-first control, while Firebase is stronger if you want tighter managed mobile defaults.",
            confidence=0.86,
            trust_score=0.86,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Supabase is Postgres-first and easy to self-understand.",
                    "confidence": 0.71,
                    "source_profile_label": "Official docs",
                    "result_title": "Supabase docs",
                    "result_url": "https://supabase.com/docs",
                    "origin_domain": "supabase.com",
                }
            ],
            [
                {
                    "summary": "Firebase offers tighter managed integrations for auth and messaging.",
                    "confidence": 0.69,
                    "source_profile_label": "Official docs",
                    "result_title": "Firebase docs",
                    "result_url": "https://firebase.google.com/docs",
                    "origin_domain": "firebase.google.com",
                }
            ],
        ],
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "compare supabase vs firebase for a telegram bot backend",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 2
    assert result["response_class"] == "generic_conversation"
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["compared_sources"] is True
    assert len(result["research_controller"]["queries_run"]) >= 2
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "adaptive_research" in model_input
    assert "compare_sources" in model_input
    assert "queries_run" in model_input
    assert "live web results for" not in model_input
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()


def test_workflow_planner_does_not_hijack_adaptive_research_chat(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="adaptive-research-main-lane",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Supabase gives you more direct control, while Firebase gives you more managed defaults.",
            confidence=0.85,
            trust_score=0.85,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.plan_tool_workflow",
        side_effect=AssertionError("ordinary research chat should not enter the workflow planner"),
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Supabase is Postgres-first and easy to self-understand.",
                    "confidence": 0.71,
                    "source_profile_label": "Official docs",
                    "result_title": "Supabase docs",
                    "result_url": "https://supabase.com/docs",
                    "origin_domain": "supabase.com",
                }
            ],
            [
                {
                    "summary": "Firebase offers tighter managed integrations for auth and messaging.",
                    "confidence": 0.69,
                    "source_profile_label": "Official docs",
                    "result_title": "Firebase docs",
                    "result_url": "https://firebase.google.com/docs",
                    "origin_domain": "firebase.google.com",
                }
            ],
        ],
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "compare supabase vs firebase for a telegram bot backend",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "generic_conversation"
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["compared_sources"] is True
    assert result["model_execution"]["used_model"] is True


def test_adaptive_research_surfaces_uncertainty_when_evidence_stays_weak(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="adaptive-uncertain",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can't verify that cleanly from the evidence I found, so this should stay tentative.",
            confidence=0.71,
            trust_score=0.71,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[[], [], []],
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "verify whether telegram allows unlimited bots per phone number",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["admitted_uncertainty"] is True
    assert result["research_controller"]["uncertainty_reason"]
    assert "can't verify" in result["response"].lower()
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "admitted_uncertainty" in model_input
    assert "uncertainty_reason" in model_input
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()


def test_fuzzy_solana_entity_lookup_recovers_from_short_name(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="toly-solana",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Toly is Anatoly Yakovenko, Solana's co-founder.",
            confidence=0.83,
            trust_score=0.83,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Anatoly Yakovenko, often called Toly, co-founded Solana.",
                    "confidence": 0.76,
                    "source_profile_label": "Official docs",
                    "result_title": "Solana leadership",
                    "result_url": "https://solana.com/team",
                    "origin_domain": "solana.com",
                },
                {
                    "summary": "The Solana co-founder account on X is Anatoly Yakovenko, known as Toly.",
                    "confidence": 0.72,
                    "source_profile_label": "Public profile",
                    "result_title": "toly on X",
                    "result_url": "https://x.com/aeyakovenko",
                    "origin_domain": "x.com",
                },
            ]
        ],
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "who is Toly in Solana",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 1
    assert result["response_class"] == "generic_conversation"
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["queries_run"][0] == "toly solana"
    assert "anatoly yakovenko" in result["response"].lower()


def test_fuzzy_solana_entity_lookup_retries_misspelling_from_x_hint(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="tolly-x-solana",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="That looks like Toly, Anatoly Yakovenko from Solana.",
            confidence=0.81,
            trust_score=0.81,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[
            [],
            [
                {
                    "summary": "Anatoly Yakovenko, known as Toly, is Solana's co-founder and posts on X.",
                    "confidence": 0.73,
                    "source_profile_label": "Official docs",
                    "result_title": "Anatoly Yakovenko",
                    "result_url": "https://solana.com/team/anatoly-yakovenko",
                    "origin_domain": "solana.com",
                },
                {
                    "summary": "Toly is Anatoly Yakovenko on X.",
                    "confidence": 0.7,
                    "source_profile_label": "Public profile",
                    "result_title": "toly on X",
                    "result_url": "https://x.com/aeyakovenko",
                    "origin_domain": "x.com",
                },
            ],
        ],
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "Tolly on X in Solana who is he",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 2
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["queries_run"][:2] == ["tolly x solana", "toly x solana"]
    assert result["research_controller"]["narrowed"] is True
    assert "anatoly yakovenko" in result["response"].lower()


def test_explicit_check_on_x_escalates_into_real_lookup_instead_of_generic_chat(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="check-toly-x",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I checked live signals and this points to Anatoly Yakovenko, usually called Toly.",
            confidence=0.8,
            trust_score=0.8,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Anatoly Yakovenko, also known as Toly, is the co-founder of Solana.",
                    "confidence": 0.74,
                    "source_profile_label": "Official docs",
                    "result_title": "Solana team",
                    "result_url": "https://solana.com/team",
                    "origin_domain": "solana.com",
                },
                {
                    "summary": "The X profile for Toly points to Anatoly Yakovenko.",
                    "confidence": 0.71,
                    "source_profile_label": "Public profile",
                    "result_title": "toly on X",
                    "result_url": "https://x.com/aeyakovenko",
                    "origin_domain": "x.com",
                },
            ]
        ],
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "check Toly on X",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 1
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["queries_run"][0] == "toly x"
    assert "checked live signals" in result["response"].lower()


def test_fuzzy_entity_lookup_admits_uncertainty_when_live_signals_stay_weak(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="unknown-x-solana",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I couldn't pin that down confidently from the live signals I found.",
            confidence=0.7,
            trust_score=0.7,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[[], [], [], []],
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "check Tolyy on X in Solana",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["admitted_uncertainty"] is True
    assert "public figure" in result["research_controller"]["uncertainty_reason"].lower()
    assert "couldn't pin that down confidently" in result["response"].lower()


def test_empty_fresh_lookup_honestly_degrades_instead_of_using_memory_as_final_speaker(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(source="memory_hit", task_hash="fresh-fallback", used_model=False)
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(
        agent,
        "_live_info_search_notes",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 1
    assert "remembered text as a fresh answer" not in result["response"].lower()
    assert "could not ground a current answer confidently" in result["response"].lower()
    assert result["model_execution"]["source"] == "memory_hit"
    assert result["model_execution"]["used_model"] is False
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()
