from __future__ import annotations

import uuid
from contextlib import ExitStack, contextmanager
from unittest import mock

import pytest

from core.bootstrap_context import build_bootstrap_context
from core.curiosity_roamer import AdaptiveResearchResult, CuriosityResult
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from core.persistent_memory import append_conversation_event
from core.task_router import create_task_record
from core.tool_intent_executor import ToolIntentExecution
from storage.dialogue_memory import get_dialogue_session

FORBIDDEN_PLANNER_LEAKS = (
    "workflow:",
    "here's what i'd suggest",
    "real steps completed:",
    "summary_block",
    "action_plan",
)

ALPHA_SHIP_THRESHOLDS = {
    "primary_chat_surface_hit_rate": 1.0,
    "planner_wrapper_regression": 1.0,
    "capability_truth_regression": 1.0,
    "hive_truth_label_regression": 1.0,
    "continuity_drift": 1.0,
    "builder_bounded_truth": 1.0,
    "honest_degradation": 1.0,
}

FAST_PATH_CHAT_CASES = (
    ("help", "utility_answer", "wired on this runtime:"),
    ("hey", "smalltalk", "hey."),
    ("hello", "smalltalk", "hello."),
    ("how are you", "smalltalk", "running clean. what do you need?"),
)

MODEL_FINAL_CHAT_CASES = (
    ("do you think boredom is useful?", "Boredom is useful when it exposes shallow defaults."),
    ("how should i position my b2b analytics product?", "Position it around the painful decision it makes faster."),
    ("what should i eat after lifting?", "Prioritize protein, carbs, and something you will actually repeat."),
    ("my partner and i keep having the same argument. what should i do?", "Slow the loop down and name the real repeated trigger."),
    ("brainstorm a launch campaign idea for a weird soda brand", "Turn the weirdness into a clear, memorable ritual."),
    ("tell me about stoicism", "Stoicism is about judgment and discipline, not emotional numbness."),
    ("why does python late binding in closures surprise people?", "Because the closure captures the variable, not the value at definition time."),
    ("what is a clean way to structure retries in a local-first queue?", "Separate retry policy from job state and record every attempt explicitly."),
    ("my react dev server keeps reloading on save; where would you look first?", "Start with the file watcher, symlinks, and editor temp-file behavior."),
    ("postgres vs sqlite for a local-first app?", "Start with SQLite unless your actual concurrency or replication needs already exceed it."),
    ("what makes a good research question?", "A good research question is specific enough to test and open enough to learn from."),
)

FAST_PATH_LIVE_INFO_CASES = (
    (
        "what is the weather in London today?",
        "search_query",
        [
            {
                "summary": "Cloudy with light rain, around 11C, with breezy afternoon conditions.",
                "source_label": "duckduckgo.com",
                "origin_domain": "bbc.com",
                "result_title": "BBC Weather - London",
                "result_url": "https://www.bbc.com/weather/2643743",
                "used_browser": False,
            }
        ],
        ("weather in london:", "[bbc.com](https://www.bbc.com/weather/2643743)"),
    ),
)

MODEL_FINAL_LIVE_INFO_CASES = (
    (
        "latest telegram bot api updates",
        "planned_search_query",
        [
            {
                "summary": "Telegram Bot API docs remain the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
        "Telegram Bot API docs are still the canonical source for these updates.",
    ),
)

PLANNER_LEAK_CORPUS = (
    ("do you think boredom is useful?", "Workflow:\n- classify the thought\n\nBoredom can expose shallow defaults."),
    ("how should i position my b2b analytics product?", "Here's what I'd suggest:\n\nOwn the painful decision, not the dashboard."),
    ("what should i eat after lifting?", 'Real steps completed:\n- compared recovery basics\n\n{"summary":"Eat protein plus carbs.","bullets":["Keep it simple.","Stay consistent."]}'),
    ("my partner and i keep having the same argument. what should i do?", "Workflow:\n- map the conflict\n\nName the recurring trigger before solving it."),
    ("brainstorm a launch campaign idea for a weird soda brand", "Here's what I'd suggest:\n\nMake the weirdness the ritual, not a random gimmick."),
    ("tell me about stoicism", 'Real steps completed:\n- summarized sources\n\n{"summary":"Stoicism trains judgment.","bullets":["Attention matters.","Discipline matters."]}'),
    ("why does python late binding in closures surprise people?", "Workflow:\n- inspect closure semantics\n\nThe closure captures the variable, not a frozen value."),
    ("what is a clean way to structure retries in a local-first queue?", "Here's what I'd suggest:\n\nSplit retry policy from durable job state."),
    ("my react dev server keeps reloading on save; where would you look first?", "Workflow:\n- inspect watcher causes\n\nStart with the file watcher and temp-file churn."),
    ("postgres vs sqlite for a local-first app?", "Here's what I'd suggest:\n\nUse SQLite until real concurrency and replication pressure justify Postgres."),
    ("what makes a good research question?", 'Real steps completed:\n- compared weak and strong questions\n\n{"summary":"A good research question is falsifiable and useful."}'),
    ("how do i explain unit economics to a founder?", "Workflow:\n- simplify the framing\n\nAnchor it in one customer, one period, and one margin story."),
)

CAPABILITY_TRUTH_CASES = (
    {
        "label": "email_unwired",
        "prompt": "can you send email from here?",
        "expected": ("email sending is not wired on this runtime", "draft the email text"),
    },
    {
        "label": "builder_partial_ios",
        "prompt": "can you build a full ios app end to end?",
        "entries": [
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only, not a full autonomous build/debug/test loop.",
            }
        ],
        "expected": ("partially", "not a full autonomous build/debug/test loop"),
    },
    {
        "label": "builder_partial_android",
        "prompt": "are you able to build a full android app?",
        "entries": [
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only, not a full autonomous build/debug/test loop.",
            }
        ],
        "expected": ("partially", "scaffold-level support"),
    },
    {
        "label": "swarm_future",
        "prompt": "can you delegate to other agents and merge helper outputs?",
        "expected": ("not wired on this runtime yet", "hive"),
    },
    {
        "label": "impossible",
        "prompt": "can you read my mind?",
        "expected": ("outside what this runtime can actually do",),
    },
)

HIVE_TRUTH_CASES = (
    {
        "label": "watcher_fresh",
        "kind": "task_list",
        "details": {
            "command_kind": "task_list",
            "watcher_status": "ok",
            "response_text": (
                "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 2 total):\n"
                "- [open] OpenClaw integration audit (#7d33994f)\n"
                "- [researching] Hive footer cleanup (#ada43859)\n"
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
                {"topic_id": "topic-1", "title": "OpenClaw integration audit", "status": "open"},
                {"topic_id": "topic-2", "title": "Hive footer cleanup", "status": "researching"},
            ],
            "online_agents": [],
        },
        "reply": "I can see two Hive tasks open right now.",
        "must_contain": ("Hive truth: watcher-derived.",),
        "model_input_markers": ("watcher-derived", "fresh"),
    },
    {
        "label": "watcher_stale",
        "kind": "task_list",
        "details": {
            "command_kind": "task_list",
            "watcher_status": "ok",
            "response_text": (
                "Available Hive tasks right now (watcher-derived; presence stale (420s old); 1 total):\n"
                "- [open] OpenClaw continuity cleanup (#7d33994f)\n"
            ),
            "truth_source": "watcher",
            "truth_label": "watcher-derived",
            "truth_status": "ok",
            "presence_claim_state": "visible",
            "presence_source": "watcher",
            "presence_truth_label": "watcher-derived",
            "presence_freshness_label": "stale",
            "presence_age_seconds": 420,
            "topics": [
                {"topic_id": "topic-1", "title": "OpenClaw continuity cleanup", "status": "open"},
            ],
            "online_agents": [],
        },
        "reply": "I can see one Hive task, but watcher presence looks stale.",
        "must_contain": ("Hive truth: watcher-derived.",),
        "model_input_markers": ("watcher-derived", "stale"),
    },
    {
        "label": "public_bridge_status",
        "kind": "status",
        "reply": "Agent Commons is still researching with 1 active claim and 2 artifacts so far.",
        "must_contain": ("Hive truth: public-bridge-derived.",),
        "model_input_markers": ("public-bridge-derived",),
    },
    {
        "label": "local_only",
        "kind": "task_list",
        "details": {
            "command_kind": "task_list",
            "watcher_status": "unavailable",
            "response_text": "Local Hive topics in this runtime (local-only; 1 total):\n- [open] Local queue repair (#abcd1234)\n",
            "truth_source": "local",
            "truth_label": "local-only",
            "truth_status": "fallback",
            "presence_claim_state": "unknown",
            "presence_source": "local",
            "presence_truth_label": "local-only",
            "presence_freshness_label": "unknown",
            "presence_age_seconds": None,
            "topics": [{"topic_id": "abcd1234", "title": "Local queue repair", "status": "open"}],
            "online_agents": [],
        },
        "reply": "I can only see the local Hive state in this runtime right now.",
        "must_contain": ("Hive truth: local-only.",),
        "model_input_markers": ("local-only",),
    },
    {
        "label": "future_unsupported",
        "kind": "disabled_followup",
        "reply": "I can't claim that live Hive task here.",
        "must_contain": ("future/unsupported",),
        "model_input_markers": (),
    },
)

CONTINUITY_PRESERVE_CASES = (
    (
        "telegram bot",
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        "I'll compare the tradeoffs and sketch a cleaner plan next.",
        "ok do that",
    ),
    (
        "telegram bot",
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        "I'll compare the tradeoffs and sketch a cleaner plan next.",
        "what do you mean by that?",
    ),
    (
        "telegram bot",
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        "I'll compare the tradeoffs and sketch a cleaner plan next.",
        "ok do that",
    ),
)

CONTINUITY_CLEAR_CASES = (
    (
        "telegram bot",
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        "I'll compare the tradeoffs and sketch a cleaner plan next.",
        "ok do that",
        "What should I eat after lifting?",
        "eat after lifting",
    ),
    (
        "telegram bot",
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        "I'll compare the tradeoffs and sketch a cleaner plan next.",
        "ok do that",
        "What should I eat after lifting?",
        "eat after lifting",
    ),
    (
        "telegram bot",
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        "I'll compare the tradeoffs and sketch a cleaner plan next.",
        "ok do that",
        "What should I eat after lifting?",
        "eat after lifting",
    ),
)

HONEST_DEGRADATION_CASES = (
    (
        "provider_unavailable",
        "do you think boredom is useful?",
        ModelExecutionDecision(
            source="no_provider_available",
            task_hash="alpha-provider-missing",
            confidence=0.84,
            trust_score=0.84,
            used_model=False,
        ),
        "couldn't get a live model response",
    ),
    (
        "provider_unusable",
        "how should i position my b2b analytics product?",
        ModelExecutionDecision(
            source="provider_execution",
            task_hash="alpha-provider-empty",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="",
            confidence=0.84,
            trust_score=0.84,
        ),
        "couldn't get a usable model response",
    ),
    (
        "cache_blocked",
        "do you think boredom is useful?",
        ModelExecutionDecision(
            source="exact_cache_hit",
            task_hash="alpha-cache-hit",
            used_model=False,
            output_text="Cached answer that should not be reused.",
            confidence=0.84,
            trust_score=0.84,
        ),
        "not passing cached text off as a fresh answer",
    ),
    (
        "memory_blocked",
        "how should i position my b2b analytics product?",
        ModelExecutionDecision(
            source="memory_hit",
            task_hash="alpha-memory-hit",
            used_model=False,
            output_text="Remembered answer that should not be reused.",
            confidence=0.84,
            trust_score=0.84,
        ),
        "not presenting remembered text as a fresh answer",
    ),
    (
        "live_info_memory_blocked",
        "latest telegram bot api updates",
        ModelExecutionDecision(
            source="memory_hit",
            task_hash="alpha-live-memory-hit",
            used_model=False,
            output_text="Remembered Bot API answer that should not become the reply.",
            confidence=0.84,
            trust_score=0.84,
        ),
        "could not ground a current answer confidently",
    ),
)


def _session_id(label: str) -> str:
    return f"openclaw:alpha:{label}:{uuid.uuid4().hex}"


def _provider_decision(*, task_hash: str, output_text: str) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider",
        task_hash=task_hash,
        provider_id="ollama:qwen",
        used_model=True,
        output_text=output_text,
        confidence=0.84,
        trust_score=0.84,
    )


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


def _normalized(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _assert_threshold(*, name: str, passed: int, total: int, threshold: float, failures: list[str]) -> None:
    rate = (float(passed) / float(total)) if total else 0.0
    if rate >= threshold:
        return
    details = "\n".join(f"- {item}" for item in failures[:12])
    pytest.fail(
        f"{name} failed threshold {threshold:.0%}: {passed}/{total} passed ({rate:.1%}).\nFailures:\n{details or '- none captured'}"
    )


def _disable_adaptive_research(agent) -> None:
    agent._collect_adaptive_research = mock.Mock(  # type: ignore[assignment]
        return_value=AdaptiveResearchResult(enabled=False, reason="alpha_gauntlet_disabled")
    )


def _configure_model_chat_path(agent, context_result_factory, *, decision: ModelExecutionDecision, disable_research: bool = True) -> None:
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(return_value=decision)  # type: ignore[assignment]
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="alpha_gauntlet")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )
    if disable_research:
        _disable_adaptive_research(agent)


def _common_runtime_patches():
    return (
        mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]),
        mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None),
        mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]),
        mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None),
    )


@contextmanager
def _common_runtime_patch_stack():
    with ExitStack() as stack:
        for patcher in _common_runtime_patches():
            stack.enter_context(patcher)
        yield


def _run_plain_chat_case(make_agent, context_result_factory, prompt: str, reply: str) -> tuple[bool, str]:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(task_hash=f"alpha-chat-{uuid.uuid4().hex}", output_text=reply),
    )
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            session_id_override=_session_id("chat"),
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"{prompt}: expected 1 metric event, got {len(events)}"
    event = events[0]
    ok = (
        event.get("fast_path_hit") is False
        and event.get("model_inference_used") is True
        and event.get("model_final_answer_hit") is True
        and event.get("template_renderer_hit") is False
        and result["response"] == reply
    )
    return ok, f"{prompt}: metrics={event} response={result['response']!r}"


def _run_fast_path_chat_case(
    make_agent,
    context_result_factory,
    prompt: str,
    expected_class: str,
    expected_snippet: str,
) -> tuple[bool, str]:
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("fast chat should stay on fast path"))  # type: ignore[assignment]
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            session_id_override=_session_id("fast-chat"),
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"{prompt}: expected 1 metric event, got {len(events)}"
    event = events[0]
    ok = (
        event.get("fast_path_hit") is True
        and event.get("model_inference_used") is False
        and event.get("model_final_answer_hit") is False
        and event.get("template_renderer_hit") is False
        and result["response_class"] == expected_class
        and expected_snippet in _normalized(result["response"])
        and result["model_execution"]["used_model"] is False
        and agent.memory_router.resolve.call_count == 0
    )
    return ok, f"{prompt}: metrics={event} response={result['response']!r}"


def _run_live_info_case(
    make_agent,
    context_result_factory,
    prompt: str,
    search_method: str,
    search_results: list[dict[str, object]],
    reply: str,
) -> tuple[bool, str]:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(task_hash=f"alpha-live-{uuid.uuid4().hex}", output_text=reply),
        disable_research=False,
    )
    planned_search_return = search_results if search_method == "planned_search_query" else mock.DEFAULT
    search_return = search_results if search_method == "search_query" else mock.DEFAULT
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=planned_search_return,
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        return_value=search_return,
    ), _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            session_id_override=_session_id("live-info"),
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"{prompt}: expected 1 metric event, got {len(events)}"
    event = events[0]
    ok = (
        event.get("fast_path_hit") is False
        and event.get("model_inference_used") is True
        and event.get("model_final_answer_hit") is True
        and event.get("template_renderer_hit") is False
        and event.get("tool_backing_sources") == ["web_lookup"]
        and result["response"] == reply
    )
    return ok, f"{prompt}: metrics={event} response={result['response']!r}"


def _run_fast_path_live_info_case(
    make_agent,
    context_result_factory,
    prompt: str,
    search_method: str,
    search_results: list[dict[str, object]],
    expected_snippets: tuple[str, ...],
) -> tuple[bool, str]:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(task_hash=f"alpha-fast-live-{uuid.uuid4().hex}", output_text="stale provider text"),
        disable_research=False,
    )
    planned_search_return = search_results if search_method == "planned_search_query" else mock.DEFAULT
    search_return = search_results if search_method == "search_query" else mock.DEFAULT
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=planned_search_return,
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        return_value=search_return,
    ), _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            session_id_override=_session_id("fast-live-info"),
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"{prompt}: expected 1 metric event, got {len(events)}"
    event = events[0]
    response_text = _normalized(result["response"])
    ok = (
        event.get("fast_path_hit") is True
        and event.get("model_inference_used") is False
        and event.get("model_final_answer_hit") is False
        and event.get("template_renderer_hit") is False
        and event.get("tool_backing_sources") == ["web_lookup"]
        and result["response_class"] == "utility_answer"
        and all(snippet in response_text for snippet in expected_snippets)
        and result["model_execution"]["used_model"] is False
        and agent.memory_router.resolve.call_count == 0
    )
    return ok, f"{prompt}: metrics={event} response={result['response']!r}"


def _run_hive_model_case(make_agent, label: str) -> tuple[bool, str]:
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""
    if label == "task_list":
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
            True,
            {
                "command_kind": "task_list",
                "watcher_status": "ok",
                "response_text": (
                    "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 2 total):\n"
                    "- [open] OpenClaw integration audit (#7d33994f)\n"
                    "- [researching] Hive footer cleanup (#ada43859)\n"
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
                    {"topic_id": "topic-1", "title": "OpenClaw integration audit", "status": "open"},
                    {"topic_id": "topic-2", "title": "Hive footer cleanup", "status": "researching"},
                ],
                "online_agents": [],
            },
        )
        prompt = "show me the open hive tasks"
        reply = "I can see two Hive tasks open right now."
    else:
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (False, None)
        prompt = "what is the status"
        reply = "Agent Commons is still researching with 1 active claim and 2 artifacts so far."
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(task_hash=f"alpha-hive-{label}", output_text=reply)
    )
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        if label == "task_list":
            result = agent.run_once(
                prompt,
                session_id_override=_session_id("hive-list"),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
        else:
            hive_state = {
                "watched_topic_ids": ["topic-1"],
                "interaction_payload": {"active_topic_id": "topic-1"},
            }
            packet = {
                "topic": {"topic_id": "topic-1", "title": "Agent Commons", "status": "researching"},
                "truth_source": "public_bridge",
                "truth_label": "public-bridge-derived",
                "truth_transport": "direct",
                "truth_timestamp": "2026-03-13T09:10:00+00:00",
                "execution_state": {"execution_state": "claimed", "active_claim_count": 1, "artifact_count": 2},
                "counts": {"post_count": 1, "active_claim_count": 1},
                "posts": [{"post_kind": "result", "body": "First bounded pass landed."}],
            }
            with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
                agent.public_hive_bridge, "enabled", return_value=True
            ), mock.patch.object(
                agent.public_hive_bridge, "get_public_research_packet", return_value=packet
            ):
                result = agent.run_once(
                    prompt,
                    session_id_override=_session_id("hive-status"),
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"hive {label}: expected 1 metric event, got {len(events)}"
    event = events[0]
    ok = (
        event.get("fast_path_hit") is False
        and event.get("model_inference_used") is True
        and event.get("model_final_answer_hit") is True
        and event.get("template_renderer_hit") is False
        and event.get("tool_backing_sources") == ["hive"]
    )
    return ok, f"hive {label}: metrics={event} response={result['response']!r}"


def _run_capability_truth_case(make_agent, case: dict[str, object]) -> tuple[bool, str]:
    agent = make_agent()
    entries = case.get("entries")
    patcher = mock.patch.object(agent, "_capability_ledger_entries", return_value=list(entries or [])) if entries else nullcontext()
    with patcher:
        result = agent.run_once(
            str(case["prompt"]),
            session_id_override=_session_id("capability"),
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    lowered = result["response"].lower()
    ok = result["model_execution"]["used_model"] is False and all(
        marker in lowered for marker in [str(item).lower() for item in tuple(case["expected"])]
    )
    return ok, f"{case['label']}: response={result['response']!r}"


@contextmanager
def nullcontext():
    yield


def _run_hive_truth_case(make_agent, case: dict[str, object]) -> tuple[bool, str]:
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""
    agent.hive_activity_tracker.maybe_handle_command_details.return_value = (False, None)
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_provider_decision(task_hash=f"alpha-hive-truth-{case['label']}", output_text=str(case.get("reply") or "ok"))
    )
    kind = str(case["kind"])
    with _common_runtime_patch_stack():
        if kind == "task_list":
            agent.hive_activity_tracker.maybe_handle_command_details.return_value = (True, dict(case["details"]))
            result = agent.run_once(
                "show me the open hive tasks",
                session_id_override=_session_id("hive-truth"),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
        elif kind == "status":
            agent.hive_activity_tracker.maybe_handle_command_details.return_value = (False, None)
            hive_state = {
                "watched_topic_ids": ["topic-1"],
                "interaction_payload": {"active_topic_id": "topic-1"},
            }
            packet = {
                "topic": {"topic_id": "topic-1", "title": "Agent Commons", "status": "researching"},
                "truth_source": "public_bridge",
                "truth_label": "public-bridge-derived",
                "truth_transport": "direct",
                "truth_timestamp": "2026-03-13T09:10:00+00:00",
                "execution_state": {"execution_state": "claimed", "active_claim_count": 1, "artifact_count": 2},
                "counts": {"post_count": 1, "active_claim_count": 1},
                "posts": [{"post_kind": "result", "body": "First bounded pass landed."}],
            }
            with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
                agent.public_hive_bridge, "enabled", return_value=True
            ), mock.patch.object(
                agent.public_hive_bridge, "get_public_research_packet", return_value=packet
            ):
                result = agent.run_once(
                    "what is the status",
                    session_id_override=_session_id("hive-status-truth"),
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
            model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
        else:
            hive_state = {
                "pending_topic_ids": ["topic-1"],
                "interaction_mode": "hive_task_selection_pending",
                "interaction_payload": {"shown_topic_ids": ["topic-1"], "shown_titles": ["OpenClaw integration audit"]},
            }
            with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
                agent.public_hive_bridge, "enabled", return_value=False
            ):
                result = agent.run_once(
                    "yes",
                    session_id_override=_session_id("hive-disabled-truth"),
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
            model_input = ""
    lowered = result["response"].lower()
    ok = all(str(marker).lower() in lowered for marker in tuple(case["must_contain"]))
    ok = ok and all(str(marker).lower() in model_input for marker in tuple(case["model_input_markers"]))
    return ok, f"{case['label']}: response={result['response']!r} model_input={model_input!r}"


def _run_builder_scaffold_once(make_agent, context_result_factory, run_label: str) -> tuple[bool, str]:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(
            task_hash=f"alpha-builder-scaffold-{run_label}",
            output_text="I finished the bounded Telegram build loop and the compile check passed.",
        ),
        disable_research=True,
    )
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
    agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]

    def _execute_builder_step(
        payload,
        *,
        task_id,
        session_id,
        source_context,
        hive_activity_tracker,
        public_hive_bridge=None,
        checkpoint_id=None,
        step_index=0,
        trusted_local_only=False,
    ):
        tool_name = str(payload.get("intent") or "")
        arguments = dict(payload.get("arguments") or {})
        if tool_name == "workspace.write_file":
            path = str(arguments.get("path") or "generated/file.txt")
            content = str(arguments.get("content") or "")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text=f"Created file `{path}` with {len(content.splitlines())} lines.",
                mode="tool_executed",
                tool_name="workspace.write_file",
                details={
                    "artifacts": [
                        {
                            "artifact_type": "file_diff",
                            "path": path,
                            "action": "created",
                            "line_count": len(content.splitlines()),
                            "diff_preview": f"--- a/{path}\n+++ b/{path}\n@@\n+created",
                        }
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": path,
                        "line_count": len(content.splitlines()),
                        "action": "created",
                    },
                },
            )
        if tool_name == "sandbox.run_command":
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="Command executed in `.`:\n$ python3 -m compileall -q generated/telegram-bot/src\n- Exit code: 0",
                mode="tool_executed",
                tool_name="sandbox.run_command",
                details={
                    "artifacts": [
                        {
                            "artifact_type": "command_output",
                            "command": "python3 -m compileall -q generated/telegram-bot/src",
                            "cwd": ".",
                            "returncode": 0,
                            "stdout": "",
                            "stderr": "",
                            "status": "executed",
                        }
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 -m compileall -q generated/telegram-bot/src",
                        "cwd": ".",
                        "returncode": 0,
                    },
                },
            )
        raise AssertionError(f"unexpected builder tool: {tool_name}")

    with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_execute_builder_step), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ):
        result = agent.run_once(
            "build a telegram bot in this workspace and write the files",
            session_id_override=_session_id(f"builder-scaffold-{run_label}"),
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": f"/tmp/vool-alpha-builder-{run_label}"},
        )
    controller = result["details"]["builder_controller"]
    artifacts = controller["artifacts"]
    ok = (
        controller["mode"] == "scaffold"
        and 1 <= int(controller["step_count"]) <= 6
        and str(controller["stop_reason"]).strip() != ""
        and bool(artifacts["file_diffs"])
        and bool(artifacts["command_outputs"])
        and "artifacts:" in result["response"].lower()
    )
    return ok, f"scaffold {run_label}: stop={controller['stop_reason']} artifacts={artifacts}"


def _run_builder_retry_once(make_agent, context_result_factory, run_label: str) -> tuple[bool, str]:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(
            task_hash=f"alpha-builder-workflow-{run_label}",
            output_text="I repaired the file and the rerun passed.",
        ),
        disable_research=True,
    )
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
    agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]
    step_counter = {"count": 0}
    command_counter = {"python3 app.py": 0}

    def _execute_builder_step(
        payload,
        *,
        task_id,
        session_id,
        source_context,
        hive_activity_tracker,
        public_hive_bridge=None,
        checkpoint_id=None,
        step_index=0,
        trusted_local_only=False,
    ):
        tool_name = str(payload.get("intent") or "")
        step_counter["count"] += 1
        if tool_name == "sandbox.run_command":
            command = str(dict(payload.get("arguments") or {}).get("command") or "").strip()
            command_counter[command] = int(command_counter.get(command, 0)) + 1
        if tool_name == "sandbox.run_command" and command_counter.get("python3 app.py", 0) == 1:
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="Command executed in `.`:\n$ python3 app.py\n- Exit code: 1\n- Stderr:\nFAILED test_example",
                mode="tool_executed",
                tool_name="sandbox.run_command",
                details={
                    "artifacts": [
                        {
                            "artifact_type": "command_output",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 1,
                            "stdout": "",
                            "stderr": "FAILED test_example",
                            "status": "executed",
                        },
                        {
                            "artifact_type": "failure",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 1,
                            "summary": "FAILED test_example",
                            "stdout": "",
                            "stderr": "FAILED test_example",
                        },
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "stderr": "FAILED test_example",
                        "failure_summary": "FAILED test_example",
                        "error_path": "app.py",
                        "error_line": 1,
                    },
                },
            )
        if tool_name == "workspace.search_text":
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="Found 1 match for `FAILED test_example` in `app.py`.",
                mode="tool_executed",
                tool_name="workspace.search_text",
                details={
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "workspace.search_text",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "query": "FAILED test_example",
                        "match_count": 1,
                        "matches": [{"path": "app.py", "line": 1, "preview": "TODO"}],
                    }
                },
            )
        if tool_name == "workspace.read_file":
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="File `app.py`:\n1: TODO",
                mode="tool_executed",
                tool_name="workspace.read_file",
                details={
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 1,
                    }
                },
            )
        if tool_name == "workspace.replace_in_file":
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="Applied 1 replacement in `app.py`.",
                mode="tool_executed",
                tool_name="workspace.replace_in_file",
                details={
                    "artifacts": [
                        {
                            "artifact_type": "file_diff",
                            "path": "app.py",
                            "action": "replaced",
                            "replacements": 1,
                            "diff_preview": "--- a/app.py\n+++ b/app.py\n@@\n-TODO\n+DONE",
                        }
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "workspace.replace_in_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "replacements": 1,
                        "diff_preview": "--- a/app.py\n+++ b/app.py\n@@\n-TODO\n+DONE",
                    },
                },
            )
        if tool_name == "sandbox.run_command" and command_counter.get("python3 app.py", 0) == 2:
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="Command executed in `.`:\n$ python3 app.py\n- Exit code: 0\n- Stdout:\nclean",
                mode="tool_executed",
                tool_name="sandbox.run_command",
                details={
                    "artifacts": [
                        {
                            "artifact_type": "command_output",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 0,
                            "stdout": "clean",
                            "stderr": "",
                            "status": "executed",
                        }
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "cwd": ".",
                        "returncode": 0,
                        "stdout": "clean",
                    },
                },
            )
        raise AssertionError(f"unexpected builder tool: {tool_name}")

    with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_execute_builder_step), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch(
        "core.agent_runtime.agent.classify",
        return_value={"task_class": "debugging", "risk_flags": [], "confidence_hint": 0.82},
    ):
        result = agent.run_once(
            "run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            session_id_override=_session_id(f"builder-retry-{run_label}"),
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": f"/tmp/vool-alpha-retry-{run_label}"},
        )
    controller = result["details"]["builder_controller"]
    artifacts = controller["artifacts"]
    retry_history = list(artifacts.get("retry_history") or [])
    ok = (
        controller["mode"] == "workflow"
        and 1 <= int(controller["step_count"]) <= 6
        and str(controller["stop_reason"]).strip() != ""
        and bool(artifacts["failures"])
        and bool(retry_history)
        and int(retry_history[0].get("attempts") or 0) == 2
        and "failures seen" in result["response"].lower()
        and "retries" in result["response"].lower()
    )
    return ok, f"workflow {run_label}: stop={controller['stop_reason']} artifacts={artifacts}"


def _run_degradation_case(make_agent, context_result_factory, case) -> tuple[bool, str]:
    label, prompt, decision, expected_snippet = case
    agent = make_agent()
    _configure_model_chat_path(agent, context_result_factory, decision=decision, disable_research=False)
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.render_response",
        side_effect=AssertionError("alpha degradation should not fall back to planner renderer"),
    ), _common_runtime_patch_stack():
        if label == "live_info_memory_blocked":
            with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
                "core.agent_runtime.agent.WebAdapter.planned_search_query",
                return_value=[
                    {
                        "summary": "Telegram Bot API docs remain the canonical source for Bot API updates.",
                        "confidence": 0.67,
                        "source_profile_id": "messaging_platform_docs",
                        "source_profile_label": "Messaging platform docs",
                        "result_title": "Telegram Bot API",
                        "result_url": "https://core.telegram.org/bots/api",
                        "origin_domain": "core.telegram.org",
                    }
                ],
            ):
                result = agent.run_once(
                    prompt,
                    session_id_override=_session_id("degrade-live"),
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
        else:
            result = agent.run_once(
                prompt,
                session_id_override=_session_id("degrade-chat"),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
    events = _chat_truth_events(audit_log)
    ok = len(events) == 1 and expected_snippet.lower() in result["response"].lower()
    ok = ok and all(marker not in result["response"].lower() for marker in FORBIDDEN_PLANNER_LEAKS)
    return ok, f"{label}: response={result['response']!r} events={events}"


def test_alpha_hardening_definition_is_complete() -> None:
    assert set(ALPHA_SHIP_THRESHOLDS) == {
        "primary_chat_surface_hit_rate",
        "planner_wrapper_regression",
        "capability_truth_regression",
        "hive_truth_label_regression",
        "continuity_drift",
        "builder_bounded_truth",
        "honest_degradation",
    }
    assert len(FAST_PATH_CHAT_CASES) + len(MODEL_FINAL_CHAT_CASES) + len(FAST_PATH_LIVE_INFO_CASES) + len(MODEL_FINAL_LIVE_INFO_CASES) + 2 == 19
    assert len(PLANNER_LEAK_CORPUS) == 12
    assert len(CAPABILITY_TRUTH_CASES) == 5
    assert len(HIVE_TRUTH_CASES) == 5
    assert len(CONTINUITY_PRESERVE_CASES) + len(CONTINUITY_CLEAR_CASES) == 6
    assert len(HONEST_DEGRADATION_CASES) == 5


def test_alpha_primary_chat_surface_hit_rate_broader_non_command_corpus(make_agent, context_result_factory, enable_web) -> None:
    passed = 0
    failures: list[str] = []

    for prompt, expected_class, expected_snippet in FAST_PATH_CHAT_CASES:
        ok, detail = _run_fast_path_chat_case(make_agent, context_result_factory, prompt, expected_class, expected_snippet)
        passed += int(ok)
        if not ok:
            failures.append(detail)

    for prompt, reply in MODEL_FINAL_CHAT_CASES:
        ok, detail = _run_plain_chat_case(make_agent, context_result_factory, prompt, reply)
        passed += int(ok)
        if not ok:
            failures.append(detail)

    for prompt, search_method, search_results, expected_snippets in FAST_PATH_LIVE_INFO_CASES:
        ok, detail = _run_fast_path_live_info_case(
            make_agent,
            context_result_factory,
            prompt,
            search_method,
            search_results,
            expected_snippets,
        )
        passed += int(ok)
        if not ok:
            failures.append(detail)

    for prompt, search_method, search_results, reply in MODEL_FINAL_LIVE_INFO_CASES:
        ok, detail = _run_live_info_case(make_agent, context_result_factory, prompt, search_method, search_results, reply)
        passed += int(ok)
        if not ok:
            failures.append(detail)

    for label in ("task_list", "status"):
        ok, detail = _run_hive_model_case(make_agent, label)
        passed += int(ok)
        if not ok:
            failures.append(detail)

    total = len(FAST_PATH_CHAT_CASES) + len(MODEL_FINAL_CHAT_CASES) + len(FAST_PATH_LIVE_INFO_CASES) + len(MODEL_FINAL_LIVE_INFO_CASES) + 2
    _assert_threshold(
        name="primary_chat_surface_hit_rate",
        passed=passed,
        total=total,
        threshold=ALPHA_SHIP_THRESHOLDS["primary_chat_surface_hit_rate"],
        failures=failures,
    )


def test_alpha_planner_wrapper_regression_long_tail(make_agent, context_result_factory) -> None:
    passed = 0
    failures: list[str] = []

    for prompt, reply in PLANNER_LEAK_CORPUS:
        agent = make_agent()
        _configure_model_chat_path(
            agent,
            context_result_factory,
            decision=_provider_decision(task_hash=f"alpha-leak-{uuid.uuid4().hex}", output_text=reply),
        )
        with _common_runtime_patch_stack():
            result = agent.run_once(
                prompt,
                session_id_override=_session_id("planner-leak"),
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
        ok = all(marker not in result["response"].lower() for marker in FORBIDDEN_PLANNER_LEAKS)
        passed += int(ok)
        if not ok:
            failures.append(f"{prompt}: leaked response={result['response']!r}")

    _assert_threshold(
        name="planner_wrapper_regression",
        passed=passed,
        total=len(PLANNER_LEAK_CORPUS),
        threshold=ALPHA_SHIP_THRESHOLDS["planner_wrapper_regression"],
        failures=failures,
    )


def test_alpha_capability_truth_regression_free_form(make_agent) -> None:
    passed = 0
    failures: list[str] = []
    for case in CAPABILITY_TRUTH_CASES:
        ok, detail = _run_capability_truth_case(make_agent, case)
        passed += int(ok)
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="capability_truth_regression",
        passed=passed,
        total=len(CAPABILITY_TRUTH_CASES),
        threshold=ALPHA_SHIP_THRESHOLDS["capability_truth_regression"],
        failures=failures,
    )


def test_alpha_hive_truth_label_regression(make_agent) -> None:
    passed = 0
    failures: list[str] = []
    for case in HIVE_TRUTH_CASES:
        ok, detail = _run_hive_truth_case(make_agent, case)
        passed += int(ok)
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="hive_truth_label_regression",
        passed=passed,
        total=len(HIVE_TRUTH_CASES),
        threshold=ALPHA_SHIP_THRESHOLDS["hive_truth_label_regression"],
        failures=failures,
    )


def test_alpha_continuity_followup_drift() -> None:
    passed = 0
    failures: list[str] = []
    persona = load_active_persona("default")

    for label, user_input, assistant_output, followup_text in CONTINUITY_PRESERVE_CASES:
        session_id = _session_id(f"continuity-preserve-{label}")
        adapt_user_input(user_input, session_id=session_id)
        append_conversation_event(
            session_id=session_id,
            user_input=user_input,
            assistant_output=assistant_output,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        followup = adapt_user_input(followup_text, session_id=session_id)
        session = get_dialogue_session(session_id)
        items = build_bootstrap_context(
            persona=persona,
            task=create_task_record(followup_text),
            classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
            interpretation=followup,
            session_id=session_id,
        )
        continuity_items = [item for item in items if item.source_type == "dialogue_continuity"]
        ok = bool(followup.reference_targets)
        ok = ok and bool(session.get("assistant_commitments")) and bool(session.get("unresolved_followups"))
        ok = ok and label in _normalized(str(session.get("current_user_goal") or ""))
        ok = ok and continuity_items and label in continuity_items[0].content.lower()
        passed += int(ok)
        if not ok:
            failures.append(f"preserve {label}: session={session} continuity_items={[item.content for item in continuity_items]}")

    for label, user_input, assistant_output, followup_text, unrelated_turn, new_goal_snippet in CONTINUITY_CLEAR_CASES:
        session_id = _session_id(f"continuity-clear-{label}")
        adapt_user_input(user_input, session_id=session_id)
        append_conversation_event(
            session_id=session_id,
            user_input=user_input,
            assistant_output=assistant_output,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        adapt_user_input(followup_text, session_id=session_id)
        unrelated = adapt_user_input(unrelated_turn, session_id=session_id)
        session = get_dialogue_session(session_id)
        items = build_bootstrap_context(
            persona=persona,
            task=create_task_record(unrelated_turn),
            classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
            interpretation=unrelated,
            session_id=session_id,
        )
        continuity_items = [item for item in items if item.source_type == "dialogue_continuity"]
        old_commitment = assistant_output.strip(".").lower()
        ok = new_goal_snippet in _normalized(str(session.get("current_user_goal") or ""))
        ok = ok and session.get("assistant_commitments") == []
        ok = ok and session.get("unresolved_followups") == []
        ok = ok and (
            not continuity_items or old_commitment not in continuity_items[0].content.lower()
        )
        passed += int(ok)
        if not ok:
            failures.append(f"clear {label}: session={session} continuity_items={[item.content for item in continuity_items]}")

    total = len(CONTINUITY_PRESERVE_CASES) + len(CONTINUITY_CLEAR_CASES)
    _assert_threshold(
        name="continuity_drift",
        passed=passed,
        total=total,
        threshold=ALPHA_SHIP_THRESHOLDS["continuity_drift"],
        failures=failures,
    )


def test_alpha_builder_bounded_flow_truth_soak(make_agent, context_result_factory) -> None:
    passed = 0
    failures: list[str] = []
    for index in range(3):
        ok, detail = _run_builder_scaffold_once(make_agent, context_result_factory, f"s{index}")
        passed += int(ok)
        if not ok:
            failures.append(detail)
    for index in range(3):
        ok, detail = _run_builder_retry_once(make_agent, context_result_factory, f"w{index}")
        passed += int(ok)
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="builder_bounded_truth",
        passed=passed,
        total=6,
        threshold=ALPHA_SHIP_THRESHOLDS["builder_bounded_truth"],
        failures=failures,
    )


def test_alpha_honest_degradation_under_provider_failure(make_agent, context_result_factory, enable_web) -> None:
    passed = 0
    failures: list[str] = []
    for case in HONEST_DEGRADATION_CASES:
        ok, detail = _run_degradation_case(make_agent, context_result_factory, case)
        passed += int(ok)
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="honest_degradation",
        passed=passed,
        total=len(HONEST_DEGRADATION_CASES),
        threshold=ALPHA_SHIP_THRESHOLDS["honest_degradation"],
        failures=failures,
    )
