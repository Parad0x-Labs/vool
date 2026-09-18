from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.candidate_knowledge_lane import (
    build_task_hash,
    get_candidate_by_id,
    get_exact_candidate,
    record_candidate_output,
)
from core.curiosity_policy import (
    CuriosityConfig,
    curiosity_decision,
    load_curiosity_config,
    policy_snapshot,
    source_kind_limit,
)
from core.personal_knowledge import (
    PERSONAL_KNOWLEDGE_ADMISSION,
    is_personal_knowledge_question,
    memory_has_personal_answer,
)
from core.retrieval_constraints import analyze_retrieval_request_authority
from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval
from core.source_credibility import SourceCredibilityVerdict, evaluate_source_domain, is_domain_allowed
from core.source_reputation import SourceProfile, profiles_for_topic, render_query
from core.task_router import looks_like_explicit_lookup_request, looks_like_public_entity_lookup_request
from network.signer import get_local_peer_id
from retrieval.web_adapter import WebAdapter
from storage.curiosity_state import (
    count_curiosity_runs,
    queue_curiosity_topic,
    record_curiosity_run,
    requeue_curiosity_topic,
    update_curiosity_topic,
)

_IDLE_COMMONS_SEEDS: tuple[tuple[str, str, str], ...] = (
    ("integration", "OpenClaw and Liquefy integration improvements", "integration refresh"),
    ("technical", "safer self-tool creation and verification loops", "toolsmith hardening"),
    ("design", "better human-visible watcher and task-flow UX", "watcher usability"),
    ("technical", "swarm memory reuse without leaking private traces", "memory discipline"),
    ("integration", "public-hive task participation and reward proof loops", "hive ops"),
)

_ADAPTIVE_RESEARCH_MARKERS: tuple[str, ...] = (
    "research",
    "look up",
    "search",
    "compare",
    "versus",
    "vs",
    "difference",
    "verify",
    # Claim-shaped phrasings only -- bare "confirm" is a conversational verb more often than an
    # external-claim demand (see _VERIFY_MARKERS for the measured 2026-09-02 case).
    "confirm that",
    "confirm whether",
    "is it true",
    "check whether",
    "evidence",
    "sources",
    "docs",
    "documentation",
    "best practice",
    "best practices",
    "latest",
    "current",
    "today",
)
_COMPARE_MARKERS: tuple[str, ...] = (
    "compare",
    "versus",
    " vs ",
    "difference",
)
_VERIFY_MARKERS: tuple[str, ...] = (
    "verify",
    # Bare "confirm" was here until 2026-09-02. It is a conversational verb ("confirm you are
    # back", "confirm you still remember the code") far more often than an external-claim
    # verification, and every chat-escalation it raised widened the turn's canonical
    # current-information requirement (`current_info_signal:lane:adaptive_research`) and routed
    # the turn through research -- measured live as the first wrong decision on a benign
    # post-restart turn. The claim-shaped phrasings keep the escalation; the bare verb does not.
    "confirm that",
    "confirm whether",
    "is it true",
    "is this true",
    "check whether",
    "fact check",
    "rumor",
    "accurate",
)
# Words that on their own testify to a PROBLEM being described. Any one of these alone is
# troubleshooting evidence.
_TROUBLESHOOTING_PROBLEM_MARKERS: tuple[str, ...] = (
    "traceback",
    "stack trace",
    "exception",
    "error",
    "failed",
    "undefined",
)
# Artifact/format names. Naming a format is NOT evidence of a problem: "answer as raw JSON" is an
# output-format constraint, not a broken system. These count as troubleshooting evidence only when
# the same text also carries a problem cue (below). Live defect this split fixes (2026-08-14): a
# geography question whose only "signal" was the clause "Constraints: raw JSON key-value" was
# co-signed into a live web retrieval because the bare token "json" matched here.
_TROUBLESHOOTING_ARTIFACT_MARKERS: tuple[str, ...] = (
    ".env",
    "yaml",
    "json",
    "config",
    "dependency",
    "version",
)
# Problem cues that let an artifact marker count. A conservative linguistic detector over the whole
# text -- substring matching on lowered text, same mechanism as every other marker family here.
_TROUBLESHOOTING_PROBLEM_CUES: tuple[str, ...] = (
    "error",
    "exception",
    "traceback",
    "stack trace",
    "fail",       # fails / failed / failing / failure
    "broken",
    "breaks",
    "crash",
    "invalid",
    "malformed",
    "parse",      # parse error / won't parse / parsing issue
    "cannot",
    "can't",
    "cant ",
    "won't",
    "wont ",
    "not working",
    "doesn't work",
    "does not work",
    "stopped working",
    "throw",      # throws / throwing / thrown
    "debug",
    "fix ",
    "fixing",
    "fix?",
)
_ENTITY_LOOKUP_DROP_TOKENS: frozenset[str] = frozenset(
    {
        "who",
        "is",
        "he",
        "she",
        "they",
        "them",
        "tell",
        "me",
        "about",
        "what",
        "do",
        "you",
        "know",
        "check",
        "find",
        "look",
        "up",
        "lookup",
        "search",
        "google",
        "in",
        "on",
        "the",
        "web",
        "pls",
        "please",
    }
)
_ENTITY_LOOKUP_KEEP_SHORT_TOKENS: frozenset[str] = frozenset({"x", "ai"})
_RESEARCH_PRIORITY_TASK_CLASSES: frozenset[str] = frozenset(
    {
        "research",
        "system_design",
        "debugging",
        "dependency_resolution",
        "config",
        "business_advisory",
        "food_nutrition",
        "relationship_advisory",
        "creative_ideation",
        "general_advisory",
        "chat_conversation",
        "chat_research",
    }
)


#: Attempts (any outcome) after which a queued topic is `exhausted` rather than re-queued.
CURIOSITY_TOPIC_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class CuriosityTopic:
    topic: str
    topic_kind: str
    reason: str
    priority: float
    source_profiles: tuple[SourceProfile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "topic_kind": self.topic_kind,
            "reason": self.reason,
            "priority": self.priority,
            "source_profiles": [profile.to_dict() for profile in self.source_profiles],
        }


@dataclass
class CuriosityResult:
    enabled: bool
    mode: str
    reason: str
    topics: list[dict[str, Any]] = field(default_factory=list)
    queued_topic_ids: list[str] = field(default_factory=list)
    executed_topic_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    cached_topic_hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "reason": self.reason,
            "topics": list(self.topics),
            "queued_topic_ids": list(self.queued_topic_ids),
            "executed_topic_ids": list(self.executed_topic_ids),
            "candidate_ids": list(self.candidate_ids),
            "cached_topic_hits": int(self.cached_topic_hits),
        }


@dataclass
class AdaptiveResearchResult:
    enabled: bool
    reason: str
    strategy: str = "not_needed"
    escalated_from_chat: bool = False
    actions_taken: list[str] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    source_domains: list[str] = field(default_factory=list)
    evidence_strength: str = "none"
    broadened: bool = False
    narrowed: bool = False
    compared_sources: bool = False
    verified_claim: bool = False
    stop_reason: str = ""
    admitted_uncertainty: bool = False
    uncertainty_reason: str = ""
    tool_gap_note: str = ""
    rounds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "strategy": self.strategy,
            "escalated_from_chat": self.escalated_from_chat,
            "actions_taken": list(self.actions_taken),
            "queries_run": list(self.queries_run),
            "notes": list(self.notes),
            "source_domains": list(self.source_domains),
            "evidence_strength": self.evidence_strength,
            "broadened": self.broadened,
            "narrowed": self.narrowed,
            "compared_sources": self.compared_sources,
            "verified_claim": self.verified_claim,
            "stop_reason": self.stop_reason,
            "admitted_uncertainty": self.admitted_uncertainty,
            "uncertainty_reason": self.uncertainty_reason,
            "tool_gap_note": self.tool_gap_note,
            "rounds": self.rounds,
        }


class CuriosityRoamer:
    def __init__(self, config: CuriosityConfig | None = None) -> None:
        self.config = config or load_curiosity_config()

    def maybe_roam(
        self,
        *,
        task: Any,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        session_id: str,
    ) -> CuriosityResult:
        interest_score = curiosity_interest_score(
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
        )
        decision = curiosity_decision(
            config=self.config,
            task_class=str(classification.get("task_class", "unknown")),
            understanding_confidence=float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0),
            retrieval_confidence_score=float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0),
            interest_score=interest_score,
        )
        topics = derive_curiosity_topics(
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            config=self.config,
        )
        result = CuriosityResult(
            enabled=decision.enabled,
            mode=self.config.mode,
            reason=decision.reason,
            topics=[topic.to_dict() for topic in topics],
        )
        if not decision.enabled or not topics:
            return result

        for topic in topics:
            topic_id = queue_curiosity_topic(
                session_id=session_id,
                task_id=str(getattr(task, "task_id", "")),
                trace_id=str(getattr(task, "task_id", "")),
                topic=topic.topic,
                topic_kind=topic.topic_kind,
                reason=topic.reason,
                priority=topic.priority,
                source_profiles=[profile.to_dict() for profile in topic.source_profiles],
            )
            result.queued_topic_ids.append(topic_id)
            # QUEUE, NEVER EXECUTE INLINE (F46). Measured on the served path: the frontloaded
            # roam auto-executed its topics with real generations INSIDE the user's turn —
            # "What is the capital of France?" spent 60-97 s in curiosity generations before
            # its own answer began (faulthandler-named frame: this method, this loop; the
            # 16 s qwen3:8b GENERATE telemetry rows were the roam's own calls). The served
            # turn is the user's critical path; the roamer's curiosity is background work
            # and belongs to a background executor the durable queue already feeds. This
            # also removes the evidence-poisoning surface the frontload gate's own comment
            # describes: executed candidates merged into the turn's context were the
            # "irrelevant sources" of three measured reports. decision.auto_execute stays a
            # policy fact; the SERVED path declines to act on it here.

        audit_logger.log(
            "curiosity_roam_completed",
            target_id=str(getattr(task, "task_id", "")),
            target_type="task",
            trace_id=str(getattr(task, "task_id", "")),
            details={
                "decision": result.reason,
                "mode": self.config.mode,
                "topics": [topic["topic"] for topic in result.topics],
                "queued_topic_ids": list(result.queued_topic_ids),
                "executed_topic_ids": list(result.executed_topic_ids),
                "candidate_ids": list(result.candidate_ids),
                "cached_topic_hits": result.cached_topic_hits,
                "policy": policy_snapshot(self.config),
            },
        )
        return result

    def execute_queued_topic(
        self,
        row: dict[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Execute ONE claimed queue row -- the background lane's unit of work.

        The row is what `storage.curiosity_state.claim_next_queued_curiosity_topic` returned
        (status already `running`). The executor is the same `_execute_topic` every other
        entry uses; this method only rebuilds the topic from its durable form, bounds the run
        (`deadline_seconds`, default the policy's `max_total_roam_seconds`) and reports the
        terminal state the store now holds: `completed` (candidate recorded or cache hit),
        `empty` (no usable sources), `queued` (cancelled / deadline, to be picked up again) or
        `exhausted` (could not finish CURIOSITY_TOPIC_MAX_ATTEMPTS times).
        """
        from storage.curiosity_state import recent_curiosity_topics

        topic_id = str(row.get("topic_id") or "")
        topic = topic_from_queue_row(row)
        budget = float(deadline_seconds) if deadline_seconds else float(self.config.max_total_roam_seconds)
        started = time.monotonic()
        task_id = str(row.get("originating_task_id") or row.get("task_id") or "curiosity-queue")
        trace_id = str(row.get("trace_id") or task_id)
        candidate_id, cached = self._execute_topic(
            topic_id=topic_id,
            topic=topic,
            task_id=task_id,
            trace_id=trace_id,
            cancelled=cancelled,
            deadline_monotonic=started + max(1.0, budget),
        )
        status = ""
        for stored in recent_curiosity_topics(limit=50):
            if str(stored.get("topic_id") or "") == topic_id:
                status = str(stored.get("status") or "")
                break
        return {
            "topic_id": topic_id,
            "topic": topic.topic,
            "topic_kind": topic.topic_kind,
            "status": status,
            "candidate_id": candidate_id,
            "cached": bool(cached),
            "seconds": round(time.monotonic() - started, 3),
        }

    def run_idle_commons(
        self,
        *,
        session_id: str,
        task_id: str = "agent-commons",
        trace_id: str = "agent-commons",
        seed_index: int | None = None,
    ) -> dict[str, Any]:
        topic = _idle_commons_topic(seed_index=seed_index)
        topic_id = queue_curiosity_topic(
            session_id=session_id,
            task_id=task_id,
            trace_id=trace_id,
            topic=topic.topic,
            topic_kind=topic.topic_kind,
            reason=topic.reason,
            priority=topic.priority,
            source_profiles=[profile.to_dict() for profile in topic.source_profiles],
        )
        candidate_id, cached = self._execute_topic(
            topic_id=topic_id,
            topic=topic,
            task_id=task_id,
            trace_id=trace_id,
        )
        candidate = get_candidate_by_id(candidate_id) if candidate_id else None
        structured = dict(candidate.get("structured_output") or {}) if candidate else {}
        snippets = list(structured.get("snippets") or [])
        summary = str(candidate.get("normalized_output") or candidate.get("raw_output") or "").strip() if candidate else ""
        return {
            "topic_id": topic_id,
            "candidate_id": candidate_id,
            "cached": bool(cached),
            "topic": topic.to_dict(),
            "summary": summary,
            "snippets": snippets,
            "public_body": _commons_public_body(topic=topic, summary=summary, snippets=snippets),
            "topic_tags": ["agent_commons", "brainstorm", topic.topic_kind],
        }

    def run_external_topic(
        self,
        *,
        session_id: str,
        topic_text: str,
        topic_kind: str = "technical",
        reason: str = "external_topic",
        task_id: str = "external-research",
        trace_id: str | None = None,
        priority: float = 0.72,
    ) -> dict[str, Any]:
        clean_topic_text = " ".join(str(topic_text or "").split()).strip()
        clean_topic_kind = str(topic_kind or "technical").strip() or "technical"
        if not clean_topic_text:
            return {
                "topic_id": "",
                "candidate_id": None,
                "cached": False,
                "topic": {},
                "summary": "",
                "snippets": [],
            }
        topic = CuriosityTopic(
            topic=clean_topic_text,
            topic_kind=clean_topic_kind,
            reason=str(reason or "external_topic").strip() or "external_topic",
            priority=max(0.0, min(1.0, float(priority))),
            source_profiles=tuple(profiles_for_topic(clean_topic_kind, clean_topic_text)),
        )
        topic_id = queue_curiosity_topic(
            session_id=session_id,
            task_id=task_id,
            trace_id=str(trace_id or task_id or clean_topic_text),
            topic=topic.topic,
            topic_kind=topic.topic_kind,
            reason=topic.reason,
            priority=topic.priority,
            source_profiles=[profile.to_dict() for profile in topic.source_profiles],
        )
        candidate_id, cached = self._execute_topic(
            topic_id=topic_id,
            topic=topic,
            task_id=task_id,
            trace_id=str(trace_id or task_id or clean_topic_text),
        )
        candidate = get_candidate_by_id(candidate_id) if candidate_id else None
        structured = dict(candidate.get("structured_output") or {}) if candidate else {}
        snippets = list(structured.get("snippets") or [])
        summary = str(candidate.get("normalized_output") or candidate.get("raw_output") or "").strip() if candidate else ""
        return {
            "topic_id": topic_id,
            "candidate_id": candidate_id,
            "cached": bool(cached),
            "topic": topic.to_dict(),
            "summary": summary,
            "snippets": snippets,
        }

    def adaptive_research(
        self,
        *,
        task_id: str,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, Any] | None = None,
        max_rounds: int = 3,
    ) -> AdaptiveResearchResult:
        raw_text = " ".join(str(user_input or "").split()).strip()
        authority = analyze_retrieval_request_authority(raw_text)
        clean_text = " ".join(str(authority.candidate_text or "").split()).strip()
        # What the message ASKS, read off the message as written (newlines are clause boundaries
        # to the interpretation; the collapsed copy above is for query building). See
        # `_adaptive_research_decision`.
        try:
            from core.execution_requirements import asked_text

            asking_text = " ".join(str(asked_text(str(user_input or "")) or "").split()).strip()
        except Exception:
            asking_text = ""
        decision = _adaptive_research_decision(
            user_input=raw_text,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            asking_text=asking_text,
        )
        result = AdaptiveResearchResult(
            enabled=bool(decision["enabled"]),
            reason=str(decision["reason"]),
            strategy=str(decision["strategy"]),
            escalated_from_chat=bool(decision["escalated_from_chat"]),
            tool_gap_note=str(decision.get("tool_gap_note") or ""),
        )
        if not result.enabled:
            return result

        task_class = str(classification.get("task_class") or "unknown").strip().lower() or "unknown"
        max_steps = max(1, min(int(max_rounds or 3), 4))
        all_notes: list[dict[str, Any]] = []
        note_keys: set[str] = set()
        pending_steps: list[tuple[str, str]] = [
            ("initial_search", _adaptive_query_seed(clean_text, interpretation=interpretation, decision=decision))
        ]
        # An enumerated request gets one bounded query per facet, subject first, before any generic
        # stage: measured 2026-09-06, every stage re-fetched the same top-two pages for the whole
        # question while five of seven available sources (sales, regions, engines, prices) were never
        # read. Facet rounds extend the round bound to at most _FACET_MAX + 1; never beyond it.
        facet_subject, facets = _request_facets(clean_text)
        for facet in facets:
            pending_steps.append(("facet_search", f"{facet_subject} {facet}"))
        if facets:
            max_steps = max(max_steps, min(1 + len(facets), _FACET_MAX + 1))
        subject_seed = facet_subject or ""

        while pending_steps and result.rounds < max_steps:
            if _turn_cancelled(source_context):
                # The operator cancelled the turn: no new query starts. Measured 2026-09-06: a
                # cancel acknowledged mid-research was followed by every remaining facet query.
                result.actions_taken.append("cancelled")
                result.stop_reason = "cancelled"
                break
            action, query = pending_steps.pop(0)
            clean_query = " ".join(str(query or "").split()).strip()
            if not clean_query or clean_query in result.queries_run:
                continue
            # M2b -- the canonical door, per ROUND. Adaptive research retrieves repeatedly
            # inside one turn, so asking once at the top would authorize rounds two and three on
            # a decision that may have closed between them. Asking here means a roam already in
            # flight when synthesis freezes stops at the next round instead of continuing to
            # search behind an answer that is already being written.
            from core.retrieval_authority_gate import authorize_retrieval

            if not authorize_retrieval(
                source_context,
                clean_text,
                lane="adaptive_research",
                proposal_reason=f"adaptive_research:{action}",
                query=clean_query,
            ):
                break
            result.actions_taken.append(action)
            result.queries_run.append(clean_query)
            result.rounds += 1
            receipt = begin_web_retrieval(
                source_context,
                kind="adaptive_research",
                query=clean_query,
                task_id=task_id,
                action=action,
            )
            try:
                notes = WebAdapter.planned_search_query(
                    clean_query,
                    task_id=task_id or None,
                    limit=self.config.max_snippets_per_query + 1,
                    task_class=task_class,
                    topic_hints=list(getattr(interpretation, "topic_hints", []) or []),
                    source_label="web.search",
                )
            except Exception as exc:
                finish_web_retrieval(source_context, receipt, failure=exc)
                raise
            finish_web_retrieval(source_context, receipt, notes=list(notes or []))
            for note in list(notes or []):
                enriched = dict(note)
                if action == "broaden_search":
                    enriched["adaptive_action"] = "broadened"
                elif action == "narrow_search":
                    enriched["adaptive_action"] = "narrowed"
                elif action == "compare_sources":
                    enriched["adaptive_action"] = "compared"
                elif action == "verify_claim":
                    enriched["adaptive_action"] = "verified"
                elif action == "facet_search":
                    enriched["adaptive_action"] = "facet"
                    enriched["adaptive_facet"] = (
                        clean_query[len(facet_subject):].strip()
                        if facet_subject and clean_query.startswith(facet_subject)
                        else clean_query
                    )
                key = _adaptive_note_key(enriched)
                if not key or key in note_keys:
                    continue
                note_keys.add(key)
                all_notes.append(enriched)

            metrics = _adaptive_research_metrics(all_notes)
            result.notes = all_notes[:_ADAPTIVE_NOTES_MAX]
            result.source_domains = metrics["domains"]
            result.evidence_strength = str(metrics["strength"])
            result.compared_sources = bool(result.compared_sources or (decision["needs_compare"] and metrics["domain_count"] >= 2))
            result.verified_claim = bool(result.verified_claim or (decision["needs_verify"] and metrics["official_count"] >= 1))
            stop_reason = _adaptive_stop_reason(
                metrics=metrics,
                needs_compare=bool(decision["needs_compare"]),
                needs_verify=bool(decision["needs_verify"]),
            )
            if stop_reason and pending_steps:
                # Planned facet rounds are the request's own plan: the generic "enough evidence"
                # rule may end the research only once every planned query has run (measured
                # 2026-09-06: it stopped after two of five facets because two domains had answered).
                stop_reason = ""
            if stop_reason:
                result.actions_taken.append("stop_answer")
                result.stop_reason = stop_reason
                break

            if pending_steps:
                # Facet rounds first: they are the request's own plan. Generic stages are consulted
                # only once every planned query has run.
                continue
            next_step = _adaptive_next_step(
                decision=decision,
                metrics=metrics,
                already_broadened=result.broadened,
                already_narrowed=result.narrowed,
                already_compared=result.compared_sources,
                already_verified=result.verified_claim,
                subject_seed=subject_seed,
            )
            if next_step is None:
                result.actions_taken.append("stop_answer")
                result.stop_reason = "bounded_research_complete"
                break
            next_action, next_query = next_step
            if next_action == "broaden_search":
                result.broadened = True
            elif next_action == "narrow_search":
                result.narrowed = True
            elif next_action == "compare_sources":
                result.compared_sources = True
            elif next_action == "verify_claim":
                result.verified_claim = True
            pending_steps.append((next_action, next_query))

        if not result.stop_reason:
            result.stop_reason = "bounded_research_complete"
        if result.rounds and not result.notes:
            # The ask widened the turn provisionally; the retrieval found nothing. Retract before
            # synthesis so the authority's own reading (stable knowledge) decides publication.
            from core.execution_requirements import retract_provisional_escalation

            if retract_provisional_escalation(
                source_context, source="adaptive_research", reason="no_evidence", text=raw_text
            ):
                result.actions_taken.append("requirement_retracted")
        if result.evidence_strength in {"weak", "none"}:
            result.admitted_uncertainty = True
            result.uncertainty_reason = _adaptive_uncertainty_reason(
                result=result,
                decision=decision,
            )
        audit_logger.log(
            "adaptive_research_completed",
            target_id=task_id,
            target_type="task",
            details=result.to_dict(),
        )
        return result

    def _execute_topic(
        self,
        *,
        topic_id: str,
        topic: CuriosityTopic,
        task_id: str,
        trace_id: str,
        cancelled: Callable[[], bool] | None = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[str | None, bool]:
        task_hash = build_task_hash(
            normalized_input=f"curiosity::{topic.topic_kind}::{topic.topic}",
            task_class="curiosity_roam",
            output_mode="summary_block",
        )
        cached = get_exact_candidate(task_hash, output_mode="summary_block")
        if cached:
            update_curiosity_topic(topic_id, status="completed", candidate_id=str(cached["candidate_id"]))
            record_curiosity_run(
                topic_id=topic_id,
                task_id=task_id,
                trace_id=trace_id,
                query_text=topic.topic,
                source_profile_ids=[profile.profile_id for profile in topic.source_profiles],
                snippets=[],
                candidate_id=str(cached["candidate_id"]),
                outcome="cache_hit",
            )
            return str(cached["candidate_id"]), True

        snippets: list[dict[str, Any]] = []
        selected_profiles = list(topic.source_profiles)[: self.config.max_queries_per_topic]
        for profile in selected_profiles:
            # CANCELLATION AND DEADLINE, at the granularity of one web call. A background
            # consumer hands in `cancelled` (user became active, runtime shutting down) and a
            # deadline; either one puts the topic back to `queued` with a run row naming why,
            # and the caller returns without a candidate. Never inside a call: a search that
            # already started completes or fails on its own budget.
            stop_reason = ""
            if cancelled is not None and cancelled():
                stop_reason = "cancelled"
            elif deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                stop_reason = "deadline"
            if stop_reason:
                # BOUNDED RETRIES. Measured on rig s51 (7338f1e9, no search provider reachable):
                # every web call took the adapter's 24 s to fail, the 8 s roam deadline cut the
                # topic after the first call, and the consumer re-queued it every tick -- an
                # unbounded retry that never ends. A topic that could not finish
                # CURIOSITY_TOPIC_MAX_ATTEMPTS times is `exhausted`, a terminal state the run
                # rows explain; a cancellation counts too, so a topic cannot be resumed forever.
                if count_curiosity_runs(topic_id) + 1 >= CURIOSITY_TOPIC_MAX_ATTEMPTS:
                    update_curiosity_topic(topic_id, status="exhausted")
                else:
                    requeue_curiosity_topic(topic_id)
                record_curiosity_run(
                    topic_id=topic_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    query_text=topic.topic,
                    source_profile_ids=[profile.profile_id for profile in selected_profiles],
                    snippets=snippets,
                    candidate_id=None,
                    outcome=stop_reason,
                )
                return None, False
            query = render_query(profile, topic.topic)
            notes = WebAdapter.search_query(
                query,
                task_id=task_id or None,
                limit=self.config.max_snippets_per_query,
                source_label="web.search",
                allowed_domains=profile.allow_domains,
                blocked_domains=profile.deny_domains,
            )
            for note in notes:
                domain = str(note.get("origin_domain") or "")
                if domain:
                    if not is_domain_allowed(domain, allow_domains=profile.allow_domains, deny_domains=profile.deny_domains):
                        continue
                    verdict = evaluate_source_domain(domain)
                    if verdict.blocked or verdict.score < 0.40:
                        continue
                else:
                    verdict = SourceCredibilityVerdict(
                        domain="",
                        score=max(0.42, float(profile.trust_weight)),
                        category=profile.credibility_class,
                        blocked=False,
                        reason="Domain missing; falling back to curated source profile trust.",
                    )
                enriched = dict(note)
                enriched["source_profile_id"] = profile.profile_id
                enriched["source_profile_label"] = profile.label
                enriched["source_credibility"] = verdict.to_dict()
                snippets.append(enriched)

        if not snippets:
            update_curiosity_topic(topic_id, status="empty")
            record_curiosity_run(
                topic_id=topic_id,
                task_id=task_id,
                trace_id=trace_id,
                query_text=topic.topic,
                source_profile_ids=[profile.profile_id for profile in selected_profiles],
                snippets=[],
                candidate_id=None,
                outcome="no_results",
            )
            return None, False

        best_ttl = min(profile.ttl_seconds for profile in selected_profiles) if selected_profiles else 3600
        confidence = _candidate_confidence(topic, snippets)
        summary_lines = [f"Bounded curiosity notes for {topic.topic}:"]
        for snippet in snippets[: self.config.max_queries_per_topic * self.config.max_snippets_per_query]:
            label = str(snippet.get("source_profile_label") or "source")
            summary_lines.append(f"- [{label}] {str(snippet.get('summary') or '').strip()}")
        candidate_id = record_candidate_output(
            task_hash=task_hash,
            task_id=task_id or None,
            trace_id=trace_id or None,
            task_class="curiosity_roam",
            task_kind=f"curiosity_{topic.topic_kind}",
            output_mode="summary_block",
            provider_name="curiosity_roamer",
            model_name="bounded_web_research",
            raw_output="\n".join(summary_lines),
            normalized_output="\n".join(summary_lines),
            structured_output={
                "topic": topic.topic,
                "topic_kind": topic.topic_kind,
                "snippets": snippets,
            },
            confidence=confidence,
            trust_score=confidence,
            validation_state="valid",
            metadata={
                "candidate_only": True,
                "curiosity_topic": topic.topic,
                "curiosity_reason": topic.reason,
                "source_profile_ids": [profile.profile_id for profile in selected_profiles],
                "interest_priority": topic.priority,
                "source_credibility_min": min(
                    (
                        float(dict(snippet.get("source_credibility") or {}).get("score") or 0.0)
                        for snippet in snippets
                    ),
                    default=0.0,
                ),
            },
            provenance={
                "search_engine": str(snippets[0].get("search_provider") or "unknown") if snippets else "unknown",
                "search_provider_order": list(dict.fromkeys(str(snippet.get("search_provider") or "unknown") for snippet in snippets)),
                "source_profiles": [profile.to_dict() for profile in selected_profiles],
            },
            ttl_seconds=best_ttl,
        )
        update_curiosity_topic(topic_id, status="completed", candidate_id=candidate_id)
        record_curiosity_run(
            topic_id=topic_id,
            task_id=task_id,
            trace_id=trace_id,
            query_text=topic.topic,
            source_profile_ids=[profile.profile_id for profile in selected_profiles],
            snippets=snippets,
            candidate_id=candidate_id,
            outcome="candidate_recorded",
        )
        return candidate_id, False


def topic_from_queue_row(row: dict[str, Any]) -> CuriosityTopic:
    """Rebuild a `CuriosityTopic` from its durable `curiosity_topics` row.

    Source profiles are stored as the dataclass's own `to_dict` form; a row whose profiles are
    missing or malformed falls back to the profiles the topic kind selects today, so an old
    row is executable rather than poison.
    """
    topic_text = " ".join(str(row.get("topic") or "").split()).strip()
    topic_kind = str(row.get("topic_kind") or "technical").strip() or "technical"
    profiles: list[SourceProfile] = []
    for entry in list(row.get("source_profiles") or []):
        if not isinstance(entry, dict):
            continue
        try:
            payload = {
                key: (tuple(value) if isinstance(value, list) else value)
                for key, value in entry.items()
            }
            profiles.append(SourceProfile(**payload))
        except Exception:
            continue
    if not profiles:
        profiles = list(profiles_for_topic(topic_kind, topic_text))
    try:
        priority = max(0.0, min(1.0, float(row.get("priority") or 0.0)))
    except Exception:
        priority = 0.0
    return CuriosityTopic(
        topic=topic_text,
        topic_kind=topic_kind,
        reason=str(row.get("reason") or "queued").strip() or "queued",
        priority=priority,
        source_profiles=tuple(profiles),
    )


def curiosity_interest_score(*, user_input: str, classification: dict[str, Any], interpretation: Any, context_result: Any) -> float:
    text = (user_input or "").lower()
    task_class = str(classification.get("task_class", "unknown"))
    score = 0.34

    if task_class in {"research", "system_design"}:
        score += 0.24
    if any(token in text for token in ("learn", "research", "look up", "search", "best", "design", "telegram", "discord", "bot", "app", "web", "news")):
        score += 0.16
    if len(getattr(interpretation, "topic_hints", []) or []) >= 2:
        score += 0.08
    if float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0) >= 0.70:
        score += 0.08
    if float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0) < 0.55:
        score += 0.10

    return max(0.0, min(1.0, score))


def derive_curiosity_topics(*, user_input: str, classification: dict[str, Any], interpretation: Any, config: CuriosityConfig | None = None) -> list[CuriosityTopic]:
    config = config or load_curiosity_config()
    text = (user_input or "").strip()
    if not text:
        return []

    topics: list[tuple[str, str, str, float]] = []
    task_class = str(classification.get("task_class", "unknown"))
    topic_hints = [str(item) for item in getattr(interpretation, "topic_hints", []) or []]

    if topic_hints:
        for hint in topic_hints[:4]:
            kind = _topic_kind(hint, task_class, text, config=config)
            reason = f"topic_hint:{hint}"
            priority = 0.66 if kind != "news" else 0.52
            topics.append((hint, kind, reason, priority))

    condensed = " ".join(text.split()[:10]).strip()
    if condensed:
        kind = _topic_kind(condensed, task_class, text, config=config)
        topics.append((condensed, kind, "user_request", 0.72 if kind != "news" else 0.55))

    deduped: list[CuriosityTopic] = []
    seen: set[str] = set()
    kind_counts: dict[str, int] = {}
    for topic_text, topic_kind, reason, priority in topics:
        normalized = topic_text.lower().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        limit = source_kind_limit(config, topic_kind)
        if kind_counts.get(topic_kind, 0) >= limit:
            continue
        profiles = tuple(profiles_for_topic(topic_kind, topic_text))
        if not profiles:
            continue
        deduped.append(
            CuriosityTopic(
                topic=topic_text,
                topic_kind=topic_kind,
                reason=reason,
                priority=priority,
                source_profiles=profiles,
            )
        )
        kind_counts[topic_kind] = kind_counts.get(topic_kind, 0) + 1
        if len(deduped) >= config.max_topics_per_task:
            break
    return deduped


def _topic_kind(topic: str, task_class: str, full_text: str, *, config: CuriosityConfig) -> str:
    lowered = f" {topic} {full_text} ".lower()
    words = {word.strip(".,;:!?()[]\"'") for word in lowered.replace("/", " ").replace("-", " ").split() if word}
    has_design = any(
        phrase in lowered
        for phrase in (" design ", " layout ", " theme ", " mobile app ", " web app ")
    ) or bool({"ux", "ui"} & words)
    # WORDS, not substrings. Measured on the controlled rig (9d7b6a3d, F58): "What is the
    # capital of France?" was classified `integration` because "api" is a substring of
    # "capital", so its topic ran under messaging/repo/docs profiles whose allow-lists rejected
    # every source, and the consumer ended it `empty`. A kind decides which source profiles a
    # topic gets; it must read the words the user wrote.
    has_integration = bool({"telegram", "discord", "bot", "bots", "api", "apis", "integration", "integrations"} & words)
    news_words = {"news", "headline", "headlines", "pulse", "today", "planet"}
    if config.allow_news_pulse and (bool(news_words & words) or " current events " in lowered):
        return "news"
    if has_integration:
        return "integration"
    if has_design:
        return "design"
    if task_class in {"research", "system_design", "dependency_resolution", "config"}:
        return "technical"
    return "general"


def _candidate_confidence(topic: CuriosityTopic, snippets: list[dict[str, Any]]) -> float:
    base = 0.34 + min(0.24, 0.04 * len(snippets))
    avg_trust = sum(profile.trust_weight for profile in topic.source_profiles[:2]) / max(1, min(2, len(topic.source_profiles)))
    domain_scores = [
        float(dict(snippet.get("source_credibility") or {}).get("score") or 0.0)
        for snippet in snippets
        if snippet.get("source_credibility")
    ]
    if domain_scores:
        avg_trust = (avg_trust + (sum(domain_scores) / len(domain_scores))) / 2.0
    if topic.topic_kind == "news":
        avg_trust = min(avg_trust, 0.58)
    return max(0.25, min(0.84, base + (0.42 * avg_trust)))


def _idle_commons_topic(*, seed_index: int | None = None) -> CuriosityTopic:
    if seed_index is None:
        hour_index = int(datetime.now(timezone.utc).timestamp() // 3600)
        peer_salt = sum(ord(ch) for ch in get_local_peer_id()[:12])
        seed_index = (hour_index + peer_salt) % len(_IDLE_COMMONS_SEEDS)
    topic_kind, seed_text, reason = _IDLE_COMMONS_SEEDS[int(seed_index) % len(_IDLE_COMMONS_SEEDS)]
    topic_text = f"Agent commons brainstorm: {seed_text}"
    return CuriosityTopic(
        topic=topic_text,
        topic_kind=topic_kind,
        reason=reason,
        priority=0.74,
        source_profiles=tuple(profiles_for_topic(topic_kind, topic_text)),
    )


def _commons_public_body(*, topic: CuriosityTopic, summary: str, snippets: list[dict[str, Any]]) -> str:
    lines = [
        f"Agent commons update: {topic.topic}.",
        f"Reason: {topic.reason}.",
    ]
    clean_summary = " ".join(str(summary or "").split()).strip()
    if clean_summary:
        lines.append(clean_summary[:900])
    labels: list[str] = []
    for snippet in snippets[:3]:
        domain = str(snippet.get("origin_domain") or "").strip()
        label = domain or str(snippet.get("source_profile_label") or "curated_source").strip()
        if label and label not in labels:
            labels.append(label)
    if labels:
        lines.append("Signals reviewed: " + ", ".join(labels[:3]) + ".")
    return " ".join(part.strip() for part in lines if part.strip())[:1500]


def _adaptive_research_decision(
    *,
    user_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, Any] | None,
    asking_text: str = "",
) -> dict[str, Any]:
    authority = analyze_retrieval_request_authority(user_input)
    text = " ".join(str(authority.candidate_text or "").split()).strip()
    # The cue and marker arms below read what the message ASKS -- its request units and the
    # statements they refer back to (`core.execution_requirements.asked_text`) -- never the
    # material it merely describes. Measured served 2026-09-16 (candidate 035dea9b): a design
    # brief for a community bot listed "view bot status and recent errors" and "restart selected
    # bot services" among a proposed admin panel's features; the problem cue and artifact marker
    # read them as a specific issue to troubleshoot, three searches ran on a DIRECT design
    # question, and their empty result was appended to the answer prompt. The whole text still
    # seeds the queries when research IS warranted.
    asking = " ".join(str(asking_text or "").split()).strip() or text
    if authority.constraints.has_prohibition:
        asking = " ".join(str(analyze_retrieval_request_authority(asking).candidate_text or "").split()).strip() or asking
    lowered = f" {asking.lower()} "
    task_class = str(classification.get("task_class") or "unknown").strip().lower() or "unknown"
    topic_hints = [str(item).strip().lower() for item in list(getattr(interpretation, "topic_hints", []) or []) if str(item).strip()]
    surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    platform = str((source_context or {}).get("platform", "") or "").strip().lower()
    source_context = dict(source_context or {})
    explicit_remote_policy = "allow_remote_fetch" in source_context
    allow_remote_fetch = bool(source_context.get("allow_remote_fetch", False))
    trusted_surface = surface in {"channel", "openclaw", "api"} or platform in {"openclaw", "web_companion", "telegram", "discord"}
    # Explicit false is an operator/privacy boundary; trusted surfaces only default
    # to live research when the caller omits the remote-fetch policy entirely.
    remote_fetch_allowed = allow_remote_fetch if explicit_remote_policy else trusted_surface
    if authority.constraints.forbids_external_retrieval:
        return {
            "enabled": False,
            "reason": "user_retrieval_veto",
            "strategy": "not_allowed",
            "escalated_from_chat": False,
        }
    if not text or (
        authority.constraints.has_prohibition
        and authority.constraints.forbids_candidate(text)
    ):
        return {"enabled": False, "reason": "empty_query", "strategy": "not_needed", "escalated_from_chat": False}
    # Questions about the user's own private/personal info (family, PII) are never researchable on
    # the web, regardless of the web/surface policy. If VOOL was told the fact, answer it from
    # memory; otherwise admit it plainly and offer to remember it — never run a web/research loop
    # on "what is my sister's middle name?".
    if is_personal_knowledge_question(text):
        from core.memory.entries import resolve_memory_access_policy
        from core.persistent_memory import search_relevant_memory

        chat_id = str(source_context.get("chat_id") or "").strip()
        try:
            access_policy = resolve_memory_access_policy(
                chat_id=chat_id,
            )
        except (TypeError, ValueError):
            access_policy = None

        def _scoped_memory_search(
            query: str,
            *,
            limit: int = 5,
        ) -> list[dict[str, Any]]:
            if access_policy is None:
                return []
            return search_relevant_memory(
                query,
                access_policy=access_policy,
                limit=limit,
            )

        if memory_has_personal_answer(text, _scoped_memory_search):
            # VOOL holds this fact — answer it from memory (no web, no forced uncertainty).
            return {
                "enabled": False,
                "reason": "personal_knowledge_answer_from_memory",
                "strategy": "not_needed",
                "escalated_from_chat": False,
            }
        return {
            "enabled": False,
            "reason": "personal_knowledge_not_researchable",
            "strategy": "tool_gap",
            "escalated_from_chat": False,
            "admitted_uncertainty": True,
            "tool_gap_note": PERSONAL_KNOWLEDGE_ADMISSION,
        }
    if not policy_engine.allow_web_fallback():
        return {
            "enabled": False,
            "reason": "web_lookup_disabled",
            "strategy": "tool_gap",
            "escalated_from_chat": False,
            "tool_gap_note": "Live research is not available on this runtime.",
        }
    if not remote_fetch_allowed:
        return {
            "enabled": False,
            "reason": "surface_disallows_remote_fetch",
            "strategy": "tool_gap",
            "escalated_from_chat": False,
            "tool_gap_note": "This surface is not allowed to perform live research.",
        }

    has_compare = any(marker in lowered for marker in _COMPARE_MARKERS)
    has_tradeoff_compare = _looks_like_concrete_tradeoff_query(text=asking, topic_hints=topic_hints)
    has_verify = any(marker in lowered for marker in _VERIFY_MARKERS)
    has_research_marker = any(marker in lowered for marker in _ADAPTIVE_RESEARCH_MARKERS) or has_tradeoff_compare
    has_compare = bool(has_compare or has_tradeoff_compare)
    has_version_number = bool(
        re.search(r"\b\d+\.\d+(?:\.\d+)?\b", asking)
        and any(
            marker in lowered
            for marker in (" version ", " release ", " upgrade ", " python ", " node.js ", " dependency ")
        )
    )
    # A problem word alone is troubleshooting evidence. An artifact/format name alone is not --
    # "answer as raw JSON" names a format, not a fault -- it counts only alongside a problem cue.
    # This is a detector over the whole text, keyed to nothing about any specific prompt.
    has_problem_cue = any(cue in lowered for cue in _TROUBLESHOOTING_PROBLEM_CUES)
    has_specific_issue = (
        any(marker in lowered for marker in _TROUBLESHOOTING_PROBLEM_MARKERS)
        or (has_problem_cue and any(marker in lowered for marker in _TROUBLESHOOTING_ARTIFACT_MARKERS))
        or bool(re.search(r"`[^`]+`|[A-Z][A-Za-z]+Error", asking))
        or has_version_number
    )
    explicit_lookup = looks_like_explicit_lookup_request(asking)
    public_entity_lookup = looks_like_public_entity_lookup_request(asking)
    entity_seed = ""
    entity_retry_query = ""
    if public_entity_lookup:
        entity_seed, entity_retry_query = _adaptive_public_entity_lookup_queries(
            text,
            interpretation=interpretation,
        )
    chat_escalation = task_class not in {"research", "chat_research", "system_design", "debugging", "dependency_resolution", "config"} and (
        has_research_marker or has_compare or has_verify or has_specific_issue or explicit_lookup or public_entity_lookup
    )
    enabled = bool(
        explicit_lookup
        or public_entity_lookup
        or chat_escalation
        or (
            task_class in _RESEARCH_PRIORITY_TASK_CLASSES
            and (
                has_research_marker
                or has_compare
                or has_verify
                or has_specific_issue
                or explicit_lookup
                or public_entity_lookup
            )
        )
    )
    strategy = "general_research"
    if public_entity_lookup:
        strategy = "entity_lookup"
    elif has_compare:
        strategy = "compare"
    elif has_verify:
        strategy = "verify"
    elif has_specific_issue:
        strategy = "narrow"
    elif has_research_marker:
        strategy = "broaden"
    # A turn that assigns its own values cannot be answered by an external source, whatever the
    # classifier called it. Measured live 2026-08-15: "Variable X holds 'Apple' ... What is the
    # current value of Y?" was classified `research` and searched the WEB for the value of a
    # variable the user had defined one sentence earlier -- the query was the puzzle itself. The
    # gate trusted the classification and never asked whether anything out there COULD know.
    from core.self_contained_turn import turn_answers_itself

    self_contained = turn_answers_itself(str(user_input or ""))
    if self_contained:
        enabled = False
    if not enabled:
        strategy = "not_needed"
    return {
        "enabled": enabled,
        "reason": (
            "self_contained_turn"
            if self_contained
            else "chat_escalation"
            if chat_escalation
            else "research_task"
            if enabled
            else "research_not_needed"
        ),
        "strategy": strategy,
        "escalated_from_chat": chat_escalation,
        "needs_compare": has_compare,
        "needs_verify": has_verify,
        "needs_specific_focus": has_specific_issue,
        "topic_hints": topic_hints[:4],
        "explicit_lookup": explicit_lookup,
        "public_entity_lookup": public_entity_lookup,
        "entity_seed": entity_seed,
        "entity_retry_query": entity_retry_query,
    }


def _looks_like_concrete_tradeoff_query(*, text: str, topic_hints: list[str]) -> bool:
    lowered = f" {' '.join(str(text or '').split()).lower()} "
    if not any(marker in lowered for marker in ("tradeoff", "tradeoffs", "pros and cons")):
        return False
    if " between " in lowered and " and " in lowered:
        return True
    if " vs " in lowered or " versus " in lowered:
        return True
    generic_hints = {
        "architecture",
        "design",
        "system design",
        "integration",
        "technical",
        "general",
    }
    concrete_hints = {
        hint
        for hint in (str(item or "").strip().lower() for item in topic_hints)
        if hint and hint not in generic_hints
    }
    return len(concrete_hints) >= 2


# An enumerated multi-facet request: a subject, then a colon (or "in detail:"), then two to six short
# facet phrases separated by commas / "and". Domain-neutral -- the SHAPE of the request decides, never the
# subject. "compare the VW Passat and the VW Golf in detail: production periods, sales, regions, engines
# and prices" -> subject "VW Passat and the VW Golf", facets [production periods, sales, regions, engines,
# prices]. A sentence after a colon ("Explain photosynthesis: the light reactions matter most") is not a
# facet list: its items are too long, or there are too few of them.
_FACET_SUBJECT_LEAD_RE = re.compile(
    r"^(?:(?:now|then|also|please|next|ok|okay|so|and|could you|can you|would you)[\s,]+)*"
    r"(?:(?:compare|contrast|evaluate|assess|review|describe|explain|analy[sz]e|summari[sz]e)\s+)?",
    re.IGNORECASE,
)
_FACET_SUBJECT_TAIL_RE = re.compile(r"\s*(?:in\s+(?:detail|depth|full)|thoroughly|side\s+by\s+side|for\s+me)\s*$", re.IGNORECASE)
_FACET_MAX = 6
_FACET_MAX_WORDS = 4
#: Notes the lane keeps for the answering call: the initial search plus two per facet at most.
#: The old fixed six dropped the seventh source of a five-facet comparison (the price list).
_ADAPTIVE_NOTES_MAX = 1 + 2 * _FACET_MAX


def _turn_cancelled(source_context: dict[str, Any] | None) -> bool:
    """Whether the turn's cancel marker (the same one the model lane consults) is set."""
    marker = (source_context or {}).get("cancel_event") or (source_context or {}).get("cancellation_token")
    if marker is None:
        return False
    try:
        if callable(marker):
            return bool(marker())
        is_set = getattr(marker, "is_set", None)
        return bool(is_set()) if callable(is_set) else False
    except Exception:
        return False


def _request_facets(user_input: str) -> tuple[str, list[str]]:
    """(subject, facets) for an enumerated request; ("", []) when the request enumerates nothing."""
    text = " ".join(str(user_input or "").split()).strip()
    if ":" not in text:
        return "", []
    head, _, tail = text.rpartition(":")
    tail = tail.strip().rstrip(".!?").strip()
    head = head.strip()
    if not head or not tail:
        return "", []
    items = [part.strip(" ,;") for part in re.split(r",|\band\b|;|/", tail)]
    items = [" ".join(item.split()) for item in items if item.strip()]
    if not (2 <= len(items) <= _FACET_MAX):
        return "", []
    for item in items:
        words = item.split()
        if not words or len(words) > _FACET_MAX_WORDS or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 \-'/]*", item):
            return "", []
    subject = _FACET_SUBJECT_LEAD_RE.sub("", head, count=1).strip()
    subject = _FACET_SUBJECT_TAIL_RE.sub("", subject).strip(" ,")
    subject = re.sub(r"^(?:the|a|an)\s+", "", subject, flags=re.IGNORECASE).strip()
    if not subject:
        return "", []
    return subject, [item.lower() for item in items]


def _adaptive_query_seed(user_input: str, *, interpretation: Any, decision: dict[str, Any] | None = None) -> str:
    text = " ".join(str(user_input or "").split()).strip()
    decision = dict(decision or {})
    entity_seed = str(decision.get("entity_seed") or "").strip()
    if entity_seed:
        return entity_seed
    hints = [str(item).strip() for item in list(getattr(interpretation, "topic_hints", []) or []) if str(item).strip()]
    if hints and len(text.split()) > 14:
        return f"{hints[0]} {text}"
    return text


def _adaptive_public_entity_lookup_queries(user_input: str, *, interpretation: Any) -> tuple[str, str]:
    tokens = _adaptive_entity_lookup_tokens(user_input, interpretation=interpretation)
    if not tokens:
        clean = " ".join(str(user_input or "").split()).strip()
        return clean, clean
    primary_tokens = list(dict.fromkeys(tokens))[:6]
    retry_tokens = [_collapse_entity_lookup_token(token) for token in primary_tokens]
    retry_tokens = list(dict.fromkeys(token for token in retry_tokens if token))
    if retry_tokens == primary_tokens:
        if any(token in {"x", "twitter"} for token in retry_tokens) and "twitter" not in retry_tokens:
            retry_tokens.append("twitter")
        elif "profile" not in retry_tokens:
            retry_tokens.append("profile")
    primary = " ".join(primary_tokens).strip()
    retry = " ".join(retry_tokens).strip() or primary
    return primary, retry


def _adaptive_entity_lookup_tokens(user_input: str, *, interpretation: Any) -> list[str]:
    normalized = " ".join(str(user_input or "").strip().lower().split())
    if not normalized:
        return []
    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9\.]+", normalized):
        token = token.strip(".")
        if not token or token in _ENTITY_LOOKUP_DROP_TOKENS:
            continue
        if token == "x.com":
            token = "x"
        if len(token) == 1 and token not in _ENTITY_LOOKUP_KEEP_SHORT_TOKENS:
            continue
        tokens.append(token)
    hint_tokens: list[str] = []
    for hint in list(getattr(interpretation, "topic_hints", []) or [])[:3]:
        for token in re.findall(r"[a-z0-9\.]+", str(hint).lower()):
            token = token.strip(".")
            if not token or token in _ENTITY_LOOKUP_DROP_TOKENS:
                continue
            if len(token) == 1 and token not in _ENTITY_LOOKUP_KEEP_SHORT_TOKENS:
                continue
            hint_tokens.append(token)
    return list(dict.fromkeys(tokens + hint_tokens))


def _collapse_entity_lookup_token(token: str) -> str:
    lowered = str(token or "").strip().lower()
    if len(lowered) < 3 or lowered in {"solana", "twitter"}:
        return lowered
    return re.sub(r"(.)\1+", r"\1", lowered)


def _adaptive_note_key(note: dict[str, Any]) -> str:
    url = str(note.get("result_url") or note.get("url") or "").strip()
    title = str(note.get("result_title") or note.get("title") or "").strip()
    summary = str(note.get("summary") or note.get("snippet") or "").strip()
    return "||".join(part for part in (url, title, summary[:120]) if part)


def _adaptive_research_metrics(notes: list[dict[str, Any]]) -> dict[str, Any]:
    domains: list[str] = []
    official_count = 0
    confidence_scores: list[float] = []
    for note in list(notes or []):
        domain = str(note.get("origin_domain") or "").strip().lower()
        if domain and domain not in domains:
            domains.append(domain)
        if domain:
            verdict = evaluate_source_domain(domain)
            confidence_scores.append(float(verdict.score))
            if verdict.category in {"official", "docs", "reference"} or domain.endswith((".gov", ".edu")):
                official_count += 1
        raw_confidence = note.get("confidence")
        if raw_confidence not in {None, ""}:
            with contextlib.suppress(Exception):
                confidence_scores.append(float(raw_confidence))
        label = str(note.get("source_profile_label") or "").strip().lower()
        if "official" in label or "docs" in label:
            official_count += 1
    domain_count = len(domains)
    note_count = len(list(notes or []))
    average_confidence = (sum(confidence_scores) / len(confidence_scores)) if confidence_scores else 0.0
    if note_count >= 3 and domain_count >= 2 and (official_count >= 1 or average_confidence >= 0.58):
        strength = "strong"
    elif note_count >= 2 and (domain_count >= 2 or average_confidence >= 0.48):
        strength = "moderate"
    elif note_count >= 1:
        strength = "weak"
    else:
        strength = "none"
    return {
        "strength": strength,
        "note_count": note_count,
        "domain_count": domain_count,
        "domains": domains[:6],
        "official_count": official_count,
        "average_confidence": average_confidence,
    }


def _adaptive_stop_reason(
    *,
    metrics: dict[str, Any],
    needs_compare: bool,
    needs_verify: bool,
) -> str:
    strength = str(metrics.get("strength") or "none")
    domain_count = int(metrics.get("domain_count") or 0)
    official_count = int(metrics.get("official_count") or 0)
    if strength == "strong" and (not needs_compare or domain_count >= 2) and (not needs_verify or official_count >= 1):
        return "strong_evidence"
    if strength == "moderate" and not needs_compare and not needs_verify:
        return "sufficient_evidence"
    if needs_compare and domain_count >= 2 and strength in {"moderate", "strong"}:
        return "comparison_ready"
    if needs_verify and official_count >= 1 and strength in {"moderate", "strong"}:
        return "verification_ready"
    return ""


def _adaptive_next_step(
    *,
    decision: dict[str, Any],
    metrics: dict[str, Any],
    already_broadened: bool,
    already_narrowed: bool,
    already_compared: bool,
    already_verified: bool,
    subject_seed: str = "",
) -> tuple[str, str] | None:
    strength = str(metrics.get("strength") or "none")
    domains = list(metrics.get("domains") or [])
    # The stage seed is the request's own subject: a topic hint when the interpreter produced one,
    # else the enumerated request's subject. Never a template word -- measured 2026-09-06, an
    # empty hint produced the literal query "topic comparison tradeoffs sources", twice.
    seed_hint = str((list(decision.get("topic_hints") or [])[:1] or [""])[0]).strip() or str(subject_seed or "").strip()
    entity_retry_query = str(decision.get("entity_retry_query") or "").strip()
    if decision.get("public_entity_lookup") and strength in {"none", "weak"} and entity_retry_query and not already_narrowed:
        return ("narrow_search", entity_retry_query)
    if decision.get("needs_compare") and int(metrics.get("domain_count") or 0) < 2 and not already_compared and seed_hint:
        query = f"{seed_hint} comparison tradeoffs sources".strip()
        return ("compare_sources", query)
    if decision.get("needs_verify") and int(metrics.get("official_count") or 0) < 1 and not already_verified:
        verify_base = seed_hint or str((domains[:1] or [""])[0]).strip() or "claim"
        return ("verify_claim", f"{verify_base} official source verify")
    if strength in {"none", "weak"} and decision.get("needs_specific_focus") and not already_narrowed:
        focus = seed_hint or "specific error"
        return ("narrow_search", f"{focus} exact fix documentation")
    if strength in {"none", "weak"} and not already_broadened and seed_hint:
        return ("broaden_search", f"{seed_hint} overview reliable sources")
    return None


def _adaptive_uncertainty_reason(*, result: AdaptiveResearchResult, decision: dict[str, Any]) -> str:
    if result.tool_gap_note:
        return result.tool_gap_note
    if decision.get("public_entity_lookup") and not result.notes:
        return "I couldn't pin down that public figure confidently from live evidence."
    if decision.get("public_entity_lookup") and result.notes and result.evidence_strength in {"weak", "none"}:
        return "I found some signals, but not enough to identify that public figure confidently."
    if not result.notes:
        return "No grounded live evidence came back for this question."
    if decision.get("needs_verify") and not result.verified_claim:
        return "I found some signals, but not enough authoritative evidence to verify the claim cleanly."
    if decision.get("needs_compare") and len(result.source_domains) < 2:
        return "I found some signals, but not enough independent sources to compare this confidently."
    return "The evidence stayed thin or inconsistent, so the answer should stay tentative."
