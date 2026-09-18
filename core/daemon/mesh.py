from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.capability_tokens import revoke_capability_tokens_for_task
from core.daemon.peer_delivery import send_to_peer_or_log
from core.discovery_index import peer_trust
from core.idle_assist_policy import IdleAssistConfig
from core.task_state_machine import current_state, transition
from network.assist_router import (
    build_capability_ad_message,
    build_task_assign_message,
    load_task_offer_payload,
    persist_task_assignment,
    pick_best_claim_for_task,
    prepare_task_assignment,
)
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection


def run_order_book_loop(daemon: Any) -> None:
    from core.credit_dex import check_and_generate_credit_offer
    from core.liquefy_bridge import stream_telemetry_event
    from core.order_book import global_order_book
    from network.assist_router import build_task_claim_message
    from retrieval.swarm_query import broadcast_credit_offer

    last_credit_check_time = 0.0
    last_reconcile_time = 0.0

    while daemon._order_book_running:
        time.sleep(1.0)
        try:
            accepts_hive_tasks = daemon._refresh_assist_status()
            advertised_capacity = daemon._refresh_advertised_capacity()

            now_time = time.time()
            if now_time - last_credit_check_time > 30.0:
                last_credit_check_time = now_time
                offer_dict = check_and_generate_credit_offer()
                if offer_dict:
                    broadcast_credit_offer(offer_dict)
            reconcile_interval = max(5.0, float(policy_engine.get("assist_mesh.reconcile_interval_seconds", 10)))
            if now_time - last_reconcile_time >= reconcile_interval:
                last_reconcile_time = now_time
                daemon._reconcile_mesh_state()

            if not daemon.local_capability_ad:
                continue

            current_assignments = daemon._active_assignment_count()
            if current_assignments >= advertised_capacity:
                continue

            if not accepts_hive_tasks:
                continue

            if not daemon._helper_scheduler.can_accept_mesh_task():
                continue

            available_slots = max(0, int(advertised_capacity) - int(current_assignments))
            if available_slots <= 0:
                continue

            for _ in range(available_slots):
                best_offer = global_order_book.pop_best_offer()
                if not best_offer:
                    break

                task_id = best_offer.offer_dict.get("task_id")
                parent_peer_id = best_offer.offer_dict.get("parent_agent_id", "")

                if not task_id or not parent_peer_id:
                    continue

                claim_msg = build_task_claim_message(
                    task_id=task_id,
                    declared_capabilities=daemon.local_capability_ad.capabilities,
                    current_load=current_assignments,
                    host_group_hint_hash=daemon.local_capability_ad.assist_filters.host_group_hint_hash,
                )

                if send_to_peer_or_log(
                    parent_peer_id,
                    claim_msg,
                    message_type="TASK_CLAIM",
                    target_id=str(task_id),
                    include_candidates=True,
                    fallback_addr=best_offer.source_addr,
                    send_attempt=lambda host, port, raw, _task_id=str(task_id): daemon._send_or_log(
                        host,
                        int(port),
                        raw,
                        message_type="TASK_CLAIM",
                        target_id=_task_id,
                    ),
                ):
                    current_assignments += 1
                    stream_telemetry_event("ORDER_BOOK_CLAIM", task_id, {"bid_price": best_offer.bid_price})
        except Exception as exc:
            audit_logger.log(
                "order_book_loop_error",
                target_id=local_peer_id(),
                target_type="daemon",
                details={"error": str(exc)},
            )
            time.sleep(0.2)


def build_capability_ad_message_payload(daemon: Any) -> bytes:
    daemon._refresh_assist_status()
    advertised_capacity = daemon._refresh_advertised_capacity()
    return build_capability_ad_message(
        status=daemon.config.assist_status,
        capabilities=daemon.config.capabilities,
        compute_class=daemon.config.compute_class,
        supported_models=daemon.config.supported_models,
        capacity=advertised_capacity,
        trust_score=peer_trust(local_peer_id()),
        assist_filters={
            "allow_research": True,
            "allow_code_reasoning": False,
            "allow_validation": True,
            "min_reward_points": 0,
            "trusted_peers_only": False,
            "host_group_hint_hash": daemon.config.local_host_group_hint_hash,
        },
        pow_difficulty=daemon.local_capability_ad.pow_difficulty if daemon.local_capability_ad else 4,
        genesis_nonce=daemon.local_capability_ad.genesis_nonce if daemon.local_capability_ad else "",
    )


def active_assignment_count(daemon: Any) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM task_assignments
            WHERE helper_peer_id = ?
              AND status = 'active'
            """,
            (local_peer_id(),),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def accepts_hive_tasks(*, hooks: Any) -> bool:
    try:
        return bool(hooks.hive_task_intake_enabled())
    except Exception:
        return True


def effective_assist_status(daemon: Any, *, hooks: Any) -> str:
    current_assignments = daemon._active_assignment_count()
    if current_assignments > 0:
        return "busy"
    if not daemon._accepts_hive_tasks():
        return "limited"
    return "idle"


def refresh_assist_status(daemon: Any, *, hooks: Any) -> bool:
    accepts_tasks = accepts_hive_tasks(hooks=hooks)
    status = effective_assist_status(daemon, hooks=hooks)
    daemon.config.assist_status = status
    if daemon.local_capability_ad is not None:
        daemon.local_capability_ad.status = status
    return accepts_tasks


def idle_assist_config(daemon: Any) -> IdleAssistConfig:
    accepts_tasks = daemon._accepts_hive_tasks()
    return IdleAssistConfig(
        mode="passive" if accepts_tasks else "off",
        max_concurrent_tasks=daemon.config.capacity,
        trusted_peers_only=False,
        min_reward_points=0,
        allow_research=True,
        allow_code_reasoning=False,
        allow_validation=True,
        strict_privacy_only=True,
        require_idle_status=False,
    )


def refresh_advertised_capacity(daemon: Any) -> int:
    advertised = max(0, int(daemon._helper_scheduler.adjust_advertised_capacity(int(daemon.config.capacity))))
    if daemon.local_capability_ad is not None:
        daemon.local_capability_ad.capacity = advertised
    return advertised


def transition_requeued_subtask(task_id: str) -> None:
    state = current_state("subtask", task_id)
    if state in {"claimed", "assigned", "running"}:
        transition(
            entity_type="subtask",
            entity_id=task_id,
            to_state="timed_out",
            trace_id=task_id,
            details={"reason": "mesh_requeue"},
        )
        state = "timed_out"
    if state == "timed_out":
        transition(
            entity_type="subtask",
            entity_id=task_id,
            to_state="offered",
            trace_id=task_id,
            details={"reason": "mesh_requeue"},
        )


def requeue_stale_parent_assignments(daemon: Any, *, limit: int = 20) -> list[str]:
    blocked_grace_seconds = max(5, int(policy_engine.get("assist_mesh.blocked_requeue_seconds", 15)))
    now_dt = datetime.now(timezone.utc)
    reopened: list[str] = []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT assignment_id, task_id, claim_id, helper_peer_id, status, updated_at, lease_expires_at
            FROM task_assignments
            WHERE parent_peer_id = ?
              AND status IN ('active', 'blocked')
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (local_peer_id(), max(1, int(limit))),
        ).fetchall()

        for row in rows:
            status = str(row["status"] or "")
            updated_at = str(row["updated_at"] or "")
            lease_expires_at = str(row["lease_expires_at"] or "")
            due = False
            if lease_expires_at:
                try:
                    due = datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00")) <= now_dt
                except Exception:
                    due = False
            if not due and status == "blocked" and updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    due = (now_dt - updated_dt).total_seconds() >= float(blocked_grace_seconds)
                except Exception:
                    due = False
            if not due:
                continue

            conn.execute(
                """
                UPDATE task_assignments
                SET status = 'timed_out',
                    updated_at = ?,
                    completed_at = COALESCE(completed_at, ?)
                WHERE assignment_id = ?
                """,
                (now_dt.isoformat(), now_dt.isoformat(), row["assignment_id"]),
            )
            conn.execute(
                """
                UPDATE task_claims
                SET status = 'timed_out',
                    updated_at = ?
                WHERE claim_id = ?
                """,
                (now_dt.isoformat(), row["claim_id"]),
            )
            conn.execute(
                """
                UPDATE task_offers
                SET status = 'open',
                    updated_at = ?
                WHERE task_id = ?
                  AND status != 'completed'
                """,
                (now_dt.isoformat(), row["task_id"]),
            )
            reopened.append(str(row["task_id"]))
            revoke_capability_tokens_for_task(
                str(row["task_id"]),
                helper_peer_id=str(row["helper_peer_id"]),
                reason="mesh_requeue",
            )
        conn.commit()
    finally:
        conn.close()

    for task_id in reopened:
        try:
            transition_requeued_subtask(task_id)
        except Exception:
            continue
    return sorted(set(reopened))


def assign_pending_claims_for_open_offers(daemon: Any, *, hooks: Any, limit: int = 20) -> int:
    lease_seconds = max(60, int(policy_engine.get("assist_mesh.assignment_lease_seconds", 900)))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT task_id, parent_peer_id, max_helpers
            FROM task_offers
            WHERE parent_peer_id = ?
              AND status = 'open'
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (local_peer_id(), max(1, int(limit))),
        ).fetchall()
    finally:
        conn.close()

    assigned = 0
    for row in rows:
        task_id = str(row["task_id"] or "")
        if not task_id:
            continue
        conn = get_connection()
        try:
            active_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM task_assignments WHERE task_id = ? AND status = 'active'",
                (task_id,),
            ).fetchone()
            active_count = int(active_row["cnt"]) if active_row else 0
        finally:
            conn.close()
        if active_count >= int(row["max_helpers"]):
            continue
        best = pick_best_claim_for_task(task_id, str(row["parent_peer_id"]))
        if not best:
            continue
        claim_id, helper_peer_id = best
        assign = prepare_task_assignment(
            task_id=task_id,
            claim_id=claim_id,
            parent_agent_id=str(row["parent_peer_id"]),
            helper_agent_id=helper_peer_id,
            assignment_mode="verification" if active_count > 0 else "single",
            lease_seconds=lease_seconds,
        )
        if not assign:
            continue
        persist_task_assignment(assign)
        if send_to_peer_or_log(
            helper_peer_id,
            build_task_assign_message(assign),
            message_type="TASK_ASSIGN",
            target_id=task_id,
            include_candidates=True,
            send_attempt=lambda host, port, raw, _task_id=str(task_id): daemon._send_or_log(
                host,
                int(port),
                raw,
                message_type="TASK_ASSIGN",
                target_id=_task_id,
            ),
        ):
            assigned += 1
    return assigned


def rebroadcast_parent_offers(daemon: Any, task_ids: list[str]) -> int:
    from retrieval.swarm_query import broadcast_task_offer

    sent = 0
    for task_id in task_ids:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT status FROM task_offers WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row or str(row["status"] or "") != "open":
            continue
        payload_bundle = load_task_offer_payload(task_id)
        if not payload_bundle:
            continue
        payload, required_capabilities = payload_bundle
        sent += broadcast_task_offer(
            offer_payload=payload,
            required_capabilities=required_capabilities,
            exclude_host_group_hint_hash=daemon.config.local_host_group_hint_hash,
            limit=max(4, int(policy_engine.get("assist_mesh.rebroadcast_helper_limit", 8))),
        )
    return sent


def resume_incomplete_parent_tasks(*, hooks: Any, limit: int = 20) -> int:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT tc.parent_task_ref
            FROM task_capsules tc
            LEFT JOIN finalized_responses fr ON fr.parent_task_id = tc.parent_task_ref
            WHERE tc.parent_task_ref IS NOT NULL
              AND tc.parent_task_ref != ''
              AND fr.parent_task_id IS NULL
            ORDER BY tc.updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    finally:
        conn.close()

    resumed = 0
    for row in rows:
        parent_task_id = str(row["parent_task_ref"] or "")
        if not parent_task_id:
            continue
        result = hooks.continue_parent_orchestration(parent_task_id)
        if result.action != "no_action":
            resumed += 1
    return resumed


def reconcile_mesh_state(daemon: Any, *, hooks: Any) -> None:
    timed_out = hooks.reap_stale_subtasks(
        limit=max(10, int(policy_engine.get("assist_mesh.reconcile_subtask_limit", 50)))
    )
    expired_tokens = hooks.expire_stale_capability_tokens(
        limit=max(20, int(policy_engine.get("assist_mesh.reconcile_token_limit", 100)))
    )
    reopened = daemon._requeue_stale_parent_assignments(
        limit=max(10, int(policy_engine.get("assist_mesh.reconcile_assignment_limit", 25)))
    )
    reassigned = daemon._assign_pending_claims_for_open_offers(
        limit=max(10, int(policy_engine.get("assist_mesh.reconcile_open_offer_limit", 25)))
    )
    rebroadcast = daemon._rebroadcast_parent_offers(reopened) if reopened else 0
    resumed = daemon._resume_incomplete_parent_tasks(
        limit=max(10, int(policy_engine.get("assist_mesh.reconcile_parent_limit", 25)))
    )
    if any((timed_out, expired_tokens, reopened, reassigned, rebroadcast, resumed)):
        audit_logger.log(
            "mesh_reconcile_cycle",
            target_id=local_peer_id(),
            target_type="daemon",
            details={
                "timed_out_subtasks": int(timed_out),
                "expired_tokens": int(expired_tokens),
                "reopened_assignments": len(reopened),
                "reassigned_offers": int(reassigned),
                "rebroadcasts": int(rebroadcast),
                "resumed_parents": int(resumed),
            },
        )
