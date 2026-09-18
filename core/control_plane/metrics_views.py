from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.useful_output_store import summarize_useful_outputs


def load_swarm_budget_summary(
    conn: Any,
    *,
    table_exists_fn: Any,
    utc_day_bucket_fn: Any,
    utcnow_fn: Any,
    policy_getter: Any,
) -> dict[str, Any]:
    day_bucket = utc_day_bucket_fn()
    items: list[dict[str, Any]] = []
    if table_exists_fn(conn, "swarm_dispatch_budget_events"):
        rows = conn.execute(
            """
            SELECT peer_id, day_bucket, dispatch_mode, reason, SUM(amount) AS amount, COUNT(*) AS event_count
            FROM swarm_dispatch_budget_events
            WHERE day_bucket = ?
            GROUP BY peer_id, day_bucket, dispatch_mode, reason
            ORDER BY amount DESC
            """,
            (day_bucket,),
        ).fetchall()
        items = [dict(row) for row in rows]
    used_total = round(sum(float(item.get("amount") or 0.0) for item in items), 4)
    daily_cap = float(policy_getter("economics.free_tier_daily_swarm_points", 24.0) or 24.0)
    return {
        "generated_at": utcnow_fn(),
        "day_bucket": day_bucket,
        "free_tier_daily_swarm_points": daily_cap,
        "free_tier_max_dispatch_points": float(policy_getter("economics.free_tier_max_dispatch_points", 12.0) or 12.0),
        "used_total": used_total,
        "remaining_estimated": round(max(0.0, daily_cap - used_total), 4),
        "items": items,
    }


def load_public_hive_budget_summary(
    conn: Any,
    *,
    table_exists_fn: Any,
    utc_day_bucket_fn: Any,
    utcnow_fn: Any,
    policy_getter: Any,
) -> dict[str, Any]:
    day_bucket = utc_day_bucket_fn()
    items: list[dict[str, Any]] = []
    if table_exists_fn(conn, "public_hive_write_quota_events"):
        rows = conn.execute(
            """
            SELECT peer_id, day_bucket, route, MAX(trust_score) AS trust_score, MAX(trust_tier) AS trust_tier,
                   SUM(amount) AS amount, COUNT(*) AS event_count
            FROM public_hive_write_quota_events
            WHERE day_bucket = ?
            GROUP BY peer_id, day_bucket, route
            ORDER BY amount DESC
            """,
            (day_bucket,),
        ).fetchall()
        items = [dict(row) for row in rows]
    active_claim_count = 0
    if table_exists_fn(conn, "hive_topic_claims"):
        row = conn.execute("SELECT COUNT(*) AS cnt FROM hive_topic_claims WHERE status = 'active'").fetchone()
        active_claim_count = int((row["cnt"] if row else 0) or 0)
    used_total = round(sum(float(item.get("amount") or 0.0) for item in items), 4)
    trust_tier = str(items[0].get("trust_tier") or "low") if items else "low"
    quota_low = float(policy_getter("economics.public_hive_daily_quota_low", 24.0) or 24.0)
    quota_mid = float(policy_getter("economics.public_hive_daily_quota_mid", 192.0) or 192.0)
    quota_high = float(policy_getter("economics.public_hive_daily_quota_high", 768.0) or 768.0)
    bonus_per_claim = float(
        policy_getter("economics.public_hive_daily_quota_bonus_per_active_claim", 24.0) or 24.0
    )
    bonus_cap = float(
        policy_getter("economics.public_hive_daily_quota_max_active_claim_bonus", 192.0) or 192.0
    )
    base_quota = quota_mid if trust_tier == "established" else quota_high if trust_tier == "trusted" else quota_low
    active_claim_bonus = min(bonus_cap, active_claim_count * bonus_per_claim)
    estimated_daily_quota = round(base_quota + active_claim_bonus, 4)
    return {
        "generated_at": utcnow_fn(),
        "day_bucket": day_bucket,
        "daily_quota_low": quota_low,
        "daily_quota_mid": quota_mid,
        "daily_quota_high": quota_high,
        "active_claim_bonus_per_claim": bonus_per_claim,
        "active_claim_bonus_cap": bonus_cap,
        "active_claim_count": active_claim_count,
        "used_total": used_total,
        "trust_tier": trust_tier,
        "estimated_daily_quota": estimated_daily_quota,
        "remaining_estimated": round(max(0.0, estimated_daily_quota - used_total), 4),
        "route_costs": dict(policy_getter("economics.public_hive_route_costs", {}) or {}),
        "items": items,
    }


def load_adaptation_status(
    conn: Any,
    *,
    db_path: str | Path | None = None,
    table_exists_fn: Any,
    json_loads_fn: Any,
    utcnow_fn: Any,
    summarize_useful_outputs_fn: Any = summarize_useful_outputs,
) -> dict[str, Any]:
    loop_state = {}
    if table_exists_fn(conn, "adaptation_loop_state"):
        row = conn.execute(
            """
            SELECT loop_name, status, base_model_ref, base_provider_name, base_model_name,
                   active_job_id, active_provider_name, active_model_name,
                   previous_job_id, previous_provider_name, previous_model_name,
                   last_corpus_id, last_example_count, last_quality_score, last_eval_id,
                   last_canary_eval_id, last_decision, last_reason, last_error_text,
                   last_tick_at, last_completed_tick_at, last_metadata_publish_at, metrics_json
            FROM adaptation_loop_state
            WHERE loop_name = 'default'
            LIMIT 1
            """
        ).fetchone()
        if row:
            loop_state = dict(row)
            loop_state["metrics"] = json_loads_fn(loop_state.pop("metrics_json", "{}"), fallback={})
    try:
        from core.trainable_base_manager import list_staged_trainable_bases

        staged_bases = list_staged_trainable_bases()
    except Exception:
        staged_bases = []
    try:
        from storage.adaptation_store import list_adaptation_eval_runs

        recent_evals = list_adaptation_eval_runs(limit=8)
    except Exception:
        recent_evals = []
    return {
        "generated_at": utcnow_fn(),
        "loop_state": loop_state,
        "staged_bases": staged_bases,
        "recent_evals": recent_evals,
        "useful_outputs": summarize_useful_outputs_fn(str(db_path) if db_path is not None else None),
    }


def load_proof_of_useful_work_summary(
    conn: Any,
    *,
    limit: int,
    db_path: str | Path | None = None,
    table_exists_fn: Any,
    utcnow_fn: Any,
) -> dict[str, Any]:
    if not table_exists_fn(conn, "contribution_ledger"):
        return {
            "generated_at": utcnow_fn(),
            "pending_count": 0,
            "confirmed_count": 0,
            "finalized_count": 0,
            "rejected_count": 0,
            "slashed_count": 0,
            "released_compute_credits": 0.0,
            "finalized_compute_credits": 0.0,
            "leaders": [],
        }

    finality_state = """
    CASE
        WHEN finality_state IS NOT NULL AND TRIM(finality_state) != '' THEN LOWER(finality_state)
        WHEN outcome = 'pending' THEN 'pending'
        WHEN outcome = 'released' THEN 'confirmed'
        WHEN outcome = 'slashed' THEN 'slashed'
        WHEN outcome IN ('rejected', 'harmful', 'failed') THEN 'rejected'
        ELSE 'pending'
    END
    """
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN {finality_state} = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN {finality_state} = 'confirmed' THEN 1 ELSE 0 END) AS confirmed_count,
            SUM(CASE WHEN {finality_state} = 'finalized' THEN 1 ELSE 0 END) AS finalized_count,
            SUM(CASE WHEN {finality_state} = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
            SUM(CASE WHEN {finality_state} = 'slashed' THEN 1 ELSE 0 END) AS slashed_count,
            COALESCE(SUM(compute_credits_released), 0) AS released_compute_credits,
            COALESCE(SUM(CASE WHEN {finality_state} = 'finalized' THEN compute_credits_released ELSE 0 END), 0) AS finalized_compute_credits
        FROM contribution_ledger
        """
    ).fetchone()

    try:
        from core.contribution_proof import list_contribution_proof_receipts
        from core.scoreboard_engine import get_glory_leaderboard

        leaders = get_glory_leaderboard(limit=max(1, int(limit)), db_path=db_path)
        recent_receipts = list_contribution_proof_receipts(limit=max(1, int(limit)), db_path=db_path)
        challenged_receipts = list_contribution_proof_receipts(
            limit=max(1, min(8, int(limit))),
            stages=["slashed", "rejected"],
            db_path=db_path,
        )
    except Exception:
        leaders = []
        recent_receipts = []
        challenged_receipts = []

    return {
        "generated_at": utcnow_fn(),
        "pending_count": int((row["pending_count"] if row else 0) or 0),
        "confirmed_count": int((row["confirmed_count"] if row else 0) or 0),
        "finalized_count": int((row["finalized_count"] if row else 0) or 0),
        "rejected_count": int((row["rejected_count"] if row else 0) or 0),
        "slashed_count": int((row["slashed_count"] if row else 0) or 0),
        "released_compute_credits": round(float((row["released_compute_credits"] if row else 0.0) or 0.0), 4),
        "finalized_compute_credits": round(float((row["finalized_compute_credits"] if row else 0.0) or 0.0), 4),
        "leaders": leaders,
        "recent_receipts": recent_receipts,
        "challenged_receipts": challenged_receipts,
    }


def load_adaptation_proof_summary(
    conn: Any,
    *,
    db_path: str | Path | None = None,
    table_exists_fn: Any,
    json_loads_fn: Any,
    utcnow_fn: Any,
) -> dict[str, Any]:
    del db_path
    evals: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    if table_exists_fn(conn, "adaptation_eval_runs"):
        rows = conn.execute(
            """
            SELECT *
            FROM adaptation_eval_runs
            ORDER BY updated_at DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["metrics"] = json_loads_fn(item.pop("metrics_json", "{}"), fallback={})
            evals.append(item)
    if table_exists_fn(conn, "adaptation_jobs"):
        rows = conn.execute(
            """
            SELECT *
            FROM adaptation_jobs
            ORDER BY updated_at DESC
            LIMIT 24
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["dependency_status"] = json_loads_fn(item.pop("dependency_status_json", "{}"), fallback={})
            item["training_config"] = json_loads_fn(item.pop("training_config_json", "{}"), fallback={})
            item["metrics"] = json_loads_fn(item.pop("metrics_json", "{}"), fallback={})
            item["metadata"] = json_loads_fn(item.pop("metadata_json", "{}"), fallback={})
            item["registered_manifest"] = json_loads_fn(item.pop("registered_manifest_json", "{}"), fallback={})
            jobs.append(item)

    completed_evals = [row for row in evals if str(row.get("status") or "").strip().lower() == "completed"]
    promotion_evals = [row for row in completed_evals if str(row.get("eval_kind") or "") == "promotion_gate"]
    pre_canaries = [row for row in completed_evals if str(row.get("eval_kind") or "") == "pre_promotion_canary"]
    post_canaries = [row for row in completed_evals if str(row.get("eval_kind") or "") == "post_promotion_canary"]
    promoted_jobs = [row for row in jobs if str(row.get("promoted_at") or "").strip()]
    rolled_back_jobs = [row for row in jobs if str(row.get("rolled_back_at") or "").strip()]
    active_promoted = next(
        (
            row
            for row in jobs
            if str(row.get("status") or "").strip().lower() == "promoted"
            and not str(row.get("rolled_back_at") or "").strip()
        ),
        {},
    )
    latest_eval = dict(completed_evals[0] or {}) if completed_evals else {}
    latest_promotion_eval = dict(promotion_evals[0] or {}) if promotion_evals else {}
    latest_canary = dict((post_canaries or pre_canaries or [None])[0] or {})
    positive_eval_count = sum(1 for row in completed_evals if float(row.get("score_delta") or 0.0) > 0.0)
    negative_eval_count = sum(1 for row in completed_evals if float(row.get("score_delta") or 0.0) < 0.0)
    mean_delta = round(
        sum(float(row.get("score_delta") or 0.0) for row in completed_evals) / max(1, len(completed_evals)),
        4,
    ) if completed_evals else 0.0
    proof_state = "no_recent_eval"
    if rolled_back_jobs:
        proof_state = "rollback_recorded"
    elif latest_promotion_eval and str(latest_promotion_eval.get("decision") or "") == "promote_candidate":
        if latest_canary and str(latest_canary.get("decision") or "") in {"canary_pass", "keep_live"}:
            proof_state = "candidate_beating_baseline"
        else:
            proof_state = "candidate_unproven_after_eval"
    elif latest_eval:
        proof_state = "positive_eval_signal" if float(latest_eval.get("score_delta") or 0.0) > 0.0 else "flat_or_negative_eval_signal"

    return {
        "generated_at": utcnow_fn(),
        "proof_state": proof_state,
        "recent_eval_count": len(completed_evals),
        "positive_eval_count": positive_eval_count,
        "negative_eval_count": negative_eval_count,
        "mean_delta": mean_delta,
        "promoted_job_count": len(promoted_jobs),
        "rolled_back_job_count": len(rolled_back_jobs),
        "latest_eval": latest_eval,
        "latest_promotion_eval": latest_promotion_eval,
        "latest_canary": latest_canary,
        "active_promoted_job": active_promoted,
        "promotion_history": [
            {
                "job_id": str(row.get("job_id") or ""),
                "label": str(row.get("label") or ""),
                "status": str(row.get("status") or ""),
                "promoted_at": str(row.get("promoted_at") or ""),
                "rolled_back_at": str(row.get("rolled_back_at") or ""),
                "quality_score": float((row.get("metadata") or {}).get("quality_score") or 0.0),
                "adapter_provider_name": str(row.get("adapter_provider_name") or ""),
                "adapter_model_name": str(row.get("adapter_model_name") or ""),
            }
            for row in promoted_jobs[:6]
        ],
    }
