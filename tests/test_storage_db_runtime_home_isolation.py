from __future__ import annotations

from core.runtime_paths import configure_runtime_home
from storage.db import (
    active_default_db_path,
    configure_default_db_path,
    get_connection,
    reset_default_connection,
)


def test_default_connection_tracks_runtime_home_changes(tmp_path) -> None:
    original_database = active_default_db_path()
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    try:
        configure_default_db_path(None)
        configure_runtime_home(first_home)
        first = get_connection()
        first.execute("CREATE TABLE runtime_marker (value TEXT NOT NULL)")
        first.execute("INSERT INTO runtime_marker (value) VALUES ('first')")
        first.commit()

        configure_runtime_home(second_home)
        second = get_connection()
        row = second.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'runtime_marker'
            """
        ).fetchone()

        assert row is None
    finally:
        reset_default_connection()
        configure_runtime_home(None)
        configure_default_db_path(original_database)
