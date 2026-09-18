from __future__ import annotations

from core.capacity_predictor import predict_local_override_necessity
from storage.db import get_connection
from storage.migrations import run_migrations


def test_predict_local_override_disabled_when_local_worker_pool_enabled() -> None:
    run_migrations()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM peers")
        conn.commit()
    finally:
        conn.close()

    assert predict_local_override_necessity() is False
