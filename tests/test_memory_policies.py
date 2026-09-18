from __future__ import annotations

from core.memory.policies import (
    describe_session_memory_policy,
    parse_session_scope_command,
    session_memory_policy,
    set_session_memory_policy,
)
from storage.db import get_connection


def setup_function() -> None:
    conn = get_connection()
    try:
        for table in ("session_memory_policies", "learning_shards", "local_tasks", "knowledge_holders", "knowledge_manifests"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def test_session_memory_policy_defaults_to_local_scope_without_row() -> None:
    policy = session_memory_policy("session-a")

    assert policy["session_id"] == "session-a"
    assert policy["share_scope"] == "local_only"
    assert policy["restricted_terms"] == []
    assert "PRIVATE VAULT" in describe_session_memory_policy("session-a")


def test_set_session_memory_policy_normalizes_terms_and_roundtrips() -> None:
    result = set_session_memory_policy(
        "session-b",
        share_scope="shared pack",
        restricted_terms=["Real Name", " address ", "real name"],
    )

    assert result["share_scope"] == "hive_mind"
    assert result["restricted_terms"] == ["real name", "address"]

    policy = session_memory_policy("session-b")
    assert policy["share_scope"] == "hive_mind"
    assert policy["restricted_terms"] == ["real name", "address"]
    assert "SHARED PACK" in describe_session_memory_policy("session-b")


def test_parse_session_scope_command_preserves_hive_task_queries() -> None:
    assert parse_session_scope_command("shared pack except api key and password") == {
        "action": "set",
        "share_scope": "hive_mind",
        "restricted_terms": ["api key", "password"],
    }
    assert parse_session_scope_command("show memory scope") == {"action": "show"}
    assert parse_session_scope_command("shared pack tasks available now") is None
