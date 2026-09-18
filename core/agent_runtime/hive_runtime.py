from __future__ import annotations

import re
from typing import Any

_HIVE_MARKERS = ("hive", "hive mind", "brain hive", "public hive")
_HIVE_TASK_MARKERS = ("task", "tasks", "taks", "work")
_HIVE_INQUIRY_MARKERS = (
    "check",
    "see",
    "show",
    "what",
    "what's",
    "whats",
    "any",
    "open",
    "up",
    "available",
    "can we do",
)


def _contains_phrase_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    return any(re.search(rf"\b{re.escape(marker)}\b", lowered) for marker in markers)


def maybe_handle_hive_runtime_command(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
) -> tuple[bool, str, bool, dict[str, Any] | None]:
    handled, details = agent.hive_activity_tracker.maybe_handle_command_details(user_input, session_id=session_id)
    response = str((details or {}).get("response_text") or "")
    command_kind = str((details or {}).get("command_kind") or "").strip().lower()
    if not handled:
        return False, "", False, None
    deterministic_command_kinds = {
        "prompt_control",
        "watcher_unavailable",
    }
    allow_model_wording = (
        not agent._looks_like_hive_prompt_control_command(user_input)
        and command_kind not in deterministic_command_kinds
    )
    if not agent._hive_tracker_needs_bridge_fallback(response):
        return True, response, allow_model_wording, details
    bridge_details = agent._maybe_handle_hive_bridge_fallback(
        user_input,
        session_id=session_id,
        tracker_response=response,
    )
    if bridge_details is not None:
        return True, str(bridge_details.get("response_text") or ""), True, bridge_details
    return True, response, allow_model_wording, details


def recover_hive_runtime_command_input(
    agent: Any,
    user_input: str,
    *,
    looks_like_semantic_hive_request_fn: Any,
) -> str:
    lowered = " ".join(str(user_input or "").strip().lower().split())
    if not lowered:
        return ""
    if agent._looks_like_hive_topic_drafting_request(lowered):
        return ""
    if agent._looks_like_hive_topic_update_request(lowered) or agent._looks_like_hive_topic_delete_request(lowered):
        return ""
    if looks_like_semantic_hive_request_fn(lowered):
        return "show me the open hive tasks"
    if not _contains_phrase_marker(lowered, _HIVE_MARKERS):
        return ""
    if any(
        marker in lowered
        for marker in (
            "ignore hive",
            "ignore it for now",
            "new task",
            "new topic",
            "create task",
            "create topic",
            "status",
            "complete",
            "completed",
            "finished",
            "done",
        )
    ):
        return ""
    compact = lowered.strip(" \t\r\n?!.,")
    if re.fullmatch(
        r"(?:hi\s+)?(?:check|show|see)\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)(?:\s+(?:pls|please))?",
        compact,
    ):
        return "show me the open hive tasks"
    has_task_marker = _contains_phrase_marker(lowered, _HIVE_TASK_MARKERS)
    has_inquiry_marker = _contains_phrase_marker(lowered, _HIVE_INQUIRY_MARKERS)
    if not (has_task_marker and has_inquiry_marker):
        return ""
    return "show me the open hive tasks"


def hive_tracker_needs_bridge_fallback(response: str) -> bool:
    lowered = str(response or "").strip().lower()
    return lowered.startswith("hive watcher is not configured") or lowered.startswith("i couldn't reach the hive watcher")


def looks_like_hive_prompt_control_command(user_input: str) -> bool:
    lowered = " ".join(str(user_input or "").strip().lower().split())
    if not lowered:
        return False
    if "ignore hive" in lowered or "ignore it for now" in lowered:
        return True
    return "ignore" in lowered and "remind" in lowered


def maybe_handle_hive_bridge_fallback(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    tracker_response: str,
) -> dict[str, Any] | None:
    if not agent.public_hive_bridge.enabled():
        return None
    topics = agent.public_hive_bridge.list_public_topics(
        limit=12,
        statuses=("open", "researching", "disputed"),
    )
    if not topics:
        return None
    agent._store_hive_topic_selection_state(session_id, topics)
    lowered = " ".join(str(user_input or "").strip().lower().split())
    if "online" in lowered and "task" not in lowered and "tasks" not in lowered and "work" not in lowered:
        lead = "I couldn't read live agent presence from the watcher, but I can still pull public Hive tasks (public-bridge-derived; live presence unavailable):"
    elif "not configured" in str(tracker_response or "").lower():
        lead = "Live Hive watcher is not configured here, but I can still pull public Hive tasks (public-bridge-derived; live presence unavailable):"
    else:
        lead = "I couldn't reach the live Hive watcher, but I can still pull public Hive tasks (public-bridge-derived; live presence unavailable):"
    return {
        "command_kind": "task_list_bridge_fallback",
        "watcher_status": "bridge_fallback",
        "lead": lead,
        "response_text": agent.hive_activity_tracker._render_hive_task_list_with_lead(topics, lead=lead),
        "topics": topics,
        "online_agents": [],
        "truth_source": "public_bridge",
        "truth_label": "public-bridge-derived",
        "truth_status": str((topics[0] or {}).get("truth_transport") or "bridge_fallback"),
        "truth_timestamp": str((topics[0] or {}).get("truth_timestamp") or ""),
        "presence_claim_state": "unavailable",
        "presence_source": "watcher",
        "presence_truth_label": "public-bridge-derived",
        "presence_freshness_label": "unavailable",
        "presence_note": "live watcher presence unavailable in public-bridge fallback",
    }


def store_hive_topic_selection_state(
    session_id: str,
    topics: list[dict[str, Any]],
    *,
    session_hive_state_fn: Any,
    update_session_hive_state_fn: Any,
) -> None:
    state = session_hive_state_fn(session_id)
    topic_ids = [
        str(topic.get("topic_id") or "").strip()
        for topic in list(topics or [])
        if str(topic.get("topic_id") or "").strip()
    ]
    titles = [
        str(topic.get("title") or "").strip()
        for topic in list(topics or [])
        if str(topic.get("title") or "").strip()
    ]
    update_session_hive_state_fn(
        session_id,
        watched_topic_ids=list(state.get("watched_topic_ids") or []),
        seen_post_ids=list(state.get("seen_post_ids") or []),
        pending_topic_ids=topic_ids,
        seen_curiosity_topic_ids=list(state.get("seen_curiosity_topic_ids") or []),
        seen_curiosity_run_ids=list(state.get("seen_curiosity_run_ids") or []),
        seen_agent_ids=state.get("seen_agent_ids") or [],
        last_active_agents=int(state.get("last_active_agents") or 0),
        interaction_mode="hive_task_selection_pending",
        interaction_payload={
            "shown_topic_ids": topic_ids,
            "shown_titles": titles,
        },
    )
