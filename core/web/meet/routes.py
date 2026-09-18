from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import unquote

from pydantic import ValidationError

from core import audit_logger, policy_engine
from core.brain_hive_dashboard import (
    build_dashboard_snapshot,
    render_dashboard_html,
    render_not_found_html,
    render_topic_detail_html,
)
from core.brain_hive_models import (
    HiveClaimLinkRequest,
    HiveCommonsCommentRequest,
    HiveCommonsEndorseRequest,
    HiveCommonsPromotionActionRequest,
    HiveCommonsPromotionCandidateRequest,
    HiveCommonsPromotionReviewRequest,
    HiveModerationReviewRequest,
    HivePostCreateRequest,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicDeleteRequest,
    HiveTopicStatusUpdateRequest,
    HiveTopicUpdateRequest,
)
from core.brain_hive_service import BrainHiveService
from core.meet_and_greet_models import (
    ApiEnvelope,
    KnowledgeChallengeIssueRequest,
    KnowledgeChallengeResponseRequest,
    KnowledgeChallengeVerifyRequest,
    KnowledgeSearchRequest,
    MeetNodeRegisterRequest,
    PaymentStatusUpsertRequest,
    PresenceUpsertRequest,
    PresenceWithdrawRequest,
)
from core.meet_and_greet_service import MeetAndGreetService
from core.public_landing_page import render_public_landing_page_html
from core.public_status_page import render_public_status_page_html
from network.knowledge_models import KnowledgeAdvert, KnowledgeRefresh, KnowledgeReplicaAd, KnowledgeWithdraw
from network.signer import get_local_peer_id

from .readiness import build_meet_readiness

_SCOPED_HIVE_WRITE_PATHS = {
    "/v1/hive/topics",
    "/v1/hive/posts",
    "/v1/hive/topic-claims",
    "/v1/hive/topic-status",
    "/v1/hive/topic-update",
    "/v1/hive/topic-delete",
    "/v1/hive/commons/endorsements",
    "/v1/hive/commons/comments",
    "/v1/hive/commons/promotion-candidates",
    "/v1/hive/commons/promotion-reviews",
    "/v1/hive/commons/promotions",
}


def resolve_static_route(path: str) -> tuple[int, str, bytes] | None:
    clean_path = path.rstrip("/") or "/"
    if clean_path == "/":
        return 200, "text/html; charset=utf-8", render_public_landing_page_html().encode("utf-8")
    if clean_path == "/status":
        return 200, "text/html; charset=utf-8", render_public_status_page_html().encode("utf-8")
    if clean_path == "/brain-hive":
        return 200, "text/html; charset=utf-8", render_dashboard_html(public_surface=False).encode("utf-8")
    if clean_path == "/hive":
        return 200, "text/html; charset=utf-8", render_dashboard_html(public_surface=True).encode("utf-8")
    voolbook_surface_by_path = {
        "/voolbook": "feed",
        "/feed": "feed",
        "/tasks": "tasks",
        "/agents": "agents",
        "/proof": "proof",
    }
    if clean_path in voolbook_surface_by_path:
        from core.voolbook_feed_page import render_voolbook_page_html

        return (
            200,
            "text/html; charset=utf-8",
            render_voolbook_page_html(initial_tab=voolbook_surface_by_path[clean_path]).encode("utf-8"),
        )
    if clean_path.startswith("/agent/"):
        from core.voolbook_profile_page import render_voolbook_profile_page_html

        handle = unquote(clean_path.removeprefix("/agent/").strip("/"))
        if handle:
            return 200, "text/html; charset=utf-8", render_voolbook_profile_page_html(handle=handle).encode("utf-8")
    if clean_path.startswith("/task/"):
        topic_id = unquote(clean_path.removeprefix("/task/").strip("/"))
        if topic_id:
            return (
                200,
                "text/html; charset=utf-8",
                render_topic_detail_html(
                    topic_api_endpoint=f"/v1/hive/topics/{topic_id}",
                    posts_api_endpoint=f"/v1/hive/topics/{topic_id}/posts",
                ).encode("utf-8"),
            )
    if clean_path.startswith("/brain-hive/topic/"):
        topic_id = unquote(clean_path.removeprefix("/brain-hive/topic/").strip("/"))
        if topic_id:
            return (
                200,
                "text/html; charset=utf-8",
                render_topic_detail_html(
                    topic_api_endpoint=f"/v1/hive/topics/{topic_id}",
                    posts_api_endpoint=f"/v1/hive/topics/{topic_id}/posts",
                ).encode("utf-8"),
            )
    if clean_path == "/earnings":
        from core.earnings_page import render_earnings_html

        return 200, "text/html; charset=utf-8", render_earnings_html().encode("utf-8")
    if clean_path in {"/null-browser", "/web0"}:
        # /web0 is the public-facing alias for the .null browser: a normal browser
        # cannot open a .null site (it lives on Arweave behind the resolver), so this
        # VOOL-served page is the entry point. Both paths render the same UI.
        from core.null_browser_page import render_null_browser_html

        return 200, "text/html; charset=utf-8", render_null_browser_html().encode("utf-8")
    if clean_path == "/404":
        return 404, "text/html; charset=utf-8", render_not_found_html(path).encode("utf-8")
    return None


def dispatch_request(
    method: str,
    path: str,
    query: dict[str, list[str]] | None,
    payload: dict[str, Any] | None,
    service: MeetAndGreetService,
    hive_service: BrainHiveService | None = None,
    metrics: Any | None = None,
    *,
    request_meta: dict[str, Any] | None = None,
    policy_get=None,
) -> tuple[int, dict[str, Any]]:
    clean_path = path.rstrip("/") or "/"
    query = query or {}
    payload = payload or {}
    hive = hive_service or BrainHiveService()
    try:
        if method == "GET":
            if clean_path == "/v1/metrics":
                return _ok(metrics.snapshot() if metrics else {})
            if clean_path == "/v1/hive/dashboard":
                topic_limit = _query_int(query, "topic_limit") or 12
                post_limit = _query_int(query, "post_limit") or 24
                agent_limit = _query_int(query, "agent_limit") or 24
                return _ok(
                    build_dashboard_snapshot(
                        hive=hive,
                        topic_limit=topic_limit,
                        post_limit=post_limit,
                        agent_limit=agent_limit,
                    )
                )
            if clean_path == "/v1/health":
                return _ok(service.health().model_dump(mode="json"))
            if clean_path in {"/v1/readyz", "/readyz"}:
                readiness = build_meet_readiness(service)
                payload = readiness.model_dump(mode="json")
                if readiness.status == "ready":
                    return _ok(payload)
                return 503, ApiEnvelope(ok=False, result=payload, error="Meet service is not ready.").model_dump(mode="json")
            if clean_path == "/v1/cluster/nodes":
                limit = _query_int(query, "limit")
                active_only = _query_bool(query, "active_only", default=True)
                rows = [item.model_dump(mode="json") for item in service.list_meet_nodes(limit=limit, active_only=active_only)]
                return _ok(rows)
            if clean_path == "/v1/cluster/sync-state":
                limit = _query_int(query, "limit")
                rows = [item.model_dump(mode="json") for item in service.list_sync_state(limit=limit)]
                return _ok(rows)
            if clean_path == "/v1/presence/active":
                limit = _query_int(query, "limit")
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                rows = [
                    item.model_dump(mode="json")
                    for item in service.list_presence(limit=limit, target_region=target_region, summary_mode=summary_mode)
                ]
                return _ok(rows)
            if clean_path == "/v1/knowledge/index":
                limit = _query_int(query, "limit")
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                rows = [
                    item.model_dump(mode="json")
                    for item in service.list_knowledge_index(limit=limit, target_region=target_region, summary_mode=summary_mode)
                ]
                return _ok(rows)
            if clean_path.startswith("/v1/knowledge/entries/"):
                shard_id = clean_path.split("/v1/knowledge/entries/", 1)[1]
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                return _ok(
                    service.get_knowledge_entry(
                        shard_id,
                        target_region=target_region,
                        summary_mode=summary_mode,
                    ).model_dump(mode="json")
                )
            if clean_path == "/v1/index/snapshot":
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                return _ok(service.get_snapshot(target_region=target_region, summary_mode=summary_mode).model_dump(mode="json"))
            if clean_path == "/v1/index/deltas":
                since_created_at = _query_str(query, "since_created_at")
                limit = _query_int(query, "limit")
                rows = [item.model_dump(mode="json") for item in service.get_deltas(since_created_at=since_created_at, limit=limit)]
                return _ok(rows)
            if clean_path == "/v1/payments/status":
                limit = _query_int(query, "limit")
                rows = [item.model_dump(mode="json") for item in service.list_payment_status(limit=limit)]
                return _ok(rows)
            if clean_path.startswith("/v1/knowledge/challenges/"):
                return _error(405, f"Unsupported method: {method}")
            if clean_path == "/v1/hive/topics":
                status = _query_str(query, "status")
                limit = _query_int(query, "limit") or 100
                include_flagged = _query_bool(query, "include_flagged", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_topics(status=status, limit=limit, include_flagged=include_flagged)
                ]
                return _ok(rows)
            if clean_path == "/v1/hive/review-queue":
                limit = _query_int(query, "limit") or 50
                object_type = _query_str(query, "object_type")
                return _ok(hive.list_review_queue(object_type=object_type, limit=limit))
            if clean_path == "/v1/hive/research-queue":
                limit = _query_int(query, "limit") or 24
                return _ok(hive.list_research_queue(limit=limit))
            if clean_path == "/v1/hive/commons/promotion-candidates":
                limit = _query_int(query, "limit") or 50
                status = _query_str(query, "status")
                rows = [item.model_dump(mode="json") for item in hive.list_commons_promotion_candidates(limit=limit, status=status)]
                return _ok(rows)
            if clean_path.startswith("/v1/hive/commons/posts/") and clean_path.endswith("/endorsements"):
                post_id = clean_path.removeprefix("/v1/hive/commons/posts/").removesuffix("/endorsements").strip("/")
                if not post_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                rows = [item.model_dump(mode="json") for item in hive.list_post_endorsements(post_id, limit=limit)]
                return _ok(rows)
            if clean_path.startswith("/v1/hive/commons/posts/") and clean_path.endswith("/comments"):
                post_id = clean_path.removeprefix("/v1/hive/commons/posts/").removesuffix("/comments").strip("/")
                if not post_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                include_flagged = _query_bool(query, "include_flagged", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_post_comments(post_id, limit=limit, include_flagged=include_flagged)
                ]
                return _ok(rows)
            if clean_path == "/v1/hive/artifacts/search":
                query_text = _query_str(query, "q") or ""
                topic_id = _query_str(query, "topic_id")
                limit = _query_int(query, "limit") or 24
                return _ok(hive.search_artifacts(query_text, topic_id=topic_id, limit=limit))
            if clean_path.startswith("/v1/hive/topics/") and clean_path.endswith("/research-packet"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").removesuffix("/research-packet").strip("/")
                if not topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                return _ok(hive.get_topic_research_packet(topic_id))
            if clean_path.startswith("/v1/hive/topics/") and clean_path.endswith("/posts"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").removesuffix("/posts").strip("/")
                if not topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                include_flagged = _query_bool(query, "include_flagged", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_posts(topic_id, limit=limit, include_flagged=include_flagged)
                ]
                return _ok(rows)
            if clean_path.startswith("/v1/hive/topics/") and clean_path.endswith("/claims"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").removesuffix("/claims").strip("/")
                if not topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                active_only = _query_bool(query, "active_only", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_topic_claims(topic_id, limit=limit, active_only=active_only)
                ]
                return _ok(rows)
            if clean_path == "/v1/hive/moderation/reviews":
                object_type = _query_str(query, "object_type")
                object_id = _query_str(query, "object_id")
                if not object_type or not object_id:
                    return _error(422, "object_type and object_id are required.")
                return _ok(hive.get_review_summary(object_type, object_id).model_dump(mode="json"))
            if clean_path.startswith("/v1/hive/topics/"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").strip("/")
                if not topic_id or "/" in topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                include_flagged = _query_bool(query, "include_flagged", default=False)
                return _ok(hive.get_topic(topic_id, include_flagged=include_flagged).model_dump(mode="json"))
            if clean_path == "/v1/hive/events":
                limit = _query_int(query, "limit") or 100
                return _ok(build_dashboard_snapshot(hive=hive, topic_limit=32, post_limit=48, agent_limit=24)["task_event_stream"][:limit])
            if clean_path == "/v1/hive/agents":
                limit = _query_int(query, "limit") or 100
                online_only = _query_bool(query, "online_only", default=False)
                rows = [item.model_dump(mode="json") for item in hive.list_agent_profiles(limit=limit, online_only=online_only)]
                return _ok(rows)
            if clean_path == "/v1/hive/stats":
                return _ok(hive.get_stats().model_dump(mode="json"))
            if clean_path == "/v1/voolbook/feed":
                return _handle_voolbook_feed(query)
            if clean_path.startswith("/v1/voolbook/profile/"):
                handle = clean_path.removeprefix("/v1/voolbook/profile/").strip("/")
                return _handle_voolbook_profile(handle, query)
            if clean_path.startswith("/v1/voolbook/check-handle/"):
                handle = clean_path.removeprefix("/v1/voolbook/check-handle/").strip("/")
                return _handle_voolbook_check_handle(handle)
            if clean_path.startswith("/v1/voolbook/post/") and not clean_path.endswith("/reply"):
                post_id = clean_path.removeprefix("/v1/voolbook/post/").strip("/")
                return _handle_voolbook_get_post(post_id)
            if clean_path == "/v1/voolbook/search":
                return _handle_voolbook_search(query)
            if clean_path == "/v1/hive/search":
                return _handle_hive_search(query)
            if clean_path == "/v1/wallet/info":
                from core.vool_wallet import legacy_wallet_view

                return _ok(legacy_wallet_view(include_balances=True))
            if clean_path == "/v1/credits/balance":
                from core.credit_ledger import get_credit_balance, list_credit_ledger_entries
                from network.signer import get_local_peer_id

                peer_id = _query_str(query, "peer_id") or get_local_peer_id()
                limit = _query_int(query, "limit") or 20
                return _ok({
                    "peer_id": peer_id,
                    "balance": get_credit_balance(peer_id),
                    "entries": list_credit_ledger_entries(peer_id, limit=limit),
                })
            if clean_path == "/v1/tasks/queue":
                from storage.task_offer_store import list_open_task_offers

                limit = _query_int(query, "limit") or 50
                return _ok(list_open_task_offers(limit=limit))
            if clean_path.startswith("/v1/tasks/") and "/" not in clean_path.removeprefix("/v1/tasks/"):
                task_id = clean_path.removeprefix("/v1/tasks/").strip("/")
                from storage.task_offer_store import get_task_offer

                offer = get_task_offer(task_id)
                if offer is None:
                    return _error(404, f"Task not found: {task_id}")
                return _ok(offer)
            if clean_path == "/v1/workers":
                from core.web0_mesh_registry import evict_expired, list_workers

                evict_expired()
                active_only = _query_bool(query, "active_only", default=True)
                limit = _query_int(query, "limit") or 200
                return _ok({"workers": list_workers(active_only=active_only, limit=limit)})
            if clean_path.startswith("/v1/workers/"):
                from core.web0_mesh_registry import get_worker

                worker_id = clean_path.removeprefix("/v1/workers/").strip("/")
                if not worker_id:
                    return _error(404, "worker_id required in path")
                entry = get_worker(worker_id)
                if entry is None:
                    return _error(404, f"Worker not found: {worker_id}")
                return _ok(entry)
            return _error(404, f"Unknown GET path: {clean_path}")

        if method == "POST":
            if clean_path == "/v1/cluster/nodes":
                model = MeetNodeRegisterRequest.model_validate(payload)
                return _ok(service.register_meet_node(model).model_dump(mode="json"))
            if clean_path == "/v1/presence/register":
                model = PresenceUpsertRequest.model_validate(payload)
                return _ok(service.register_presence(model, request_meta=request_meta).model_dump(mode="json"))
            if clean_path == "/v1/presence/heartbeat":
                model = PresenceUpsertRequest.model_validate(payload)
                return _ok(service.heartbeat_presence(model, request_meta=request_meta).model_dump(mode="json"))
            if clean_path == "/v1/presence/withdraw":
                model = PresenceWithdrawRequest.model_validate(payload)
                return _ok(service.withdraw_presence(model))
            if clean_path == "/v1/knowledge/advertise":
                model = KnowledgeAdvert.model_validate(payload)
                return _ok(service.advertise_knowledge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/replicate":
                model = KnowledgeReplicaAd.model_validate(payload)
                return _ok(service.replicate_knowledge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/refresh":
                model = KnowledgeRefresh.model_validate(payload)
                return _ok(service.refresh_knowledge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/withdraw":
                model = KnowledgeWithdraw.model_validate(payload)
                return _ok(service.withdraw_knowledge(model))
            if clean_path == "/v1/knowledge/search":
                model = KnowledgeSearchRequest.model_validate(payload)
                rows = [item.model_dump(mode="json") for item in service.search_knowledge(model)]
                return _ok(rows)
            if clean_path == "/v1/payments/status":
                model = PaymentStatusUpsertRequest.model_validate(payload)
                return _ok(service.upsert_payment_status(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/challenges/issue":
                model = KnowledgeChallengeIssueRequest.model_validate(payload)
                return _ok(service.issue_knowledge_challenge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/challenges/respond":
                model = KnowledgeChallengeResponseRequest.model_validate(payload)
                return _ok(service.respond_knowledge_challenge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/challenges/verify":
                model = KnowledgeChallengeVerifyRequest.model_validate(payload)
                return _ok(service.verify_knowledge_challenge(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topics":
                model = HiveTopicCreateRequest.model_validate(payload)
                return _ok(hive.create_topic(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/posts":
                model = HivePostCreateRequest.model_validate(payload)
                return _ok(hive.create_post(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topic-claims":
                model = HiveTopicClaimRequest.model_validate(payload)
                return _ok(hive.claim_topic(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topic-status":
                model = HiveTopicStatusUpdateRequest.model_validate(payload)
                return _ok(hive.update_topic_status(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topic-update":
                model = HiveTopicUpdateRequest.model_validate(payload)
                return _ok(hive.update_topic(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topic-delete":
                model = HiveTopicDeleteRequest.model_validate(payload)
                return _ok(hive.delete_topic(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/claim-links":
                model = HiveClaimLinkRequest.model_validate(payload)
                return _ok(hive.claim_link(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/moderation/reviews":
                model = HiveModerationReviewRequest.model_validate(payload)
                return _ok(hive.review_object(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/endorsements":
                model = HiveCommonsEndorseRequest.model_validate(payload)
                return _ok(hive.endorse_post(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/comments":
                model = HiveCommonsCommentRequest.model_validate(payload)
                return _ok(hive.comment_on_post(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/promotion-candidates":
                model = HiveCommonsPromotionCandidateRequest.model_validate(payload)
                return _ok(hive.evaluate_promotion_candidate(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/promotion-reviews":
                model = HiveCommonsPromotionReviewRequest.model_validate(payload)
                return _ok(hive.review_promotion_candidate(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/promotions":
                model = HiveCommonsPromotionActionRequest.model_validate(payload)
                return _ok(hive.promote_commons_candidate(model).model_dump(mode="json"))
            if clean_path == "/v1/voolbook/post":
                return _handle_voolbook_create_post(payload)
            if clean_path.startswith("/v1/voolbook/post/") and clean_path.endswith("/reply"):
                parent_id = clean_path.removeprefix("/v1/voolbook/post/").removesuffix("/reply").strip("/")
                return _handle_voolbook_reply(parent_id, payload)
            if clean_path == "/v1/voolbook/register":
                return _handle_voolbook_register(payload)
            if clean_path.startswith("/v1/voolbook/post/") and clean_path.endswith("/edit"):
                post_id = clean_path.removeprefix("/v1/voolbook/post/").removesuffix("/edit").strip("/")
                return _handle_voolbook_edit_post(post_id, payload)
            if clean_path.startswith("/v1/voolbook/post/") and clean_path.endswith("/delete"):
                post_id = clean_path.removeprefix("/v1/voolbook/post/").removesuffix("/delete").strip("/")
                return _handle_voolbook_delete_post(post_id, payload)
            if clean_path == "/v1/voolbook/upvote":
                return _handle_voolbook_upvote(payload)
            if clean_path == "/v1/workers/announce":
                from core.web0_mesh_registry import announce_worker

                result = announce_worker(payload)
                if not result.get("ok"):
                    return _error(422, result.get("error") or "Invalid announce payload")
                return _ok(result)
            if clean_path.startswith("/v1/tasks/") and clean_path.endswith("/claim"):
                task_id = clean_path.removeprefix("/v1/tasks/").removesuffix("/claim").strip("/")
                from network.signer import get_local_peer_id
                from storage.task_offer_store import claim_task_offer

                # The claimant is the SIGNED identity (request_meta), never the
                # caller-supplied helper_peer_id — recording an attacker-named
                # wallet here would let it later collect the escrow. Fall back to
                # the local peer id only for unsigned/local dispatch.
                signed_peer_id = _signed_peer_id(request_meta)
                helper_id = signed_peer_id or get_local_peer_id()
                if not claim_task_offer(task_id, helper_id):
                    return _error(409, "Task already claimed or not found")
                return _ok({"task_id": task_id, "status": "claimed", "helper_peer_id": helper_id})
            if clean_path.startswith("/v1/tasks/") and clean_path.endswith("/complete"):
                task_id = clean_path.removeprefix("/v1/tasks/").removesuffix("/complete").strip("/")
                result_hash = str(payload.get("result_hash") or "")
                from core.credit_ledger import get_escrow_for_task, release_escrow_to_helper
                from network.signer import get_local_peer_id
                from storage.task_offer_store import complete_task_offer, get_task_offer_claimed_by

                # The payout recipient is derived from the SIGNED identity, never
                # from the attacker-controlled request body (helper_peer_id). A
                # caller-named wallet must never receive the escrow remainder.
                signed_peer_id = _signed_peer_id(request_meta)
                completer_id = signed_peer_id or get_local_peer_id()
                if not completer_id:
                    return _error(403, "Signed identity required to complete a task.")
                claimed_by = get_task_offer_claimed_by(task_id)
                if claimed_by and completer_id != claimed_by:
                    return _error(403, "Only the peer that claimed this task may complete it.")
                # Pay the recorded claimer when known, otherwise the verified
                # completer (legacy rows that never stored claimed_by). Either
                # way the recipient is an authenticated identity, not the body.
                payout_recipient = claimed_by or completer_id
                complete_task_offer(task_id, result_hash)
                escrow = get_escrow_for_task(task_id)
                released = 0.0
                if escrow and escrow.get("status") == "active":
                    payout = float(escrow.get("total_escrowed") or 0) - float(escrow.get("total_released") or 0)
                    if payout > 0:
                        release_escrow_to_helper(task_id, payout_recipient, payout)
                        released = payout
                return _ok({
                    "task_id": task_id,
                    "status": "complete",
                    "credits_released": released,
                    "helper_peer_id": payout_recipient,
                })
            if clean_path == "/v1/credits/settle":
                from core.credit_ledger import reconcile_ledger
                from network.signer import get_local_peer_id

                peer_id = str(payload.get("peer_id") or get_local_peer_id())
                result = reconcile_ledger(peer_id)
                return _ok({"peer_id": result.peer_id, "balance": result.balance, "entries": result.entries, "mode": result.mode})
            return _error(404, f"Unknown POST path: {clean_path}")

        return _error(405, f"Unsupported method: {method}")
    except ValidationError as exc:
        audit_logger.log(
            "meet_dispatch_validation_error",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(422, "Invalid request payload.")
    except ValueError as exc:
        audit_logger.log(
            "meet_dispatch_value_error",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(400, str(exc) or "Invalid request.")
    except KeyError as exc:
        audit_logger.log(
            "meet_dispatch_missing_resource",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(404, "Resource not found.")
    except Exception as exc:
        audit_logger.log(
            "meet_dispatch_error",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(500, "Request handling failed.")


def _query_int(
    query: dict[str, list[str]],
    key: str,
    *,
    policy_get=None,
) -> int | None:
    raw = _query_str(query, key)
    if not raw:
        return None
    try:
        value = int(raw)
    except Exception:
        return None
    getter = policy_get or policy_engine.get
    max_limit = max(1, int(getter("meet.max_query_limit", 2000)))
    if value <= 0:
        return None
    if value > max_limit:
        return max_limit
    return value


def _query_str(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def _query_bool(query: dict[str, list[str]], key: str, *, default: bool) -> bool:
    raw = _query_str(query, key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _query_summary_mode(query: dict[str, list[str]]) -> str:
    raw = _query_str(query, "summary_mode")
    if raw in {"regional_detail", "global_summary"}:
        return raw
    return "regional_detail"


def _verify_voolbook_token_safe(raw_token: str) -> str | None:
    try:
        from core.voolbook_identity import verify_token

        return verify_token(raw_token)
    except Exception:
        return None


def _voolbook_post_hook(peer_id: str) -> None:
    try:
        from core.voolbook_identity import increment_post_count

        increment_post_count(peer_id)
    except Exception:
        pass


def _handle_voolbook_feed(query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from storage.voolbook_store import get_post_by_id, list_feed, list_replies, post_to_dict

    post_id = _query_str(query, "post_id") or ""
    if post_id:
        post = get_post_by_id(post_id)
        if not post:
            return _ok({"posts": [], "count": 0})
        entry = post_to_dict(post)
        entry["author"] = _voolbook_author_summary(post.peer_id, post.handle)
        return _ok({"posts": [entry], "count": 1})
    parent = _query_str(query, "parent") or ""
    if parent:
        limit = _query_int(query, "limit") or 50
        posts = list_replies(parent, limit=limit)
        items = []
        for post in posts:
            entry = post_to_dict(post)
            entry["author"] = _voolbook_author_summary(post.peer_id, post.handle)
            items.append(entry)
        return _ok({"posts": items, "count": len(items)})
    limit = _query_int(query, "limit") or 20
    before = _query_str(query, "before") or ""
    posts = list_feed(limit=limit, before=before)
    items = []
    for post in posts:
        entry = post_to_dict(post)
        entry["author"] = _voolbook_author_summary(post.peer_id, post.handle)
        items.append(entry)
    return _ok({"posts": items, "count": len(items)})


def _handle_voolbook_profile(handle: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from core.voolbook_identity import get_profile_by_handle
    from core.scoreboard_engine import get_peer_scoreboard
    from storage.voolbook_store import count_posts, list_user_posts, post_to_dict

    if not handle:
        return _error(400, "Handle is required.")
    profile = get_profile_by_handle(handle)
    if not profile:
        return _error(404, f"No VoolBook profile found for handle '{handle}'.")
    limit = _query_int(query, "limit") or 20
    posts = list_user_posts(handle, limit=limit)
    active_post_count = count_posts(handle=profile.handle)
    board = get_peer_scoreboard(profile.peer_id)
    return _ok(
        {
            "profile": {
                "peer_id": profile.peer_id,
                "handle": profile.handle,
                "display_name": profile.display_name,
                "bio": profile.bio,
                "avatar_seed": profile.avatar_seed,
                "twitter_handle": profile.twitter_handle or "",
                "post_count": active_post_count,
                "claim_count": profile.claim_count,
                "glory_score": profile.glory_score,
                "status": profile.status,
                "joined_at": profile.joined_at,
                "tier": board.tier,
                "trust_score": board.trust,
                "provider_score": board.provider,
                "validator_score": board.validator,
                "finality_ratio": board.finality_ratio,
                "pending_work_count": board.pending_work_count,
                "confirmed_work_count": board.confirmed_work_count,
                "finalized_work_count": board.finalized_work_count,
                "rejected_work_count": board.rejected_work_count,
                "slashed_work_count": board.slashed_work_count,
            },
            "posts": [post_to_dict(p) for p in posts],
        }
    )


def _handle_voolbook_check_handle(handle: str) -> tuple[int, dict[str, Any]]:
    from core.agent_name_registry import get_peer_by_name, validate_agent_name
    from core.voolbook_identity import get_profile_by_handle

    if not handle:
        return _error(400, "Handle is required.")
    valid, reason = validate_agent_name(handle)
    if not valid:
        return _ok({"available": False, "reason": reason})
    existing = get_peer_by_name(handle)
    if existing:
        return _ok({"available": False, "reason": f"Handle '{handle}' is already claimed."})
    profile = get_profile_by_handle(handle)
    if profile:
        return _ok({"available": False, "reason": f"Handle '{handle}' is already taken on VoolBook."})
    return _ok({"available": True, "reason": "Handle is available."})


def _handle_voolbook_get_post(post_id: str) -> tuple[int, dict[str, Any]]:
    from storage.voolbook_store import get_post, list_replies, post_to_dict

    if not post_id:
        return _error(400, "Post ID is required.")
    post = get_post(post_id)
    if not post:
        return _error(404, "Post not found.")
    entry = post_to_dict(post)
    entry["author"] = _voolbook_author_summary(post.peer_id, post.handle)
    replies = list_replies(post_id, limit=50)
    reply_items = []
    for reply in replies:
        item = post_to_dict(reply)
        item["author"] = _voolbook_author_summary(reply.peer_id, reply.handle)
        reply_items.append(item)
    entry["replies"] = reply_items
    return _ok(entry)


def _handle_voolbook_create_post(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.voolbook_identity import get_profile, increment_post_count
    from storage.voolbook_store import create_post, post_to_dict

    peer_id = str(payload.get("voolbook_peer_id") or "").strip()
    if not peer_id:
        return _error(401, "VoolBook token required. Include X-VoolBook-Token header.")
    profile = get_profile(peer_id)
    if not profile or profile.status != "active":
        return _error(403, "VoolBook profile not found or inactive.")
    content = str(payload.get("content") or "").strip()
    if not content:
        return _error(400, "Post content is required.")
    if len(content) > 5000:
        return _error(400, "Post content too long (max 5000 chars).")
    post = create_post(
        peer_id=peer_id,
        handle=profile.handle,
        content=content,
        post_type=str(payload.get("post_type") or "social").strip()[:20],
        origin_kind=str(payload.get("_voolbook_origin_kind") or "human").strip()[:16],
        origin_channel=str(payload.get("_voolbook_origin_channel") or "voolbook_token").strip()[:32],
        origin_peer_id=str(payload.get("_voolbook_origin_peer_id") or peer_id).strip(),
        hive_post_id=str(payload.get("hive_post_id") or "").strip(),
        topic_id=str(payload.get("topic_id") or "").strip(),
        link_url=str(payload.get("link_url") or "").strip()[:500],
        link_title=str(payload.get("link_title") or "").strip()[:200],
    )
    increment_post_count(peer_id)
    entry = post_to_dict(post)
    entry["author"] = _voolbook_author_summary(post.peer_id, post.handle)
    return _ok(entry)


def _handle_voolbook_reply(parent_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.voolbook_identity import get_profile
    from storage.voolbook_store import create_post, get_post, post_to_dict

    peer_id = str(payload.get("voolbook_peer_id") or "").strip()
    if not peer_id:
        return _error(401, "VoolBook token required.")
    profile = get_profile(peer_id)
    if not profile or profile.status != "active":
        return _error(403, "VoolBook profile not found or inactive.")
    parent = get_post(parent_id)
    if not parent:
        return _error(404, "Parent post not found.")
    content = str(payload.get("content") or "").strip()
    if not content:
        return _error(400, "Reply content is required.")
    if len(content) > 5000:
        return _error(400, "Reply content too long (max 5000 chars).")
    post = create_post(
        peer_id=peer_id,
        handle=profile.handle,
        content=content,
        post_type="reply",
        origin_kind=str(payload.get("_voolbook_origin_kind") or "human").strip()[:16],
        origin_channel=str(payload.get("_voolbook_origin_channel") or "voolbook_token").strip()[:32],
        origin_peer_id=str(payload.get("_voolbook_origin_peer_id") or peer_id).strip(),
        parent_post_id=parent_id,
    )
    return _ok(post_to_dict(post))


def _handle_voolbook_register(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.voolbook_identity import get_profile, register_voolbook_account, rename_handle, update_profile

    handle = str(payload.get("handle") or "").strip()
    bio = str(payload.get("bio") or "").strip()
    twitter = str(payload.get("twitter_handle") or "").strip()
    peer_id = str(payload.get("peer_id") or payload.get("voolbook_peer_id") or "").strip()
    if not handle:
        return _error(400, "Handle is required.")
    if not peer_id:
        return _error(400, "Peer ID is required.")
    display_name = str(payload.get("display_name") or "").strip()
    existing = get_profile(peer_id)
    if existing and existing.status == "active":
        if handle != existing.handle:
            try:
                existing = rename_handle(peer_id, handle)
            except Exception as exc:
                return _error(409, str(exc))
        updates: dict[str, Any] = {}
        if bio and bio != existing.bio:
            updates["bio"] = bio
        if twitter:
            updates["twitter_handle"] = twitter
        if display_name and display_name != existing.display_name:
            updates["display_name"] = display_name
        if updates:
            update_profile(peer_id, **updates)
            existing = get_profile(peer_id)
        return _ok(
            {
                "handle": existing.handle,
                "display_name": existing.display_name,
                "bio": existing.bio,
                "twitter_handle": existing.twitter_handle,
                "status": existing.status,
                "joined_at": existing.joined_at,
            }
        )
    try:
        registration = register_voolbook_account(handle, bio=bio, peer_id=peer_id, twitter_handle=twitter)
    except Exception as exc:
        return _error(409, str(exc))
    if display_name and display_name != registration.profile.display_name:
        update_profile(registration.profile.peer_id, display_name=display_name)
        registration_profile = get_profile(registration.profile.peer_id)
    else:
        registration_profile = registration.profile
    return _ok(
        {
            "handle": registration_profile.handle,
            "display_name": registration_profile.display_name,
            "bio": registration_profile.bio,
            "twitter_handle": registration_profile.twitter_handle,
            "status": registration_profile.status,
            "joined_at": registration_profile.joined_at,
        }
    )


def _handle_voolbook_edit_post(post_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from storage.voolbook_store import post_to_dict, update_post

    peer_id = str(payload.get("voolbook_peer_id") or "").strip()
    new_content = str(payload.get("content") or "").strip()
    if not peer_id:
        return _error(401, "VoolBook peer ID required.")
    if not new_content:
        return _error(400, "New content is required.")
    if len(new_content) > 5000:
        return _error(400, "Content too long (max 5000 chars).")
    updated = update_post(post_id, peer_id, new_content)
    if not updated:
        return _error(404, "Post not found, not yours, or not a social post.")
    return _ok(post_to_dict(updated))


def _handle_voolbook_delete_post(post_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from storage.voolbook_store import delete_post

    peer_id = str(payload.get("voolbook_peer_id") or "").strip()
    if not peer_id:
        return _error(401, "VoolBook peer ID required.")
    deleted = delete_post(post_id, peer_id)
    if not deleted:
        return _error(404, "Post not found, not yours, or not a social post.")
    return _ok({"deleted": True, "post_id": post_id})


def _handle_voolbook_upvote(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from storage.voolbook_store import ensure_upvote_columns, post_to_dict, upvote_post

    if not _voolbook_human_upvotes_enabled():
        return _error(403, "Public human voting is disabled on this runtime.")
    ensure_upvote_columns()
    post_id = str(payload.get("post_id") or "").strip()
    vote_type = str(payload.get("vote_type") or "human").strip()
    if vote_type not in ("human", "agent"):
        vote_type = "human"
    if not post_id:
        return _error(400, "post_id is required.")
    post = upvote_post(post_id, vote_type=vote_type)
    if not post:
        return _error(404, "Post not found.")
    return _ok(post_to_dict(post))


def _handle_voolbook_search(query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from storage.voolbook_store import post_to_dict, search_posts

    q = _query_str(query, "q") or ""
    if not q or len(q) < 2:
        return _error(400, "Query parameter 'q' is required (min 2 chars).")
    post_type = _query_str(query, "type") or ""
    limit = _query_int(query, "limit") or 20
    posts = search_posts(q, limit=limit, post_type=post_type)
    items = []
    for post in posts:
        entry = post_to_dict(post)
        entry["author"] = _voolbook_author_summary(post.peer_id, post.handle)
        items.append(entry)
    return _ok({"posts": items, "count": len(items), "query": q})


def _handle_hive_search(query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from core.agent_name_registry import get_agent_name, list_agent_names
    from core.voolbook_identity import get_profile, get_profile_by_handle
    from storage.brain_hive_store import search_topics
    from storage.voolbook_store import post_to_dict, search_posts

    q = _query_str(query, "q") or ""
    if not q or len(q) < 2:
        return _error(400, "Query parameter 'q' is required (min 2 chars).")
    kind = _query_str(query, "type") or "all"
    limit = _query_int(query, "limit") or 20
    results: dict[str, Any] = {"query": q, "type": kind}
    if kind in ("all", "task", "topic"):
        topics = search_topics(q, limit=limit)
        for topic in topics:
            agent_id = str(topic.get("created_by_agent_id") or "")
            topic["creator_display_name"] = get_agent_name(agent_id) or f"agent-{agent_id[:8]}"
        results["topics"] = topics
    if kind in ("all", "agent"):
        matched = []
        for name in list_agent_names():
            if q.lower() in str(name.get("display_name") or "").lower() or q.lower() in str(name.get("peer_id") or "").lower():
                entry = {"peer_id": name["peer_id"], "display_name": name["display_name"]}
                try:
                    profile = get_profile_by_handle(name.get("display_name") or "")
                    if profile:
                        entry["twitter_handle"] = profile.twitter_handle or ""
                except Exception:
                    pass
                matched.append(entry)
            if len(matched) >= limit:
                break
        results["agents"] = matched
    if kind in ("all", "post"):
        results["posts"] = [post_to_dict(post) for post in search_posts(q, limit=limit)]
    if kind == "profile":
        peer_id = _query_str(query, "peer_id") or ""
        profile = get_profile(peer_id)
        if profile:
            results["profile"] = {
                "handle": profile.handle,
                "display_name": profile.display_name,
                "avatar_seed": profile.avatar_seed,
                "bio": profile.bio,
                "twitter_handle": profile.twitter_handle or "",
                "glory_score": profile.glory_score,
            }
    return _ok(results)


def _voolbook_author_summary(peer_id: str, handle: str) -> dict[str, Any]:
    try:
        from core.voolbook_identity import get_profile

        profile = get_profile(peer_id)
        if profile:
            return {
                "handle": profile.handle,
                "display_name": profile.display_name,
                "avatar_seed": profile.avatar_seed,
                "bio": profile.bio,
                "twitter_handle": profile.twitter_handle or "",
                "glory_score": profile.glory_score,
            }
    except Exception:
        pass
    return {
        "handle": handle,
        "display_name": handle,
        "avatar_seed": "",
        "bio": "",
        "twitter_handle": "",
        "glory_score": 0,
    }


def _requires_write_auth(host: str) -> bool:
    return host not in {"127.0.0.1", "localhost", "::1"}


def _metrics_access_allowed(host: str) -> bool:
    return not _requires_write_auth(host)


def _is_forbidden_write_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return exc.__class__.__name__ == "SignedWriteIdentityError"


def _requires_auth_for_request(host: str) -> bool:
    return _requires_write_auth(host)


def _requires_scoped_hive_grant(host: str, path: str, *, policy_get=None) -> bool:
    clean = path.rstrip("/") or "/"
    if clean not in _SCOPED_HIVE_WRITE_PATHS:
        return False
    getter = policy_get or policy_engine.get
    require_grants = bool(getter("economics.public_hive_require_scoped_write_grants", False))
    if not require_grants:
        return False
    return _requires_write_auth(host)


def _requires_public_hive_quota(host: str, path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if clean not in _SCOPED_HIVE_WRITE_PATHS:
        return False
    return _requires_write_auth(host)


def _format_public_hive_quota_error(quota: Any) -> str:
    reason = str(getattr(quota, "reason", "") or "")
    if reason == "insufficient_claim_trust":
        return "Public Hive claim blocked: peer trust is too low for claiming tasks."
    if reason == "insufficient_route_trust":
        return (
            "Public Hive write blocked: peer trust is too low for this route at tier "
            f"{getattr(quota, 'trust_tier', 'newcomer')!s}."
        )
    if reason == "daily_public_hive_quota_exhausted":
        return (
            "Public Hive write quota exhausted for today. "
            f"Used {float(getattr(quota, 'used_points', 0.0)):.1f}/"
            f"{float(getattr(quota, 'limit_points', 0.0)):.1f} points at tier "
            f"{getattr(quota, 'trust_tier', 'newcomer')!s}."
        )
    if reason == "quota_storage_error":
        return "Public Hive write blocked because quota storage failed."
    return "Public Hive write blocked by quota controls."


def _server_peer_id() -> str:
    return get_local_peer_id()


def _voolbook_human_upvotes_enabled() -> bool:
    flag = str(os.environ.get("VOOL_ENABLE_VOOLBOOK_HUMAN_UPVOTES", "") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _is_voolbook_mutation_path(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if clean == "/v1/voolbook/register":
        return True
    if clean == "/v1/voolbook/post":
        return True
    return clean.startswith("/v1/voolbook/post/") and clean.endswith(("/reply", "/edit", "/delete"))


def _enforce_voolbook_request_identity(
    path: str,
    payload: dict[str, Any],
    *,
    signer_peer_id: str,
    token_peer_id: str | None,
) -> None:
    clean = path.rstrip("/") or "/"
    identity_fields = ("peer_id", "voolbook_peer_id") if clean == "/v1/voolbook/register" else ("voolbook_peer_id",)
    identities: list[str] = []
    if signer_peer_id:
        identities.append(signer_peer_id)
    if token_peer_id:
        identities.append(str(token_peer_id).strip())
    for field in identity_fields:
        value = str(payload.get(field) or "").strip()
        if value:
            identities.append(value)
    identities = [value for value in identities if value]
    if not identities:
        return
    canonical = identities[0]
    for value in identities[1:]:
        if value != canonical:
            raise PermissionError("VoolBook write identity mismatch.")
    if clean == "/v1/voolbook/register":
        payload["peer_id"] = canonical
        if "voolbook_peer_id" in payload or token_peer_id:
            payload["voolbook_peer_id"] = canonical


def _is_protected_api_path(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if not clean.startswith("/v1/"):
        return False
    return clean not in {"/v1/health", "/v1/readyz", "/v1/voolbook/upvote"}


def _allow_write(
    bucket_key: str,
    max_requests_per_minute: int,
    windows: dict[str, deque[float]],
    lock: threading.Lock,
    *,
    max_clients: int = 4096,
) -> bool:
    if max_requests_per_minute <= 0:
        return True
    now = time.time()
    cutoff = now - 60.0
    with lock:
        stale_hosts = []
        for host, events in windows.items():
            while events and events[0] < cutoff:
                events.popleft()
            if not events:
                stale_hosts.append(host)
        for host in stale_hosts:
            windows.pop(host, None)
        if max_clients > 0 and len(windows) >= max_clients and bucket_key not in windows:
            oldest_host = min(windows.items(), key=lambda item: item[1][-1] if item[1] else 0.0)[0]
            windows.pop(oldest_host, None)
        bucket = windows.setdefault(bucket_key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests_per_minute:
            return False
        bucket.append(now)
        return True


def _resolve_write_rate_limit(
    host: str,
    path: str,
    *,
    client_host: str,
    request_meta: dict[str, Any] | None,
    default_limit: int,
    policy_get=None,
) -> tuple[str, int]:
    signer_peer_id = str(dict(request_meta or {}).get("signer_peer_id") or "").strip()
    if not signer_peer_id:
        return str(client_host or "").strip() or "anonymous", max(0, int(default_limit))
    getter = policy_get or policy_engine.get
    try:
        signed_limit = int(
            getter(
                "economics.authenticated_write_requests_per_minute",
                max(int(default_limit), 600),
            )
        )
    except (TypeError, ValueError):
        signed_limit = max(int(default_limit), 600)
    signed_limit = max(0, signed_limit)
    clean_path = str(path or "").rstrip("/") or "/"
    if _requires_public_hive_quota(host, clean_path):
        return f"hive:{signer_peer_id}:{clean_path}", signed_limit
    return f"signed:{signer_peer_id}", signed_limit


def _signed_peer_id(request_meta: dict[str, Any] | None) -> str:
    """Return the verified Ed25519 signer for this request, or '' when unsigned.

    ``request_meta`` is populated by :func:`unwrap_signed_write_with_meta` after
    the signature has been verified, so ``signer_peer_id`` is a trustworthy
    identity — unlike anything in the request body.
    """
    return str(dict(request_meta or {}).get("signer_peer_id") or "").strip()


def _ok(result: Any) -> tuple[int, dict[str, Any]]:
    return 200, ApiEnvelope(ok=True, result=result).model_dump(mode="json")


def _error(status_code: int, error: str) -> tuple[int, dict[str, Any]]:
    return status_code, ApiEnvelope(ok=False, error=error).model_dump(mode="json")


def _error_envelope(error: str) -> dict[str, Any]:
    return ApiEnvelope(ok=False, error=error).model_dump(mode="json")
