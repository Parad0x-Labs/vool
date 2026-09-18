from __future__ import annotations

import hashlib
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from core.brain_hive_artifacts import (
    count_artifact_manifests,
    get_artifact_manifest,
    list_artifact_manifests,
    load_artifact_manifest_payload,
)
from core.hardware_tier import recommended_ollama_model
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)

_log = logging.getLogger(__name__)

_LIVE_SMOKE_TAG_RE = re.compile(r"\[VOOL_SMOKE:[^\]]+\]\s*", re.IGNORECASE)
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_STAMP_TOKEN_RE = re.compile(r"\b20\d{6}T\d{6}Z\b", re.IGNORECASE)
_HEX_TOKEN_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)


def build_topic_research_packet(
    *,
    topic: Any,
    posts: list[Any],
    claims: list[Any],
) -> dict[str, Any]:
    topic_row = _as_dict(topic)
    post_rows = [_as_dict(item) for item in list(posts or [])]
    claim_rows = [_as_dict(item) for item in list(claims or [])]
    topic_id = str(topic_row.get("topic_id") or "")
    evidence_kind_counts: Counter[str] = Counter()
    post_kind_counts: Counter[str] = Counter()
    source_domains: Counter[str] = Counter()
    evidence_count = 0
    for post in post_rows:
        post_kind_counts[str(post.get("post_kind") or "analysis")] += 1
        for ref in list(post.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or ref.get("type") or "reference").strip() or "reference"
            evidence_kind_counts[kind] += 1
            evidence_count += 1
            for domain in _ref_domains(ref):
                source_domains[domain] += 1

    trading_feature_export = _extract_trading_feature_export(topic_row=topic_row, post_rows=post_rows)
    active_claims = [
        claim
        for claim in claim_rows
        if str(claim.get("status") or "").strip().lower() == "active"
    ]
    artifacts = list_artifact_manifests(topic_id=topic_id, limit=16)
    artifact_manifest_by_id = {
        str(item.get("artifact_id") or "").strip(): dict(item)
        for item in artifacts
        if str(item.get("artifact_id") or "").strip()
    }
    bundle_quality = _latest_bundle_quality(
        topic_row=topic_row,
        artifact_manifests=artifacts,
    )
    for domain, count in bundle_quality["source_domain_counts"].most_common():
        source_domains[domain] += int(count)
    artifact_refs = _collect_artifact_refs(
        topic_id=topic_id,
        post_rows=post_rows,
        artifact_manifest_by_id=artifact_manifest_by_id,
    )
    artifact_resolution = _summarize_artifact_resolution(artifact_refs)
    research_quality = _research_quality_summary(
        topic_row=topic_row,
        trading_feature_export=trading_feature_export,
        source_domain_count=len(source_domains),
        artifact_resolution=artifact_resolution,
        bundle_quality=bundle_quality,
    )
    latest_synthesis_card = _latest_synthesis_card(post_rows)
    packet = {
        "packet_schema": "brain_hive.research_packet.v1",
        "topic": {
            "topic_id": topic_id,
            "title": str(topic_row.get("title") or "Untitled topic"),
            "summary": str(topic_row.get("summary") or ""),
            "status": str(topic_row.get("status") or "open"),
            "visibility": str(topic_row.get("visibility") or ""),
            "evidence_mode": str(topic_row.get("evidence_mode") or ""),
            "linked_task_id": str(topic_row.get("linked_task_id") or ""),
            "topic_tags": [str(item) for item in list(topic_row.get("topic_tags") or []) if str(item).strip()][:16],
            "created_at": str(topic_row.get("created_at") or ""),
            "updated_at": str(topic_row.get("updated_at") or ""),
            "creator": {
                "agent_id": str(topic_row.get("created_by_agent_id") or ""),
                "display_name": str(topic_row.get("creator_display_name") or ""),
                "claim_label": str(topic_row.get("creator_claim_label") or ""),
            },
        },
        "execution_state": {
            "topic_status": str(topic_row.get("status") or "open"),
            "claim_count": len(claim_rows),
            "active_claim_count": len(active_claims),
            "execution_state": _execution_state(topic_row=topic_row, claim_rows=claim_rows),
            "artifact_count": count_artifact_manifests(topic_id=topic_id),
        },
        "counts": {
            "post_count": len(post_rows),
            "claim_count": len(claim_rows),
            "active_claim_count": len(active_claims),
            "evidence_count": evidence_count,
            "source_domain_count": len(source_domains),
            "nonempty_query_count": int(bundle_quality["nonempty_query_count"]),
            "dead_query_count": int(bundle_quality["dead_query_count"]),
            "promoted_finding_count": int(bundle_quality["promoted_finding_count"]),
            "mined_feature_count": int(bundle_quality["mined_feature_count"]),
            "offtopic_hit_count": int(bundle_quality["offtopic_hit_count"]),
        },
        "post_kind_counts": [
            {"kind": kind, "count": int(count)}
            for kind, count in post_kind_counts.most_common(8)
        ],
        "evidence_kind_counts": [
            {"kind": kind, "count": int(count)}
            for kind, count in evidence_kind_counts.most_common(12)
        ],
        "source_domains": [
            {"domain": domain, "count": int(count)}
            for domain, count in source_domains.most_common(16)
        ],
        "claims": [
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "agent_id": str(claim.get("agent_id") or ""),
                "agent_label": str(claim.get("agent_claim_label") or claim.get("agent_display_name") or ""),
                "status": str(claim.get("status") or ""),
                "note": str(claim.get("note") or ""),
                "capability_tags": [str(item) for item in list(claim.get("capability_tags") or []) if str(item).strip()][:12],
                "updated_at": str(claim.get("updated_at") or ""),
            }
            for claim in claim_rows
        ],
        "posts": [
            {
                "post_id": str(post.get("post_id") or ""),
                "post_kind": str(post.get("post_kind") or "analysis"),
                "stance": str(post.get("stance") or ""),
                "author_agent_id": str(post.get("author_agent_id") or ""),
                "author_label": str(post.get("author_claim_label") or post.get("author_display_name") or ""),
                "body": str(post.get("body") or ""),
                "created_at": str(post.get("created_at") or ""),
                "evidence_refs": [dict(ref) for ref in list(post.get("evidence_refs") or []) if isinstance(ref, dict)],
            }
            for post in post_rows
        ],
        "event_flow": _build_topic_event_flow(topic_row=topic_row, post_rows=post_rows, claim_rows=claim_rows),
        "derived_research_questions": derive_research_questions(
            topic_row=topic_row,
            claim_rows=claim_rows,
            evidence_kind_counts=evidence_kind_counts,
            trading_feature_export=trading_feature_export,
        ),
        "trading_feature_export": trading_feature_export,
        "artifacts": [
            {
                "artifact_id": str(item.get("artifact_id") or ""),
                "source_kind": str(item.get("source_kind") or ""),
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "tags": [str(tag) for tag in list(item.get("tags") or []) if str(tag).strip()][:12],
                "created_at": str(item.get("created_at") or ""),
                "storage_backend": str(item.get("storage_backend") or ""),
                "file_path": str(item.get("file_path") or ""),
            }
            for item in artifacts
        ],
        "artifact_refs": artifact_refs,
        "artifact_resolution_status": str(artifact_resolution.get("status") or "none"),
        "artifact_resolution": artifact_resolution,
        "nonempty_query_count": int(bundle_quality["nonempty_query_count"]),
        "dead_query_count": int(bundle_quality["dead_query_count"]),
        "promoted_finding_count": int(bundle_quality["promoted_finding_count"]),
        "mined_feature_count": int(bundle_quality["mined_feature_count"]),
        "research_quality_status": str(research_quality.get("status") or "insufficient_evidence"),
        "research_quality_reasons": list(research_quality.get("reasons") or [])[:8],
        "latest_synthesis_card": latest_synthesis_card,
        "synthesis_card_count": int(latest_synthesis_card.get("count") or 0),
        "latest_bundle_artifact_id": str(bundle_quality.get("bundle_artifact_id") or ""),
        "latest_bundle_created_at": str(bundle_quality.get("latest_bundle_created_at") or ""),
    }
    return packet


def build_research_queue_entry(
    *,
    topic: Any,
    posts: list[Any],
    claims: list[Any],
    commons_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = build_topic_research_packet(topic=topic, posts=posts, claims=claims)
    topic_row = dict(packet["topic"])
    trading_export = dict(packet.get("trading_feature_export") or {})
    priority, steering_reasons, commons_signal_strength = _research_priority(packet, commons_signal=commons_signal)
    return {
        "topic_id": str(topic_row.get("topic_id") or ""),
        "title": str(topic_row.get("title") or ""),
        "summary": str(topic_row.get("summary") or ""),
        "status": str(topic_row.get("status") or "open"),
        "topic_tags": [str(item) for item in list(topic_row.get("topic_tags") or []) if str(item).strip()][:16],
        "linked_task_id": str(topic_row.get("linked_task_id") or ""),
        "post_count": int(dict(packet.get("counts") or {}).get("post_count") or 0),
        "claim_count": int(dict(packet.get("counts") or {}).get("claim_count") or 0),
        "active_claim_count": int(dict(packet.get("counts") or {}).get("active_claim_count") or 0),
        "evidence_count": int(dict(packet.get("counts") or {}).get("evidence_count") or 0),
        "source_domain_count": int(dict(packet.get("counts") or {}).get("source_domain_count") or 0),
        "artifact_count": int(dict(packet.get("execution_state") or {}).get("artifact_count") or 0),
        "execution_state": str(dict(packet.get("execution_state") or {}).get("execution_state") or "open"),
        "artifact_resolution_status": str(packet.get("artifact_resolution_status") or "none"),
        "nonempty_query_count": int(packet.get("nonempty_query_count") or 0),
        "dead_query_count": int(packet.get("dead_query_count") or 0),
        "promoted_finding_count": int(packet.get("promoted_finding_count") or 0),
        "mined_feature_count": int(packet.get("mined_feature_count") or 0),
        "research_quality_status": str(packet.get("research_quality_status") or "insufficient_evidence"),
        "research_quality_reasons": list(packet.get("research_quality_reasons") or [])[:6],
        "research_priority": priority,
        "commons_signal_strength": commons_signal_strength,
        "steering_reasons": steering_reasons[:8],
        "commons_signal": dict(commons_signal or {}),
        "suggested_questions": list(packet.get("derived_research_questions") or [])[:6],
        "trading_signal_count": int(
            len(list(trading_export.get("heuristic_seed_signals") or []))
            + len(list(trading_export.get("hidden_edges") or []))
            + len(list(trading_export.get("discoveries") or []))
        ),
        "packet_schema": str(packet.get("packet_schema") or ""),
    }


def derive_research_questions(
    *,
    topic_row: dict[str, Any],
    claim_rows: list[dict[str, Any]],
    evidence_kind_counts: Counter[str],
    trading_feature_export: dict[str, Any],
) -> list[str]:
    title = str(topic_row.get("title") or "this topic").strip()
    summary = str(topic_row.get("summary") or "").strip()
    tags = [str(item).strip().lower() for item in list(topic_row.get("topic_tags") or []) if str(item).strip()]
    normalized_title = _normalize_research_subject(title)
    normalized_summary = _normalize_research_subject(summary)

    if _is_disposable_research_topic(
        raw_title=title,
        raw_summary=summary,
        normalized_title=normalized_title,
        normalized_summary=normalized_summary,
        tags=tags,
    ):
        return []

    if _live_question_derivation_enabled():
        model_questions = _derive_questions_via_model(
            title=normalized_title or title,
            summary=normalized_summary or summary,
            tags=tags,
            trading_feature_export=trading_feature_export,
        )
        if model_questions:
            return model_questions

    return _derive_questions_template(
        title=normalized_title or title,
        summary=normalized_summary or summary,
        tags=tags,
        trading_feature_export=trading_feature_export,
        evidence_kind_counts=evidence_kind_counts,
    )


def is_disposable_research_topic(
    *,
    title: str,
    summary: str,
    tags: list[str] | None = None,
) -> bool:
    return _is_disposable_research_topic(
        raw_title=title,
        raw_summary=summary,
        normalized_title=_normalize_research_subject(title),
        normalized_summary=_normalize_research_subject(summary),
        tags=[str(item).strip().lower() for item in list(tags or []) if str(item).strip()],
    )


def _normalize_research_subject(text: str) -> str:
    cleaned = str(text or "")
    cleaned = _LIVE_SMOKE_TAG_RE.sub("", cleaned)
    cleaned = _UUID_RE.sub(" ", cleaned)
    cleaned = _STAMP_TOKEN_RE.sub(" ", cleaned)
    cleaned = _HEX_TOKEN_RE.sub(" ", cleaned)
    cleaned = " ".join(cleaned.replace("_", " ").split()).strip()
    return cleaned


def _is_disposable_research_topic(
    *,
    raw_title: str,
    raw_summary: str,
    normalized_title: str,
    normalized_summary: str,
    tags: list[str],
) -> bool:
    del normalized_title, normalized_summary
    tag_set = {str(item or "").strip().lower() for item in list(tags or []) if str(item).strip()}
    haystack = f"{raw_title} {raw_summary}".lower()
    if "smoke" in tag_set and any(marker in haystack for marker in ("vool_smoke", "smoke verification", "disposable")):
        return True
    if "vool_smoke" in haystack or "[vool_smoke:" in haystack:
        return True
    return "disposable smoke" in haystack or "cleanup artifact" in haystack


def _derive_questions_via_model(
    *,
    title: str,
    summary: str,
    tags: list[str],
    trading_feature_export: dict[str, Any],
) -> list[str]:
    """Use the local LLM to generate focused, specific research questions."""
    base_url = _ollama_base_url()
    if not base_url:
        return []

    context_parts = [f"Topic: {title}"]
    if summary:
        context_parts.append(f"Summary: {summary}")
    if tags:
        context_parts.append(f"Tags: {', '.join(tags[:8])}")
    if trading_feature_export:
        context_parts.append("This topic involves trading/financial heuristic research.")

    prompt = (
        "You are a research assistant generating web search queries for a decentralized AI system.\n\n"
        f"{chr(10).join(context_parts)}\n\n"
        "Generate exactly 6 focused, specific web search queries that would find concrete, "
        "actionable evidence for this topic. Each query should target different aspects:\n"
        "1. Core technical implementation details\n"
        "2. Real-world examples, case studies, or benchmarks\n"
        "3. Known limitations, failure modes, or constraints\n"
        "4. Best practices or proven patterns from production systems\n"
        "5. Integration points, APIs, or tooling\n"
        "6. Troubleshooting or common pitfalls\n\n"
        "Requirements:\n"
        "- Each query must be specific enough to return relevant results (not generic)\n"
        "- Include technical terms, library names, or framework names when relevant\n"
        "- Avoid vague queries like 'best practices for X' without specifics\n"
        "- Each query should be a single line, no numbering\n\n"
        "Return ONLY the 6 queries, one per line, nothing else."
    )

    try:
        model = recommended_ollama_model()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 400,
        }
        permit = seal_direct_provider_invocation(
            provider_id="ollama:brain-hive-research",
            model_id=model,
            operation="research_question_generation",
            payload=payload,
            request_id=(
                "research-questions-"
                + hashlib.sha256(
                    f"{title}\n{summary}".encode()
                ).hexdigest()
            ),
            max_output_tokens=400,
            header_names=("Content-Type",),
        )
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            json=permit.consume(),
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        lines = [
            line.strip().lstrip("0123456789.-) ").strip()
            for line in text.splitlines()
            if line.strip() and len(line.strip()) > 10
        ]
        questions = list(dict.fromkeys(q[:240] for q in lines if q))[:6]
        if len(questions) >= 2:
            _log.info("Model-derived %d research questions for: %s", len(questions), title[:80])
            return questions
    except Exception as exc:
        _log.debug("Model research question generation failed, using templates: %s", exc)

    return []


def _ollama_base_url() -> str:
    """Resolve the local Ollama endpoint. Empty string if not reachable."""
    # The canonical LocalModelPolicy: disabled means no discovery probe either — callers
    # fall back to template-derived questions, exactly as when Ollama is unreachable.
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return ""
    from core.ollama_endpoint import ollama_base_url

    url = ollama_base_url()
    try:
        requests.get(f"{url}/api/tags", timeout=2)
        return url
    except Exception:
        return ""


def _live_question_derivation_enabled() -> bool:
    value = str(os.environ.get("VOOL_BRAIN_HIVE_LIVE_QUESTION_DERIVATION") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _derive_questions_template(
    *,
    title: str,
    summary: str,
    tags: list[str],
    trading_feature_export: dict[str, Any],
    evidence_kind_counts: Counter[str],
) -> list[str]:
    """Fallback: template-based question generation when model is unavailable."""
    questions: list[str] = []
    core_query = _sharp_core_query(title=title, summary=summary)
    if core_query:
        questions.append(core_query)
    implementation_query = _implementation_query(title=title, summary=summary, tags=tags)
    if implementation_query:
        questions.append(implementation_query)
    evidence_gap_query = _evidence_gap_query(title=title, summary=summary, tags=tags, evidence_kind_counts=evidence_kind_counts)
    if evidence_gap_query:
        questions.append(evidence_gap_query)
    comparison_query = _comparison_query(title=title, summary=summary, tags=tags)
    if comparison_query:
        questions.append(comparison_query)
    limitations_query = _limitations_query(title=title, summary=summary)
    if limitations_query:
        questions.append(limitations_query)
    if trading_feature_export:
        questions.append(f"Which exported trading features best explain misses or hidden edges in {title}?")
        for signal in list(trading_feature_export.get("heuristic_seed_signals") or [])[:2]:
            label = str(signal.get("label") or signal.get("metric") or signal.get("reason") or "").strip()
            if label:
                questions.append(f"How should {label} be researched or backtested before promotion?")
    deduped: list[str] = []
    seen: set[str] = set()
    for question in questions:
        clean = " ".join(str(question or "").split()).strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean[:240])
    return deduped[:6]


def _sharp_core_query(*, title: str, summary: str) -> str:
    summary_terms = _important_terms(summary, limit=5)
    if summary_terms:
        return f'"{title}" {" ".join(summary_terms)}'
    return f'"{title}"'


def _implementation_query(*, title: str, summary: str, tags: list[str]) -> str:
    lowered = f"{title} {summary}".lower()
    if {"design", "ux", "ui"} & set(tags) or any(token in lowered for token in ("ux", "ui", "watcher", "workflow", "task flow")):
        return f"{title} UX design patterns real-world examples 2025 2026"
    if {"integration", "api", "bot", "automation", "script"} & set(tags) or any(
        token in lowered for token in ("api", "integration", "automation", "script", "bot")
    ):
        return f"{title} implementation tutorial GitHub example code"
    if any(token in lowered for token in ("trading", "crypto", "solana", "defi", "token")):
        return f"{title} strategy backtest results performance data"
    if any(token in lowered for token in ("cold email", "marketing", "sales", "outreach")):
        return f"{title} proven templates conversion rates case study"
    return f"{title} how to implement step by step guide"


def _evidence_gap_query(
    *,
    title: str,
    summary: str,
    tags: list[str],
    evidence_kind_counts: Counter[str],
) -> str:
    del evidence_kind_counts
    summary_terms = _important_terms(summary, limit=3)
    tag_str = " ".join(tags[:4])
    if "script" in tag_str or "automation" in tag_str:
        return f"{title} benchmark results comparison {' '.join(summary_terms)}".strip()
    if "trading" in tag_str or "crypto" in f"{title} {summary}".lower():
        return f"{title} risk analysis common mistakes failures {' '.join(summary_terms)}".strip()
    return f"{title} real examples case studies {' '.join(summary_terms)} site:reddit.com OR site:github.com".strip()


def _comparison_query(*, title: str, summary: str, tags: list[str]) -> str:
    lowered = f"{title} {summary}".lower()
    if any(token in lowered for token in ("tool", "framework", "library", "platform", "service")):
        return f"{title} alternatives comparison pros cons 2025 2026"
    if any(token in lowered for token in ("strategy", "approach", "method", "technique")):
        return f"{title} vs alternatives which is better comparison"
    return f"{title} expert opinions discussion site:reddit.com OR site:news.ycombinator.com"


def _limitations_query(*, title: str, summary: str) -> str:
    lowered = f"{title} {summary}".lower()
    if any(token in lowered for token in ("ai", "model", "llm", "agent", "autonomous")):
        return f"{title} limitations challenges pitfalls lessons learned"
    return f"{title} common problems troubleshooting FAQ"


def _research_priority(packet: dict[str, Any], *, commons_signal: dict[str, Any] | None = None) -> tuple[float, list[str], float]:
    topic = dict(packet.get("topic") or {})
    state = dict(packet.get("execution_state") or {})
    counts = dict(packet.get("counts") or {})
    tags = {str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()}
    priority = 0.20
    steering_reasons: list[str] = []
    status = str(topic.get("status") or "open").strip().lower()
    if status == "open":
        priority += 0.26
        steering_reasons.append("topic_open")
    elif status == "researching":
        priority += 0.18
        steering_reasons.append("topic_researching")
    elif status == "disputed":
        priority += 0.12
        steering_reasons.append("topic_disputed")
    if int(state.get("active_claim_count") or 0) == 0:
        priority += 0.24
        steering_reasons.append("no_active_claims")
    if int(counts.get("evidence_count") or 0) <= 2:
        priority += 0.10
        steering_reasons.append("thin_evidence")
    if {"trading_learning", "manual_trader", "agent_commons"} & tags:
        priority += 0.08
        steering_reasons.append("priority_lane")
    if dict(packet.get("trading_feature_export") or {}):
        priority += 0.08
        steering_reasons.append("trading_signal_export")
    if int(state.get("artifact_count") or 0) == 0:
        priority += 0.04
        steering_reasons.append("no_artifacts")

    commons_signal_strength = _commons_signal_strength(commons_signal or {})
    if commons_signal_strength > 0.0:
        priority += min(0.22, commons_signal_strength * 0.22)
        steering_reasons.extend(
            reason
            for reason in list((commons_signal or {}).get("reasons") or [])
            if isinstance(reason, str) and reason.strip()
        )

    deduped_reasons: list[str] = []
    seen: set[str] = set()
    for reason in steering_reasons:
        clean = str(reason or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped_reasons.append(clean)
    return round(max(0.0, min(1.0, priority)), 4), deduped_reasons, commons_signal_strength


def _commons_signal_strength(commons_signal: dict[str, Any]) -> float:
    if not commons_signal:
        return 0.0
    candidate_count = max(0, int(commons_signal.get("candidate_count") or 0))
    review_required_count = max(0, int(commons_signal.get("review_required_count") or 0))
    approved_count = max(0, int(commons_signal.get("approved_count") or 0))
    promoted_count = max(0, int(commons_signal.get("promoted_count") or 0))
    top_score = max(0.0, float(commons_signal.get("top_score") or 0.0))
    support_weight = max(0.0, float(commons_signal.get("support_weight") or 0.0))
    challenge_weight = max(0.0, float(commons_signal.get("challenge_weight") or 0.0))
    training_signal_count = max(0, int(commons_signal.get("training_signal_count") or 0))
    downstream_use_count = max(0, int(commons_signal.get("downstream_use_count") or 0))

    strength = 0.0
    strength += min(0.18, candidate_count * 0.04)
    strength += min(0.20, review_required_count * 0.08)
    strength += min(0.24, approved_count * 0.10)
    strength += min(0.24, promoted_count * 0.12)
    strength += min(0.18, top_score * 0.04)
    strength += min(0.10, max(0.0, support_weight - challenge_weight) * 0.04)
    strength += min(0.08, training_signal_count * 0.02)
    strength += min(0.08, downstream_use_count * 0.02)
    strength -= min(0.18, max(0.0, challenge_weight - support_weight) * 0.05)
    return round(max(0.0, min(1.0, strength)), 4)


def _execution_state(*, topic_row: dict[str, Any], claim_rows: list[dict[str, Any]]) -> str:
    status = str(topic_row.get("status") or "open").strip().lower()
    if status in {"solved", "closed", "partial", "needs_improvement"}:
        return status
    active_claims = [
        claim
        for claim in claim_rows
        if str(claim.get("status") or "").strip().lower() == "active"
    ]
    if active_claims:
        return "claimed"
    if status == "researching":
        return "idle_researching"
    if status == "disputed":
        return "disputed"
    return "open"


def _build_topic_event_flow(
    *,
    topic_row: dict[str, Any],
    post_rows: list[dict[str, Any]],
    claim_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "event_type": "topic_created",
            "timestamp": str(topic_row.get("created_at") or ""),
            "detail": str(topic_row.get("summary") or ""),
            "status": str(topic_row.get("status") or "open"),
        }
    ]
    for claim in claim_rows:
        events.append(
            {
                "event_type": f"claim_{str(claim.get('status') or 'active').strip().lower()}",
                "timestamp": str(claim.get("updated_at") or claim.get("created_at") or ""),
                "detail": str(claim.get("note") or ""),
                "claim_id": str(claim.get("claim_id") or ""),
                "agent_label": str(claim.get("agent_claim_label") or claim.get("agent_display_name") or claim.get("agent_id") or ""),
            }
        )
    for post in post_rows:
        meta = _task_event_meta(post)
        events.append(
            {
                "event_type": str(meta.get("event_type") or f"post_{post.get('post_kind') or 'analysis'!s}"),
                "timestamp": str(post.get("created_at") or ""),
                "detail": str(post.get("body") or ""),
                "post_kind": str(post.get("post_kind") or "analysis"),
                "progress_state": str(meta.get("progress_state") or ""),
                "claim_id": str(meta.get("claim_id") or ""),
            }
        )
    return sorted(events, key=lambda row: str(row.get("timestamp") or ""), reverse=True)[:32]


def _task_event_meta(post: dict[str, Any]) -> dict[str, Any]:
    for ref in list(post.get("evidence_refs") or []):
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "task_event":
            return dict(ref)
    return {}


def _extract_trading_feature_export(*, topic_row: dict[str, Any], post_rows: list[dict[str, Any]]) -> dict[str, Any]:
    tags = {str(item).strip().lower() for item in list(topic_row.get("topic_tags") or []) if str(item).strip()}
    title = str(topic_row.get("title") or "").lower()
    summary = str(topic_row.get("summary") or "").lower()
    if "trading_learning" not in tags and "manual_trader" not in tags and "trading" not in f"{title} {summary}":
        return {}

    latest_summary: dict[str, Any] = {}
    latest_heartbeat: dict[str, Any] = {}
    lab_summary: dict[str, Any] = {}
    decision_funnel: dict[str, Any] = {}
    pattern_health: dict[str, Any] = {}
    calls_by_key: dict[str, dict[str, Any]] = {}
    missed_by_key: dict[str, dict[str, Any]] = {}
    edges_by_key: dict[str, dict[str, Any]] = {}
    discoveries_by_key: dict[str, dict[str, Any]] = {}
    flow_rows: list[dict[str, Any]] = []
    lessons: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    for post in post_rows:
        for ref in list(post.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "").strip().lower()
            if kind == "trading_learning_summary" and isinstance(ref.get("summary"), dict):
                latest_summary = dict(ref.get("summary") or {})
            elif kind == "trading_runtime_heartbeat" and isinstance(ref.get("heartbeat"), dict):
                latest_heartbeat = dict(ref.get("heartbeat") or {})
            elif kind == "trading_learning_lab_summary" and isinstance(ref.get("summary"), dict):
                lab_summary = dict(ref.get("summary") or {})
            elif kind == "trading_decision_funnel" and isinstance(ref.get("summary"), dict):
                decision_funnel = dict(ref.get("summary") or {})
            elif kind == "trading_pattern_health" and isinstance(ref.get("summary"), dict):
                pattern_health = dict(ref.get("summary") or {})
            elif kind == "trading_calls":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("token_mint") or item.get("call_id") or "").strip()
                        if key:
                            calls_by_key[key] = _merge_sparse(calls_by_key.get(key), item)
            elif kind == "trading_missed_mooners":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("id") or item.get("token_mint") or "").strip()
                        if key:
                            missed_by_key[key] = _merge_sparse(missed_by_key.get(key), item)
            elif kind == "trading_hidden_edges":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("id") or item.get("metric") or "").strip()
                        if key:
                            edges_by_key[key] = _merge_sparse(edges_by_key.get(key), item)
            elif kind == "trading_discoveries":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("id") or item.get("discovery") or "").strip()
                        if key:
                            discoveries_by_key[key] = _merge_sparse(discoveries_by_key.get(key), item)
            elif kind == "trading_live_flow":
                flow_rows.extend([dict(item) for item in list(ref.get("items") or []) if isinstance(item, dict)])
            elif kind == "trading_lessons":
                lessons.extend([dict(item) for item in list(ref.get("items") or []) if isinstance(item, dict)])
            elif kind == "trading_ath_updates":
                updates.extend([dict(item) for item in list(ref.get("items") or []) if isinstance(item, dict)])

    hidden_edges = sorted(edges_by_key.values(), key=lambda row: float(row.get("score", 0.0) or 0.0), reverse=True)[:20]
    discoveries = sorted(discoveries_by_key.values(), key=lambda row: float(row.get("ts", 0.0) or 0.0), reverse=True)[:20]
    flow = sorted(flow_rows, key=lambda row: float(row.get("ts", 0.0) or 0.0), reverse=True)[:30]
    flow_reason_counts: Counter[str] = Counter()
    for item in flow:
        reason = str(item.get("detail") or item.get("kind") or "unknown").strip()
        if reason:
            flow_reason_counts[reason] += 1
    heuristic_seed_signals = []
    for edge in hidden_edges[:6]:
        metric = str(edge.get("metric") or edge.get("id") or "").strip()
        heuristic_seed_signals.append(
            {
                "kind": "hidden_edge",
                "metric": metric,
                "label": metric or "hidden_edge",
                "score": float(edge.get("score", 0.0) or 0.0),
                "support": int(edge.get("support", 0) or 0),
            }
        )
    for reason, count in flow_reason_counts.most_common(4):
        heuristic_seed_signals.append(
            {
                "kind": "flow_reason",
                "reason": reason,
                "label": reason,
                "count": int(count),
            }
        )
    return {
        "latest_summary": latest_summary,
        "latest_heartbeat": latest_heartbeat,
        "lab_summary": lab_summary,
        "decision_funnel": decision_funnel,
        "pattern_health": pattern_health,
        "calls": sorted(calls_by_key.values(), key=lambda row: float(row.get("call_ts", 0.0) or 0.0), reverse=True)[:40],
        "missed_mooners": sorted(missed_by_key.values(), key=lambda row: float(row.get("ts", 0.0) or 0.0), reverse=True)[:20],
        "hidden_edges": hidden_edges,
        "discoveries": discoveries,
        "flow": flow,
        "lessons": lessons[:12],
        "updates": updates[:12],
        "flow_reason_counts": [{"reason": reason, "count": int(count)} for reason, count in flow_reason_counts.most_common(8)],
        "heuristic_seed_signals": heuristic_seed_signals[:10],
    }


def _merge_sparse(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in incoming.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _ref_domains(ref: dict[str, Any]) -> list[str]:
    domains: list[str] = []
    for key in ("url", "href", "value", "source_url"):
        raw = str(ref.get(key) or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            domain = str(urlsplit(raw).netloc or "").strip().lower()
            if domain:
                domains.append(domain)
    return list(dict.fromkeys(domains))


def _collect_artifact_refs(
    *,
    topic_id: str,
    post_rows: list[dict[str, Any]],
    artifact_manifest_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    refs_by_id: dict[str, dict[str, Any]] = {}
    for post in post_rows:
        for ref in list(post.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            artifact_id = str(ref.get("artifact_id") or "").strip()
            if not artifact_id:
                continue
            manifest = dict(artifact_manifest_by_id.get(artifact_id) or get_artifact_manifest(artifact_id) or {})
            file_path = str(manifest.get("file_path") or ref.get("file_path") or "").strip()
            exists_local = bool(file_path and Path(file_path).expanduser().is_file())
            exists_public_index = bool(manifest)
            refs_by_id[artifact_id] = {
                "artifact_id": artifact_id,
                "kind": str(ref.get("kind") or "").strip(),
                "source_kind": str(manifest.get("source_kind") or "").strip(),
                "topic_id": str(manifest.get("topic_id") or topic_id or "").strip(),
                "title": str(manifest.get("title") or "").strip(),
                "storage_backend": str(manifest.get("storage_backend") or ref.get("storage_backend") or "").strip(),
                "content_sha256": str(manifest.get("content_sha256") or ref.get("content_sha256") or "").strip(),
                "resolvable_path": file_path,
                "exists_local": exists_local,
                "exists_public_index": exists_public_index,
                "public_search_hit": exists_public_index,
                "surfaceable": exists_public_index,
                "failure_reason": _artifact_failure_reason(
                    exists_local=exists_local,
                    exists_public_index=exists_public_index,
                    file_path=file_path,
                ),
                "created_at": str(manifest.get("created_at") or post.get("created_at") or "").strip(),
            }
    rows = list(refs_by_id.values())
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("artifact_id") or "")), reverse=True)
    return rows[:24]


def _artifact_failure_reason(*, exists_local: bool, exists_public_index: bool, file_path: str) -> str:
    if not exists_public_index:
        return "artifact_not_indexed_on_this_node"
    if file_path and not exists_local:
        return "artifact_path_missing"
    return ""


def _summarize_artifact_resolution(artifact_refs: list[dict[str, Any]]) -> dict[str, Any]:
    total_refs = len(list(artifact_refs or []))
    resolved_count = sum(1 for ref in list(artifact_refs or []) if not str(ref.get("failure_reason") or "").strip())
    local_missing_count = sum(1 for ref in list(artifact_refs or []) if str(ref.get("failure_reason") or "") == "artifact_path_missing")
    public_index_missing_count = sum(
        1
        for ref in list(artifact_refs or [])
        if str(ref.get("failure_reason") or "") == "artifact_not_indexed_on_this_node"
    )
    unresolved_count = total_refs - resolved_count
    if total_refs <= 0:
        status = "none"
    elif unresolved_count <= 0:
        status = "resolved"
    elif resolved_count > 0:
        status = "partial"
    else:
        status = "missing"
    return {
        "status": status,
        "total_refs": total_refs,
        "resolved_count": resolved_count,
        "unresolved_count": unresolved_count,
        "local_missing_count": local_missing_count,
        "public_index_missing_count": public_index_missing_count,
    }


def _latest_bundle_quality(
    *,
    topic_row: dict[str, Any],
    artifact_manifests: list[dict[str, Any]],
) -> dict[str, Any]:
    bundle_rows = [
        dict(item)
        for item in list(artifact_manifests or [])
        if str(item.get("source_kind") or "").strip() == "research_bundle"
    ]
    bundle_rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    payload: dict[str, Any] = {}
    bundle_manifest: dict[str, Any] = {}
    for manifest in bundle_rows:
        loaded = load_artifact_manifest_payload(manifest=manifest)
        if isinstance(loaded, dict):
            payload = dict(loaded)
            bundle_manifest = dict(manifest)
            break
    query_results = [dict(item) for item in list(payload.get("query_results") or []) if isinstance(item, dict)]
    source_domain_counts: Counter[str] = Counter()
    offtopic_hit_count = 0
    for item in query_results:
        for domain in _query_result_domains(item):
            source_domain_counts[domain] += 1
        if _query_result_looks_off_topic(topic_row=topic_row, query_result=item):
            offtopic_hit_count += 1
    promotion_decisions = [dict(item) for item in list(payload.get("promotion_decisions") or []) if isinstance(item, dict)]
    mined_features = dict(payload.get("mined_features") or {})
    nonempty_query_count = sum(1 for item in query_results if _query_result_has_evidence(item))
    queries_total = len(query_results)
    return {
        "bundle_artifact_id": str(bundle_manifest.get("artifact_id") or ""),
        "latest_bundle_created_at": str(bundle_manifest.get("created_at") or ""),
        "queries_total": queries_total,
        "nonempty_query_count": nonempty_query_count,
        "dead_query_count": max(0, queries_total - nonempty_query_count),
        "promoted_finding_count": sum(
            1 for item in promotion_decisions if bool(dict(item.get("gate") or {}).get("can_promote"))
        ),
        "mined_feature_count": _mined_feature_count(mined_features),
        "offtopic_hit_count": offtopic_hit_count,
        "source_domain_counts": source_domain_counts,
    }


def _query_result_has_evidence(query_result: dict[str, Any]) -> bool:
    if str(query_result.get("relevance_status") or "").strip().lower() == "off_topic":
        return False
    if int(query_result.get("snippet_count") or 0) > 0:
        return True
    return bool(str(query_result.get("summary") or "").strip())


def _query_result_domains(query_result: dict[str, Any]) -> list[str]:
    domains = [str(item or "").strip().lower() for item in list(query_result.get("source_domains") or []) if str(item or "").strip()]
    return list(dict.fromkeys(domains))


def _query_result_looks_off_topic(*, topic_row: dict[str, Any], query_result: dict[str, Any]) -> bool:
    if str(query_result.get("relevance_status") or "").strip().lower() == "off_topic":
        return True
    if not _query_result_has_evidence(query_result):
        return False
    haystack_tokens = _topic_tokens(
        " ".join(
            [
                str(topic_row.get("title") or ""),
                str(topic_row.get("summary") or ""),
                str(query_result.get("query") or ""),
            ]
        )
    )
    summary_tokens = _topic_tokens(str(query_result.get("summary") or ""))
    if not haystack_tokens or not summary_tokens:
        return False
    overlap = len(haystack_tokens & summary_tokens)
    generic_nav_hits = sum(
        1
        for marker in (
            "skip navigation",
            "get started",
            "platforms",
            "components",
            "documentation",
            "wear os",
        )
        if marker in str(query_result.get("summary") or "").lower()
    )
    return overlap < 2 and generic_nav_hits > 0


def _latest_synthesis_card(post_rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    latest_timestamp = ""
    count = 0
    for post in post_rows:
        timestamp = str(post.get("created_at") or "")
        for ref in list(post.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            if str(ref.get("kind") or "").strip().lower() != "research_synthesis_card":
                continue
            count += 1
            if timestamp >= latest_timestamp:
                latest = {
                    "question": str(ref.get("question") or "").strip(),
                    "searched": [str(item) for item in list(ref.get("searched") or []) if str(item).strip()][:4],
                    "found": [str(item) for item in list(ref.get("found") or []) if str(item).strip()][:4],
                    "source_domains": [str(item) for item in list(ref.get("source_domains") or []) if str(item).strip()][:8],
                    "artifacts": [dict(item) for item in list(ref.get("artifacts") or []) if isinstance(item, dict)][:6],
                    "promoted_findings": [str(item) for item in list(ref.get("promoted_findings") or []) if str(item).strip()][:6],
                    "confidence": str(ref.get("confidence") or "").strip(),
                    "blockers": [str(item) for item in list(ref.get("blockers") or []) if str(item).strip()][:6],
                    "state_token": str(ref.get("state_token") or "").strip(),
                    "created_at": timestamp,
                }
                latest_timestamp = timestamp
    if latest:
        latest["count"] = count
    return latest


def _important_terms(text: str, *, limit: int) -> list[str]:
    stopwords = {
        "about",
        "agent",
        "agents",
        "better",
        "build",
        "flow",
        "have",
        "improve",
        "into",
        "make",
        "need",
        "vool",
        "task",
        "tasks",
        "that",
        "this",
        "topic",
        "watcher",
        "with",
    }
    terms: list[str] = []
    for token in _topic_tokens(text):
        if token in stopwords:
            continue
        terms.append(token)
    return terms[: max(0, int(limit))]


def _topic_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in str(text or "").lower()).split()
        if len(token) >= 4
    }
    return tokens


def _mined_feature_count(mined_features: dict[str, Any]) -> int:
    return (
        len(list(mined_features.get("feature_rows") or []))
        + len(list(mined_features.get("heuristic_candidates") or []))
        + len(list(mined_features.get("script_ideas") or []))
    )


def _research_quality_summary(
    *,
    topic_row: dict[str, Any],
    trading_feature_export: dict[str, Any],
    source_domain_count: int,
    artifact_resolution: dict[str, Any],
    bundle_quality: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    nonempty_query_count = int(bundle_quality.get("nonempty_query_count") or 0)
    dead_query_count = int(bundle_quality.get("dead_query_count") or 0)
    promoted_finding_count = int(bundle_quality.get("promoted_finding_count") or 0)
    offtopic_hit_count = int(bundle_quality.get("offtopic_hit_count") or 0)
    unresolved_artifacts = int(artifact_resolution.get("unresolved_count") or 0)
    explicit_local_only = bool(trading_feature_export) and str(topic_row.get("evidence_mode") or "").strip().lower() in {
        "mixed",
        "local_only",
        "internal_only",
        "candidate_only",
    }
    if unresolved_artifacts > 0:
        reasons.append(f"Artifacts unresolved: {unresolved_artifacts}.")
    if nonempty_query_count <= 0:
        reasons.append("No non-empty research queries produced evidence.")
    elif nonempty_query_count < 2:
        reasons.append(f"Only {nonempty_query_count} research query returned usable evidence.")
    if dead_query_count > 0:
        reasons.append(f"{dead_query_count} research queries returned no usable evidence.")
    if source_domain_count < 2 and not explicit_local_only:
        reasons.append("Distinct source domains are below the grounded threshold.")
    if promoted_finding_count <= 0:
        reasons.append("No promoted findings passed the evidence gate.")
    if offtopic_hit_count > 0:
        reasons.append(f"Detected {offtopic_hit_count} off-topic research contamination hit(s).")

    if unresolved_artifacts > 0:
        status = "artifact_missing"
    elif nonempty_query_count <= 0:
        status = "query_failed"
    elif offtopic_hit_count >= max(1, nonempty_query_count):
        status = "off_topic"
    elif nonempty_query_count < 2 or (source_domain_count < 2 and not explicit_local_only):
        status = "insufficient_evidence"
    elif promoted_finding_count <= 0 or dead_query_count > 0:
        status = "partial"
    else:
        status = "grounded"

    return {
        "status": status,
        "reasons": reasons[:8],
    }


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    return dict(value)
