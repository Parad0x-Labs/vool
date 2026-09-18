from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from core.active_context_capsule import load_latest_capsule
from core.bootstrap_context import build_bootstrap_context
from core.cold_context_gate import ColdContextDecision, evaluate_cold_context_gate
from core.context_budgeter import (
    BudgetedLayer,
    ContextBudget,
    available_prompt_tokens,
    budget_layer,
    normalize_budget,
    scale_budget_to_window,
)
from core.context_history_authority import select_history_policy
from core.context_manifest import build_context_manifest
from core.context_namespace import load_chat_namespace
from core.context_relevance_ranker import rank_context_items, retrieval_confidence
from core.context_retrieval import exact_recall_requires_current_session
from core.context_scope import (
    ContextScopePolicy,
    CurrentTurnCorrection,
    annotate_and_filter,
    current_turn_corrections,
    reconcile_context_candidates,
)
from core.context_source_authority import ContextSource, ContextSourceAuthority
from core.human_input_adapter import continuation_classifier_text
from core.knowledge_fetcher import consult_relevant_swarm_metadata
from core.memory.entries import (
    find_disputed_memory_facts,
    resolve_memory_access_policy,
)
from core.persistent_memory import search_relevant_memory, search_session_summaries, search_user_heuristics
from core.prompt_assembly_report import ContextItem, PromptAssemblyReport
from core.provenance_store import store_manifest
from core.request_trust import request_is_owner_local
from core.runtime_continuity import load_tool_receipt
from core.shard_matcher import find_local_candidates
from core.shard_ranker import rank
from core.task_router import context_strategy
from storage.context_access_log import record_context_access
from storage.db import get_connection
from storage.dialogue_memory import recent_dialogue_turns, session_lexicon
from storage.shard_reuse_outcomes import summarize_reuse_outcomes_for_shards
from storage.swarm_memory import search_recent_contexts

_ADJACENT_TOOL_FOLLOWUP_RE = re.compile(
    r"^(?:"
    r"try again|retry(?: it| that| the command)?|"
    r"run (?:the )?(?:tests?|test suite|linter|checks?|same check|command)\b.*|"
    r"(?:now )?(?:edit|open|read|inspect|apply|revert|undo|fix|verify|test|execute) "
    r"(?:it|that|this|the (?:file|result|change|failure|command|check|patch)|that file|that change)\b.*"
    r")$",
    re.IGNORECASE,
)
_IMPORTED_SUMMARY_RE = re.compile(
    r"\b(?:"
    r"(?:previous|prior|earlier|other|last)\s+(?:session|chat|conversation)|"
    r"session\s+summar(?:y|ies)|"
    r"(?:across|between)\s+(?:sessions|chats|conversations)|"
    r"import(?:ed)?\s+(?:summary|history|context)"
    r")\b",
    re.IGNORECASE,
)
_SHARED_CONTEXT_RE = re.compile(
    r"\b(?:swarm|hive|remote\s+peers?|peer\s+(?:memory|research|context)|"
    r"shared\s+(?:context|research|memory)|knowledge\s+shards?|continuity)\b",
    re.IGNORECASE,
)


@dataclass
class TieredContextResult:
    bootstrap_items: list[ContextItem]
    relevant_items: list[ContextItem]
    cold_items: list[ContextItem]
    local_candidates: list[dict[str, Any]]
    swarm_metadata: list[dict[str, Any]]
    report: PromptAssemblyReport
    retrieval_confidence_score: float
    cold_decision: ColdContextDecision

    def assembled_context(self, *, prompt_profile: str = "default") -> str:
        sections: list[str] = []
        bootstrap_items = _filter_context_items_for_prompt_profile(self.bootstrap_items, prompt_profile=prompt_profile)
        if prompt_profile == "chat_capsule":
            bootstrap_items = [item for item in bootstrap_items if item.source_type != "active_mission"]
        relevant_items = (
            []
            if prompt_profile == "chat_capsule"
            else _filter_context_items_for_prompt_profile(self.relevant_items, prompt_profile=prompt_profile)
        )
        cold_items = (
            []
            if prompt_profile == "chat_capsule"
            else _filter_context_items_for_prompt_profile(self.cold_items, prompt_profile=prompt_profile)
        )
        bootstrap_sections = _render_context_sections("Bootstrap Context", bootstrap_items)
        relevant_sections = _render_context_sections("Relevant Context", relevant_items)
        cold_sections = _render_context_sections("Cold Context", cold_items)
        # OX-CONTEXT-RUNTIME zone law (NIA-011): served context is COMPILED, not
        # accumulated — stable prefix first (bootstrap/frozen), then the append-only
        # digest (closed cold work), then the volatile frontier (relevant open work).
        # Fixed order is what a provider exact-prefix cache can key on; when there is
        # no stable prefix at all the compiler law refuses, so fall back to the
        # legacy join rather than shipping a context that can never cache.
        if bootstrap_sections:
            from core.context_layout import ContextBlock, Role, compile_context

            zone_text = {
                Role.FROZEN: "\n\n".join(bootstrap_sections),
                Role.DIGEST: "\n\n".join(cold_sections),
                Role.FRONTIER: "\n\n".join(relevant_sections),
            }
            blocks = [
                ContextBlock(name=role.value, role=role, text=text)
                for role, text in zone_text.items()
                if text
            ]
            return compile_context(blocks).render()
        sections.extend(bootstrap_sections)
        sections.extend(relevant_sections)
        sections.extend(cold_sections)
        return "\n\n".join(sections)

    def context_snippets(self) -> list[dict[str, Any]]:
        snippets: list[dict[str, Any]] = []
        for item in self.relevant_items + self.cold_items:
            payload = {
                "title": item.title,
                "source_type": item.source_type,
                "summary": item.content,
                "confidence": item.confidence,
                "priority": item.priority,
                "metadata": dict(item.metadata),
            }
            observation = _observation_payload_from_item(item)
            if observation is not None:
                payload["observation"] = observation
            citation = dict(item.metadata or {}).get("reuse_citation")
            if isinstance(citation, dict) and citation:
                payload["citation"] = dict(citation)
            snippets.append(payload)
        return snippets

    def retrieval_profile(self) -> dict[str, Any]:
        return {
            "retrieval_confidence": self.report.retrieval_confidence,
            "retrieval_confidence_score": self.retrieval_confidence_score,
            "swarm_metadata_consulted": bool(self.report.swarm_metadata_consulted),
            "cold_archive_opened": bool(self.report.cold_archive_opened),
            "local_candidate_count": len(self.local_candidates),
            "swarm_metadata_count": len(self.swarm_metadata),
        }


def empty_tiered_context_result(
    *,
    task_id: str = "",
    reason: str = "context_not_loaded",
    source_context: dict[str, Any] | None = None,
) -> TieredContextResult:
    """Build a signed empty selection receipt for a provider-bound plain turn."""
    context = source_context if isinstance(source_context, dict) else None
    chat_id = str((context or {}).get("chat_id") or "").strip()
    project_id = str((context or {}).get("_trusted_project_id") or "").strip()
    trace_id = str((context or {}).get("request_id") or task_id or "").strip()
    report = PromptAssemblyReport(
        task_id=str(task_id or ""),
        trace_id=trace_id,
        total_context_budget=0,
        bootstrap_budget=0,
        relevant_budget=0,
        cold_budget=0,
        retrieval_confidence="none",
        chat_id=chat_id,
        project_id=project_id,
        context_scopes=["chat"] if chat_id else [],
        access_policy={
            "chat_id": chat_id,
            "project_id": project_id,
            "namespace_state": "active",
            "grant_count": 0,
            "project_context_allowed": False,
            "profile_context_allowed": False,
            "action_receipts_allowed": False,
            "shared_context_allowed": False,
            "cold_context_allowed": False,
        },
        context_manifest_required=bool(context),
    )
    report.trimming_decisions.append(str(reason or "context_not_loaded"))
    if context is not None:
        manifest = build_context_manifest(
            task_id=report.task_id,
            trace_id=report.trace_id,
            evidence_items=[],
            source_metadata=[],
            redaction_markers=["context_scope_default_deny"],
            truncation_markers=list(report.trimming_decisions),
            chat_id=report.chat_id,
            project_id=report.project_id,
            context_scopes=report.context_scopes,
            selected_items=[],
            excluded_items=[],
            capsule_version="none",
            access_policy=report.access_policy,
            candidate_items=[],
        )
        store_manifest(manifest)
        report.context_manifest_id = manifest.manifest_id
        context["context_manifest_id"] = manifest.manifest_id
        context["context_manifest_trace_id"] = manifest.trace_id
        record_context_access(report.to_dict())
    return TieredContextResult(
        bootstrap_items=[],
        relevant_items=[],
        cold_items=[],
        local_candidates=[],
        swarm_metadata=[],
        report=report,
        retrieval_confidence_score=0.0,
        cold_decision=ColdContextDecision(False, str(reason or "context_not_loaded")),
    )


def _filter_context_items_for_prompt_profile(
    items: list[ContextItem],
    *,
    prompt_profile: str,
) -> list[ContextItem]:
    if prompt_profile != "chat_minimal":
        return list(items)
    return [
        item
        for item in items
        if not bool((item.metadata or {}).get("exclude_from_chat_minimal_system_prompt"))
    ]


def _observation_payload_from_item(item: ContextItem) -> dict[str, Any] | None:
    metadata = dict(item.metadata or {})
    observation = metadata.get("observation")
    if isinstance(observation, dict) and observation:
        return dict(observation)
    if str(metadata.get("context_format") or "").strip() != "structured_observation":
        return None
    try:
        loaded = json.loads(str(item.content or ""))
    except Exception:
        return None
    return dict(loaded) if isinstance(loaded, dict) else None


def _remote_shard_citation(
    candidate: dict[str, Any],
    *,
    reuse_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    receipt = dict(candidate.get("retrieval_receipt") or {})
    if str(candidate.get("source_type") or "") != "peer_received" or not receipt:
        return None
    citation = {
        "kind": "remote_shard",
        "shard_id": str(candidate.get("shard_id") or "").strip(),
        "source_peer_id": str(receipt.get("source_peer_id") or "").strip(),
        "source_node_id": str(candidate.get("source_node_id") or "").strip(),
        "manifest_id": str(receipt.get("manifest_id") or "").strip(),
        "content_hash": str(receipt.get("content_hash") or "").strip(),
        "receipt_id": str(receipt.get("receipt_id") or "").strip(),
        "validation_state": str(receipt.get("validation_state") or "").strip(),
        "fetched_at": str(receipt.get("created_at") or "").strip(),
    }
    effective_summary = dict(reuse_summary or candidate.get("reuse_outcomes") or {})
    if effective_summary:
        citation["reuse_outcomes"] = effective_summary
    return citation


def _render_context_sections(label: str, items: list[ContextItem]) -> list[str]:
    if not items:
        return []
    narrative_items = [item for item in items if _observation_payload_from_item(item) is None]
    observation_payloads = [
        payload
        for payload in (_observation_payload_from_item(item) for item in items)
        if isinstance(payload, dict) and payload
    ]
    sections: list[str] = []
    if narrative_items:
        sections.append(label + ":\n" + "\n".join(f"- {item.title}: {item.content}" for item in narrative_items))
    if observation_payloads:
        sections.append(
            label.replace("Context", "Observations") + ":\n" +
            json.dumps(observation_payloads, indent=2, sort_keys=True, ensure_ascii=True, default=str)
        )
    return sections


def _local_candidate_items(
    task: Any,
    classification: dict[str, Any],
    *,
    policy: ContextScopePolicy,
    include_current_chat: bool,
) -> tuple[list[ContextItem], list[dict[str, Any]]]:
    task_class = str(classification.get("task_class") or "").strip()
    ranked = rank(
        find_local_candidates(
            task,
            classification,
            access_policy=policy,
            include_current_chat=include_current_chat,
        ),
        task,
    )
    missing_peer_ids = [
        str(candidate.get("shard_id") or "").strip()
        for candidate in ranked
        if str(candidate.get("source_type") or "") == "peer_received"
        and not dict(candidate.get("reuse_outcomes") or {})
    ]
    outcome_summaries = (
        summarize_reuse_outcomes_for_shards(
            missing_peer_ids,
            task_class=task_class,
        )
        if missing_peer_ids
        else {}
    )
    items: list[ContextItem] = []
    for candidate in ranked[:8]:
        pattern = list(candidate.get("resolution_pattern") or [])[:4]
        shard_id = str(candidate.get("shard_id") or "").strip()
        reuse_summary = dict(candidate.get("reuse_outcomes") or outcome_summaries.get(shard_id) or {})
        citation = _remote_shard_citation(candidate, reuse_summary=reuse_summary)
        if citation:
            reuse_outcomes = dict(citation.get("reuse_outcomes") or {})
            reuse_note = ""
            quality_backed = int(reuse_outcomes.get("quality_backed_count") or 0)
            quality_backed_durable = int(reuse_outcomes.get("quality_backed_durable_count") or 0)
            answer_backed = int(reuse_outcomes.get("answer_backed_count") or 0)
            answer_backed_durable = int(reuse_outcomes.get("answer_backed_durable_count") or 0)
            selected = int(reuse_outcomes.get("selected_count") or 0)
            success = int(reuse_outcomes.get("success_count") or 0)
            durable = int(reuse_outcomes.get("durable_count") or 0)
            if quality_backed > 0:
                reuse_note = (
                    f" Previously improved clean answers for this task class in {quality_backed} turns "
                    f"({quality_backed_durable} durable)."
                )
            elif answer_backed > 0:
                reuse_note = (
                    f" Previously backed answers for this task class in {answer_backed} turns "
                    f"({answer_backed_durable} durable), but clean-answer proof is still weaker."
                )
            elif selected > 0:
                reuse_note = f" Previously selected during planning for this task class in {selected} turns."
            elif success > 0:
                reuse_note = (
                    f" Previously cited for this task class in {success} successful turns "
                    f"({durable} durable); answer-backed proof not established yet."
                )
            content = (
                f"Cached remote shard from {citation.get('source_peer_id', 'unknown peer')[:12]}... "
                f"with validation {citation.get('validation_state', 'unknown')}. "
                f"{candidate.get('summary', '')} "
                f"Pattern: {', '.join(str(step) for step in pattern) or 'n/a'}."
                f"{reuse_note}"
            ).strip()
            source_type = "remote_shard_cache"
            include_reason = "remote_shard_reuse"
        else:
            content = (
                f"{candidate.get('summary', '')} "
                f"Pattern: {', '.join(str(step) for step in pattern) or 'n/a'}."
            ).strip()
            source_type = "local_shard"
            include_reason = "local_shard_match"
        share_scope = str(candidate.get("share_scope") or "").strip()
        origin_chat_id = (
            str(candidate.get("origin_session_id") or "").strip()
            if share_scope == "local_only"
            else "shared:swarm"
        )
        metadata = {
            "shard_id": candidate["shard_id"],
            "problem_class": candidate["problem_class"],
            "freshness_ts": candidate.get("freshness_ts"),
            "trust_score": candidate.get("trust_score"),
            "share_scope": share_scope,
            "scope": "chat",
            "source": source_type,
            "status": "active",
            "origin_chat_id": origin_chat_id,
            "origin_project_id": "",
        }
        if citation:
            metadata["reuse_citation"] = citation
        items.append(
            ContextItem(
                item_id=f"local-shard-{candidate['shard_id']}",
                layer="relevant",
                source_type=source_type,
                title=f"Local shard {candidate['problem_class']}",
                content=content[:420],
                priority=float(candidate.get("score") or 0.0),
                confidence=float(candidate.get("score") or 0.0),
                include_reason=include_reason,
                metadata=metadata,
                provenance={
                    "kind": "ranked_shard",
                    "source_id": str(candidate["shard_id"]),
                    "source_node_id": candidate.get("source_node_id"),
                    "content_hash": hashlib.sha256(
                        content.encode()
                    ).hexdigest(),
                },
            )
        )
    return items, ranked


def _dialogue_items(session_id: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    for turn in recent_dialogue_turns(session_id, limit=6):
        items.append(
            ContextItem(
                item_id=f"dialogue-{turn['turn_id']}",
                layer="relevant",
                source_type="dialogue_turn",
                title="Recent dialogue turn",
                content=str(turn.get("normalized_input") or turn.get("raw_input") or turn.get("reconstructed_input") or "")[:260],
                confidence=float(turn.get("understanding_confidence") or 0.0),
                include_reason="recent_dialogue_memory",
                metadata={
                    "created_at": turn.get("created_at"),
                    "topic_hints": list(turn.get("topic_hints") or []),
                    "session_id": session_id,
                },
            )
        )
    return items


def _current_chat_corrections(
    session_id: str,
    *,
    current_user_text: str,
) -> tuple[CurrentTurnCorrection, ...]:
    """Carry the latest explicit correction into retrieval on later turns.

    Corrections are resolved from the current chat only and before candidates are ranked.  This
    is read-only: it does not alter the stored transcript or promote untyped model output.
    """
    latest: dict[str, CurrentTurnCorrection] = {}
    for correction in current_turn_corrections(current_user_text):
        latest[correction.fact_key] = correction
    for turn in recent_dialogue_turns(
        session_id,
        limit=50,
        speaker_roles=("user",),
    ):
        content = str(
            turn.get("normalized_input")
            or turn.get("raw_input")
            or turn.get("reconstructed_input")
            or ""
        )
        for correction in current_turn_corrections(content):
            latest.setdefault(correction.fact_key, correction)
    return tuple(latest[key] for key in sorted(latest))


def _runtime_tool_observation_items(
    session_id: str,
    *,
    policy: ContextScopePolicy,
    parent_user_turn_id: str,
) -> list[ContextItem]:
    clean_parent_turn_id = str(parent_user_turn_id or "").strip()
    if not str(session_id or "").strip() or not clean_parent_turn_id:
        return []
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT checkpoint_id, status, state_json, source_context_json, updated_at
            FROM runtime_checkpoints
            WHERE session_id = ?
              AND json_extract(source_context_json, '$._canonical_user_turn_id') = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (session_id, clean_parent_turn_id),
        ).fetchone()
    except Exception:
        return []
    finally:
        conn.close()
    if not row:
        return []
    try:
        checkpoint_source_context = json.loads(str(row["source_context_json"] or "{}"))
    except Exception:
        return []
    if (
        not isinstance(checkpoint_source_context, dict)
        or str(checkpoint_source_context.get("_canonical_user_turn_id") or "").strip()
        != clean_parent_turn_id
    ):
        return []
    try:
        state = json.loads(str(row["state_json"] or "{}"))
    except Exception:
        return []
    if not isinstance(state, dict):
        return []
    last_tool_response = dict(state.get("last_tool_response") or {})
    if not last_tool_response:
        return []
    receipt = dict(last_tool_response.get("receipt") or {})
    receipt_id = str(receipt.get("receipt_id") or "").strip()
    if not receipt_id:
        # Old checkpoints may contain raw tool observations. Retain them for
        # recovery, but never promote them into conversational context.
        return []
    durable_receipt = load_tool_receipt(receipt_id)
    if not durable_receipt or str(
        durable_receipt.get("session_id") or ""
    ).strip() != str(session_id or "").strip():
        return []
    if str(durable_receipt.get("checkpoint_id") or "").strip() != str(
        row["checkpoint_id"] or ""
    ).strip():
        return []
    action_record = dict(
        dict(durable_receipt.get("execution") or {}).get("action_record") or {}
    )
    if str(action_record.get("receipt_id") or "").strip() != receipt_id:
        return []
    origin = dict(action_record.get("origin") or {})
    origin_chat_id = str(origin.get("chat_id") or "").strip()
    origin_project_id = str(origin.get("project_id") or "").strip()
    if origin_chat_id != str(session_id or "").strip():
        return []
    safe_summary = str(
        dict(action_record.get("result") or {}).get("summary") or ""
    ).strip()
    if not safe_summary:
        return []
    payload = {
        "receipt_id": receipt_id,
        "safe_summary": safe_summary[:500],
    }
    content = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    tool_name = (
        str(last_tool_response.get("tool_name") or "tool").strip()
        or "tool"
    )
    provenance = {
        "kind": "tool_action_receipt",
        "source_id": receipt_id,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }
    metadata = {
        "checkpoint_id": str(row["checkpoint_id"] or "").strip(),
        "parent_user_turn_id": clean_parent_turn_id,
        "session_id": session_id,
        "updated_at": str(row["updated_at"] or "").strip(),
        "scope": "action_receipt",
        "source": "tool_observation",
        "source_id": receipt_id,
        "receipt_id": receipt_id,
        "status": "active",
        "origin_chat_id": origin_chat_id,
        "origin_project_id": origin_project_id,
        "context_format": "receipt_reference",
        "provenance": provenance,
    }
    allowed, _ = policy.allows_metadata(
        source_type="tool_observation",
        metadata=metadata,
    )
    if not allowed:
        return []
    return [
        ContextItem(
            item_id=f"runtime-tool-observation-{row['checkpoint_id']}",
            layer="relevant",
            source_type="tool_observation",
            title=f"Recent tool observation {tool_name}",
            content=content[:1800],
            priority=0.92,
            confidence=0.79,
            include_reason="recent_runtime_tool_observation",
            metadata=metadata,
            provenance=provenance,
        )
    ]


def _adjacent_completed_user_turn_id(session_id: str, current_user_turn_id: str) -> str:
    """Return the user identity of the completed exchange immediately before this turn."""

    clean_current_turn_id = str(current_user_turn_id or "").strip()
    if not str(session_id or "").strip() or not clean_current_turn_id:
        return ""
    turns = recent_dialogue_turns(
        session_id,
        limit=6,
        speaker_roles=("user", "assistant"),
    )
    current_index = next(
        (
            index
            for index, turn in enumerate(turns)
            if str(turn.get("turn_id") or "").strip() == clean_current_turn_id
            and str(turn.get("speaker_role") or "").strip().lower() == "user"
        ),
        None,
    )
    if current_index is None or current_index + 2 >= len(turns):
        return ""
    previous_assistant = turns[current_index + 1]
    previous_user = turns[current_index + 2]
    if str(previous_assistant.get("speaker_role") or "").strip().lower() != "assistant":
        return ""
    if str(previous_user.get("speaker_role") or "").strip().lower() != "user":
        return ""
    return str(previous_user.get("turn_id") or "").strip()


def _requests_adjacent_tool_state(user_input: str) -> bool:
    """Recognize an explicit operational follow-up after literal/code neutralization."""

    classifier_text = continuation_classifier_text(user_input)
    normalized = " ".join(classifier_text.strip().strip(" \t\n\r?!.,").split())
    return bool(normalized and _ADJACENT_TOOL_FOLLOWUP_RE.fullmatch(normalized))


def _requests_imported_summary(user_input: str) -> bool:
    return bool(_IMPORTED_SUMMARY_RE.search(continuation_classifier_text(user_input)))


def _requests_shared_context(user_input: str, topic_hints: list[str]) -> bool:
    classified = continuation_classifier_text(user_input)
    topics = " ".join(str(topic or "") for topic in topic_hints)
    return bool(_SHARED_CONTEXT_RE.search(f"{classified} {topics}"))


def _persistent_memory_items(
    query_text: str,
    topic_hints: list[str],
    *,
    session_id: str,
    policy: ContextScopePolicy,
    allow_instruction_memory: bool = True,
) -> list[ContextItem]:
    items: list[ContextItem] = []
    for entry in search_relevant_memory(
        query_text,
        access_policy=policy,
        topic_hints=topic_hints,
        limit=12,
    ):
        category = str(entry.get("category") or "fact")
        if category == "instruction" and not allow_instruction_memory:
            continue
        content = str(entry.get("text") or "")[:320]
        items.append(
            ContextItem(
                item_id=f"runtime-memory-{category}-{abs(hash(str(entry.get('text') or '')))}",
                layer="relevant",
                source_type="runtime_memory",
                title=f"Persistent memory {category}",
                content=content,
                priority=float(entry.get("score") or 0.0),
                confidence=float(entry.get("confidence") or 0.0),
                include_reason="relevant_persistent_memory",
                metadata={
                    "category": category,
                    # Sealed provenance is mandatory downstream: a context item reaching
                    # _validate_selected_sources without a content_hash raises, and because that is a
                    # property of the REQUEST every provider then fails identically. Measured
                    # 2026-07-29 — "selected context source 6 is missing content_hash" killed an
                    # audit turn on qwen3:8b, qwen3:14b and the cloud lane in 0.1s each.
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    "created_at": entry.get("created_at"),
                    "fact_key": entry.get("fact_key"),
                    "fact_value": entry.get("fact_value"),
                    "authority": entry.get("authority"),
                    "superseded_record_id": entry.get("superseded_record_id"),
                    "scope": entry.get("scope"),
                    "source": entry.get("source"),
                    "source_id": entry.get("source_id"),
                    "status": entry.get("status"),
                    "origin_chat_id": entry.get("origin_chat_id"),
                    "origin_project_id": entry.get("origin_project_id"),
                    "provenance": entry.get("provenance"),
                },
            )
        )
    return items


def _memory_dispute_items(
    query_text: str,
    *,
    session_id: str,
    policy: ContextScopePolicy,
) -> list[ContextItem]:
    items: list[ContextItem] = []
    for descriptor in find_disputed_memory_facts(
        query_text,
        access_policy=policy,
        limit=4,
    ):
        fact_key = str(descriptor.get("fact_key") or "").strip()
        provenance_hash = str(
            descriptor.get("provenance_hash") or ""
        ).strip()
        if not fact_key or len(provenance_hash) != 64:
            continue
        content = (
            f"Stored memory is disputed for fact key {fact_key}. "
            "Ask one concise clarification before using that fact; "
            "do not infer or choose a value."
        )
        source_id = f"memory-conflict:{provenance_hash[:24]}"
        provenance = {
            "kind": "memory_conflict_descriptor",
            "source_id": source_id,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "descriptor_hash": provenance_hash,
        }
        items.append(
            ContextItem(
                item_id=source_id,
                layer="relevant",
                source_type="memory_conflict",
                title="Stored memory conflict",
                content=content,
                priority=1.0,
                confidence=1.0,
                must_keep=True,
                include_reason="clarification_required_for_disputed_memory",
                metadata={
                    "scope": "chat",
                    "source": "memory_conflict",
                    "source_id": source_id,
                    "status": "active",
                    "origin_chat_id": session_id,
                    "origin_project_id": policy.project_id,
                    "provenance": provenance,
                },
                provenance=provenance,
            )
        )
    return items


def _user_heuristic_items(
    query_text: str,
    topic_hints: list[str],
    *,
    session_id: str,
    policy: ContextScopePolicy,
) -> list[ContextItem]:
    items: list[ContextItem] = []
    for entry in search_user_heuristics(
        query_text,
        access_policy=policy,
        topic_hints=topic_hints,
        limit=4,
    ):
        category = str(entry.get("category") or "heuristic")
        signal = str(entry.get("signal") or category)
        content = str(entry.get("text") or "")[:320]
        items.append(
            ContextItem(
                item_id=f"user-heuristic-{category}-{signal}",
                layer="relevant",
                source_type="user_heuristic",
                title=f"User heuristic {category}",
                content=content,
                priority=float(entry.get("score") or 0.0),
                confidence=float(entry.get("confidence") or 0.0),
                include_reason="inferred_user_heuristic",
                metadata={
                    "category": category,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    "signal": signal,
                    "mentions": int(entry.get("mentions") or 0),
                    "updated_at": entry.get("updated_at"),
                    "fact_key": entry.get("fact_key"),
                    "fact_value": entry.get("fact_value"),
                    "authority": entry.get("authority"),
                    "superseded_record_id": entry.get("superseded_record_id"),
                    "scope": "user_profile",
                    "source": "user_heuristic",
                    "source_id": entry.get("source_id"),
                    "status": entry.get("status"),
                    "origin_chat_id": entry.get("origin_chat_id"),
                    "origin_project_id": entry.get("origin_project_id"),
                    "provenance": entry.get("provenance"),
                },
            )
        )
    return items


# Exact-value classes that a cross-session summary can carry and that must never override the
# current turn's own authoritative active-mission slot. Kept in sync with active_mission._SLOT_PATTERNS
# for spend_cap (amounts) and active_domain (.null). Wallet-prefix/OS are intentionally NOT matched:
# a base58 prefix regex also matches ordinary English words, so redacting them would corrupt benign
# continuity text — and in the observed leak those fields came correctly from the slots, not the summary.
_MONEY_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:SOL|USDC)\b", re.IGNORECASE)
_NULL_DOMAIN_TOKEN_RE = re.compile(r"\b[a-z0-9][a-z0-9_.\-]*\.null\b", re.IGNORECASE)


def _norm_value(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _current_mission_literals(session_id: str) -> dict[str, str]:
    """Authoritative exact values (spend_cap, active_domain) the current session owns, if any. Empty
    when there is no active mission — callers then leave cross-session continuity untouched."""
    literals: dict[str, str] = {}
    try:
        from core.active_mission import current_active_mission_slots

        for slot in current_active_mission_slots(session_id):
            name = str(slot.get("slot_name") or "")
            if name in ("spend_cap", "active_domain"):
                value = str(slot.get("value") or "").strip()
                if value:
                    literals[name] = value
    except Exception:
        return {}
    return literals


def _summary_conflicts_with_mission(summary: str, mission_literals: dict[str, str] | None) -> bool:
    """True when a cross-session summary states a money amount or .null domain that DIFFERS from the
    current turn's authoritative active-mission slot — i.e. it would contaminate an exact value."""
    if not mission_literals:
        return False
    text = str(summary or "")
    cap = mission_literals.get("spend_cap")
    if cap and any(_norm_value(tok) != _norm_value(cap) for tok in _MONEY_TOKEN_RE.findall(text)):
        return True
    domain = mission_literals.get("active_domain")
    return bool(domain) and any(
        _norm_value(tok) != _norm_value(domain) for tok in _NULL_DOMAIN_TOKEN_RE.findall(text)
    )


def _session_summary_items(
    query_text: str,
    topic_hints: list[str],
    *,
    session_id: str,
    mission_literals: dict[str, str] | None = None,
    policy: ContextScopePolicy | None = None,
) -> list[ContextItem]:
    effective_policy = policy
    if effective_policy is None:
        return []
    if not effective_policy.allow_cross_chat:
        return []
    items: list[ContextItem] = []
    for entry in search_session_summaries(
        query_text,
        access_policy=effective_policy,
        topic_hints=topic_hints,
        limit=2,
        exclude_session_id=session_id,
    ):
        summary = str(entry.get("summary") or "")
        # Defense-in-depth for recall turns the deterministic mission render does not short-circuit:
        # drop an OTHER session's summary that conflicts with an exact value the current mission owns,
        # so a stale cross-session cap/domain can never override the authoritative slot. When the turn
        # has no active mission (mission_literals empty), behavior is unchanged.
        if _summary_conflicts_with_mission(summary, mission_literals):
            continue
        items.append(
            ContextItem(
                item_id=f"session-summary-{abs(hash(str(entry.get('session_id') or '')))}",
                layer="relevant",
                source_type="session_summary",
                title="Prior session continuity",
                content=summary[:360],
                priority=float(entry.get("score") or 0.0),
                confidence=0.68,
                include_reason="relevant_session_summary",
                metadata={
                    "content_hash": hashlib.sha256(summary[:360].encode()).hexdigest(),
                    "created_at": entry.get("created_at"),
                    "scope": "chat",
                    "source": "session_summary",
                    # No source_id of its own; the gateway falls back to item_id, which carries the
                    # originating session. Stated rather than left to that fallback by accident.
                    "source_id": entry.get("source_id") or entry.get("session_id"),
                    "status": entry.get("status") or "active",
                    "origin_chat_id": entry.get("session_id"),
                    "origin_project_id": entry.get("project_id"),
                    "provenance": entry.get("provenance"),
                    "turn_count": entry.get("turn_count"),
                },
            )
        )
    return items


def _shared_swarm_context_items(query_text: str) -> list[ContextItem]:
    items: list[ContextItem] = []
    for idx, entry in enumerate(search_recent_contexts(query_text, limit=2), start=1):
        content = (
            f"Prompt: {entry.get('prompt_preview') or 'n/a'} "
            f"Result: {entry.get('result_preview') or 'n/a'}"
        ).strip()
        items.append(
            ContextItem(
                item_id=f"swarm-context-{idx}-{abs(hash(content))}",
                layer="relevant",
                source_type="swarm_context",
                title="Shared swarm memory",
                content=content[:380],
                priority=float(entry.get("score") or 0.0),
                confidence=0.62,
                include_reason="shared_swarm_context",
                metadata={
                    "created_at": entry.get("timestamp"),
                    "parent_peer_id": entry.get("parent_peer_id"),
                    "learning_value": entry.get("learning_value"),
                    "scope": "chat",
                    "source": "swarm_context",
                    "status": "active",
                    "origin_chat_id": "shared:swarm",
                    "origin_project_id": "",
                },
                provenance={
                    "kind": "shared_swarm_context",
                    "source_id": (
                        "swarm:"
                        + hashlib.sha256(
                            (
                                f"{entry.get('parent_peer_id') or ''}\0"
                                f"{entry.get('timestamp') or ''}\0{content}"
                            ).encode()
                        ).hexdigest()
                    ),
                    "content_hash": hashlib.sha256(
                        content.encode()
                    ).hexdigest(),
                },
            )
        )
    return items


def _shorthand_items(session_id: str, query_text: str) -> list[ContextItem]:
    lexicon = session_lexicon(session_id)
    lower = query_text.lower()
    selected = [(term, canonical) for term, canonical in lexicon.items() if term in lower or canonical in lower][:4]
    if not selected:
        return []
    return [
        ContextItem(
            item_id=f"shorthand-{term}",
            layer="relevant",
            source_type="shorthand",
            title="User shorthand mapping",
            content=f"{term} maps to {canonical}.",
            confidence=0.7,
            include_reason="user_shorthand_memory",
            metadata={"session_id": session_id},
        )
        for term, canonical in selected
    ]


def _current_session_scoped_items(items: list[ContextItem], *, session_id: str) -> list[ContextItem]:
    current = str(session_id or "").strip()
    scoped: list[ContextItem] = []
    for item in items:
        item_session = str(
            (item.metadata or {}).get("origin_chat_id")
            or (item.metadata or {}).get("session_id")
            or ""
        ).strip()
        if item_session and item_session == current:
            scoped.append(item)
    return scoped


def _swarm_metadata_items(
    task: Any,
    classification: dict[str, Any],
    *,
    allow_swarm_metadata: bool,
    allow_swarm_fetch: bool,
) -> tuple[list[ContextItem], list[dict[str, Any]], bool]:
    if not allow_swarm_metadata:
        return [], [], False
    result = consult_relevant_swarm_metadata(
        classification.get("task_class", "unknown"),
        getattr(task, "task_summary", ""),
        limit=4,
        allow_fetch=allow_swarm_fetch,
    )
    items: list[ContextItem] = []
    for entry in result["items"]:
        tags = [str(tag) for tag in list(entry.get("topic_tags") or [])[:4] if str(tag).strip()]
        problem_class = str(entry.get("problem_class") or "unknown").strip() or "unknown"
        utility = float(entry.get("utility_score") or 0.0)
        trust = float(entry.get("trust_weight") or 0.0)
        quality = float(entry.get("quality_score") or 0.0)
        status_note = (
            "Live swarm fetch requested from the holder."
            if bool(entry.get("fetched"))
            else "Metadata-only remote context."
        )
        items.append(
            ContextItem(
                item_id=f"swarm-{entry['shard_id']}-{entry['holder_peer_id']}",
                layer="relevant",
                source_type="swarm_remote_context" if bool(entry.get("fetched")) else "swarm_metadata",
                title=f"Swarm shard {problem_class}",
                content=(
                    f"{status_note} Holder {entry['holder_peer_id'][:12]}..., region {entry.get('home_region', 'global')}, "
                    f"problem class {problem_class}, tags {', '.join(tags) or 'n/a'}, utility {utility:.2f}, "
                    f"quality {quality:.2f}, trust {trust:.2f}, fetch method {dict(entry.get('fetch_route') or {}).get('method', 'unknown')}."
                ),
                priority=float(entry.get("relevance_score") or 0.0),
                confidence=min(0.8, float(entry.get("trust_weight") or 0.0) + 0.15),
                include_reason="swarm_fetch_requested" if bool(entry.get("fetched")) else "swarm_metadata_only",
                metadata={
                    "freshness_ts": entry.get("freshness_ts"),
                    "home_region": entry.get("home_region"),
                    "shard_id": entry["shard_id"],
                    "problem_class": problem_class,
                    "utility_score": utility,
                    "quality_score": quality,
                    "fetched": bool(entry.get("fetched")),
                    "scope": "chat",
                    "source": (
                        "swarm_remote_context"
                        if bool(entry.get("fetched"))
                        else "swarm_metadata"
                    ),
                    "status": "active",
                    "origin_chat_id": "shared:swarm",
                    "origin_project_id": "",
                },
                provenance={
                    "kind": "swarm_metadata",
                    "source_id": (
                        f"shard:{entry['shard_id']}:"
                        f"{entry['holder_peer_id']}"
                    ),
                    "fetch_route": entry.get("fetch_route"),
                    "content_hash": hashlib.sha256(
                        (
                            f"{entry['shard_id']}\0"
                            f"{entry['holder_peer_id']}\0"
                            f"{problem_class}\0{tags}"
                        ).encode()
                    ).hexdigest(),
                },
            )
        )
    return items, result["items"], bool(result.get("consulted"))


def _cold_context_items(query_text: str, session_id: str) -> list[ContextItem]:
    del query_text
    items: list[ContextItem] = []
    older_turns = recent_dialogue_turns(session_id, limit=10)[4:]
    for turn in older_turns[:2]:
        items.append(
            ContextItem(
                item_id=f"cold-dialogue-{turn['turn_id']}",
                layer="cold",
                source_type="cold_archive",
                title="Older dialogue archive",
                content=str(turn.get("normalized_input") or turn.get("raw_input") or turn.get("reconstructed_input") or "")[:260],
                confidence=0.4,
                include_reason="older_dialogue_archive",
                metadata={"created_at": turn.get("created_at")},
            )
        )
    return items


def _report_exclusions(report: PromptAssemblyReport, layer: BudgetedLayer) -> None:
    for item, reason in layer.excluded:
        if reason == "trimmed_to_fit":
            report.trimming_decisions.append(f"{item.title} trimmed to fit {item.layer} budget.")
            report.items_excluded.append(item.to_record(included=False, reason=reason))
        else:
            report.items_excluded.append(item.to_record(included=False, reason=reason))


_SHADOW_CAPSULE_VERSION_RE = re.compile(r"^capsule-[0-9a-f]{32}$")
_SHADOW_CAPSULE_CLAIM_SOURCES = frozenset(
    {"transcript_user", "verified_action", "receipt"}
)


def _normalized_audit_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _validated_shadow_claim_texts(capsule: Any) -> tuple[str, ...] | None:
    claims = [
        getattr(capsule, "objective", None),
        *tuple(getattr(capsule, "decisions", ()) or ()),
        *tuple(getattr(capsule, "constraints", ()) or ()),
        *tuple(getattr(capsule, "system_state", ()) or ()),
        *tuple(getattr(capsule, "unresolved_work", ()) or ()),
        *tuple(getattr(capsule, "verified_actions", ()) or ()),
    ]
    validated: list[str] = []
    seen_hashes: set[str] = set()
    for claim in claims:
        if claim is None:
            continue
        text = str(getattr(claim, "text", "") or "")
        source_type = str(getattr(claim, "source_type", "") or "")
        source_id = str(getattr(claim, "source_id", "") or "").strip()
        content_hash = str(getattr(claim, "content_hash", "") or "")
        expected_hash = hashlib.sha256(text.encode()).hexdigest()
        if (
            not text.strip()
            or source_type not in _SHADOW_CAPSULE_CLAIM_SOURCES
            or not source_id
            or content_hash != expected_hash
        ):
            return None
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        validated.append(text)
    return tuple(validated)


def _record_shadow_capsule_audit(
    report: PromptAssemblyReport,
    *,
    bootstrap_items: list[ContextItem],
    relevant_items: list[ContextItem],
    cold_items: list[ContextItem],
) -> None:
    report.shadow_capsule_version = "none"
    report.shadow_capsule_claim_count = 0
    report.shadow_capsule_claim_coverage_count = 0
    report.shadow_capsule_claim_coverage_ratio = 0.0
    report.shadow_capsule_used_in_prompt = False
    try:
        namespace = load_chat_namespace(report.chat_id)
        if (
            namespace is None
            or namespace.lifecycle_state != "active"
            or namespace.chat_id != report.chat_id
            or namespace.project_id != report.project_id
        ):
            return
        capsule = load_latest_capsule(namespace.chat_id)
        version_id = str(getattr(capsule, "version_id", "") or "")
        if (
            capsule is None
            or getattr(capsule, "mode", None) != "shadow"
            or getattr(capsule, "chat_id", None) != namespace.chat_id
            or getattr(capsule, "project_id", None) != namespace.project_id
            or _SHADOW_CAPSULE_VERSION_RE.fullmatch(version_id) is None
        ):
            return
        claim_texts = _validated_shadow_claim_texts(capsule)
        if claim_texts is None:
            return
        prompt_sections: list[str] = []
        prompt_sections.extend(
            _render_context_sections("Bootstrap Context", bootstrap_items)
        )
        prompt_sections.extend(
            _render_context_sections("Relevant Context", relevant_items)
        )
        prompt_sections.extend(
            _render_context_sections("Cold Context", cold_items)
        )
        included_text = _normalized_audit_text("\n\n".join(prompt_sections))
        coverage_count = sum(
            1
            for claim_text in claim_texts
            if (
                normalized_claim := _normalized_audit_text(claim_text)
            )
            and normalized_claim in included_text
        )
        report.shadow_capsule_version = version_id
        report.shadow_capsule_claim_count = len(claim_texts)
        report.shadow_capsule_claim_coverage_count = coverage_count
        report.shadow_capsule_claim_coverage_ratio = (
            round(coverage_count / len(claim_texts), 6)
            if claim_texts
            else 0.0
        )
    except Exception:
        return


def _source_allows_private_context(source_context: dict[str, Any] | None) -> bool:
    context = dict(source_context or {})
    surface = str(context.get("surface") or "").strip().lower()
    platform = str(context.get("platform") or "").strip().lower()
    group_like = bool(
        context.get("is_group")
        or context.get("group_id")
        or context.get("channel_is_group")
        or surface in {"discord", "telegram", "slack", "whatsapp", "group"}
        or platform in {"discord", "telegram", "slack", "whatsapp", "group"}
    )
    if group_like:
        return False
    explicit = context.get("private_context_enabled")
    if explicit is False:
        return False
    return request_is_owner_local(context)


def _resolve_context_source_authority(
    *,
    policy: ContextScopePolicy,
    include_current_chat: bool,
    include_private_imports: bool,
    user_profile_relevant: bool,
    query_text: str,
    topic_hints: list[str],
    exact_recall_current_session_only: bool,
    adjacent_tool_followup: bool,
    adjacent_parent_user_turn_id: str,
) -> ContextSourceAuthority:
    """Resolve non-transcript scope and relevance without continuation classification."""

    scoped: set[ContextSource] = set()
    relevant: set[ContextSource] = set()
    if include_current_chat:
        scoped.update(
            {
                ContextSource.PERSISTENT_RETRIEVAL,
                ContextSource.MISSION_STATE,
                ContextSource.SHORTHAND,
            }
        )
        relevant.update(
            {
                ContextSource.PERSISTENT_RETRIEVAL,
                ContextSource.SHORTHAND,
            }
        )
        if policy.allow_action_receipts:
            scoped.add(ContextSource.TOOL_STATE)
        if exact_recall_current_session_only:
            relevant.add(ContextSource.MISSION_STATE)
        if adjacent_tool_followup and adjacent_parent_user_turn_id:
            relevant.add(ContextSource.TOOL_STATE)
    if policy.allow_cross_chat:
        scoped.add(ContextSource.SESSION_SUMMARY)
        if _requests_imported_summary(query_text):
            relevant.add(ContextSource.SESSION_SUMMARY)
    if include_private_imports and policy.allow_user_profile_context:
        scoped.add(ContextSource.USER_HEURISTIC)
        if user_profile_relevant:
            relevant.add(ContextSource.USER_HEURISTIC)
    if policy.allow_shared_context:
        scoped.add(ContextSource.SHARED_CONTEXT)
        if _requests_shared_context(query_text, topic_hints):
            relevant.add(ContextSource.SHARED_CONTEXT)
    if policy.allow_cold_context:
        scoped.add(ContextSource.COLD_CONTEXT)
    return ContextSourceAuthority.from_sources(scoped=scoped, relevant=relevant)


# C09: how many characters of compiled turn-context one prompt may carry. A
# slice of the relevant budget (tokens ≈ chars/4), hard-capped so a full
# relevant budget can never be eaten by remembered pages alone.
TURN_CONTEXT_MAX_BLOCK_CHARS = 4000


def _turn_context_compilation(
    session_id: str,
    query_text: str,
    *,
    source_context: dict[str, Any] | None,
    total_context_budget: int,
):
    """Compile the scoped turn-context block through the ONE authority.

    Admission scoping, A8 withhold/erasure gating, relevance, overflow and
    truncation are the authority's decisions (core.turn_context); this loader
    only renders what it admits as typed items so they flow through the same
    budget/rank/compile path as every other candidate.
    """
    from core.turn_context import compile_turn_context, resolve_principal

    context = source_context if isinstance(source_context, dict) else {}
    principal = resolve_principal(context)
    project_id = str(context.get("_trusted_project_id") or "").strip()
    budget_chars = max(
        0,
        min(
            TURN_CONTEXT_MAX_BLOCK_CHARS,
            (int(total_context_budget or 0) * 4) // 2,
        ),
    )
    return compile_turn_context(
        principal,
        session_id,
        project_id=project_id,
        query_text=query_text,
        budget_chars=budget_chars,
        source_context=context,
    )


def _turn_context_items(
    compilation,
    *,
    session_id: str,
    project_id: str,
) -> tuple[list[ContextItem], list[str]]:
    """Render an authority compilation into relevant-layer items.

    Returns (items, trimming_decisions): overflow pages the authority
    explicitly truncated surface as typed trimming decisions, never as
    silent omissions.
    """
    items: list[ContextItem] = []
    for selection in compilation.items:
        content_hash = hashlib.sha256(selection.content.encode("utf-8")).hexdigest()
        items.append(
            ContextItem(
                item_id=f"turn-context:{selection.admission_id}",
                layer="relevant",
                source_type="turn_context_page",
                title=selection.title or "remembered context",
                content=selection.content,
                priority=0.9 if selection.pinned else 0.6,
                confidence=1.0,
                must_keep=selection.pinned,
                include_reason="scoped_turn_context_page",
                metadata={
                    "scope": "chat",
                    "origin_chat_id": session_id,
                    "origin_project_id": project_id,
                    "page_hash": selection.page_hash,
                    "admission_id": selection.admission_id,
                    "pinned": selection.pinned,
                    "generation": selection.generation,
                    "provenance": {
                        "kind": "server_assembled_context",
                        "source_id": selection.admission_id,
                        "content_hash": content_hash,
                        "origin": selection.origin,
                        "authority": "core.turn_context",
                    },
                },
                provenance={
                    "kind": "server_assembled_context",
                    "source_id": selection.admission_id,
                    "content_hash": content_hash,
                    "origin": selection.origin,
                    "authority": "core.turn_context",
                },
            )
        )
    trimming = [
        f"turn_context_truncated:{entry.get('admission_id', '')}:"
        f"{entry.get('reason', 'overflow')}"
        for entry in compilation.truncations
    ]
    return items, trimming


class TieredContextLoader:
    def load(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        persona: Any,
        session_id: str,
        total_context_budget: int | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> TieredContextResult:
        user_input = (
            getattr(interpretation, "normalized_text", "")
            or getattr(interpretation, "raw_text", "")
            or getattr(task, "task_summary", "")
        )
        corrections = _current_chat_corrections(
            session_id,
            current_user_text=user_input,
        )
        strategy = context_strategy(
            classification.get("task_class", "unknown"),
            context=getattr(interpretation, "as_context", lambda: {})(),
            user_input=user_input,
        )
        # The strategy's numbers are tuned for the smallest model this runtime can pick. Scaled to
        # whatever model is actually answering, so a turn on a large-context model is not given the
        # same shredded context as one on a 4k local model. Never shrinks -- an unknown window
        # leaves the declared values untouched.
        budget = normalize_budget(
            scale_budget_to_window(
                ContextBudget(
                    total_tokens=int(total_context_budget or strategy["total_context_budget"]),
                    bootstrap_tokens=int(strategy["bootstrap_budget"]),
                    relevant_tokens=int(strategy["relevant_budget"]),
                    cold_tokens=int(strategy["cold_budget"]),
                    max_bootstrap_items=int(strategy["max_bootstrap_items"]),
                    max_relevant_items=int(strategy["max_relevant_items"]),
                    max_cold_items=int(strategy["max_cold_items"]),
                ),
                available_prompt_tokens=available_prompt_tokens(source_context),
            )
        )

        include_private_imports = _source_allows_private_context(source_context)
        scoped_source_context = dict(source_context or {})
        query_text = user_input
        turn_context_compilation = None
        turn_context_trimming: list[str] = []
        topic_hints = list(getattr(interpretation, "topic_hints", []) or [])
        exact_recall_current_session_only = exact_recall_requires_current_session(query_text)
        adjacent_parent_user_turn_id = _adjacent_completed_user_turn_id(
            session_id,
            str(getattr(interpretation, "turn_id", "") or ""),
        )
        adjacent_tool_followup = _requests_adjacent_tool_state(query_text)
        scope_policy = resolve_memory_access_policy(
            chat_id=session_id,
        )
        include_current_chat = scope_policy.namespace_state == "active"
        user_profile_items = (
            _user_heuristic_items(
                query_text,
                topic_hints,
                session_id=session_id,
                policy=scope_policy,
            )
            if include_private_imports and scope_policy.allow_user_profile_context
            else []
        )
        history_selection = select_history_policy(
            scope_allows_transcript=include_current_chat,
            expansion_hint=getattr(interpretation, "is_continuation", None),
            authority_reason=f"chat_namespace_{scope_policy.namespace_state}",
        )
        source_authority = _resolve_context_source_authority(
            policy=scope_policy,
            include_current_chat=include_current_chat,
            include_private_imports=include_private_imports,
            user_profile_relevant=bool(user_profile_items),
            query_text=query_text,
            topic_hints=topic_hints,
            exact_recall_current_session_only=exact_recall_current_session_only,
            adjacent_tool_followup=adjacent_tool_followup,
            adjacent_parent_user_turn_id=adjacent_parent_user_turn_id,
        )
        bootstrap_candidates = build_bootstrap_context(
            persona=persona,
            task=task,
            classification=classification,
            interpretation=interpretation,
            session_id=session_id,
            source_context=scoped_source_context,
            include_private_context=include_current_chat,
            # Continuation classification may widen transcript selection only.  Session goals,
            # commitments, and mission slots are operational state and need their own authority.
            include_history_context=False,
            include_mission_context=source_authority.admits(
                ContextSource.MISSION_STATE
            ),
            include_user_profile_context=(
                include_private_imports
                and scope_policy.allow_user_profile_context
            ),
        )
        bootstrap_candidates, bootstrap_scope_excluded = annotate_and_filter(
            bootstrap_candidates,
            scope_policy,
        )
        bootstrap_candidates, bootstrap_correction_excluded = reconcile_context_candidates(
            bootstrap_candidates,
            corrections,
        )
        bootstrap_layer = budget_layer(
            bootstrap_candidates,
            token_budget=budget.bootstrap_tokens,
            max_items=budget.max_bootstrap_items,
        )

        if source_authority.admits(
            ContextSource.PERSISTENT_RETRIEVAL
        ) or source_authority.admits(ContextSource.SHARED_CONTEXT):
            local_items, local_candidates = _local_candidate_items(
                task,
                classification,
                policy=scope_policy,
                include_current_chat=include_current_chat,
            )
        else:
            local_items, local_candidates = [], []
        relevant_candidates = (
            []
            if exact_recall_current_session_only
            else list(local_items)
            if include_current_chat
            else []
        )
        if include_current_chat:
            # Authoritative exact values the current turn owns; used to drop conflicting cross-session
            # continuity below. Computed once per turn (guarded on private context) — empty when there
            # is no active mission, in which case continuity behavior is unchanged.
            mission_literals = (
                _current_mission_literals(session_id)
                if exact_recall_current_session_only
                else {}
            )
            if source_authority.admits(ContextSource.TOOL_STATE):
                relevant_candidates.extend(
                    _runtime_tool_observation_items(
                        session_id,
                        policy=scope_policy,
                        parent_user_turn_id=adjacent_parent_user_turn_id,
                    )
                )
            if history_selection.expands_beyond_adjacency:
                relevant_candidates.extend(_dialogue_items(session_id))
            persistent_items = (
                _persistent_memory_items(
                    query_text,
                    topic_hints,
                    session_id=session_id,
                    policy=scope_policy,
                    allow_instruction_memory=exact_recall_current_session_only,
                )
                if source_authority.admits(ContextSource.PERSISTENT_RETRIEVAL)
                else []
            )
            if source_authority.admits(ContextSource.PERSISTENT_RETRIEVAL):
                relevant_candidates.extend(
                    _memory_dispute_items(
                        query_text,
                        session_id=session_id,
                        policy=scope_policy,
                    )
                )
            if exact_recall_current_session_only:
                relevant_candidates.extend(_current_session_scoped_items(persistent_items, session_id=session_id))
            else:
                if source_authority.admits(ContextSource.USER_HEURISTIC):
                    relevant_candidates.extend(user_profile_items)
                relevant_candidates.extend(persistent_items)
                if source_authority.admits(ContextSource.SESSION_SUMMARY):
                    relevant_candidates.extend(
                        _session_summary_items(
                            query_text,
                            topic_hints,
                            session_id=session_id,
                            mission_literals=mission_literals,
                            policy=scope_policy,
                        )
                    )
                if source_authority.admits(ContextSource.SHARED_CONTEXT):
                    relevant_candidates.extend(_shared_swarm_context_items(query_text))
                if source_authority.admits(ContextSource.SHORTHAND):
                    relevant_candidates.extend(
                        _shorthand_items(
                            session_id,
                            getattr(interpretation, "reconstructed_text", "") or getattr(interpretation, "normalized_text", ""),
                        )
                    )
            # C09: scoped turn-context pages (this session's remembered
            # context plus explicitly project-granted pages), compiled and
            # gated by the turn-context authority. Current-session context
            # is exactly what these are, so they flow even under
            # exact-recall-current-session-only. Overflow the authority
            # truncated is recorded as typed trimming decisions.
            turn_context_compilation = _turn_context_compilation(
                session_id,
                query_text,
                source_context=scoped_source_context,
                total_context_budget=budget.total_tokens,
            )
            turn_context_items, turn_context_trimming = _turn_context_items(
                turn_context_compilation,
                session_id=session_id,
                project_id=str(scoped_source_context.get("_trusted_project_id") or ""),
            )
            relevant_candidates.extend(turn_context_items)
        if source_authority.admits(ContextSource.SHARED_CONTEXT):
            swarm_items, swarm_metadata, swarm_consulted = _swarm_metadata_items(
                task,
                classification,
                allow_swarm_metadata=bool(strategy.get("allow_swarm_metadata", False)),
                allow_swarm_fetch=bool(strategy.get("allow_swarm_fetch", False)),
            )
        else:
            swarm_items, swarm_metadata, swarm_consulted = [], [], False
        relevant_candidates.extend(swarm_items)
        relevant_candidates, scope_excluded = annotate_and_filter(relevant_candidates, scope_policy)
        relevant_candidates, correction_excluded = reconcile_context_candidates(
            relevant_candidates,
            corrections,
        )
        ranked_relevant = rank_context_items(
            relevant_candidates,
            query_text=getattr(task, "task_summary", ""),
            topic_hints=topic_hints,
            task_class=str(classification.get("task_class", "unknown")),
        )
        confidence_label, confidence_score = retrieval_confidence(ranked_relevant)
        relevant_layer = budget_layer(
            ranked_relevant,
            token_budget=budget.relevant_tokens,
            max_items=budget.max_relevant_items,
        )

        cold_decision = evaluate_cold_context_gate(
            user_text=getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", ""),
            task_class=str(classification.get("task_class", "unknown")),
            relevant_confidence_score=confidence_score,
            strategy=strategy,
        )
        source_authority = source_authority.with_relevance(
            ContextSource.COLD_CONTEXT,
            relevant=cold_decision.allow,
        )
        cold_candidates = (
            _cold_context_items(getattr(task, "task_summary", ""), session_id)
            if source_authority.admits(ContextSource.COLD_CONTEXT)
            else []
        )
        cold_candidates, cold_scope_excluded = annotate_and_filter(cold_candidates, scope_policy)
        scope_excluded.extend(cold_scope_excluded)
        cold_candidates, cold_correction_excluded = reconcile_context_candidates(
            cold_candidates,
            corrections,
        )
        ranked_cold = rank_context_items(
            cold_candidates,
            query_text=getattr(task, "task_summary", ""),
            topic_hints=list(getattr(interpretation, "topic_hints", []) or []),
            task_class=str(classification.get("task_class", "unknown")),
        )
        cold_layer = budget_layer(
            ranked_cold,
            token_budget=budget.cold_tokens if cold_decision.allow else 0,
            max_items=budget.max_cold_items,
        )

        candidate_items = [
            item.to_record(included=False, reason="retrieval_candidate")
            for item in (
                bootstrap_candidates + relevant_candidates + cold_candidates
            )
        ]
        access_policy = {
            "chat_id": scope_policy.chat_id,
            "project_id": scope_policy.project_id,
            "namespace_state": scope_policy.namespace_state,
            "grant_count": len(scope_policy.grants),
            "project_context_allowed": scope_policy.allow_project_context,
            "profile_context_allowed": scope_policy.allow_user_profile_context,
            "action_receipts_allowed": scope_policy.allow_action_receipts,
            "shared_context_allowed": scope_policy.allow_shared_context,
            "cold_context_allowed": scope_policy.allow_cold_context,
            "cross_chat_import_count": len(scope_policy.imported_chat_ids),
            "project_import_count": len(scope_policy.imported_project_ids),
            "history_authority": history_selection.authority_reason,
            "history_expansion": history_selection.expansion_reason,
            "tool_receipt_followup": adjacent_tool_followup,
            "tool_receipt_parent_turn_id": adjacent_parent_user_turn_id,
            "source_authority": source_authority.telemetry(),
        }

        report = PromptAssemblyReport(
            task_id=str(getattr(task, "task_id", "")),
            trace_id=str(
                scoped_source_context.get("request_id")
                or getattr(task, "task_id", "")
            ),
            total_context_budget=budget.total_tokens,
            bootstrap_budget=budget.bootstrap_tokens,
            relevant_budget=budget.relevant_tokens,
            cold_budget=budget.cold_tokens if cold_decision.allow else 0,
            bootstrap_tokens_used=bootstrap_layer.used_tokens,
            relevant_tokens_used=relevant_layer.used_tokens,
            cold_tokens_used=cold_layer.used_tokens,
            bootstrap_chars_used=bootstrap_layer.used_chars,
            relevant_chars_used=relevant_layer.used_chars,
            cold_chars_used=cold_layer.used_chars,
            swarm_metadata_consulted=swarm_consulted,
            cold_archive_opened=bool(cold_decision.allow and cold_layer.included),
            retrieval_confidence=confidence_label,
            chat_id=scope_policy.chat_id,
            project_id=scope_policy.project_id,
            # Context Capsule v2 is a retrieval packer, not an active persisted
            # capsule version. Shadow capsules are not injected.
            capsule_version="none",
            input_tokens=(bootstrap_layer.used_tokens + relevant_layer.used_tokens + cold_layer.used_tokens),
            reserved_output_tokens=max(0, budget.total_tokens - (bootstrap_layer.used_tokens + relevant_layer.used_tokens + cold_layer.used_tokens)),
            provider=str(scoped_source_context.get("provider") or scoped_source_context.get("provider_name") or ""),
            model=str(scoped_source_context.get("model") or scoped_source_context.get("model_name") or ""),
            access_policy=access_policy,
            candidate_items=candidate_items,
            context_manifest_required=True,
        )
        for item in bootstrap_layer.included + relevant_layer.included + cold_layer.included:
            report.items_included.append(item.to_record(included=True, reason=item.include_reason or "included"))
        for item, reason in (
            bootstrap_scope_excluded
            + bootstrap_correction_excluded
            + scope_excluded
            + correction_excluded
            + cold_correction_excluded
        ):
            report.items_excluded.append(item.to_record(included=False, reason=reason))
        # C09 receipt: the authority's explicit truncations and its honest
        # cache markers ride with the turn's provenance, so overflow is
        # disclosed in the manifest rather than silently absorbed.
        if turn_context_compilation is not None:
            report.trimming_decisions.extend(turn_context_trimming)
            if isinstance(source_context, dict):
                source_context["turn_context_receipt"] = (
                    turn_context_compilation.receipt()
                )
        _report_exclusions(report, bootstrap_layer)
        _report_exclusions(report, relevant_layer)
        _report_exclusions(report, cold_layer)
        report.stayed_under_budget = report.total_tokens_used() <= budget.total_tokens

        report.context_scopes = sorted(
            {
                str(item.get("metadata", {}).get("scope") or "")
                for item in report.items_included
                if str(item.get("metadata", {}).get("scope") or "").strip()
            }
        )
        _record_shadow_capsule_audit(
            report,
            bootstrap_items=bootstrap_layer.included,
            relevant_items=relevant_layer.included,
            cold_items=cold_layer.included,
        )
        manifest = build_context_manifest(
            task_id=report.task_id,
            trace_id=report.trace_id,
            evidence_items=[
                {"item_id": item.get("item_id"), "content_hash": item.get("provenance", {}).get("content_hash")}
                for item in report.items_included
            ],
            source_metadata=[
                {"item_id": item.get("item_id"), "source_type": item.get("source_type"), "scope": item.get("metadata", {}).get("scope")}
                for item in report.items_included
            ],
            redaction_markers=["context_scope_default_deny"],
            truncation_markers=list(report.trimming_decisions),
            chat_id=report.chat_id,
            project_id=report.project_id,
            context_scopes=report.context_scopes,
            selected_items=report.items_included,
            excluded_items=report.items_excluded,
            capsule_version=report.capsule_version,
            input_tokens=report.input_tokens,
            reserved_output_tokens=report.reserved_output_tokens,
            provider=report.provider,
            model=report.model,
            access_policy=report.access_policy,
            candidate_items=report.candidate_items,
        )
        store_manifest(manifest)
        report.context_manifest_id = manifest.manifest_id
        if isinstance(source_context, dict):
            source_context["context_manifest_id"] = manifest.manifest_id
            source_context["context_manifest_trace_id"] = manifest.trace_id

        record_context_access(report.to_dict())

        return TieredContextResult(
            bootstrap_items=bootstrap_layer.included,
            relevant_items=relevant_layer.included,
            cold_items=cold_layer.included,
            local_candidates=local_candidates,
            swarm_metadata=swarm_metadata,
            report=report,
            retrieval_confidence_score=confidence_score,
            cold_decision=cold_decision,
        )
