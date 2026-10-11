"""Real router forget on a fresh home must erase its own task payload too."""
from __future__ import annotations

import pytest


@pytest.fixture
def router(tmp_path, monkeypatch):
    from apps.vool_agent import VoolAgent
    from core import runtime_paths, task_state_machine, trace_id
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from core.runtime_provider_defaults import apply_product_runtime_defaults
    from storage.db import configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    home = tmp_path / "fresh-home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(home / "workspace"))
    monkeypatch.setenv("VOOL_LOCAL_MODELS_ENABLED", "0")
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    configure_default_db_path(str(home / "data" / "router.db"))
    reset_default_connection()
    configure_runtime_continuity_db_path(str(home / "data" / "router.db"))
    run_migrations()
    apply_product_runtime_defaults()
    # The trace schema has a process-level lazy-init flag; a new database
    # needs the real initializer again, rather than the preceding home's flag.
    monkeypatch.setattr(trace_id, "_TABLE_READY", False)
    monkeypatch.setattr(task_state_machine, "_TABLE_READY", False)

    def turn(text, chat="fresh-forget"):
        # A new agent per call also checks that recall does not rely on an
        # instance's cached state. No provider or memory owner is replaced.
        result = VoolAgent("test-forget", "test-device").run_once(
            text, session_id_override=chat,
            source_context={"surface": "chat", "chat_id": chat, "runtime_home": str(home)},
        )
        print("ROUTER_TURN", text, result.get("response"), result.get("route"))
        return result

    yield turn
    reset_default_connection()
    configure_default_db_path(None)
    configure_runtime_continuity_db_path(None)


def test_fresh_router_remember_recall_forget_and_fresh_recall(router):
    assert "Locked in" in router("Remember that Maya's birthday is March 14.")["response"]
    router("When is Maya's birthday?")
    recalled = router("What facts do you remember about Maya's birthday from this chat?")
    assert "March 14" in recalled["response"], recalled
    forgotten = router("Forget Maya's birthday.")
    assert forgotten["response"].startswith("Forget applied"), forgotten

    from core.memory.carriers import ROW_IDENTITIES, carries, row_payload
    from storage.db import get_connection

    conn = get_connection()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        targets = {"learning_shards", "local_tasks", "response_feedback", "context_capsule_versions"}
        targets.update(t for t in tables if t.startswith("dialogue_")
                       or any(part in t for part in ("packet", "cache", "snapshot")))
        for table in sorted(targets & tables):
            rows = conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall()
            assert not any(carries(row_payload(r, structural_fields=ROW_IDENTITIES), "Maya's birthday")
                           for r in rows), table
    finally:
        conn.close()
    recalled = router("What facts do you remember about Maya's birthday from this chat?")
    assert "March 14" not in recalled["response"], recalled
    assert "Active remembered" not in recalled["response"], recalled
    inventory = router("/memory")
    assert inventory["response"] == "Memory is currently empty. Tell me what to remember.", inventory
    assert not recalled.get("recall_found"), recalled


def test_upgrade_task_rows_and_current_forget_row_keep_referenced_ids(router):
    from core.task_router import create_task_record, load_task_record
    from storage.db import get_connection

    own = create_task_record("The marina gate code is CEDAR-92.", session_id="fresh-forget")
    legacy = create_task_record("CEDAR_92 was saved before this upgrade", session_id="")
    foreign = create_task_record("The marina gate code is CEDAR-92.", session_id="other-chat")
    sibling = create_task_record("The marina gate closes at sunset.", session_id="fresh-forget")
    command = create_task_record("Forget CEDAR-92.", session_id="fresh-forget")
    conn = get_connection()
    try:
        conn.execute("UPDATE local_tasks SET created_at = '2025-01-01' WHERE task_id = ?", (own.task_id,))
        conn.execute(
            "INSERT INTO web_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("gate-note", "query-digest", "marina", None, "neutral note", 0.8, "2025-01-01",
             own.task_id, "2025-01-01"),
        )
        conn.commit()
    finally:
        conn.close()
    result = router("Forget CEDAR-92.")
    assert result["response"].startswith("Forget applied"), result
    for task in (own, legacy, command):
        stored = load_task_record(task.task_id)
        assert stored is not None and stored.task_summary == "", stored
    assert load_task_record(foreign.task_id).task_summary == foreign.task_summary
    assert load_task_record(sibling.task_id).task_summary == sibling.task_summary
    conn = get_connection()
    try:
        assert conn.execute("SELECT used_in_task_id FROM web_notes WHERE note_id = 'gate-note'").fetchone()[0] == own.task_id
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def test_unreferenced_tasks_are_deleted_and_failed_eraser_stays_closed(router):
    from core.task_router import create_task_record, load_task_record
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE shard_feedback")
        conn.execute("DROP TABLE web_notes")
        conn.execute("CREATE TRIGGER deny_task_erasure BEFORE DELETE ON local_tasks "
                     "BEGIN SELECT RAISE(ABORT, 'erasure denied'); END")
        conn.commit()
    finally:
        conn.close()
    task = create_task_record("The repair desk code is WILLOW-73.", session_id="fresh-forget")
    result = router("Forget WILLOW-73.")
    assert "could not be verified" in result["response"], result
    assert "Forget applied" not in result["response"], result
    assert load_task_record(task.task_id).task_summary == task.task_summary
    conn = get_connection()
    try:
        conn.execute("DROP TRIGGER deny_task_erasure")
        conn.commit()
    finally:
        conn.close()
    result = router("Forget WILLOW-73.")
    assert result["response"].startswith("Forget applied"), result
    assert load_task_record(task.task_id) is None


def test_task_foreign_key_names_follow_sqlite_case_insensitivity(router):
    from core.task_router import create_task_record, load_task_record
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE shard_feedback")
        conn.execute("DROP TABLE web_notes")
        conn.execute("CREATE TABLE task_history (id TEXT PRIMARY KEY, task_id TEXT "
                     "REFERENCES LOCAL_TASKS(task_id) ON DELETE CASCADE)")
        conn.commit()
    finally:
        conn.close()
    task = create_task_record("The repair desk code is WILLOW-73.", session_id="fresh-forget")
    conn = get_connection()
    try:
        conn.execute("INSERT INTO task_history VALUES (?, ?)", ("old-history", task.task_id))
        conn.commit()
    finally:
        conn.close()
    result = router("Forget WILLOW-73.")
    assert result["response"].startswith("Forget applied"), result
    stored = load_task_record(task.task_id)
    assert stored is not None and stored.task_summary == "", stored
    conn = get_connection()
    try:
        assert conn.execute("SELECT task_id FROM task_history").fetchone()[0] == task.task_id
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("entries", [1, 2])
def test_redaction_failure_is_not_hidden_by_final_verification(router, entries):
    from core.task_router import create_task_record, load_task_record
    from storage.db import get_connection

    router("Remember that the repair desk code is WILLOW-73.")
    if entries == 2:
        router("Remember that the canoe rack marker is WILLOW-73.")
    task = create_task_record("The repair desk code is WILLOW-73.", session_id="fresh-forget")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER deny_task_redaction BEFORE UPDATE OF task_summary ON local_tasks "
                     "BEGIN SELECT RAISE(ABORT, 'redaction denied'); END")
        conn.commit()
    finally:
        conn.close()
    result = router("Forget WILLOW-73.")
    assert "could not be verified" in result["response"], result
    assert "Forget applied" not in result["response"], result
    grammar = "1 entry was removed" if entries == 1 else "2 entries were removed"
    assert grammar in result["response"], result
    assert load_task_record(task.task_id).task_summary == task.task_summary
    recalled = router("What do you remember about WILLOW-73?")
    assert "WILLOW-73" not in recalled["response"], recalled


def test_legacy_derivatives_share_verifier_scope_and_preserve_other_chats(router):
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("CREATE TABLE dialogue_future (id TEXT PRIMARY KEY, session_id TEXT, "
                     "chat_id TEXT, payload_json JSON)")
        conn.executemany("INSERT INTO dialogue_future VALUES (?, ?, ?, ?)", [
            ("legacy-null", None, "other-chat", '{"code":"CEDAR-92"}'),
            ("legacy-empty", "", "other-chat", '{"code":"CEDAR_92"}'),
            ("foreign", "other-chat", "fresh-forget", '{"code":"CEDAR-92"}'),
            ("sibling", "fresh-forget", "fresh-forget", '{"code":"OAK-81"}'),
        ])
        conn.commit()
    finally:
        conn.close()
    result = router("Forget CEDAR-92.")
    assert result["response"].startswith("Forget applied"), result
    conn = get_connection()
    try:
        assert [r[0] for r in conn.execute("SELECT id FROM dialogue_future ORDER BY id")] == ["foreign", "sibling"]
    finally:
        conn.close()


@pytest.mark.parametrize("payload", ['{"code":"CEDAR_92"}', '["CEDAR-92"]', '"CEDAR-92"'])
def test_referenced_task_future_json_remains_readable_after_redaction(router, payload):
    import json

    from core.task_router import create_task_record
    from storage.db import get_connection

    task = create_task_record("The marina gate closes at sunset.", session_id="fresh-forget")
    conn = get_connection()
    try:
        conn.execute("ALTER TABLE local_tasks ADD COLUMN future_payload_json JSON")
        conn.execute("UPDATE local_tasks SET future_payload_json = ? WHERE task_id = ?", (payload, task.task_id))
        conn.commit()
    finally:
        conn.close()
    result = router("Forget CEDAR-92.")
    assert result["response"].startswith("Forget applied"), result
    conn = get_connection()
    try:
        row = conn.execute("SELECT task_summary, future_payload_json FROM local_tasks WHERE task_id = ?",
                           (task.task_id,)).fetchone()
        assert row[0] == task.task_summary
        assert json.loads(row[1]) in ({}, [], None)
    finally:
        conn.close()
