from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any

from core import audit_logger
from core.brain_hive_artifacts import store_artifact_manifest
from core.brain_hive_research import is_disposable_research_topic
from core.candidate_knowledge_lane import get_candidate_by_id
from core.curiosity_roamer import CuriosityRoamer
from core.hive_activity_tracker import HiveActivityTracker
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)
from core.public_hive_bridge import PublicHiveBridge
from core.research_promotion_gate import evaluate_research_promotion_candidate
from core.runtime_continuity import append_runtime_event
from core.trading_feature_miner import mine_exported_trading_features
from network.signer import get_local_peer_id

_log = logging.getLogger(__name__)


@dataclass
class AutonomousResearchResult:
    ok: bool
    status: str
    topic_id: str = ""
    claim_id: str = ""
    result_status: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    promotion_decisions: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "topic_id": self.topic_id,
            "claim_id": self.claim_id,
            "result_status": self.result_status,
            "artifact_ids": list(self.artifact_ids),
            "candidate_ids": list(self.candidate_ids),
            "promotion_decisions": [dict(item) for item in self.promotion_decisions],
            "response_text": self.response_text,
            "details": dict(self.details),
        }


def research_topic_from_signal(
    signal: dict[str, Any] | str,
    *,
    public_hive_bridge: PublicHiveBridge,
    curiosity: CuriosityRoamer | None = None,
    hive_activity_tracker: HiveActivityTracker | None = None,
    session_id: str | None = None,
    auto_claim: bool = True,
) -> AutonomousResearchResult:
    if not public_hive_bridge.enabled():
        return AutonomousResearchResult(
            ok=False,
            status="disabled",
            response_text="Public Hive bridge is disabled on this runtime.",
        )
    signal_row = dict(signal) if isinstance(signal, dict) else {"topic_id": str(signal or "").strip()}
    topic_id = str(signal_row.get("topic_id") or "").strip()
    if not topic_id:
        return AutonomousResearchResult(
            ok=False,
            status="missing_topic_id",
            response_text="Autonomous research needs a concrete topic_id.",
        )
    event_session = str(session_id or f"auto-research:{topic_id}")
    _event(
        event_session,
        "task_received",
        f"Autonomous research queued for topic {topic_id}.",
        topic_id=topic_id,
        task_class="autonomous_research",
    )

    packet = public_hive_bridge.get_public_research_packet(topic_id)
    if not packet:
        return AutonomousResearchResult(
            ok=False,
            status="missing_packet",
            topic_id=topic_id,
            response_text=f"Research packet for topic `{topic_id}` is unavailable.",
        )
    topic = dict(packet.get("topic") or {})
    claims = [dict(item) for item in list(packet.get("claims") or [])]
    title = str(topic.get("title") or topic_id)
    _event(
        event_session,
        "task_classified",
        f"Loaded machine-readable research packet for {title}.",
        topic_id=topic_id,
        request_preview=title,
        execution_state=str(dict(packet.get("execution_state") or {}).get("execution_state") or ""),
    )

    local_peer_id = get_local_peer_id()
    active_claims = [
        claim
        for claim in claims
        if str(claim.get("status") or "").strip().lower() == "active"
    ]
    foreign_active = [claim for claim in active_claims if str(claim.get("agent_id") or "").strip() != local_peer_id]

    claim_id = ""
    our_claim = next(
        (
            claim
            for claim in active_claims
            if str(claim.get("agent_id") or "").strip() == local_peer_id
        ),
        None,
    )
    if our_claim:
        claim_id = str(our_claim.get("claim_id") or "")
    elif auto_claim:
        claim_result = public_hive_bridge.claim_public_topic(
            topic_id=topic_id,
            note=(
                "Autonomous research lane claimed this topic for packet export, external research, and heuristic mining."
                if not foreign_active
                else "Parallel autonomous research lane joined this topic for replication, independent evidence, and heuristic mining."
            ),
            capability_tags=_claim_tags(topic),
            idempotency_key=_idempotency_key("auto-claim", topic_id),
        )
        if not claim_result.get("ok"):
            return AutonomousResearchResult(
                ok=False,
                status=str(claim_result.get("status") or "claim_failed"),
                topic_id=topic_id,
                response_text=f"Failed to claim topic `{topic_id}` for autonomous research.",
                details=dict(claim_result),
            )
        claim_id = str(claim_result.get("claim_id") or "")
        _event(
            event_session,
            "tool_executed",
            f"Claimed topic {topic_id} for autonomous research.",
            topic_id=topic_id,
            topic_title=title,
            claim_id=claim_id,
            tool_name="hive.claim_task",
        )
        if hive_activity_tracker is not None:
            with contextlib.suppress(Exception):
                hive_activity_tracker.note_watched_topic(session_id=event_session, topic_id=topic_id)

    public_hive_bridge.post_public_topic_progress(
        topic_id=topic_id,
        body=(
            (
                f"Parallel autonomous research started for {title}. "
                "Another agent already has an active claim, so this lane is adding replication, independent evidence, and bounded external research."
            )
            if foreign_active
            else (
                f"Autonomous research started for {title}. "
                "Building a machine-readable packet, mining exported signals, and running bounded external research."
            )
        ),
        progress_state="started",
        claim_id=claim_id or None,
        evidence_refs=[
            {
                "kind": "autonomous_research_state",
                "state": "started",
                "topic_id": topic_id,
                "parallel_lane": bool(foreign_active),
            }
        ],
        idempotency_key=_idempotency_key("auto-progress-start", topic_id),
    )

    packet_artifact = store_artifact_manifest(
        source_kind="hive_topic_packet",
        title=f"Research packet: {title}",
        summary=f"Machine-readable research packet for {title}.",
        payload=packet,
        topic_id=topic_id,
        claim_id=claim_id or None,
        session_id=event_session,
        tags=[*list(topic.get("topic_tags") or []), "research_packet"],
        metadata={"packet_schema": packet.get("packet_schema"), "topic_status": topic.get("status")},
    )
    _event(
        event_session,
        "tool_executed",
        f"Packed research packet artifact {packet_artifact['artifact_id']}.",
        topic_id=topic_id,
        topic_title=title,
        artifact_id=packet_artifact["artifact_id"],
        artifact_role="packet",
        tool_name="liquefy.pack_research_packet",
    )

    miner_output = mine_exported_trading_features(packet)
    query_results: list[dict[str, Any]] = []
    candidate_ids: list[str] = []
    roamer = curiosity or CuriosityRoamer()
    derived_queries = list(packet.get("derived_research_questions") or [])[:6]
    skip_reason = _external_research_skip_reason(packet=packet, derived_queries=derived_queries)

    if skip_reason:
        _event(
            event_session,
            "tool_started",
            skip_reason,
            topic_id=topic_id,
            topic_title=title,
            tool_name="research.skip_external_topic",
        )
    else:
        query_results, candidate_ids = _run_research_queries(
            queries=derived_queries,
            roamer=roamer,
            topic=topic,
            topic_id=topic_id,
            title=title,
            packet=packet,
            event_session=event_session,
            pass_label="pass-1",
        )

    nonempty_first = sum(1 for r in query_results if _query_result_has_evidence(r))
    if not skip_reason and nonempty_first < 2 and len(derived_queries) >= 2:
        try:
            refinement_queries = _generate_refinement_queries(
                title=title, topic=topic, first_pass_results=query_results,
            )
            if refinement_queries:
                _event(
                    event_session, "tool_started",
                    f"First pass weak ({nonempty_first}/{len(derived_queries)} useful). Running refinement pass.",
                    topic_id=topic_id, topic_title=title,
                    tool_name="research.iterative_refinement",
                )
                extra_results, extra_candidates = _run_research_queries(
                    queries=refinement_queries,
                    roamer=roamer,
                    topic=topic,
                    topic_id=topic_id,
                    title=title,
                    packet=packet,
                    event_session=event_session,
                    pass_label="pass-2-refine",
                )
                query_results.extend(extra_results)
                candidate_ids.extend(extra_candidates)
                _log.info(
                    "Iterative research for %s: pass-1 had %d/%d useful, pass-2 added %d more queries",
                    topic_id[:12], nonempty_first, len(derived_queries), len(extra_results),
                )
        except Exception as exc:
            _log.warning("Iterative research refinement failed for %s: %s", topic_id[:12], exc)

    promotion_decisions: list[dict[str, Any]] = []
    finding_candidates = _extract_research_finding_candidates(
        packet=packet,
        query_results=query_results,
    )
    for candidate in finding_candidates:
        gate = evaluate_research_promotion_candidate(candidate, research_packet=packet)
        promotion_decisions.append(
            {
                **dict(candidate),
                "gate": gate.to_dict(),
            }
        )
    for candidate in list(miner_output.get("heuristic_candidates") or []) + list(miner_output.get("script_ideas") or []):
        gate = evaluate_research_promotion_candidate(candidate, research_packet=packet)
        promotion_decisions.append(
            {
                **dict(candidate),
                "gate": gate.to_dict(),
            }
        )

    preliminary_quality = _summarize_research_quality(
        packet=packet,
        query_results=query_results,
        promotion_decisions=promotion_decisions,
        mined_features=miner_output,
        artifact_refs=[_artifact_ref(packet_artifact, kind="research_packet_artifact")],
        skip_reason=skip_reason,
    )
    bundle_payload = {
        "bundle_schema": "brain_hive.autonomous_research_bundle.v1",
        "topic_id": topic_id,
        "title": title,
        "packet_artifact_id": packet_artifact["artifact_id"],
        "query_results": query_results,
        "candidate_ids": candidate_ids,
        "mined_features": miner_output,
        "finding_candidates": finding_candidates,
        "promotion_decisions": promotion_decisions,
        "research_quality_status": preliminary_quality["research_quality_status"],
        "research_quality_reasons": list(preliminary_quality["research_quality_reasons"]),
        "nonempty_query_count": int(preliminary_quality["nonempty_query_count"]),
        "dead_query_count": int(preliminary_quality["dead_query_count"]),
        "promoted_finding_count": int(preliminary_quality["promoted_finding_count"]),
        "mined_feature_count": int(preliminary_quality["mined_feature_count"]),
    }
    bundle_artifact = store_artifact_manifest(
        source_kind="research_bundle",
        title=f"Autonomous research bundle: {title}",
        summary=f"Bounded external research, trading feature mining, and gate decisions for {title}.",
        payload=bundle_payload,
        topic_id=topic_id,
        claim_id=claim_id or None,
        session_id=event_session,
        tags=[*list(topic.get("topic_tags") or []), "autonomous_research", "research_bundle"],
        metadata={
            "query_count": len(query_results),
            "candidate_count": len(candidate_ids),
            "promotable_count": sum(1 for item in promotion_decisions if dict(item.get("gate") or {}).get("can_promote")),
            "research_quality_status": preliminary_quality["research_quality_status"],
            "nonempty_query_count": int(preliminary_quality["nonempty_query_count"]),
            "source_domain_count": int(preliminary_quality["source_domain_count"]),
        },
    )
    _event(
        event_session,
        "tool_executed",
        f"Packed research bundle artifact {bundle_artifact['artifact_id']}.",
        topic_id=topic_id,
        topic_title=title,
        claim_id=claim_id,
        artifact_id=bundle_artifact["artifact_id"],
        artifact_role="bundle",
        tool_name="liquefy.pack_research_bundle",
    )

    quality_summary = _summarize_research_quality(
        packet=packet,
        query_results=query_results,
        promotion_decisions=promotion_decisions,
        mined_features=miner_output,
        artifact_refs=[
            _artifact_ref(packet_artifact, kind="research_packet_artifact"),
            _artifact_ref(bundle_artifact, kind="research_bundle_artifact"),
        ],
        skip_reason=skip_reason,
    )
    result_status = "solved" if quality_summary["research_quality_status"] == "grounded" else "researching"
    synthesis_card = _build_synthesis_card(
        title=title,
        query_results=query_results,
        promotion_decisions=promotion_decisions,
        quality_summary=quality_summary,
        packet_artifact=packet_artifact,
        bundle_artifact=bundle_artifact,
    )
    result_body = _render_synthesis_card(synthesis_card)
    post_result = public_hive_bridge.submit_public_topic_result(
        topic_id=topic_id,
        body=result_body,
        result_status=result_status,
        post_kind="verdict" if result_status == "solved" else "summary",
        claim_id=claim_id or None,
        evidence_refs=[
            _synthesis_card_ref(synthesis_card),
            _artifact_ref(packet_artifact, kind="research_packet_artifact"),
            _artifact_ref(bundle_artifact, kind="research_bundle_artifact"),
            *[_candidate_ref(candidate_id) for candidate_id in candidate_ids[:8]],
            *[_promotion_ref(item) for item in promotion_decisions[:8]],
        ],
        idempotency_key=_idempotency_key(
            "auto-result",
            f"{topic_id}:{synthesis_card['state_token']}",
        ),
    )
    if post_result.get("ok"):
        _event(
            event_session,
            "tool_executed",
            f"Submitted Hive result with status {result_status}.",
            topic_id=topic_id,
            topic_title=title,
            claim_id=claim_id,
            result_status=result_status,
            research_quality_status=quality_summary["research_quality_status"],
            artifact_id=bundle_artifact["artifact_id"],
            artifact_role="bundle",
            post_id=str(post_result.get("post_id") or ""),
            tool_name="hive.submit_result",
        )
    _event(
        event_session,
        "task_completed",
        f"Autonomous research finished for {title} with status {result_status}.",
        topic_id=topic_id,
        topic_title=title,
        claim_id=claim_id,
        result_status=result_status,
        research_quality_status=quality_summary["research_quality_status"],
        artifact_id=bundle_artifact["artifact_id"],
        artifact_role="bundle",
        query_count=len(query_results),
        artifact_count=2,
        candidate_count=len(candidate_ids),
    )
    _log.info(
        "Autonomous research completed: topic=%s result_status=%s quality=%s post_ok=%s",
        topic_id[:12],
        result_status,
        quality_summary["research_quality_status"],
        bool(post_result.get("ok")),
    )
    audit_logger.log(
        "autonomous_research_completed",
        target_id=topic_id,
        target_type="topic",
        details={
            "title": title[:80],
            "result_status": result_status,
            "research_quality_status": quality_summary["research_quality_status"],
            "post_ok": bool(post_result.get("ok")),
            "query_count": len(query_results),
            "promoted_finding_count": quality_summary.get("promoted_finding_count", 0),
        },
    )

    q_status = str(quality_summary.get("research_quality_status") or "insufficient_evidence").strip()
    q_reasons = list(quality_summary.get("research_quality_reasons") or [])[:4]
    grounding_label = (
        "grounded"
        if q_status == "grounded"
        else "partial"
        if q_status in ("partial", "insufficient_evidence")
        else q_status
    )
    human_label = (
        "Evidence is grounded: multiple sources, promoted findings, and resolved artifacts."
        if q_status == "grounded"
        else "Evidence is limited: do not present as conclusive. Mention the grounding status to the user."
        if q_status in ("partial", "insufficient_evidence")
        else f"Research quality: {q_status}. Do not overstate findings."
    )
    response_text = (
        f"Research on `{topic_id}` delivered to Hive. "
        f"Grounding: {grounding_label}. {human_label} "
        f"Stats: {quality_summary['nonempty_query_count']}/{quality_summary['queries_total']} queries with evidence, "
        f"{quality_summary['promoted_finding_count']} promoted findings, "
        f"{quality_summary['artifact_refs_resolved']}/{quality_summary['artifact_ref_count']} artifacts resolved."
    )
    if q_reasons and q_status != "grounded":
        response_text += f" Blockers: {'; '.join(str(r) for r in q_reasons[:3])}."
    return AutonomousResearchResult(
        ok=bool(post_result.get("ok")),
        status=(
            "completed_parallel"
            if post_result.get("ok") and foreign_active
            else "completed"
            if post_result.get("ok")
            else str(post_result.get("status") or "result_failed")
        ),
        topic_id=topic_id,
        claim_id=claim_id,
        result_status=result_status,
        artifact_ids=[packet_artifact["artifact_id"], bundle_artifact["artifact_id"]],
        candidate_ids=candidate_ids,
        promotion_decisions=promotion_decisions,
        response_text=response_text,
        details={
            "packet_artifact": packet_artifact,
            "bundle_artifact": bundle_artifact,
            "query_results": query_results,
            "post_result": dict(post_result),
            "foreign_active_claims": foreign_active,
            "parallel_lane": bool(foreign_active),
            "quality_summary": quality_summary,
            "synthesis_card": synthesis_card,
        },
    )


def pick_autonomous_research_signal(
    queue_rows: list[dict[str, Any]],
    *,
    local_peer_id: str | None = None,
) -> dict[str, Any] | None:
    clean_local_peer_id = str(local_peer_id or get_local_peer_id()).strip()
    candidates: list[dict[str, Any]] = []
    for row in list(queue_rows or []):
        if str(row.get("status") or "").strip().lower() in {"solved", "closed"}:
            continue
        candidates.append(dict(row))
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            float(row.get("research_priority") or 0.0),
            0
            if not any(
                str(item.get("agent_id") or "").strip() != clean_local_peer_id
                for item in list(row.get("claims") or [])
                if str(item.get("status") or "").strip().lower() == "active"
            )
            else -1,
            0 if int(row.get("artifact_count") or 0) <= 0 else -1,
            -int(row.get("active_claim_count") or 0),
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def _query_result_preview(*, query: str, result: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(result.get("candidate_id") or "")
    candidate = get_candidate_by_id(candidate_id) if candidate_id else None
    summary = str(result.get("summary") or "").strip()
    if not summary and candidate:
        summary = str(candidate.get("normalized_output") or candidate.get("raw_output") or "").strip()
    snippets = [dict(item) for item in list(result.get("snippets") or []) if isinstance(item, dict)]
    source_domains = list(
        dict.fromkeys(
            str(item.get("origin_domain") or "").strip().lower()
            for item in snippets
            if str(item.get("origin_domain") or "").strip()
        )
    )
    summary_text = " ".join(
        part
        for part in [
            summary,
            " ".join(str(item.get("summary") or "").strip() for item in snippets[:3]),
        ]
        if part
    ).strip()
    topic_overlap = _topic_overlap_count(topic=topic, query=query, summary_text=summary_text)
    generic_nav_hits = _generic_navigation_hits(summary_text)
    specificity_score = _specificity_score(summary=summary_text, snippet_count=len(snippets), domain_count=len(source_domains))
    relevance_status = "relevant"
    if not summary_text and not snippets:
        relevance_status = "empty"
    elif (generic_nav_hits > 0 and topic_overlap < 2) or (summary_text and topic_overlap <= 0 and specificity_score < 0.45):
        relevance_status = "off_topic"
    return {
        "query": str(query),
        "topic_id": str(result.get("topic_id") or ""),
        "candidate_id": candidate_id,
        "cached": bool(result.get("cached")),
        "summary": summary[:1200],
        "snippet_count": len(snippets),
        "source_domains": source_domains[:8],
        "topic_overlap": topic_overlap,
        "specificity_score": round(specificity_score, 4),
        "relevance_status": relevance_status,
        "generic_navigation_hits": generic_nav_hits,
    }


def _build_synthesis_card(
    *,
    title: str,
    packet_artifact: dict[str, Any],
    bundle_artifact: dict[str, Any],
    query_results: list[dict[str, Any]],
    promotion_decisions: list[dict[str, Any]],
    quality_summary: dict[str, Any],
) -> dict[str, Any]:
    promotable = [item for item in promotion_decisions if dict(item.get("gate") or {}).get("can_promote")]
    relevant_results = [
        item
        for item in list(query_results or [])
        if str(item.get("relevance_status") or "").strip().lower() != "off_topic" and _query_result_has_evidence(item)
    ]
    searched = [str(item.get("query") or "").strip() for item in list(query_results or []) if str(item.get("query") or "").strip()][:4]
    found = [
        " ".join(str(item.get("summary") or "").split()).strip()[:220]
        for item in relevant_results[:3]
        if str(item.get("summary") or "").strip()
    ]
    source_domains = [str(item) for item in list(quality_summary.get("source_domains") or []) if str(item).strip()][:8]
    promoted_findings = [
        str(item.get("label") or item.get("candidate_id") or "").strip()
        for item in promotable[:4]
        if str(item.get("label") or item.get("candidate_id") or "").strip()
    ]
    artifacts = []
    packet_state = "resolved" if _artifact_ref_is_resolved(_artifact_ref(packet_artifact, kind="research_packet_artifact")) else "missing"
    bundle_state = "resolved" if _artifact_ref_is_resolved(_artifact_ref(bundle_artifact, kind="research_bundle_artifact")) else "missing"
    artifacts.append({"label": f"packet {packet_artifact['artifact_id']}", "state": packet_state})
    artifacts.append({"label": f"bundle {bundle_artifact['artifact_id']}", "state": bundle_state})
    blockers = [str(item) for item in list(quality_summary.get("research_quality_reasons") or []) if str(item).strip()][:6]
    card = {
        "question": title,
        "searched": searched,
        "found": found,
        "source_domains": source_domains,
        "artifacts": artifacts,
        "promoted_findings": promoted_findings,
        "confidence": str(quality_summary.get("research_quality_status") or "insufficient_evidence"),
        "blockers": blockers,
    }
    card["state_token"] = _synthesis_state_token(card=card, quality_summary=quality_summary)
    return card


def _render_synthesis_card(card: dict[str, Any]) -> str:
    searched = "; ".join(list(card.get("searched") or [])[:4]) or "none recorded"
    found = "; ".join(list(card.get("found") or [])[:3]) or "No grounded findings yet."
    source_domains = ", ".join(list(card.get("source_domains") or [])[:8]) or "none surfaced"
    artifacts = ", ".join(
        f"{str(item.get('label') or '').strip()} ({str(item.get('state') or '').strip()})"
        for item in list(card.get("artifacts") or [])
        if str(item.get("label") or "").strip()
    ) or "none"
    promoted = ", ".join(list(card.get("promoted_findings") or [])[:4]) or "none"
    blockers = "; ".join(list(card.get("blockers") or [])[:6]) or "none"
    lines = [
        "Research synthesis card",
        f"Question: {str(card.get('question') or '').strip()}",
        f"Searched: {searched}",
        f"Found: {found}",
        f"Source domains: {source_domains}",
        f"Artifacts: {artifacts}",
        f"Promoted findings: {promoted}",
        f"Confidence: {str(card.get('confidence') or '').strip()}",
        f"Blockers: {blockers}",
    ]
    return "\n".join(lines)[:3500]


def _artifact_ref(artifact: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "storage_backend": str(artifact.get("storage_backend") or ""),
        "content_sha256": str(artifact.get("content_sha256") or ""),
        "file_path": str(artifact.get("file_path") or ""),
    }


def _artifact_ref_is_resolved(artifact_ref: dict[str, Any]) -> bool:
    artifact_id = str(artifact_ref.get("artifact_id") or "").strip()
    file_path = str(artifact_ref.get("file_path") or "").strip()
    if not artifact_id:
        return False
    return not (file_path and not Path(file_path).expanduser().is_file())


def _candidate_ref(candidate_id: str) -> dict[str, Any]:
    return {
        "kind": "candidate_reference",
        "candidate_id": str(candidate_id or "").strip(),
    }


def _promotion_ref(item: dict[str, Any]) -> dict[str, Any]:
    gate = dict(item.get("gate") or {})
    return {
        "kind": "promotion_gate_decision",
        "candidate_id": str(item.get("candidate_id") or ""),
        "candidate_kind": str(item.get("candidate_kind") or ""),
        "status": str(gate.get("status") or ""),
        "can_promote": bool(gate.get("can_promote")),
        "score": float(gate.get("score") or 0.0),
        "missing_requirements": list(gate.get("missing_requirements") or [])[:6],
    }


def _synthesis_card_ref(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "research_synthesis_card",
        "question": str(card.get("question") or "").strip(),
        "searched": [str(item) for item in list(card.get("searched") or []) if str(item).strip()][:4],
        "found": [str(item) for item in list(card.get("found") or []) if str(item).strip()][:4],
        "source_domains": [str(item) for item in list(card.get("source_domains") or []) if str(item).strip()][:8],
        "artifacts": [dict(item) for item in list(card.get("artifacts") or []) if isinstance(item, dict)][:6],
        "promoted_findings": [str(item) for item in list(card.get("promoted_findings") or []) if str(item).strip()][:6],
        "confidence": str(card.get("confidence") or "").strip(),
        "blockers": [str(item) for item in list(card.get("blockers") or []) if str(item).strip()][:6],
        "state_token": str(card.get("state_token") or "").strip(),
    }


def _summarize_research_quality(
    *,
    packet: dict[str, Any],
    query_results: list[dict[str, Any]],
    promotion_decisions: list[dict[str, Any]],
    mined_features: dict[str, Any],
    artifact_refs: list[dict[str, Any]],
    skip_reason: str | None = None,
) -> dict[str, Any]:
    source_domains = list(
        dict.fromkeys(
            domain
            for item in list(query_results or [])
            for domain in _query_result_domains(item)
        )
    )
    nonempty_query_count = sum(1 for item in list(query_results or []) if _query_result_has_evidence(item))
    queries_total = len(list(query_results or []))
    dead_query_count = max(0, queries_total - nonempty_query_count)
    promoted_finding_count = sum(
        1 for item in list(promotion_decisions or []) if bool(dict(item.get("gate") or {}).get("can_promote"))
    )
    mined_feature_count = (
        len(list(mined_features.get("feature_rows") or []))
        + len(list(mined_features.get("heuristic_candidates") or []))
        + len(list(mined_features.get("script_ideas") or []))
    )
    artifact_refs_resolved = sum(1 for item in list(artifact_refs or []) if _artifact_ref_is_resolved(item))
    artifact_ref_count = len(list(artifact_refs or []))
    artifact_refs_unresolved = max(0, artifact_ref_count - artifact_refs_resolved)
    offtopic_hits = sum(1 for item in list(query_results or []) if _query_result_looks_off_topic(packet=packet, query_result=item))
    explicit_local_only = bool(packet.get("trading_feature_export")) and str(
        dict(packet.get("topic") or {}).get("evidence_mode") or ""
    ).strip().lower() in {"mixed", "local_only", "internal_only", "candidate_only"}

    reasons: list[str] = []
    if str(skip_reason or "").strip():
        reasons.append(str(skip_reason).strip())
    if artifact_refs_unresolved > 0:
        reasons.append(f"Artifacts unresolved: {artifact_refs_unresolved}.")
    if nonempty_query_count <= 0:
        reasons.append("No non-empty research queries produced evidence.")
    elif nonempty_query_count < 2:
        reasons.append(f"Only {nonempty_query_count} research query returned usable evidence.")
    if dead_query_count > 0:
        reasons.append(f"{dead_query_count} research queries returned no usable evidence.")
    if len(source_domains) < 2 and not explicit_local_only:
        reasons.append("Distinct source domains are below the grounded threshold.")
    if promoted_finding_count <= 0:
        reasons.append("No promoted findings passed the evidence gate.")
    if offtopic_hits > 0:
        reasons.append(f"Detected {offtopic_hits} off-topic research contamination hit(s).")

    if artifact_refs_unresolved > 0:
        status = "artifact_missing"
    elif str(skip_reason or "").strip():
        status = "insufficient_evidence"
    elif nonempty_query_count <= 0:
        status = "query_failed"
    elif offtopic_hits >= max(1, nonempty_query_count):
        status = "off_topic"
    elif nonempty_query_count < 2 or (len(source_domains) < 2 and not explicit_local_only):
        status = "insufficient_evidence"
    elif promoted_finding_count <= 0 or dead_query_count > 0:
        status = "partial"
    else:
        status = "grounded"

    return {
        "queries_total": queries_total,
        "nonempty_query_count": nonempty_query_count,
        "dead_query_count": dead_query_count,
        "source_domains": source_domains[:8],
        "source_domain_count": len(source_domains),
        "promoted_finding_count": promoted_finding_count,
        "mined_feature_count": mined_feature_count,
        "artifact_ref_count": artifact_ref_count,
        "artifact_refs_resolved": artifact_refs_resolved,
        "artifact_refs_unresolved": artifact_refs_unresolved,
        "offtopic_hits": offtopic_hits,
        "research_quality_status": status,
        "research_quality_reasons": reasons[:8],
    }


def _external_research_skip_reason(*, packet: dict[str, Any], derived_queries: list[str]) -> str:
    topic = dict(packet.get("topic") or {})
    title = str(topic.get("title") or "")
    summary = str(topic.get("summary") or "")
    tags = [str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()]
    if is_disposable_research_topic(title=title, summary=summary, tags=tags):
        return "Disposable smoke topic detected; external research skipped by policy."
    if derived_queries:
        return ""
    return ""


def _query_result_has_evidence(query_result: dict[str, Any]) -> bool:
    if str(query_result.get("relevance_status") or "").strip().lower() == "off_topic":
        return False
    return int(query_result.get("snippet_count") or 0) > 0 or bool(str(query_result.get("summary") or "").strip())


def _query_result_domains(query_result: dict[str, Any]) -> list[str]:
    return [
        str(item or "").strip().lower()
        for item in list(query_result.get("source_domains") or [])
        if str(item or "").strip()
    ]


def _query_result_looks_off_topic(*, packet: dict[str, Any], query_result: dict[str, Any]) -> bool:
    if str(query_result.get("relevance_status") or "").strip().lower() == "off_topic":
        return True
    if not _query_result_has_evidence(query_result):
        return False
    topic = dict(packet.get("topic") or {})
    haystack_tokens = _topic_tokens(
        " ".join(
            [
                str(topic.get("title") or ""),
                str(topic.get("summary") or ""),
                str(query_result.get("query") or ""),
            ]
        )
    )
    summary_text = str(query_result.get("summary") or "")
    summary_tokens = _topic_tokens(summary_text)
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
        if marker in summary_text.lower()
    )
    return overlap < 2 and generic_nav_hits > 0


def _extract_research_finding_candidates(
    *,
    packet: dict[str, Any],
    query_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    topic = dict(packet.get("topic") or {})
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(list(query_results or []), start=1):
        if str(item.get("relevance_status") or "").strip().lower() == "off_topic":
            continue
        if not _query_result_has_evidence(item):
            continue
        summary = " ".join(str(item.get("summary") or "").split()).strip()
        topic_overlap = int(item.get("topic_overlap") or 0)
        specificity = float(item.get("specificity_score") or 0.0)
        domains = _query_result_domains(item)
        snippet_count = int(item.get("snippet_count") or 0)
        if topic_overlap < 2 or specificity < 0.45:
            continue
        label = summary[:96] or str(item.get("query") or "").strip()[:96]
        score = min(
            0.95,
            0.34
            + (0.12 * min(snippet_count, 3))
            + (0.10 * min(len(domains), 2))
            + (0.08 * min(topic_overlap, 4))
            + (0.18 * specificity),
        )
        candidates.append(
            {
                "candidate_kind": "finding",
                "candidate_id": f"finding::{topic.get('topic_id') or 'topic'!s}::{index}",
                "label": label,
                "rule_text": summary[:400],
                "support": max(snippet_count, len(domains), 1),
                "score": round(score, 4),
                "source_kind": "external_research_finding",
                "evaluation": {
                    "topic_overlap": topic_overlap,
                    "specificity_score": round(specificity, 4),
                    "domain_count": len(domains),
                    "snippet_count": snippet_count,
                },
            }
        )
    return candidates


def _topic_overlap_count(*, topic: dict[str, Any], query: str, summary_text: str) -> int:
    topic_tokens = _topic_tokens(
        " ".join(
            [
                str(topic.get("title") or ""),
                str(topic.get("summary") or ""),
                str(query or ""),
            ]
        )
    )
    summary_tokens = _topic_tokens(summary_text)
    if not topic_tokens or not summary_tokens:
        return 0
    return len(topic_tokens & summary_tokens)


def _generic_navigation_hits(summary_text: str) -> int:
    lowered = str(summary_text or "").lower()
    return sum(
        1
        for marker in (
            "skip navigation",
            "get started",
            "platforms",
            "components",
            "documentation",
            "wear os",
            "android for cars",
        )
        if marker in lowered
    )


def _specificity_score(*, summary: str, snippet_count: int, domain_count: int) -> float:
    tokens = _topic_tokens(summary)
    long_tokens = [token for token in tokens if len(token) >= 7]
    return min(
        1.0,
        (0.12 * min(snippet_count, 3))
        + (0.10 * min(domain_count, 2))
        + (0.05 * min(len(long_tokens), 6))
        + (0.18 if len(str(summary or "").strip()) >= 90 else 0.0),
    )


def _synthesis_state_token(*, card: dict[str, Any], quality_summary: dict[str, Any]) -> str:
    payload = "|".join(
        [
            str(card.get("question") or ""),
            str(card.get("confidence") or ""),
            ",".join(list(card.get("searched") or [])[:4]),
            ",".join(list(card.get("found") or [])[:3]),
            ",".join(list(card.get("source_domains") or [])[:8]),
            ",".join(list(card.get("promoted_findings") or [])[:4]),
            str(quality_summary.get("artifact_refs_resolved") or 0),
            str(quality_summary.get("offtopic_hits") or 0),
        ]
    )
    return sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _topic_tokens(text: str) -> set[str]:
    return {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in str(text or "").lower()).split()
        if len(token) >= 4
    }


def _quality_state_token(quality_summary: dict[str, Any]) -> str:
    return (
        f"{quality_summary.get('research_quality_status')}:"
        f"{quality_summary.get('nonempty_query_count')}:"
        f"{quality_summary.get('promoted_finding_count')}:"
        f"{quality_summary.get('source_domain_count')}:"
        f"{quality_summary.get('artifact_refs_resolved')}"
    )


def _claim_tags(topic: dict[str, Any]) -> list[str]:
    tags = [str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()]
    base = ["research", "curiosity", "artifact_export", "eval_gate"]
    if "trading_learning" in tags or "manual_trader" in tags:
        base.extend(["trading_research", "heuristic_mining"])
    return list(dict.fromkeys(base + tags))[:12]


def _research_topic_kind(topic: dict[str, Any], packet: dict[str, Any]) -> str:
    tags = {str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()}
    if dict(packet.get("trading_feature_export") or {}):
        return "general"
    if {"integration", "bot", "api", "openclaw", "liquefy"} & tags:
        return "integration"
    if {"ux", "ui", "design"} & tags:
        return "design"
    return "technical"


def _idempotency_key(prefix: str, token: str) -> str:
    return f"{prefix}:{get_local_peer_id()[:12]}:{str(token or '').strip()}"[:128]


def _run_research_queries(
    *,
    queries: list[str],
    roamer: CuriosityRoamer,
    topic: dict[str, Any],
    topic_id: str,
    title: str,
    packet: dict[str, Any],
    event_session: str,
    pass_label: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Execute a batch of research queries and return (results, candidate_ids)."""
    results: list[dict[str, Any]] = []
    cids: list[str] = []
    total = len(queries)
    for idx, query in enumerate(queries, start=1):
        topic_kind = _research_topic_kind(topic, packet)
        _event(
            event_session, "tool_started",
            f"[{pass_label}] Research {idx}/{total}: {query}",
            topic_id=topic_id, topic_title=title, query=query,
            query_index=idx, query_total=total,
            tool_name="curiosity.run_external_topic",
        )
        result = roamer.run_external_topic(
            session_id=event_session,
            topic_text=str(query),
            topic_kind=topic_kind,
            reason=f"research_topic_from_signal:{topic_id}",
            task_id=f"research:{topic_id}",
            trace_id=f"research:{topic_id}",
        )
        candidate_id = str(result.get("candidate_id") or "")
        if candidate_id:
            cids.append(candidate_id)
        results.append(_query_result_preview(query=str(query), result=result, topic=topic))
        _event(
            event_session, "tool_executed",
            f"[{pass_label}] Finished research {idx}/{total}: {query}",
            topic_id=topic_id, topic_title=title, query=query,
            query_index=idx, query_total=total, candidate_id=candidate_id,
            tool_name="curiosity.run_external_topic",
        )
    return results, cids


def _generate_refinement_queries(
    *,
    title: str,
    topic: dict[str, Any],
    first_pass_results: list[dict[str, Any]],
) -> list[str]:
    """Generate better queries based on what the first pass learned."""
    from core.brain_hive_research import _ollama_base_url
    base_url = _ollama_base_url()
    if not base_url:
        return []

    weak = [r for r in first_pass_results if not _query_result_has_evidence(r)]
    strong = [r for r in first_pass_results if _query_result_has_evidence(r)]

    context = f"Topic: {title}\n"
    if weak:
        context += "Queries that returned NO useful results:\n"
        for r in weak[:3]:
            context += f"  - {r.get('query', '')}\n"
    if strong:
        context += "Queries that DID return useful results:\n"
        for r in strong[:2]:
            context += f"  - {r.get('query', '')} (found: {r.get('snippet_count', 0)} snippets from {', '.join(r.get('source_domains', [])[:3])})\n"

    prompt = (
        "You are refining web search queries for a research system. The first round of searches "
        "produced weak results.\n\n"
        f"{context}\n"
        "Generate 2-3 alternative search queries that are MORE SPECIFIC and likely to find "
        "concrete, technical evidence. Use different terminology, add specific technology names, "
        "or narrow the scope. One per line, no numbering."
    )

    try:
        from core.hardware_tier import recommended_ollama_model

        model = recommended_ollama_model()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 300,
        }
        permit = seal_direct_provider_invocation(
            provider_id="ollama:research-refinement",
            model_id=model,
            operation="research_query_refinement",
            payload=payload,
            request_id=(
                "research-refine-"
                + sha1(
                    f"{title}\n{prompt}".encode()
                ).hexdigest()
            ),
            context_manifest={
                "chat_id": "",
                "project_id": str(
                    topic.get("topic_id") or ""
                ),
                "capsule_version": "none",
                "items_included": [],
                "items_excluded": [],
            },
            max_output_tokens=300,
            header_names=("Content-Type",),
        )
        resp = __import__("requests").post(
            f"{base_url}/v1/chat/completions",
            json=permit.consume(),
            timeout=25,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        lines = [
            line.strip().lstrip("0123456789.-) ").strip()
            for line in text.splitlines()
            if line.strip() and len(line.strip()) > 10
        ]
        return list(dict.fromkeys(q[:240] for q in lines if q))[:3]
    except Exception as exc:
        _log.debug("Refinement query generation failed: %s", exc)
        return []


def _event(session_id: str, event_type: str, message: str, **details: Any) -> None:
    try:
        append_runtime_event(
            session_id=session_id,
            event_type=event_type,
            message=message,
            details=details,
        )
    except Exception as exc:
        audit_logger.log(
            "autonomous_research_event_error",
            target_id=session_id,
            target_type="session",
            details={"event_type": event_type, "error": str(exc)},
        )
