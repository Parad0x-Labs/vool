"""The frozen import-time ``DEFAULT_DB_PATH`` is a SENTINEL meaning "the active default database".

Pre-demo candidate blocker (2026-09-02): slice 8 taught the memoized path resolver to reroute the
sentinel straight to the runtime-home file, keyed on the sentinel string alone. Two defects in one:

- it ignored the active default OVERRIDE, so a module that passes the constant explicitly
  (``core/contribution_proof.py``) wrote to a different database than a module that passes
  nothing (``core/reward_engine.py``) — the ledger row landed in one file, the receipt's foreign
  key was checked in another, and five reward-engine tests plus the brain-hive dashboard count
  went red;
- the reroute target depended on runtime state (home + override) that was not part of the memo
  key, so the first resolution in a process stuck for every later home.

The owning boundary is ``storage.db._resolve_db_path``: the sentinel resolves LIVE to the active
default (override first, then runtime home) and only the pure path canonicalization is memoized.
"""

from __future__ import annotations

import uuid
from pathlib import Path


def _db_file(conn) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()[2])


def test_explicit_sentinel_binds_to_the_active_default_when_an_override_is_set() -> None:
    from storage import db as sdb

    assert sdb._DEFAULT_DB_PATH_OVERRIDE, "precondition: the session default override is in force"
    implicit = sdb.get_connection()
    explicit = sdb.get_connection(sdb.DEFAULT_DB_PATH)
    assert _db_file(implicit) == _db_file(explicit) == sdb.active_default_db_path(), (
        "an explicit sentinel and an implicit default must open the SAME database file"
    )
    # A write through the implicit handle is visible through the explicit one: one store, two
    # spellings — the exact seam the reward engine and contribution-proof modules meet at.
    token = uuid.uuid4().hex
    implicit.execute("CREATE TABLE IF NOT EXISTS sentinel_binding_probe (k TEXT PRIMARY KEY)")
    implicit.execute("INSERT INTO sentinel_binding_probe (k) VALUES (?)", (token,))
    implicit.commit()
    try:
        row = explicit.execute("SELECT 1 FROM sentinel_binding_probe WHERE k = ?", (token,)).fetchone()
        assert row is not None, "the explicit-sentinel handle reads a different database"
    finally:
        implicit.execute("DROP TABLE IF EXISTS sentinel_binding_probe")
        implicit.commit()


def test_sentinel_follows_the_runtime_home_without_a_stale_memo(tmp_path: Path, monkeypatch) -> None:
    import core.runtime_paths
    from storage import db as sdb

    monkeypatch.setattr(sdb, "_DEFAULT_DB_PATH_OVERRIDE", None)
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    monkeypatch.setenv("VOOL_HOME", str(home_a))
    first = Path(sdb._resolve_db_path(sdb.DEFAULT_DB_PATH))
    monkeypatch.setenv("VOOL_HOME", str(home_b))
    second = Path(sdb._resolve_db_path(sdb.DEFAULT_DB_PATH))
    assert first == (home_a / "data" / "vool_web0_v2.db").resolve()
    assert second == (home_b / "data" / "vool_web0_v2.db").resolve(), (
        "the sentinel must resolve against the CURRENT home on every call, not the first one memoized"
    )
    # The frozen source-root parent is never materialized by resolving the sentinel.
    assert not Path(sdb.DEFAULT_DB_PATH).parent.exists() or Path(sdb.DEFAULT_DB_PATH).parent != Path(first).parent


def test_configuring_the_default_to_the_sentinel_means_no_override(monkeypatch) -> None:
    from storage import db as sdb

    saved = sdb._DEFAULT_DB_PATH_OVERRIDE
    try:
        sdb.configure_default_db_path(sdb.DEFAULT_DB_PATH)
        assert sdb._DEFAULT_DB_PATH_OVERRIDE is None, (
            "the sentinel names 'the default', so configuring it as the default clears the override"
        )
    finally:
        sdb.configure_default_db_path(saved)
