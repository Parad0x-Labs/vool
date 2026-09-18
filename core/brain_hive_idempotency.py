from __future__ import annotations

from typing import Any

from core import brain_hive_queries, brain_hive_write_support
from storage.db import get_connection


class BrainHiveIdempotencyMixin:
    def _post_row(self, post_id: str) -> dict[str, Any]:
        return brain_hive_write_support.load_post_row(post_id)

    def _count_rows(self, table: str) -> int:
        conn = get_connection()
        try:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            return int(row["c"]) if row else 0
        finally:
            conn.close()

    def _count_where(self, table: str, where_sql: str) -> int:
        return brain_hive_queries._count_where(table, where_sql)

    def _cached_result(self, idempotency_key: str | None, model_cls: Any) -> Any | None:
        return brain_hive_write_support.cached_result(idempotency_key, model_cls)

    def _store_idempotent_result(self, idempotency_key: str | None, operation_kind: str, model: Any) -> None:
        brain_hive_write_support.store_idempotent_result(idempotency_key, operation_kind, model)
