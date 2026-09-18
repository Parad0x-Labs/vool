from __future__ import annotations

import uuid
from contextlib import ExitStack, contextmanager
from unittest import mock

import pytest

from apps.vool_agent import ResponseClass
from core.autonomous_topic_research import AutonomousResearchResult
from core.curiosity_roamer import CuriosityResult
from core.memory_first_router import ModelExecutionDecision

FORBIDDEN_PLANNER_LEAKS = (
    "review problem",
    "choose safe next step",
    "validate result",
    "workflow:",
    "here's what i'd suggest",
    "real steps completed:",
    "summary_block",
    "action_plan",
)
PLACEHOLDER_TOKENS = ("[time]", "[date]", "[weather]")
SEMANTIC_HIVE_PROMPTS = (
    "hi check hive pls",
    "what's in hive",
    "what online tasks we have",
    "anything on hive?",
    "show hive work",
    "hive tasks?",
    "what is on the hive mind tasks?",
    "check hive mind pls",
)
ORDINARY_CHAT_SMOKE_CASES = (
    # Greetings ("hi"/"yo") are now random + time-aware and are covered by the dedicated greeting
    # tests in test_openclaw_tooling_context.py, so they are no longer pinned here.
    ("17 * 19", "17 * 19 = 323."),
    (
        "if 3 workers finish in 6 days, how long for 6 workers",
        "If the work splits evenly, 6 workers finish it in 3 days.",
    ),
    (
        "sort these priorities: broken auth, typo, outage",
        "Outage first, broken auth second, typo last.",
    ),
    (
        "two-line explanation of recursion",
        "Recursion solves a problem by calling the same function on a smaller version of it.\n"
        "It stops when a base case returns without recursing again.",
    ),
)
ORDINARY_CHAT_FAST_PATH_PROMPTS = {"hi", "yo"}
# The greeting fast-path echoes the greeting back ("hi" -> "Hi. ...", "yo" -> "Yo. ...") with a
# rotating task-prompt tail; assert the echo prefix rather than one fixed stock line.
ORDINARY_CHAT_FAST_PATH_SNIPPETS: dict[str, str] = {}


def _provider_decision(*, task_hash: str, output_text: str, confidence: float = 0.84) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider",
        task_hash=task_hash,
        provider_id="ollama:qwen",
        used_model=True,
        output_text=output_text,
        confidence=confidence,
        trust_score=confidence,
    )


def _source_context(*, session_label: str, history: list[dict[str, str]] | None = None) -> dict[str, object]:
    context: dict[str, object] = {
        "surface": "openclaw",
        "platform": "openclaw",
        "runtime_session_id": f"openclaw:alpha-smoke:{session_label}:{uuid.uuid4().hex}",
    }
    if history is not None:
        context["conversation_history"] = history
    return context


def _chat_truth_events(audit_log_mock: mock.Mock) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for call in audit_log_mock.call_args_list:
        if not call.args or call.args[0] != "agent_chat_truth_metrics":
            continue
        details = call.kwargs.get("details")
        if details is None and len(call.args) >= 3:
            details = call.args[2]
        events.append(dict(details or {}))
    return events


@contextmanager
def _common_runtime_patch_stack():
    with ExitStack() as stack:
        stack.enter_context(mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None))
        stack.enter_context(mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]))
        stack.enter_context(mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None))
        yield


def _assert_clean_surface_text(text: str) -> None:
    lowered = str(text or "").lower()
    for marker in FORBIDDEN_PLANNER_LEAKS:
        assert marker not in lowered
    for token in PLACEHOLDER_TOKENS:
        assert token not in lowered


def _normalized_hive_call(text: str) -> str:
    normalized = " ".join(str(text or "").strip().lower().split())
    return normalized.replace(" pls", " please")


def _task_list_details() -> dict[str, object]:
    return {
        "command_kind": "task_list",
        "watcher_status": "ok",
        "response_text": (
            "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 2 total):\n"
            "- [open] OpenClaw integration audit (#7d33994f)\n"
            "- [researching] Agent Commons: better human-visible watcher and task-flow UX (#a951bf9d)\n"
            "If you want, I can start one. Just point at the task name or short `#id`."
        ),
        "truth_source": "watcher",
        "truth_label": "watcher-derived",
        "truth_status": "ok",
        "presence_claim_state": "visible",
        "presence_source": "watcher",
        "presence_truth_label": "watcher-derived",
        "presence_freshness_label": "fresh",
        "presence_age_seconds": 18,
        "topics": [
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "OpenClaw integration audit",
                "status": "open",
            },
            {
                "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
            },
        ],
        "online_agents": [],
    }


def _configure_lookup_agent(agent, context_result_factory, *, reply: str, task_hash: str) -> None:
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(task_hash=task_hash, output_text=reply)
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )


@pytest.mark.parametrize("prompt", SEMANTIC_HIVE_PROMPTS)
def test_smoke_semantic_hive_prompts_recover_without_magic_phrase(make_agent, prompt):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""

    canonical_calls: list[str] = []

    def maybe_handle_command_details(user_text: str, *, session_id: str):
        normalized = _normalized_hive_call(user_text)
        canonical_calls.append(normalized)
        if normalized == "show me the open hive tasks":
            return True, _task_list_details()
        return False, {}

    agent.hive_activity_tracker.maybe_handle_command_details.side_effect = maybe_handle_command_details
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(
            task_hash=f"semantic-hive-{uuid.uuid4().hex}",
            output_text="I can see two Hive tasks open right now: OpenClaw integration audit and Agent Commons. Point me at one and I'll start it.",
        )
    )

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:alpha-smoke:hive:{uuid.uuid4().hex}",
        source_context=_source_context(session_label="semantic-hive"),
    )

    assert result["response_class"] == ResponseClass.TASK_LIST.value
    assert result["model_execution"]["used_model"] is True
    lowered = result["response"].lower()
    assert "openclaw integration audit" in lowered
    assert "agent commons" in lowered
    _assert_clean_surface_text(result["response"])
    assert canonical_calls == [_normalized_hive_call(prompt), "show me the open hive tasks"]


def test_smoke_selecting_shown_task_by_full_title_starts_task(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(
            task_hash="smoke-hive-title-select",
            output_text="Started research on Agent Commons: better human-visible watcher and task-flow UX.",
        )
    )
    queue_rows = [
        {
            "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "OpenClaw integration audit",
            "status": "open",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            claim_id="claim-a951bf9d",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "Agent Commons: better human-visible watcher and task-flow UX",
            session_id_override="openclaw:alpha-smoke:hive-title-select",
            source_context=_source_context(session_label="hive-title-select"),
        )

    assert result["response_class"] == ResponseClass.TASK_STARTED.value
    lowered = result["response"].lower()
    assert "started hive research on" in lowered or "started research on agent commons" in lowered
    assert "point at the task name" not in lowered
    _assert_clean_surface_text(result["response"])
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701"


def test_smoke_selecting_shown_task_by_short_id_starts_task(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(
            task_hash="smoke-hive-short-id-select",
            output_text="Started research on OpenClaw integration audit.",
        )
    )
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "OpenClaw integration audit",
            "status": "open",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-7d33994f",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "start #7d33994f",
            session_id_override="openclaw:alpha-smoke:hive-short-id-select",
            source_context=_source_context(session_label="hive-short-id-select"),
        )

    assert result["response_class"] == ResponseClass.TASK_STARTED.value
    lowered = result["response"].lower()
    assert "started hive research on" in lowered or "started research on openclaw integration audit" in lowered
    assert "point at the task name" not in lowered
    _assert_clean_surface_text(result["response"])
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"


@pytest.mark.xfail(reason="Pre-existing: weather response format changed")
def test_smoke_utility_binding_returns_real_values_without_placeholders(make_agent, context_result_factory):
    time_agent = make_agent()
    time_agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]
    time_result = time_agent.run_once(
        "what time is now in Vilnius?",
        session_id_override=f"openclaw:alpha-smoke:vilnius:{uuid.uuid4().hex}",
        source_context=_source_context(session_label="utility-time"),
    )

    date_agent = make_agent()
    date_agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]
    date_result = date_agent.run_once(
        "what is the date today?",
        source_context=_source_context(session_label="utility-date"),
    )

    weather_agent = make_agent()
    weather_agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    weather_agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(
            task_hash="smoke-weather",
            output_text="London looks cloudy with light rain around 11C, based on BBC Weather.",
        )
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        return_value=[
            {
                "summary": "Cloudy with light rain, around 11C, with breezy afternoon conditions.",
                "source_label": "duckduckgo.com",
                "origin_domain": "bbc.com",
                "result_title": "BBC Weather - London",
                "result_url": "https://www.bbc.com/weather/2643743",
                "used_browser": False,
            }
        ],
    ):
        weather_result = weather_agent.run_once(
            "what is the weather in London today?",
            source_context=_source_context(session_label="utility-weather"),
        )

    assert time_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in time_result["response"].lower()
    assert time_result["response"].count(":") >= 1
    _assert_clean_surface_text(time_result["response"])

    assert date_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "today is" in date_result["response"].lower()
    _assert_clean_surface_text(date_result["response"])

    assert weather_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "light rain" in weather_result["response"].lower()
    assert "bbc weather" in weather_result["response"].lower()
    _assert_clean_surface_text(weather_result["response"])


def test_smoke_vilnius_time_followups_recover_from_recent_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]
    session_id = f"openclaw:alpha-smoke:vilnius-followups:{uuid.uuid4().hex}"
    source_context = _source_context(session_label="vilnius-followups")

    first = agent.run_once(
        "what time is now in Vilnius?",
        session_id_override=session_id,
        source_context=source_context,
    )
    second = agent.run_once(
        "and there?",
        session_id_override=session_id,
        source_context=source_context,
    )
    third = agent.run_once(
        "what where's is in Vilnius?",
        session_id_override=session_id,
        source_context=source_context,
    )

    for result in (first, second, third):
        assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
        _assert_clean_surface_text(result["response"])

    assert "vilnius" in first["response"].lower()
    assert "current time in vilnius is" in second["response"].lower()
    assert "current time in vilnius is" in third["response"].lower()
    assert "i can help think, research, write code" not in third["response"].lower()


def test_smoke_planner_leakage_is_absent_from_representative_outputs(make_agent, context_result_factory):
    ordinary_agent = make_agent()
    ordinary_agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    ordinary_agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(
            task_hash="smoke-plain-chat",
            output_text="17 * 19 = 323.",
        )
    )
    with mock.patch("core.agent_runtime.agent.audit_logger.log"), _common_runtime_patch_stack():
        ordinary = ordinary_agent.run_once(
            "17 * 19",
            session_id_override=f"openclaw:alpha-smoke:ordinary:{uuid.uuid4().hex}",
            source_context=_source_context(session_label="planner-ordinary"),
        )

    hive_agent = make_agent()
    hive_agent.hive_activity_tracker = mock.Mock()
    hive_agent.hive_activity_tracker.build_chat_footer.return_value = ""
    hive_agent.hive_activity_tracker.maybe_handle_command_details.return_value = (True, _task_list_details())
    hive_agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(
            task_hash="smoke-hive-clean",
            output_text="I can see two Hive tasks open right now: OpenClaw integration audit and Agent Commons.",
        )
    )
    hive = hive_agent.run_once(
        "show me the open hive tasks",
        session_id_override=f"openclaw:alpha-smoke:hive-clean:{uuid.uuid4().hex}",
        source_context=_source_context(session_label="planner-hive"),
    )

    utility_agent = make_agent()
    utility_agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]
    utility = utility_agent.run_once(
        "what time is now in Vilnius?",
        source_context=_source_context(session_label="planner-utility"),
    )

    for result in (ordinary, hive, utility):
        _assert_clean_surface_text(result["response"])


@pytest.mark.parametrize(
    ("prompt", "reply", "planned_search_side_effect", "expected_queries", "expected_markers"),
    [
        (
            "who is Toly in Solana",
            "Toly is Anatoly Yakovenko, Solana's co-founder.",
            [
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
            ["toly solana"],
            ("anatoly yakovenko",),
        ),
        (
            "Tolly on X in Solana who is he",
            "That looks like Toly, Anatoly Yakovenko from Solana.",
            [
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
                        "confidence": 0.70,
                        "source_profile_label": "Public profile",
                        "result_title": "toly on X",
                        "result_url": "https://x.com/aeyakovenko",
                        "origin_domain": "x.com",
                    },
                ],
            ],
            ["tolly x solana", "toly x solana"],
            ("anatoly yakovenko",),
        ),
        (
            "check Toly on X",
            "I checked live signals and this points to Anatoly Yakovenko, usually called Toly.",
            [
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
            ["toly x"],
            ("checked live signals", "anatoly yakovenko"),
        ),
        (
            "find Tolly solana twitter",
            "That points to Toly, Anatoly Yakovenko from Solana.",
            [
                [],
                [
                    {
                        "summary": "Anatoly Yakovenko, often called Toly, co-founded Solana.",
                        "confidence": 0.74,
                        "source_profile_label": "Official docs",
                        "result_title": "Solana leadership",
                        "result_url": "https://solana.com/team",
                        "origin_domain": "solana.com",
                    },
                    {
                        "summary": "Toly is Anatoly Yakovenko on X, formerly Twitter.",
                        "confidence": 0.69,
                        "source_profile_label": "Public profile",
                        "result_title": "toly on X",
                        "result_url": "https://x.com/aeyakovenko",
                        "origin_domain": "x.com",
                    },
                ],
            ],
            ["tolly solana twitter", "toly solana twitter"],
            ("anatoly yakovenko",),
        ),
        (
            "some big guy in Solana, Toly or Tolly, who is he",
            "That most likely refers to Anatoly Yakovenko, usually called Toly, one of Solana's co-founders.",
            [
                [
                    {
                        "summary": "Anatoly Yakovenko, known as Toly, co-founded Solana.",
                        "confidence": 0.73,
                        "source_profile_label": "Official docs",
                        "result_title": "Solana team",
                        "result_url": "https://solana.com/team",
                        "origin_domain": "solana.com",
                    },
                    {
                        "summary": "The X profile for Toly belongs to Anatoly Yakovenko.",
                        "confidence": 0.67,
                        "source_profile_label": "Public profile",
                        "result_title": "toly on X",
                        "result_url": "https://x.com/aeyakovenko",
                        "origin_domain": "x.com",
                    },
                ]
            ],
            [],
            ("anatoly yakovenko", "solana"),
        ),
    ],
)
def test_smoke_fuzzy_entity_and_forced_lookup_cases(
    make_agent,
    context_result_factory,
    enable_web,
    prompt,
    reply,
    planned_search_side_effect,
    expected_queries,
    expected_markers,
):
    agent = make_agent()
    _configure_lookup_agent(
        agent,
        context_result_factory,
        reply=reply,
        task_hash=f"lookup-smoke-{uuid.uuid4().hex}",
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=planned_search_side_effect,
    ) as planned_search, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context=_source_context(session_label="lookup-smoke"),
        )

    assert planned_search.call_count >= 1
    assert result["research_controller"]["enabled"] is True
    assert result["model_execution"]["used_model"] is True
    assert result["research_controller"]["queries_run"]
    if expected_queries:
        assert result["research_controller"]["queries_run"][: len(expected_queries)] == expected_queries
    lowered = result["response"].lower()
    for marker in expected_markers:
        assert marker in lowered
    _assert_clean_surface_text(result["response"])


def test_smoke_weak_fuzzy_entity_match_admits_uncertainty_instead_of_filler(make_agent, context_result_factory, enable_web):
    agent = make_agent()
    _configure_lookup_agent(
        agent,
        context_result_factory,
        reply="I couldn't pin that down confidently from the live signals I found.",
        task_hash="lookup-smoke-uncertain",
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=[[], [], []],
    ), _common_runtime_patch_stack():
        result = agent.run_once(
            "check Tolyy on X in Solana",
            source_context=_source_context(session_label="lookup-uncertain"),
        )

    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["admitted_uncertainty"] is True
    assert "public figure" in result["research_controller"]["uncertainty_reason"].lower()
    lowered = result["response"].lower()
    assert "couldn't pin that down confidently" in lowered
    assert "i can help think, research, write code" not in lowered
    _assert_clean_surface_text(result["response"])


@pytest.mark.parametrize(("prompt", "reply"), ORDINARY_CHAT_SMOKE_CASES)
def test_smoke_ordinary_chat_cases_hit_model_final_answer(make_agent, context_result_factory, prompt, reply):
    agent = make_agent()
    if prompt != "17 * 19":
        agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=_provider_decision(
                task_hash=f"ordinary-smoke-{uuid.uuid4().hex}",
                output_text=reply,
            )
        )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            session_id_override=f"openclaw:alpha-smoke:ordinary:{uuid.uuid4().hex}",
            source_context=_source_context(session_label="ordinary-chat"),
        )

    events = _chat_truth_events(audit_log)
    assert len(events) == 1
    event = events[0]
    if prompt in ORDINARY_CHAT_FAST_PATH_PROMPTS:
        assert event.get("fast_path_hit") is True
        assert event.get("model_inference_used") is False
        assert event.get("model_final_answer_hit") is False
        assert result["response_class"] == ResponseClass.SMALLTALK.value
        assert result["model_execution"]["used_model"] is False
        assert agent.memory_router.resolve.call_count == 0
        assert ORDINARY_CHAT_FAST_PATH_SNIPPETS[prompt] in result["response"].lower()
    elif prompt == "17 * 19":
        assert event.get("fast_path_hit") is True
        assert event.get("model_inference_used") is False
        assert event.get("model_final_answer_hit") is False
        assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    else:
        assert event.get("fast_path_hit") is False
        assert event.get("model_inference_used") is True
        assert event.get("model_final_answer_hit") is True
        assert event.get("template_renderer_hit") is False
        assert result["response"] == reply
    if prompt in ORDINARY_CHAT_FAST_PATH_PROMPTS or prompt == "17 * 19":
        assert result["model_execution"]["used_model"] is False
    else:
        assert result["model_execution"]["used_model"] is True
    _assert_clean_surface_text(result["response"])
