from __future__ import annotations

from unittest.mock import patch

import storage.migrations as migrations


def test_main_runs_migrations_with_optional_db_path() -> None:
    with patch.object(migrations, "run_migrations") as run_migrations:
        exit_code = migrations.main(["--db-path", "/tmp/vool.db"])

    run_migrations.assert_called_once_with(db_path="/tmp/vool.db")
    assert exit_code == 0
