"""Context (memory) must survive an update the same way the wallet does — both by NOT being
deleted (the DB lives under the preserved data/ dir) and by NEW code reading OLD stored context
without nuking it (additive schema migration). This is the guarantee that lets Context Capsule 2.0
keep evolving its schema without orphaning a user's accumulated memory.
"""
from __future__ import annotations

import json
import sqlite3

from core.vool_memory import VoolMemory, _resolve_db_path

# The memory_nodes schema as it existed BEFORE the ranking/temporal + FTS upgrade — i.e. a DB an
# older VOOL build would have written. No base_importance/access_count/valid_from/valid_to, no FTS.
_OLD_MEMORY_NODES_DDL = """
CREATE TABLE memory_nodes (
    node_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, content TEXT NOT NULL,
    timestamp REAL NOT NULL, keywords TEXT NOT NULL, tags TEXT NOT NULL,
    context_description TEXT NOT NULL, embedding TEXT NOT NULL, linked_node_ids TEXT NOT NULL
);
"""


def _make_old_db(path, *, node_id="n1", content="the deploy gateway port is 18789", keywords="port,18789,gateway"):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute(_OLD_MEMORY_NODES_DDL)
    con.execute(
        "INSERT INTO memory_nodes (node_id, agent_id, content, timestamp, keywords, tags, "
        "context_description, embedding, linked_node_ids) VALUES (?,?,?,?,?,?,?,?,?)",
        (node_id, "vool_chat", content, 1000.0, keywords, "user", "old", json.dumps([0.1, 0.2, 0.3]), json.dumps([])),
    )
    con.commit()
    con.close()


def test_new_code_migrates_old_db_without_losing_context(tmp_path) -> None:
    db = tmp_path / "data" / "memory" / "vool_memory.db"
    _make_old_db(db, node_id="n1", content="remember the number 8932173982718939281")

    VoolMemory(agent_id="vool_chat", db_path=db).close()  # opening runs SCHEMA + _migrate

    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT node_id, content FROM memory_nodes").fetchall()
    cols = {r[1] for r in con.execute("PRAGMA table_info(memory_nodes)")}
    fts = con.execute("SELECT count(*) FROM memory_fts WHERE node_id = 'n1'").fetchone()[0]
    con.close()

    # 1. the OLD context row survives verbatim — never dropped or reset.
    assert any(r[0] == "n1" and "8932173982718939281" in r[1] for r in rows)
    # 2. the new ranking/temporal columns were ADDED (additive migration).
    assert {"base_importance", "access_count", "valid_from", "valid_to"} <= cols
    # 3. the FTS index was backfilled for the pre-upgrade node (so hybrid/BM25 recall covers it).
    assert fts == 1


def test_migrated_old_node_is_recalled_by_hybrid_search(tmp_path) -> None:
    # The Capsule 2.0 hybrid recall (added later) must work on context stored by an OLDER build.
    db = tmp_path / "data" / "memory" / "vool_memory.db"
    _make_old_db(db, content="the deploy gateway port is 18789")
    mem = VoolMemory(agent_id="vool_chat", db_path=db)
    try:
        hits = mem.node_search_hybrid("gateway port 18789", [0.1, 0.2, 0.3], top_k=5, min_score=0.0)
    finally:
        mem.close()
    assert any("18789" in node.content for node, _score in hits)


def test_reopening_migrated_db_is_idempotent_and_keeps_data(tmp_path) -> None:
    db = tmp_path / "data" / "memory" / "vool_memory.db"
    _make_old_db(db, node_id="keep", content="my wallet label is main-vault")
    VoolMemory(agent_id="vool_chat", db_path=db).close()
    VoolMemory(agent_id="vool_chat", db_path=db).close()  # second open must not error or wipe
    con = sqlite3.connect(str(db))
    n = con.execute("SELECT count(*) FROM memory_nodes WHERE node_id = 'keep'").fetchone()[0]
    con.close()
    assert n == 1


def test_memory_db_lives_under_the_preserved_data_dir(tmp_path) -> None:
    # The updater preserves `data`/`.vool_runtime`; the memory DB must live there so an update
    # can never delete accumulated context (same guarantee as the wallet).
    p = _resolve_db_path(runtime_home=tmp_path, db_path=None)
    assert p.parts[-3:] == ("data", "memory", "vool_memory.db")
