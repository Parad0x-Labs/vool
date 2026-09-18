from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from core import policy_engine
from storage.db import get_connection
from storage.migrations import run_migrations


@dataclass(frozen=True)
class PublicHiveQuotaReservation:
    allowed: bool
    reason: str
    route: str
    amount: float
    used_points: float
    limit_points: float
    trust_score: float
    trust_tier: str


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_day_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _route_costs() -> dict[str, float]:
    raw = policy_engine.get(
        "economics.public_hive_route_costs",
        {
            "/v1/hive/topics": 3.0,
            "/v1/hive/posts": 1.0,
            "/v1/hive/topic-claims": 2.0,
            "/v1/hive/topic-status": 1.0,
            "/v1/hive/commons/endorsements": 0.25,
            "/v1/hive/commons/comments": 0.5,
            "/v1/hive/commons/promotion-candidates": 0.5,
            "/v1/hive/commons/promotion-reviews": 0.25,
            "/v1/hive/commons/promotions": 2.0,
        },
    )
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        clean_key = str(key or "").rstrip("/") or "/"
        try:
            out[clean_key] = max(0.0, float(value or 0.0))
        except (TypeError, ValueError):
            continue
    return out


def _route_cost(route: str) -> float:
    return float(_route_costs().get(str(route or "").rstrip("/") or "/", 0.0))


def _resolve_peer_trust(peer_id: str) -> float:
    clean_peer_id = str(peer_id or "").strip()
    if not clean_peer_id:
        return 0.0
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT trust_score FROM peers WHERE peer_id = ? LIMIT 1",
            (clean_peer_id,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        try:
            return max(0.0, min(1.0, float(row["trust_score"])))
        except (TypeError, ValueError):
            return 0.0
    try:
        return max(0.0, min(1.0, float(policy_engine.get("economics.public_hive_unknown_peer_trust", 0.45))))
    except (TypeError, ValueError):
        return 0.45


def _trust_tier(trust_score: float) -> tuple[str, float]:
    try:
        low = float(policy_engine.get("economics.public_hive_low_trust_threshold", 0.45))
    except (TypeError, ValueError):
        low = 0.45
    try:
        high = float(policy_engine.get("economics.public_hive_high_trust_threshold", 0.75))
    except (TypeError, ValueError):
        high = 0.75
    try:
        low_limit = max(0.0, float(policy_engine.get("economics.public_hive_daily_quota_low", 24.0)))
    except (TypeError, ValueError):
        low_limit = 24.0
    try:
        mid_limit = max(0.0, float(policy_engine.get("economics.public_hive_daily_quota_mid", 192.0)))
    except (TypeError, ValueError):
        mid_limit = 192.0
    try:
        high_limit = max(0.0, float(policy_engine.get("economics.public_hive_daily_quota_high", 768.0)))
    except (TypeError, ValueError):
        high_limit = 768.0
    score = max(0.0, min(1.0, float(trust_score or 0.0)))
    if score >= high:
        return "trusted", high_limit
    if score >= low:
        return "established", mid_limit
    return "newcomer", low_limit


def _active_claim_count(peer_id: str) -> int:
    clean_peer_id = str(peer_id or "").strip()
    if not clean_peer_id:
        return 0
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM hive_topic_claims
            WHERE agent_id = ? AND status = 'active'
            """,
            (clean_peer_id,),
        ).fetchone()
    except Exception:
        return 0
    finally:
        conn.close()
    try:
        return max(0, int(row["total"] or 0)) if row else 0
    except (TypeError, ValueError):
        return 0


def _active_claim_bonus(peer_id: str) -> float:
    claim_count = _active_claim_count(peer_id)
    if claim_count <= 0:
        return 0.0
    try:
        per_claim = max(
            0.0,
            float(policy_engine.get("economics.public_hive_daily_quota_bonus_per_active_claim", 24.0)),
        )
    except (TypeError, ValueError):
        per_claim = 24.0
    try:
        max_bonus = max(
            0.0,
            float(policy_engine.get("economics.public_hive_daily_quota_max_active_claim_bonus", 192.0)),
        )
    except (TypeError, ValueError):
        max_bonus = 192.0
    return min(max_bonus, per_claim * float(claim_count))


def _limit_for_peer(peer_id: str, trust_score: float) -> tuple[str, float]:
    tier, base_limit = _trust_tier(trust_score)
    return tier, base_limit + _active_claim_bonus(peer_id)


def _min_claim_trust() -> float:
    try:
        return max(0.0, min(1.0, float(policy_engine.get("economics.public_hive_min_claim_trust", 0.42))))
    except (TypeError, ValueError):
        return 0.42


def _route_min_trusts() -> dict[str, float]:
    raw = policy_engine.get(
        "economics.public_hive_min_route_trusts",
        {
            "/v1/hive/commons/promotion-candidates": 0.45,
            "/v1/hive/commons/promotion-reviews": 0.75,
            "/v1/hive/commons/promotions": 0.85,
        },
    )
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        clean_key = str(key or "").rstrip("/") or "/"
        try:
            out[clean_key] = max(0.0, min(1.0, float(value or 0.0)))
        except (TypeError, ValueError):
            continue
    return out


def _min_route_trust(route: str) -> float:
    return float(_route_min_trusts().get(str(route or "").rstrip("/") or "/", 0.0))


def _quota_usage_in_tx(conn, peer_id: str, *, day_bucket: str) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM public_hive_write_quota_events
        WHERE peer_id = ?
          AND day_bucket = ?
        """,
        (peer_id, day_bucket),
    ).fetchone()
    return float(row["total"]) if row else 0.0


def _init_table() -> None:
    run_migrations()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='public_hive_write_quota_events' LIMIT 1"
        ).fetchone()
        if not row:
            raise RuntimeError("public_hive_write_quota_events table is missing after migrations.")
    finally:
        conn.close()


def reserve_public_hive_write_quota(
    peer_id: str,
    route: str,
    *,
    trust_score: float | None = None,
    request_nonce: str | None = None,
    metadata: dict | None = None,
) -> PublicHiveQuotaReservation:
    clean_peer_id = str(peer_id or "").strip()
    clean_route = str(route or "").rstrip("/") or "/"
    amount = _route_cost(clean_route)
    score = _resolve_peer_trust(clean_peer_id) if trust_score is None else max(0.0, min(1.0, float(trust_score)))
    tier, limit = _limit_for_peer(clean_peer_id, score)
    if not clean_peer_id:
        return PublicHiveQuotaReservation(
            allowed=False,
            reason="missing_peer_id",
            route=clean_route,
            amount=amount,
            used_points=0.0,
            limit_points=limit,
            trust_score=score,
            trust_tier=tier,
        )
    if clean_route == "/v1/hive/topic-claims" and score < _min_claim_trust():
        return PublicHiveQuotaReservation(
            allowed=False,
            reason="insufficient_claim_trust",
            route=clean_route,
            amount=amount,
            used_points=0.0,
            limit_points=limit,
            trust_score=score,
            trust_tier=tier,
        )
    route_trust_floor = _min_route_trust(clean_route)
    if route_trust_floor > 0.0 and score < route_trust_floor:
        return PublicHiveQuotaReservation(
            allowed=False,
            reason="insufficient_route_trust",
            route=clean_route,
            amount=amount,
            used_points=0.0,
            limit_points=limit,
            trust_score=score,
            trust_tier=tier,
        )
    if amount <= 0:
        return PublicHiveQuotaReservation(
            allowed=True,
            reason="route_exempt",
            route=clean_route,
            amount=0.0,
            used_points=0.0,
            limit_points=limit,
            trust_score=score,
            trust_tier=tier,
        )

    _init_table()
    now_iso = _utcnow_iso()
    day_bucket = _utc_day_bucket()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        reused = None
        clean_nonce = str(request_nonce or "").strip()
        if clean_nonce:
            reused = conn.execute(
                """
                SELECT amount
                FROM public_hive_write_quota_events
                WHERE request_nonce = ?
                LIMIT 1
                """,
                (clean_nonce,),
            ).fetchone()
        used_points = _quota_usage_in_tx(conn, clean_peer_id, day_bucket=day_bucket)
        if reused:
            conn.rollback()
            return PublicHiveQuotaReservation(
                allowed=True,
                reason="nonce_reused",
                route=clean_route,
                amount=float(reused["amount"] or 0.0),
                used_points=used_points,
                limit_points=limit,
                trust_score=score,
                trust_tier=tier,
            )
        if used_points + amount > limit:
            conn.rollback()
            return PublicHiveQuotaReservation(
                allowed=False,
                reason="daily_public_hive_quota_exhausted",
                route=clean_route,
                amount=amount,
                used_points=used_points,
                limit_points=limit,
                trust_score=score,
                trust_tier=tier,
            )
        conn.execute(
            """
            INSERT INTO public_hive_write_quota_events (
                peer_id, day_bucket, route, amount, trust_score, trust_tier, request_nonce, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_peer_id,
                day_bucket,
                clean_route,
                amount,
                score,
                tier,
                clean_nonce or None,
                json.dumps(metadata or {}, sort_keys=True),
                now_iso,
            ),
        )
        conn.commit()
        return PublicHiveQuotaReservation(
            allowed=True,
            reason="quota_reserved",
            route=clean_route,
            amount=amount,
            used_points=used_points + amount,
            limit_points=limit,
            trust_score=score,
            trust_tier=tier,
        )
    except Exception:
        conn.rollback()
        return PublicHiveQuotaReservation(
            allowed=False,
            reason="quota_storage_error",
            route=clean_route,
            amount=amount,
            used_points=0.0,
            limit_points=limit,
            trust_score=score,
            trust_tier=tier,
        )
    finally:
        conn.close()
