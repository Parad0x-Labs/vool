"""Isolation for the conversation-truth regression kit.

The repo-root conftest already pins VOOL_HOME to a temp dir and points the default DB
there (no Keychain, no live runtime data). This file adds the per-test storage reset the
tests/ tree gets from tests/conftest.py, scoped to the tables the kit touches, so kit
cases cannot leak obligations, attempts or events into each other.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    reset_runtime_continuity_state,
)
from storage.db import (
    active_default_db_path,
    configure_default_db_path,
    get_connection,
    reset_default_connection,
)
from storage.migrations import run_migrations

# Tables the kit writes through the pure-function seams (obligation memory, attempts,
# events, dialogue rows, finalizations). Tolerant DELETEs: a fresh database may not have
# all of them until the first write.
KIT_TABLES = (
    "live_data_obligation_memory",
    "runtime_session_events",
    "runtime_attempts",
    "runtime_checkpoints",
    "runtime_sessions",
    "dialogue_turns",
    "dialogue_sessions",
    "refused_slot_register",
    "finalized_responses",
    "a7_finalizations",
    "semantic_results",
    "turn_demand_ledger",
)


@pytest.fixture(autouse=True)
def kit_storage_reset():
    configure_default_db_path(active_default_db_path())
    reset_default_connection()
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    reset_runtime_continuity_state()
    conn = get_connection()
    try:
        for table in KIT_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
    reset_default_connection()
    yield
    reset_default_connection()
