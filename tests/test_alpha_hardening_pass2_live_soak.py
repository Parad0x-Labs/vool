from __future__ import annotations

import json
import os
import subprocess
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from apps.vool_api_server import _ensure_default_provider
from core.curiosity_roamer import CuriosityResult
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from core.model_registry import ModelRegistry
from core.tool_intent_executor import ToolIntentExecution

if os.environ.get("VOOL_ALPHA_LIVE_SOAK") != "1":
    pytestmark = pytest.mark.skip(reason="set VOOL_ALPHA_LIVE_SOAK=1 to run live alpha soak")


FORBIDDEN_PLANNER_LEAKS = (
    "workflow:",
    "here's what i'd suggest",
    "real steps completed:",
    "summary_block",
    "action_plan",
)

ALPHA_LIVE_SOAK_THRESHOLDS = {
    "live_model_final_hit_rate": 0.875,
    "live_continuity_drift": 0.75,
    "live_info_noisy_evidence": 1.0,
    "hive_truth_edge_cases": 1.0,
    "builder_repeated_truth": 1.0,
    "honest_degradation": 1.0,
}

LIVE_CHAT_CORPUS = (
    "hey",
    "how are you",
    "do you think boredom is useful?",
    "how should i position my b2b analytics product?",
    "what should i eat after lifting?",
    "my partner and i keep having the same argument. what should i do?",
    "brainstorm a launch campaign idea for a weird soda brand",
    "why does python late binding in closures surprise people?",
)

LIVE_INFO_CASES = (
    (
        "latest telegram bot api updates",
        "fresh_lookup",
        "planned_search_query",
        [
            {
                "summary": "Telegram Bot API changelog documents recent Bot API updates.",
                "confidence": 0.74,
                "source_profile_label": "Official docs",
                "result_title": "Bot API changelog",
                "result_url": "https://core.telegram.org/bots/api-changelog",
                "origin_domain": "core.telegram.org",
            },
            {
                "summary": "Telegram Bot API is the canonical HTTP interface for bots.",
                "confidence": 0.68,
                "source_profile_label": "Official docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            },
            {
                "summary": "Third-party changelog mirrors are incomplete compared to the official docs.",
                "confidence": 0.42,
                "source_profile_label": "Community mirror",
                "result_title": "Telegram bots changelog",
                "result_url": "https://example.test/telegram-bots",
                "origin_domain": "example.test",
            },
        ],
    ),
    (
        "what is the weather in London today?",
        "weather",
        "search_query",
        [
            {
                "summary": "Cloudy with light rain, around 11C, breezy later in the afternoon.",
                "source_label": "duckduckgo.com",
                "origin_domain": "bbc.com",
                "result_title": "BBC Weather - London",
                "result_url": "https://www.bbc.com/weather/2643743",
                "used_browser": False,
            },
            {
                "summary": "Another forecast says scattered showers and cool temperatures.",
                "source_label": "duckduckgo.com",
                "origin_domain": "weather.com",
                "result_title": "Weather.com - London",
                "result_url": "https://weather.com/weather/today/l/London",
                "used_browser": False,
            },
        ],
    ),
)

CONTINUITY_CASES = (
    (
        [
            "I'm deciding between Python and Go for a Telegram bot. Compare them next.",
            "What were we comparing?",
        ],
        ("python", "go", "telegram"),
        "preserve_topic",
    ),
    (
        [
            "I'm deciding between Python and Go for a Telegram bot. Compare them next.",
            "What should I eat after lifting?",
            "What topic are we on now?",
        ],
        ("eat", "lifting", "food", "meal"),
        "clear_topic",
    ),
)

HIVE_EDGE_CASES = (
    (
        "watcher_stale",
        {
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
            "topics": [{"topic_id": "topic-1", "title": "OpenClaw continuity cleanup", "status": "open"}],
            "online_agents": [],
        },
        "show me the open hive tasks",
        ("hive truth: watcher-derived.", "stale"),
    ),
    (
        "public_bridge",
        None,
        "what is the status",
        ("hive truth: public-bridge-derived.",),
    ),
    (
        "local_only",
        {
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
        "show me the open hive tasks",
        ("hive truth: local-only.",),
    ),
    (
        "future_unsupported",
        None,
        "yes",
        ("future/unsupported",),
    ),
)

HONEST_DEGRADATION_CASES = (
    (
        "provider_unavailable",
        "do you think boredom is useful?",
        ModelExecutionDecision(
            source="no_provider_available",
            task_hash="alpha-pass2-provider-missing",
            confidence=0.81,
            trust_score=0.81,
            used_model=False,
        ),
        "couldn't get a live model response",
    ),
    (
        "provider_unusable",
        "how should i position my b2b analytics product?",
        ModelExecutionDecision(
            source="provider_execution",
            task_hash="alpha-pass2-provider-empty",
            provider_id="ollama-local:qwen2.5:7b",
            provider_name="ollama-local",
            used_model=True,
            output_text="",
            confidence=0.81,
            trust_score=0.81,
        ),
        "couldn't get a usable model response",
    ),
    (
        "live_info_memory_blocked",
        "latest telegram bot api updates",
        ModelExecutionDecision(
            source="memory_hit",
            task_hash="alpha-pass2-live-memory",
            used_model=False,
            output_text="Remembered Telegram answer that should not be reused.",
            confidence=0.81,
            trust_score=0.81,
        ),
        "couldn't produce a clean final synthesis",
    ),
)


def _require_live_provider() -> None:
    probe = subprocess.run(
        ["curl", "-sSf", "http://127.0.0.1:11434/api/tags"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.fail(f"live provider probe failed: {probe.stderr or probe.stdout}")
    payload = json.loads(probe.stdout or "{}")
    model_names = {str(item.get("name") or "").strip() for item in list(payload.get("models") or [])}
    if "qwen2.5:7b" not in model_names:
        pytest.fail(f"required live model qwen2.5:7b missing from Ollama tags: {sorted(model_names)}")


def _build_live_agent(make_agent) -> VoolAgent:
    agent = make_agent()
    registry = ModelRegistry()
    _ensure_default_provider(registry, "qwen2.5:7b")
    agent.memory_router.registry = registry
    return agent


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


def _assert_threshold(*, name: str, passed: int, total: int, threshold: float, failures: list[str]) -> None:
    rate = (float(passed) / float(total)) if total else 0.0
    if rate >= threshold:
        return
    details = "\n".join(f"- {item}" for item in failures[:12])
    pytest.fail(
        f"{name} failed threshold {threshold:.1%}: {passed}/{total} passed ({rate:.1%}).\nFailures:\n{details or '- none'}"
    )


@contextmanager
def _common_runtime_patch_stack():
    with ExitStack() as stack:
        stack.enter_context(mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None))
        stack.enter_context(mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]))
        stack.enter_context(mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None))
        yield


def _live_soak_source_context(*, session_label: str) -> dict[str, object]:
    return {
        "surface": "openclaw",
        "platform": "openclaw",
        "runtime_session_id": f"openclaw:alpha-live:{session_label}:{uuid.uuid4().hex}",
    }


def _run_live_chat_case(make_agent, prompt: str) -> tuple[bool, str]:
    agent = _build_live_agent(make_agent)
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context=_live_soak_source_context(session_label="chat"),
        )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"{prompt}: expected 1 metric event, got {len(events)}"
    event = events[0]
    lowered = str(result.get("response") or "").lower()
    ok = (
        bool(str(result.get("response") or "").strip())
        and result["model_execution"]["used_model"] is True
        and event.get("fast_path_hit") is False
        and event.get("model_inference_used") is True
        and event.get("model_final_answer_hit") is True
        and event.get("template_renderer_hit") is False
        and all(marker not in lowered for marker in FORBIDDEN_PLANNER_LEAKS)
    )
    return ok, f"{prompt}: metrics={event} response={result['response']!r}"


def _run_live_info_case(make_agent, prompt: str, live_mode: str, search_method: str, notes: list[dict[str, object]]) -> tuple[bool, str]:
    agent = _build_live_agent(make_agent)
    patch_target = "core.agent_runtime.agent.WebAdapter.planned_search_query" if search_method == "planned_search_query" else "core.agent_runtime.agent.WebAdapter.search_query"
    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        patch_target,
        return_value=notes,
    ), _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context=_live_soak_source_context(session_label=f"live-info-{live_mode}"),
        )
    events = _chat_truth_events(audit_log)
    if len(events) != 1:
        return False, f"{prompt}: expected 1 metric event, got {len(events)}"
    event = events[0]
    lowered = str(result.get("response") or "").lower()
    ok = (
        result["model_execution"]["used_model"] is True
        and event.get("fast_path_hit") is False
        and event.get("model_inference_used") is True
        and event.get("model_final_answer_hit") is True
        and event.get("template_renderer_hit") is False
        and event.get("tool_backing_sources") == ["web_lookup"]
        and "live web results for" not in lowered
        and "live weather results for" not in lowered
        and all(marker not in lowered for marker in FORBIDDEN_PLANNER_LEAKS)
    )
    return ok, f"{prompt}: metrics={event} response={result['response']!r}"


def _run_continuity_case(make_agent, turns: list[str], expected_markers: tuple[str, ...], label: str) -> tuple[bool, str]:
    agent = _build_live_agent(make_agent)
    session_id = f"openclaw:alpha-live:continuity:{label}:{uuid.uuid4().hex}"
    last_result: dict[str, object] | None = None
    with _common_runtime_patch_stack():
        for turn in turns:
            last_result = agent.run_once(
                turn,
                session_id_override=session_id,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
    assert last_result is not None
    response = str(last_result.get("response") or "").lower()
    ok = any(marker in response for marker in expected_markers)
    return ok, f"{label}: response={last_result['response']!r}"


def _run_hive_case(make_agent, label: str, details: dict[str, object] | None, prompt: str, expected_markers: tuple[str, ...]) -> tuple[bool, str]:
    agent = _build_live_agent(make_agent)
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""
    if details is not None:
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (True, dict(details))
    else:
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (False, None)

    with _common_runtime_patch_stack():
        if label == "public_bridge":
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
            with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"watched_topic_ids": ["topic-1"], "interaction_payload": {"active_topic_id": "topic-1"}}), mock.patch.object(
                agent.public_hive_bridge, "enabled", return_value=True
            ), mock.patch.object(agent.public_hive_bridge, "get_public_research_packet", return_value=packet):
                result = agent.run_once(prompt, source_context=_live_soak_source_context(session_label=f"hive-{label}"))
        elif label == "future_unsupported":
            hive_state = {
                "pending_topic_ids": ["topic-1"],
                "interaction_mode": "hive_task_selection_pending",
                "interaction_payload": {"shown_topic_ids": ["topic-1"], "shown_titles": ["OpenClaw integration audit"]},
            }
            with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
                agent.public_hive_bridge, "enabled", return_value=False
            ):
                result = agent.run_once(prompt, source_context=_live_soak_source_context(session_label=f"hive-{label}"))
        else:
            result = agent.run_once(prompt, source_context=_live_soak_source_context(session_label=f"hive-{label}"))
    lowered = str(result.get("response") or "").lower()
    ok = all(marker in lowered for marker in expected_markers)
    return ok, f"{label}: response={result['response']!r}"


def _builder_step_executor():
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
        if tool_name == "sandbox.run_command":
            command = str(arguments.get("command") or "").strip()
            if command == "python3 app.py":
                command_counter[command] = int(command_counter.get(command, 0)) + 1
                attempt = int(command_counter[command])
                if attempt == 1:
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
                            },
                        },
                    )
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
                            "command": command or "python3 -m compileall -q generated/telegram-bot/src",
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
                        "command": command or "python3 -m compileall -q generated/telegram-bot/src",
                        "cwd": ".",
                        "returncode": 0,
                    },
                },
            )
        raise AssertionError(f"unexpected builder tool: {tool_name}")

    return _execute_builder_step


def _run_builder_case(make_agent, prompt: str, *, label: str) -> tuple[bool, str]:
    agent = _build_live_agent(make_agent)
    agent.context_loader.load = mock.Mock(  # type: ignore[assignment]
        return_value=SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="alpha_live_soak")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
    agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
    with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_builder_step_executor()), _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "workspace": f"/tmp/vool-alpha-live-builder-{label}",
            },
        )
    controller = dict(result["details"]["builder_controller"])
    artifacts = dict(controller.get("artifacts") or {})
    lowered = str(result.get("response") or "").lower()
    ok = (
        result["model_execution"]["used_model"] is True
        and controller.get("step_count")
        and str(controller.get("stop_reason") or "").strip() != ""
        and "artifacts:" in lowered
        and bool(artifacts.get("command_outputs") or [])
        and (
            bool(artifacts.get("file_diffs") or [])
            or bool(artifacts.get("failures") or [])
        )
    )
    if "retry" in prompt.lower():
        ok = ok and bool(artifacts.get("retry_history") or []) and "failures seen" in lowered and "retries" in lowered
    return ok, f"{label}: stop={controller.get('stop_reason')} response={result['response']!r} artifacts={artifacts}"


def _run_degradation_case(make_agent, label: str, prompt: str, decision: ModelExecutionDecision, expected_snippet: str) -> tuple[bool, str]:
    agent = _build_live_agent(make_agent)
    agent.memory_router.resolve = mock.Mock(return_value=decision)  # type: ignore[assignment]
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="alpha_live_soak")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )
    with mock.patch("core.agent_runtime.agent.render_response", side_effect=AssertionError("planner renderer should not speak here")), _common_runtime_patch_stack():
        if label == "live_info_memory_blocked":
            with mock.patch(
                "core.agent_runtime.agent.WebAdapter.planned_search_query",
                return_value=[
                    {
                        "summary": "Telegram Bot API docs remain the canonical source for Bot API updates.",
                        "confidence": 0.67,
                        "source_profile_label": "Official docs",
                        "result_title": "Telegram Bot API",
                        "result_url": "https://core.telegram.org/bots/api",
                        "origin_domain": "core.telegram.org",
                    }
                ],
            ):
                result = agent.run_once(prompt, source_context=_live_soak_source_context(session_label=f"degrade-{label}"))
        else:
            result = agent.run_once(prompt, source_context=_live_soak_source_context(session_label=f"degrade-{label}"))
    lowered = str(result.get("response") or "").lower()
    ok = expected_snippet.lower() in lowered
    return ok, f"{label}: response={result['response']!r}"


def test_alpha_live_soak_model_final_hit_rate(make_agent) -> None:
    _require_live_provider()
    passed = 0
    failures: list[str] = []
    for prompt in LIVE_CHAT_CORPUS:
        ok, detail = _run_live_chat_case(make_agent, prompt)
        passed += 1 if ok else 0
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="live_model_final_hit_rate",
        passed=passed,
        total=len(LIVE_CHAT_CORPUS),
        threshold=ALPHA_LIVE_SOAK_THRESHOLDS["live_model_final_hit_rate"],
        failures=failures,
    )


def test_alpha_live_soak_longer_continuity_drift(make_agent) -> None:
    _require_live_provider()
    passed = 0
    failures: list[str] = []
    for turns, expected_markers, label in CONTINUITY_CASES:
        ok, detail = _run_continuity_case(make_agent, list(turns), expected_markers, label)
        passed += 1 if ok else 0
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="live_continuity_drift",
        passed=passed,
        total=len(CONTINUITY_CASES),
        threshold=ALPHA_LIVE_SOAK_THRESHOLDS["live_continuity_drift"],
        failures=failures,
    )


def test_alpha_live_soak_live_info_under_noisy_evidence(make_agent) -> None:
    _require_live_provider()
    passed = 0
    failures: list[str] = []
    for prompt, live_mode, search_method, notes in LIVE_INFO_CASES:
        ok, detail = _run_live_info_case(make_agent, prompt, live_mode, search_method, notes)
        passed += 1 if ok else 0
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="live_info_noisy_evidence",
        passed=passed,
        total=len(LIVE_INFO_CASES),
        threshold=ALPHA_LIVE_SOAK_THRESHOLDS["live_info_noisy_evidence"],
        failures=failures,
    )


def test_alpha_live_soak_hive_truth_edge_cases(make_agent) -> None:
    _require_live_provider()
    passed = 0
    failures: list[str] = []
    for label, details, prompt, expected_markers in HIVE_EDGE_CASES:
        ok, detail = _run_hive_case(make_agent, label, details, prompt, expected_markers)
        passed += 1 if ok else 0
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="hive_truth_edge_cases",
        passed=passed,
        total=len(HIVE_EDGE_CASES),
        threshold=ALPHA_LIVE_SOAK_THRESHOLDS["hive_truth_edge_cases"],
        failures=failures,
    )


def test_alpha_live_soak_builder_repeated_truth(make_agent) -> None:
    _require_live_provider()
    cases = (
        ("build a telegram bot in this workspace and write the files", "builder-scaffold-a"),
        ("build a telegram bot in this workspace and write the files", "builder-scaffold-b"),
        ("run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry", "builder-retry-a"),
    )
    passed = 0
    failures: list[str] = []
    for prompt, label in cases:
        ok, detail = _run_builder_case(make_agent, prompt, label=label)
        passed += 1 if ok else 0
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="builder_repeated_truth",
        passed=passed,
        total=len(cases),
        threshold=ALPHA_LIVE_SOAK_THRESHOLDS["builder_repeated_truth"],
        failures=failures,
    )


def test_alpha_live_soak_honest_degradation(make_agent) -> None:
    _require_live_provider()
    passed = 0
    failures: list[str] = []
    for label, prompt, decision, expected_snippet in HONEST_DEGRADATION_CASES:
        ok, detail = _run_degradation_case(make_agent, label, prompt, decision, expected_snippet)
        passed += 1 if ok else 0
        if not ok:
            failures.append(detail)
    _assert_threshold(
        name="honest_degradation",
        passed=passed,
        total=len(HONEST_DEGRADATION_CASES),
        threshold=ALPHA_LIVE_SOAK_THRESHOLDS["honest_degradation"],
        failures=failures,
    )
