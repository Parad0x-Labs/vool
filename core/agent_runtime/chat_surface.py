from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from core import audit_logger
from core.curiosity_roamer import AdaptiveResearchResult
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.memory_first_router import execution_visibility
from core.persistent_memory import append_conversation_event
from core.research_evidence import truthful_evidence_strength
from core.token_usage_receipt import append_token_usage_receipt

_log = logging.getLogger(__name__)


def smalltalk_model_input(agent: Any, *, user_input: str, phrase: str) -> str:
    normalized_phrase = str(phrase or "").strip().lower()
    if normalized_phrase in {"what can you do", "help"}:
        capability_summary = agent._help_capabilities_text().strip()
        return (
            f"{user_input}\n\n"
            "Ground your reply in currently wired runtime capabilities only. "
            "Do not imply unsupported abilities. "
            "Keep it crisp and operator-facing. "
            "Do not drift into generic customer-support phrasing.\n\n"
            f"{capability_summary}"
        )
    return (
        f"{str(user_input or '').strip()}\n\n"
        "Reply like a sharp local companion. Keep it brief, natural, and grounded in this runtime. "
        "Do not use canned system-status slogans, generic assistant filler, or capability speeches."
    ).strip()


def evaluative_model_input(*, user_input: str) -> str:
    return (
        f"{str(user_input or '').strip()}\n\n"
        "Reply like a sharp local operator. Acknowledge friction briefly without arguing, posturing, or dumping canned status slogans. "
        "Keep it short, natural, and direct."
    ).strip()


def observation_prompt(
    *,
    user_input: str,
    observations: dict[str, Any],
) -> str:
    channel = str(observations.get("channel") or "").strip()
    mode = str(observations.get("mode") or "").strip()
    if channel == "live_info" and mode == "fresh_lookup":
        grounding = (
            "IMPORTANT: Answer ONLY using the search results below. "
            "If the search results do not contain the answer, say so honestly - "
            "do NOT guess or fill in from general knowledge. "
            "Cite the source domain when possible."
        )
    else:
        grounding = "Grounding observations for this turn. Use them as evidence, not as a template:"
    return (
        f"{str(user_input or '').strip()}\n\n"
        f"{grounding}\n"
        f"{json.dumps(dict(observations or {}), indent=2, sort_keys=True)}"
    ).strip()


def live_info_observations(
    *,
    query: str,
    mode: str,
    notes: list[dict[str, Any]] | None = None,
    runtime_note: str = "",
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    browser_used = False
    for note in list(notes or [])[:4]:
        entry = {
            "title": str(note.get("result_title") or note.get("origin_domain") or "Source").strip(),
            "domain": str(note.get("origin_domain") or "").strip(),
            "summary": " ".join(str(note.get("summary") or "").split()).strip(),
            "url": str(note.get("result_url") or "").strip(),
        }
        if str(note.get("source_profile_label") or "").strip():
            entry["source_profile"] = str(note.get("source_profile_label") or "").strip()
        if isinstance(note.get("live_quote"), dict):
            entry["quote"] = dict(note.get("live_quote") or {})
        if bool(note.get("used_browser")):
            entry["used_browser"] = True
            browser_used = True
        sources.append(entry)
    observations: dict[str, Any] = {
        "channel": "live_info",
        "mode": str(mode or "").strip(),
        "query": str(query or "").strip(),
        "source_count": len(sources),
        "sources": sources,
    }
    if browser_used:
        observations["browser_rendering_used"] = True
    if str(runtime_note or "").strip():
        observations["runtime_note"] = str(runtime_note or "").strip()
    return observations


def live_info_model_input(
    agent: Any,
    *,
    user_input: str,
    query: str,
    mode: str,
    notes: list[dict[str, Any]] | None = None,
    runtime_note: str = "",
) -> str:
    runtime_message = str(runtime_note or "").strip() or (
        "" if notes else agent._live_info_failure_text(query=query, mode=mode)
    )
    return observation_prompt(
        user_input=user_input,
        observations=live_info_observations(
            query=query,
            mode=mode,
            notes=notes,
            runtime_note=runtime_message,
        ),
    )


#: Research notes reach the model by budget, not by a fixed count. A five-facet comparison that
#: retrieved seven short sources used to hand the model four of them (`notes[:4]`), so one facet could
#: never be answered from evidence whatever the retrieval did. The bound is characters of summary text
#: plus a hard count; order is preserved (initial search first, then the request's facets in order).
_CONTEXT_NOTE_MAX = 12
_CONTEXT_CHAR_BUDGET = 8000


def _notes_within_context_budget(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    used = 0
    for note in notes:
        if len(chosen) >= _CONTEXT_NOTE_MAX:
            break
        summary = " ".join(str(note.get("summary") or note.get("snippet") or "").split())
        cost = max(1, len(summary))
        if chosen and used + cost > _CONTEXT_CHAR_BUDGET:
            break
        chosen.append(note)
        used += cost
    return chosen


def adaptive_research_observations(
    *,
    task_class: str,
    research_result: AdaptiveResearchResult,
) -> dict[str, Any]:
    notes = [dict(note) for note in list(research_result.notes or []) if isinstance(note, dict)]
    sources: list[dict[str, Any]] = []
    for note in _notes_within_context_budget(notes):
        source = {
            "title": str(note.get("result_title") or note.get("title") or note.get("result_url") or "Source").strip(),
            "domain": str(note.get("origin_domain") or "").strip(),
            "summary": " ".join(str(note.get("summary") or note.get("snippet") or "").split()).strip(),
            "url": str(note.get("result_url") or note.get("url") or "").strip(),
        }
        if note.get("source_profile_label"):
            source["source_profile"] = str(note.get("source_profile_label") or "").strip()
        raw_confidence = note.get("confidence")
        if raw_confidence not in {None, ""}:
            with contextlib.suppress(Exception):
                source["confidence"] = float(raw_confidence)
        sources.append(source)
    observations: dict[str, Any] = {
        "channel": "adaptive_research",
        "task_class": str(task_class or "unknown").strip(),
        "strategy": str(research_result.strategy or "general_research").strip(),
        "actions_taken": list(research_result.actions_taken or []),
        "queries_run": list(research_result.queries_run or []),
        "evidence_strength": truthful_evidence_strength(research_result),
        "source_domains": list(research_result.source_domains or []),
        "source_count": len(sources),
        "sources": sources,
    }
    if research_result.escalated_from_chat:
        observations["escalated_from_chat"] = True
    if research_result.broadened:
        observations["broadened"] = True
    if research_result.narrowed:
        observations["narrowed"] = True
    if research_result.compared_sources:
        observations["compared_sources"] = True
    if research_result.verified_claim:
        observations["verified_claim"] = True
    if research_result.stop_reason:
        observations["stop_reason"] = str(research_result.stop_reason).strip()
    if research_result.admitted_uncertainty:
        observations["admitted_uncertainty"] = True
        observations["uncertainty_reason"] = str(
            research_result.uncertainty_reason or research_result.tool_gap_note or ""
        ).strip()
    elif research_result.tool_gap_note:
        observations["runtime_note"] = str(research_result.tool_gap_note).strip()
    return observations


def adaptive_research_model_input(
    agent: Any,
    *,
    user_input: str,
    task_class: str,
    research_result: AdaptiveResearchResult,
) -> str:
    return observation_prompt(
        user_input=user_input,
        observations=adaptive_research_observations(
            task_class=task_class,
            research_result=research_result,
        ),
    )


def credit_status_model_input(
    *,
    user_input: str,
    credit_snapshot: str,
) -> str:
    return (
        f"{str(user_input or '').strip()}\n\n"
        "Verified local credit, score, and wallet state for this turn:\n"
        f"{str(credit_snapshot or '').strip()}"
    ).strip()


def hive_model_input(
    agent: Any,
    *,
    user_input: str,
    observations: dict[str, Any] | None = None,
    runtime_note: str = "",
) -> str:
    payload = dict(observations or {})
    if str(runtime_note or "").strip():
        payload["runtime_note"] = str(runtime_note or "").strip()
    if not payload:
        payload = {"channel": "hive", "runtime_note": "Hive evidence was unavailable for this turn."}
    payload["_system_context"] = (
        "IMPORTANT: When the user says 'hive mind', 'hive', 'brain hive', or 'public hive', "
        "they mean the Brain Hive task queue - a decentralized research system where tasks are "
        "listed, claimed, researched, and resolved. Do NOT interpret 'hive mind' as the concept "
        "of collective intelligence. Report the actual task state from the observations below. "
        "The user can: check tasks, pick one to research, create new tasks, deliver research results."
    )
    return observation_prompt(
        user_input=user_input,
        observations=payload,
    )


def hive_queue_observations(
    agent: Any,
    queue_rows: list[dict[str, Any]],
    *,
    lead: str = "",
    truth_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observations = {
        "channel": "hive",
        "kind": "task_list",
        "lead": str(lead or "").strip(),
        "task_count": len(list(queue_rows or [])),
        "topics": [
            {
                "topic_id": str(row.get("topic_id") or "").strip(),
                "title": str(row.get("title") or "Untitled topic").strip(),
                "status": str(row.get("status") or "open").strip(),
            }
            for row in list(queue_rows or [])[:5]
        ],
    }
    observations.update(hive_truth_observation_fields(truth_payload or bridge_hive_truth_from_rows(queue_rows)))
    return observations


def hive_research_result_observations(
    agent: Any,
    *,
    topic_id: str,
    title: str,
    result: Any,
) -> dict[str, Any]:
    observations: dict[str, Any] = {
        "channel": "hive",
        "kind": "research_followup",
        "topic": {
            "topic_id": str(topic_id or "").strip(),
            "short_id": str(topic_id or "")[:8],
            "title": str(title or "Hive topic").strip(),
        },
        "dispatch_status": str(result.status or "").strip(),
    }
    if result.claim_id:
        observations["claim_id"] = str(result.claim_id).strip()
    result_status = str(result.result_status or "").strip()
    if result_status:
        observations["topic_status_after_dispatch"] = result_status
    query_count = len(list((result.details or {}).get("query_results") or []))
    if query_count:
        observations["bounded_query_count"] = query_count
    if result.artifact_ids:
        observations["artifact_count"] = len(result.artifact_ids)
    if result.candidate_ids:
        observations["candidate_note_count"] = len(result.candidate_ids)
    response_text = " ".join(str(result.response_text or "").split()).strip()
    if response_text:
        observations["research_runtime_note"] = response_text
    details = dict(result.details or {})
    synthesis_card = details.get("synthesis_card")
    if isinstance(synthesis_card, dict):
        observations["research_synthesis"] = {
            "question": str(synthesis_card.get("question") or "").strip()[:200],
            "searched": list(synthesis_card.get("searched") or [])[:5],
            "found": list(synthesis_card.get("found") or [])[:5],
            "promoted_findings": list(synthesis_card.get("promoted_findings") or [])[:5],
            "confidence": str(synthesis_card.get("confidence") or "").strip(),
            "blockers": list(synthesis_card.get("blockers") or [])[:6],
        }
    query_results = list(details.get("query_results") or [])
    if query_results:
        observations["query_summaries"] = [
            {
                "query": str(q.get("query") or "").strip()[:120],
                "summary": str(q.get("summary") or q.get("snippet") or "").strip()[:400],
            }
            for q in query_results[:6]
            if str(q.get("summary") or q.get("snippet") or "").strip()
        ]
    quality_summary = details.get("quality_summary")
    if isinstance(quality_summary, dict):
        observations["research_quality"] = {
            "status": str(quality_summary.get("status") or "").strip(),
            "evidence_count": int(quality_summary.get("evidence_count") or 0),
            "confidence": str(quality_summary.get("confidence") or "").strip(),
        }
    observations.update(
        hive_truth_observation_fields(
            {
                "truth_source": "public_bridge",
                "truth_label": "public-bridge-derived",
                "truth_status": "write_path",
            }
        )
    )
    return observations


def hive_status_observations(
    agent: Any,
    *,
    topic_id: str,
    title: str,
    status: str,
    execution_state: str,
    active_claim_count: int,
    artifact_count: int,
    post_count: int,
    latest_post_kind: str,
    latest_post_body: str,
    truth_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observations: dict[str, Any] = {
        "channel": "hive",
        "kind": "status",
        "topic": {
            "topic_id": str(topic_id or "").strip(),
            "short_id": str(topic_id or "")[:8],
            "title": str(title or "Hive topic").strip(),
        },
    }
    if status:
        observations["topic_status"] = status
    if execution_state:
        observations["execution_state"] = execution_state
    if active_claim_count:
        observations["active_claim_count"] = active_claim_count
    if post_count:
        observations["post_count"] = post_count
    if artifact_count:
        observations["artifact_count"] = artifact_count
    if latest_post_kind or latest_post_body:
        latest = latest_post_body[:220] if latest_post_body else ""
        observations["latest_post"] = {
            "kind": latest_post_kind or "post",
            "body": latest,
        }
    observations.update(hive_truth_observation_fields(truth_payload))
    return observations


def hive_command_observations(agent: Any, details: dict[str, Any]) -> dict[str, Any]:
    observations = {
        "channel": "hive",
        "kind": str(details.get("command_kind") or "command").strip(),
        "watcher_status": str(details.get("watcher_status") or "").strip(),
        "lead": str(details.get("lead") or "").strip(),
        "topics": [
            {
                "topic_id": str(topic.get("topic_id") or "").strip(),
                "title": str(topic.get("title") or "Untitled topic").strip(),
                "status": str(topic.get("status") or "open").strip(),
            }
            for topic in list(details.get("topics") or [])[:5]
        ],
        "online_agents": [
            {
                "agent_id": str(agent_item.get("agent_id") or "").strip(),
                "display_name": str(
                    agent_item.get("display_name") or agent_item.get("claim_label") or "agent"
                ).strip(),
                "status": str(agent_item.get("status") or "").strip(),
                "online": bool(agent_item.get("online")),
            }
            for agent_item in list(details.get("online_agents") or [])[:4]
        ],
    }
    observations.update(hive_truth_observation_fields(details))
    return observations


def bridge_hive_truth_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = dict((list(rows or [])[:1] or [{}])[0] or {})
    return {
        "truth_source": str(first.get("truth_source") or "public_bridge").strip(),
        "truth_label": str(first.get("truth_label") or "public-bridge-derived").strip(),
        "truth_status": str(first.get("truth_transport") or "read_path").strip(),
    }


def hive_truth_observation_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    observations: dict[str, Any] = {}
    for key in ("truth_source", "truth_label", "truth_status", "truth_timestamp"):
        value = raw.get(key)
        if value not in {None, ""}:
            observations[key] = value
    presence: dict[str, Any] = {}
    for source_key, target_key in (
        ("presence_claim_state", "claim_state"),
        ("presence_source", "source"),
        ("presence_truth_label", "truth_label"),
        ("presence_freshness_label", "freshness_label"),
        ("presence_age_seconds", "age_seconds"),
        ("presence_note", "note"),
    ):
        value = raw.get(source_key)
        if value not in {None, ""}:
            presence[target_key] = value
    if presence:
        observations["presence"] = presence
    return observations


def hive_truth_prefix(agent: Any, payload: dict[str, Any] | None) -> str:
    raw = dict(payload or {})
    presence = dict(raw.get("presence") or {})
    truth_label = str(raw.get("truth_label") or "").strip()
    if not truth_label:
        return ""
    parts = [f"Hive truth: {truth_label}."]
    presence_claim_state = str(raw.get("presence_claim_state") or presence.get("claim_state") or "").strip().lower()
    presence_note = str(raw.get("presence_note") or presence.get("note") or "").strip()
    presence_truth_label = str(raw.get("presence_truth_label") or presence.get("truth_label") or truth_label).strip()
    freshness_label = str(raw.get("presence_freshness_label") or presence.get("freshness_label") or "").strip().lower()
    age_seconds = raw.get("presence_age_seconds")
    if age_seconds in {None, ""}:
        age_seconds = presence.get("age_seconds")
    if presence_claim_state == "visible":
        freshness_suffix = freshness_label
        if freshness_label in {"fresh", "stale"} and age_seconds is not None:
            freshness_suffix = f"{freshness_label} ({human_age(age_seconds)} old)"
        elif freshness_label == "unknown":
            freshness_suffix = "freshness unknown"
        parts.append(f"Presence truth: {presence_truth_label}, {freshness_suffix}.")
    elif presence_note:
        parts.append(f"Presence truth: {presence_note}.")
    return " ".join(part for part in parts if part).strip()


def qualify_hive_response_text(
    agent: Any,
    response_text: str,
    *,
    payload: dict[str, Any] | None,
) -> str:
    clean = str(response_text or "").strip()
    prefix = hive_truth_prefix(agent, payload)
    if not prefix:
        return clean
    lowered = clean.lower()
    if "hive truth:" in lowered and ("presence truth:" in lowered or "presence" not in prefix.lower()):
        return clean
    if not clean:
        return prefix
    return f"{prefix} {clean}".strip()


def human_age(age_seconds: object) -> str:
    try:
        value = max(0, int(age_seconds))  # type: ignore[arg-type]
    except Exception:
        return ""
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{max(1, round(value / 60))}m"
    return f"{max(1, round(value / 3600))}h"


def chat_surface_hive_degraded_response(agent: Any, details: dict[str, Any]) -> str:
    topics = list(details.get("topics") or [])
    online_agents = list(details.get("online_agents") or [])
    watcher_status = str(details.get("watcher_status") or "").strip().lower()
    truth_prefix = hive_truth_prefix(agent, details)
    if topics:
        lines = [f"{truth_prefix} Hive tasks:"]
        for topic in topics[:6]:
            title = str(topic.get("title") or "Untitled topic").strip()
            short_id = str(topic.get("topic_id") or "")[:8]
            status = str(topic.get("status") or "open").strip()
            lines.append(f"- [{status}] {title} (#{short_id})")
        agent_count = len(online_agents)
        if agent_count:
            lines.append(f"{agent_count} agent(s) online.")
        lines.append("Pick one by name or #id to start research, or say 'create task' to add a new one.")
        return "\n".join(lines).strip()
    if online_agents:
        agent_count = len(online_agents)
        return f"{truth_prefix} {agent_count} agent(s) online on Hive, but no open tasks found.".strip()
    if watcher_status == "not_configured":
        return f"{truth_prefix} Hive watcher is not configured on this runtime.".strip()
    if watcher_status == "unreachable":
        return f"{truth_prefix} Hive watcher was unreachable this turn.".strip()
    return f"{truth_prefix} No live Hive data available this turn.".strip()


def chat_surface_hive_wording_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    response_class: Any,
    reason: str,
    observations: dict[str, Any] | None = None,
    fallback_response: str,
) -> dict[str, Any]:
    truth_payload = dict(observations or {})
    qualified_fallback = qualify_hive_response_text(agent, fallback_response, payload=truth_payload)
    return chat_surface_model_wording_result(
        agent,
        session_id=session_id,
        user_input=user_input,
        source_context=source_context,
        persona=load_active_persona(agent.persona_id),
        interpretation=adapt_user_input(user_input, session_id=session_id),
        task_class="research",
        response_class=response_class,
        reason=reason,
        model_input=hive_model_input(
            agent,
            user_input=user_input,
            observations=observations,
            runtime_note=qualified_fallback,
        ),
        fallback_response=qualified_fallback,
        tool_backing_sources=["hive"],
        response_postprocessor=lambda text: postprocess_hive_chat_surface_text(
            agent,
            text,
            response_class=response_class,
            payload=truth_payload,
            fallback_response=qualified_fallback,
        ),
    )


def postprocess_hive_chat_surface_text(
    agent: Any,
    text: str,
    *,
    response_class: Any,
    payload: dict[str, Any],
    fallback_response: str,
) -> str:
    clean = str(text or "").strip()
    qualified = qualify_hive_response_text(agent, clean, payload=payload)
    lowered = qualified.lower()
    response_value = getattr(response_class, "value", response_class)
    if response_value == agent.ResponseClass.TASK_STARTED.value:
        if agent._contains_generic_planner_scaffold(qualified):
            return str(fallback_response or "").strip()
        if not any(
            marker in lowered
            for marker in (
                "started hive research on",
                "started research on",
                "first bounded pass",
                "claim",
                "posted",
                "research lane is active",
            )
        ):
            return str(fallback_response or "").strip()
        return qualified
    hive_concept_hallucination_markers = (
        "concept of a",
        "concept of collective",
        "collective intelligence",
        "no specific information",
        "no information related to",
        "hive mind is a term",
        "hive mind refers to",
        "swarm intelligence",
    )
    if any(marker in lowered for marker in hive_concept_hallucination_markers):
        return str(fallback_response or "").strip()
    if response_value not in {
        agent.ResponseClass.TASK_LIST.value,
        agent.ResponseClass.TASK_SELECTION_CLARIFICATION.value,
    }:
        return qualified
    topics = [
        dict(item)
        for item in list(payload.get("topics") or [])
        if isinstance(item, dict) and str(item.get("title") or item.get("topic_id") or "").strip()
    ]
    if not topics:
        return qualified
    if hive_task_list_mentions_real_topics(agent, qualified, topics=topics):
        return qualified
    return str(fallback_response or "").strip()


def hive_task_list_mentions_real_topics(agent: Any, text: str, *, topics: list[dict[str, Any]]) -> bool:
    normalized_text = agent._normalize_hive_topic_text(text)
    compact_text = re.sub(r"\s+", "", str(text or "").lower())
    match_count = 0
    for topic in list(topics or []):
        title = agent._normalize_hive_topic_text(str(topic.get("title") or ""))
        short_id = str(topic.get("topic_id") or "").strip().lower()[:8]
        if title and title in normalized_text:
            match_count += 1
            continue
        if short_id and (f"#{short_id}" in compact_text or short_id in compact_text):
            match_count += 1
    required = 1 if len(topics) <= 1 else 2
    return match_count >= required


def builder_model_input(
    agent: Any,
    *,
    user_input: str,
    observations: dict[str, Any],
) -> str:
    return observation_prompt(
        user_input=user_input,
        observations=observations,
    )


def _model_wording_failure_note(model_execution: Any) -> str:
    """The operator-facing sentence for WHY the model-worded summary is missing.

    The cause is the router decision's own typed reason (``details.block_reason`` /
    ``reason``), never re-derived here. Empty when the decision carries no failure cause:
    a note is only ever appended when the authority that failed actually said why.
    """

    details = dict(getattr(model_execution, "details", None) or {})
    cause = str(
        details.get("block_reason") or details.get("rejection_reason") or details.get("reason") or ""
    ).strip()
    if not cause:
        return ""
    model_name = str(
        details.get("requested_model") or getattr(model_execution, "model_name", "") or ""
    ).strip()
    named = f" `{model_name}`" if model_name else " the selected model"
    return (
        f"\n\nThe model-worded summary could not be generated:{named} could not complete "
        f"({cause}). The record above is the grounded summary of what actually ran."
    )


def chat_surface_model_wording_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    persona: Any,
    interpretation: Any,
    task_class: str,
    response_class: Any,
    reason: str,
    model_input: str,
    fallback_response: str,
    allow_provider_inference: bool = True,
    tool_backing_sources: list[str] | None = None,
    response_postprocessor: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    task = agent._resolve_runtime_task(
        effective_input=user_input,
        session_id=session_id,
        source_context=source_context,
    )
    agent._update_runtime_checkpoint_context(
        source_context,
        task_id=task.task_id,
        task_class=task_class,
    )
    agent._update_task_class(task.task_id, task_class)
    # `user_input`, NOT `model_input`. `model_input` is a PROMPT -- `observation_prompt` composes it
    # as the user's text plus "Grounding observations for this turn..." plus a JSON dump of what the
    # runtime observed -- and `adapt_user_input` is not a pure function of its argument. It persists:
    # it records a dialogue turn with this string as `raw_input`, writes `current_user_goal` from it,
    # and captures active-mission slots from it.
    #
    # Measured on this branch, three real turns through /api/chat:
    #     U: which local model should i run, qwen3 8b or llama 3.1 8b
    #     A: <an offer of three options>
    #     U: yes ok my bad, do compare those models
    #   current_user_goal now reads
    #     'yes ok my bad, do compare those models Grounding observations for this turn. Use them as
    #      evidence, not as a template:{"actions_taken":["initial_search", "stop_answer"],
    #      "channel": "adaptive_research", ...'
    # and `bootstrap_context` injects that back as "Current user goal: ..." on the turns after it --
    # the runtime's own scaffolding replayed to the model as a statement of what the user wants.
    #
    # Routing, context selection and the memory router legitimately want an interpretation that
    # reflects the evidence, so the text handed in is unchanged and only the PERSISTENCE is withheld
    # -- see `adapt_user_input`'s own docstring for why the split is there and not at the text.
    model_interpretation = adapt_user_input(
        model_input,
        session_id=session_id,
        persist=False,
        # The composed prompt is fine for routing/context (it reflects the evidence), but the
        # user's demand must stay recoverable: heavy-model and other demand predicates read
        # `user_demand_text`, never the observations JSON spliced into `model_input`.
        user_demand_text=user_input,
    )
    base_classification = {
        "task_class": task_class,
        "risk_flags": [],
        "confidence_hint": max(
            0.55,
            float(getattr(model_interpretation, "understanding_confidence", 0.0) or 0.0),
        ),
    }
    classification, _ = agent._model_routing_profile(
        user_input=user_input,
        classification=base_classification,
        interpretation=model_interpretation,
        source_context=source_context,
    )
    # The answering model is resolved only AFTER this context exists (memory_router.resolve
    # below), but the loader needs that model's window NOW to size its layer budgets. The router
    # pre-resolves the ranking it will run later and stamps the planned window into the loader's
    # copy of the context; an unknown window stamps nothing and the declared budgets hold.
    context_result = agent.context_loader.load(
        task=task,
        classification=classification,
        interpretation=model_interpretation,
        persona=persona,
        session_id=session_id,
        source_context=agent.memory_router.stamp_planned_context_window(
            source_context,
            classification=classification,
        ),
    )
    model_source_context = dict(source_context or {})
    model_execution = agent.memory_router.resolve(
        task=task,
        classification=classification,
        interpretation=model_interpretation,
        context_result=context_result,
        persona=persona,
        force_model=True,
        allow_provider_inference=allow_provider_inference,
        surface=str((source_context or {}).get("surface", "cli") or "cli"),
        source_context=model_source_context,
    )
    # The router intentionally works on a private request dictionary.  Copy only
    # redacted turn receipts back to the parent so the final API trace can join
    # selection, provider payload, and output-control records.
    if isinstance(source_context, dict):
        for key in (
            "context_manifest_id",
            "context_manifest_trace_id",
            "ordinary_chat_output_policy",
            "provider_manifest_links",
            "response_control",
        ):
            if key in model_source_context:
                source_context[key] = model_source_context[key]
    final_text = agent._chat_surface_model_final_text(model_execution)
    # Internal scaffolding is never an answer to a person. Measured 2026-08-01: a build lane's
    # file-list prompt came back as `["security.py", ...]import hmac\n...`, path extraction failed,
    # the turn fell out of the build lane, and this line handed the raw completion straight to the
    # operator. The parser that should have consumed it is fixed; this is the backstop for whatever
    # goes wrong next. Treated as no answer at all, so the existing wording fallback below runs.
    from core.tool_call_dialects import looks_like_internal_payload

    if final_text and looks_like_internal_payload(final_text):
        _log.info("Chat surface refused internal payload as an answer (%d chars).", len(final_text))
        final_text = ""
    model_final_answer_hit = bool(final_text)
    if not final_text:
        model_source = str(getattr(model_execution, "source", "") or "")
        used_model = bool(getattr(model_execution, "used_model", False))
        _log.info(
            "Chat surface model wording fallback: reason=%s model_source=%s used_model=%s",
            reason,
            model_source or "unknown",
            used_model,
        )
        audit_logger.log(
            "chat_surface_model_wording_fallback",
            target_id=task.task_id,
            target_type="task",
            details={
                "reason": reason,
                "model_source": model_source,
                "used_model": used_model,
                "fallback_preview": str(fallback_response or "")[:120],
            },
        )
        final_text = str(fallback_response or "").strip()
        # The operator must not have to guess why the answer is a deterministic summary
        # instead of the model-worded one. The decision's own typed cause rides along;
        # without a typed cause the fallback text stands as it always did.
        final_text += _model_wording_failure_note(model_execution)
    if response_postprocessor is not None:
        final_text = str(response_postprocessor(final_text) or "").strip()

    turn_result = agent._turn_result(
        final_text,
        response_class,
        debug_origin=reason,
    )
    agent._apply_interaction_transition(session_id, turn_result)
    decorated_response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    # Same seam as the grounded turn: a token-cost question is answered from what the provider
    # measured and what the assembler itemised, never from what the model believes about itself.
    decorated_response = append_token_usage_receipt(
        decorated_response,
        user_input=user_input,
        model_execution=model_execution,
        report=context_result.report,
    )
    append_conversation_event(
        session_id=session_id,
        user_input=user_input,
        assistant_output=decorated_response,
        source_context=source_context,
        response_class=turn_result.response_class.value,
    )
    confidence = max(
        0.35,
        min(
            0.96,
            float(getattr(model_execution, "trust_score", 0.0) or getattr(model_execution, "confidence", 0.0) or 0.68),
        ),
    )
    agent._update_task_result(
        task.task_id,
        outcome="success" if model_final_answer_hit else "degraded",
        confidence=confidence,
    )
    agent._emit_chat_truth_metrics(
        task_id=task.task_id,
        reason=reason,
        response_text=decorated_response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via="model_final_wording",
        fast_path_hit=False,
        model_inference_used=bool(getattr(model_execution, "used_model", False)),
        model_final_answer_hit=model_final_answer_hit,
        model_execution_source=str(getattr(model_execution, "source", "") or ""),
        tool_backing_sources=list(tool_backing_sources or []),
    )
    from core.runtime_task_outcome import terminal_fulfillment_outcome

    terminal_outcome = terminal_fulfillment_outcome({
        "response": decorated_response,
        "model_execution": {
            "source": getattr(model_execution, "source", ""),
            "details": getattr(model_execution, "details", None),
        },
    }, source_context=source_context)
    agent._emit_runtime_event(
        source_context,
        event_type="task_failed" if terminal_outcome.is_unfulfilled else "task_completed",
        message=(
            "Task did not meet its completion requirements."
            if terminal_outcome.is_unfulfilled
            else f"Model-worded response ready: {agent._runtime_preview(decorated_response)}"
        ),
        task_id=task.task_id,
        status=reason,
        **({"fulfillment_outcome": terminal_outcome.to_dict()} if terminal_outcome.is_unfulfilled else {}),
    )
    agent._finalize_runtime_checkpoint(
        source_context,
        status="completed",
        final_response=decorated_response,
    )
    visibility = execution_visibility(model_execution)
    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_scope_active
    from core.turn_model_call_ledger import turn_model_calls

    return {
        "task_id": task.task_id,
        "response": str(decorated_response or ""),
        "mode": "advice_only",
        "confidence": float(confidence),
        "understanding_confidence": float(getattr(interpretation, "understanding_confidence", 1.0) or 1.0),
        "interpreted_input": user_input,
        "topic_hints": list(getattr(interpretation, "topic_hints", []) or []),
        "prompt_assembly_report": context_result.report.to_dict(),
        "model_execution": {
            "source": getattr(model_execution, "source", ""),
            "provider_id": getattr(model_execution, "provider_id", None),
            "used_model": bool(getattr(model_execution, "used_model", False)),
            "cache_hit": bool(getattr(model_execution, "cache_hit", False)),
            "validation_state": getattr(model_execution, "validation_state", "not_run"),
            "details": {
                "model_was_attempted": bool((getattr(model_execution, "details", None) or {}).get("model_was_attempted")),
            },
            "model_call_id": str(getattr(model_execution, "details", {}).get("model_call_id") or ""),
            "response_id": str(getattr(model_execution, "details", {}).get("response_id") or ""),
            "locality": visibility["residency"],
            "active_inference": visibility["active_inference"],
        },
        "model_residency": visibility,
        "media_analysis": {"used_provider": False, "reason": "not_run"},
        "curiosity": {"mode": "skipped", "reason": "chat_surface_model_wording"},
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": "",
        "response_class": turn_result.response_class.value,
        "model_calls": turn_model_calls(source_context),
        "web_calls": (
            remote_fetch_attempt_count()
            if remote_fetch_scope_active()
            else 0
        ),
    }
