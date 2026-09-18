from __future__ import annotations

from core.meet_and_greet_models import ReadinessResponse
from core.meet_and_greet_service import MeetAndGreetService
from storage.db import get_connection
from storage.knowledge_index import active_presence
from storage.migrations import run_migrations
from storage.voolbook_store import ensure_upvote_columns

_REQUIRED_TABLES: tuple[str, ...] = (
    "public_hive_write_quota_events",
    "meet_write_rate_limit_events",
    "voolbook_posts",
    "nonce_cache",
    "presence_leases",
)


def build_meet_readiness(service: MeetAndGreetService) -> ReadinessResponse:
    checks: dict[str, str] = {}
    errors: dict[str, str] = {}

    try:
        run_migrations()
        checks["migrations"] = "ok"
    except Exception as exc:
        errors["migrations"] = str(exc)

    try:
        ensure_upvote_columns()
        checks["voolbook_schema"] = "ok"
    except Exception as exc:
        errors["voolbook_schema"] = str(exc)

    try:
        # Presence tables are still created lazily by the knowledge index layer.
        # Prime that storage before we assert readiness on the underlying tables.
        active_presence(limit=1)
        checks["presence_store"] = "ok"
    except Exception as exc:
        errors["presence_store"] = str(exc)

    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
            checks["db"] = "ok"
            placeholders = ", ".join("?" for _ in _REQUIRED_TABLES)
            existing = {
                str(row["name"])
                for row in conn.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
                    _REQUIRED_TABLES,
                ).fetchall()
            }
        finally:
            conn.close()
        missing = [name for name in _REQUIRED_TABLES if name not in existing]
        if missing:
            errors["tables"] = f"missing tables: {', '.join(missing)}"
        else:
            checks["tables"] = "ok"
    except Exception as exc:
        errors["db"] = str(exc)

    try:
        service.health()
        checks["snapshot"] = "ok"
    except Exception as exc:
        errors["snapshot"] = str(exc)

    status = "ready" if not errors else "not_ready"
    return ReadinessResponse(
        service="meet_and_greet",
        status=status,
        checks={**checks, **{key: f"error: {value}" for key, value in errors.items()}},
    )
