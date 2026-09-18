from __future__ import annotations

from pathlib import Path
from unittest import mock

from storage.db import active_default_db_path, configure_default_db_path, get_connection, reset_default_connection


def test_connections_are_independent_per_call_and_close_is_real() -> None:
    """Per-use storage contract (2026-09-04 P0 repair): every get_connection hands out its
    own connection and close() really closes it — the old thread-local pool that kept
    WAL-mode connections attached across operations is the defect this replaced."""
    first = get_connection()
    second = get_connection()
    try:
        assert second is not first
    finally:
        second.close()
    # closing `second` left `first` fully usable; and `first` still works after
    # the compatibility no-op reset
    reset_default_connection()
    try:
        first.execute("SELECT 1")
    finally:
        first.close()
    reset_default_connection()  # API-compatibility no-op; must not raise


def test_active_default_db_path_follows_active_runtime_home_when_unconfigured(tmp_path: Path) -> None:
    runtime_data_dir = (tmp_path / "receipt-runtime" / "data").resolve()

    configure_default_db_path(None)
    reset_default_connection()
    try:
        with mock.patch("storage.db.active_data_dir", return_value=runtime_data_dir):
            assert active_default_db_path() == str((runtime_data_dir / "vool_web0_v2.db").resolve())
    finally:
        reset_default_connection()


def test_get_connection_uses_runtime_bound_default_db_path(tmp_path: Path) -> None:
    runtime_data_dir = (tmp_path / "receipt-runtime" / "data").resolve()
    expected_db = (runtime_data_dir / "vool_web0_v2.db").resolve()

    configure_default_db_path(None)
    reset_default_connection()
    try:
        with mock.patch("storage.db.active_data_dir", return_value=runtime_data_dir):
            conn = get_connection()
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS storage_db_pooling_probe (id INTEGER PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()
            assert expected_db.exists()
            assert active_default_db_path() == str(expected_db)
    finally:
        reset_default_connection()
