"""Opening the memory store must not get slower with the square of the chat history.

Every VoolMemory open re-checks that each memory node and each active source occurrence has its
full-text row. That check used a correlated NOT EXISTS against the FTS5 tables, which scans the
whole index once per row: with 2000 nodes one open took about 0.6 s, and revocation reads open
the store several times per turn (the CI shard 0 stall on b9530c3c). The check is now one pass
over each side. These tests pin both halves: the backfill still repairs missing index rows, and
the work done by an open grows linearly with the store, counted in SQLite VM steps so the
measure does not depend on the machine.
"""
from __future__ import annotations

import sqlite3

import core.vool_memory as vool_memory_module
from core.vool_memory import VoolMemory

_STEP = 1000  # progress-handler period in VM instructions


def _seed(home, n: int, *, agent_id: str = "vool_chat") -> str:
    memory = VoolMemory(runtime_home=str(home), agent_id=agent_id)
    db = str(memory.db_path)
    memory.close()
    conn = sqlite3.connect(db)
    occ_cols = [(r[1], (r[2] or "").upper()) for r in conn.execute("PRAGMA table_info(source_occurrences)")]
    node_cols = [(r[1], (r[2] or "").upper()) for r in conn.execute("PRAGMA table_info(memory_nodes)")]

    def blank(kind: str):
        return "" if ("TEXT" in kind or kind == "") else 0

    occ_rows = []
    for i in range(n):
        row = {name: blank(kind) for name, kind in occ_cols}
        row.update(occurrence_id=f"occ{i}", status="active", chat_scope="chat-a",
                   body=f"turn {i} the person said something about item {i} " * 4)
        occ_rows.append(tuple(row[name] for name, _ in occ_cols))
    conn.executemany(
        f"INSERT INTO source_occurrences ({','.join(c for c, _ in occ_cols)}) "
        f"VALUES ({','.join('?' * len(occ_cols))})", occ_rows)
    body_at = [c for c, _ in occ_cols].index("body")
    conn.executemany("INSERT INTO source_fts (occurrence_id, body) VALUES (?, ?)",
                     [(f"occ{i}", occ_rows[i][body_at]) for i in range(n)])
    node_rows = []
    for i in range(n):
        row = {name: blank(kind) for name, kind in node_cols}
        row.update(node_id=f"node{i}", agent_id=agent_id if i % 2 == 0 else "other_agent",
                   content=f"fact {i} about the person", keywords="[]", tags="[]",
                   context_description="", embedding="[]", linked_node_ids="[]",
                   timestamp=1.0, valid_to=None)
        node_rows.append(tuple(row[name] for name, _ in node_cols))
    conn.executemany(
        f"INSERT INTO memory_nodes ({','.join(c for c, _ in node_cols)}) "
        f"VALUES ({','.join('?' * len(node_cols))})", node_rows)
    conn.executemany(
        "INSERT INTO memory_fts (node_id, agent_id, content, keywords) VALUES (?, ?, ?, ?)",
        [(f"node{i}", agent_id if i % 2 == 0 else "other_agent", f"fact {i} about the person", "[]")
         for i in range(n)])
    conn.commit()
    conn.close()
    return db


def _open_steps(monkeypatch, home) -> int:
    """SQLite VM steps (in units of _STEP) spent by one VoolMemory open."""
    real_connect = sqlite3.connect
    ticks = [0]

    def counting_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)

        def tick():
            ticks[0] += 1
            return 0

        conn.set_progress_handler(tick, _STEP)
        return conn

    monkeypatch.setattr(vool_memory_module.sqlite3, "connect", counting_connect)
    try:
        VoolMemory(runtime_home=str(home), agent_id="vool_chat").close()
    finally:
        monkeypatch.setattr(vool_memory_module.sqlite3, "connect", real_connect)
    return ticks[0]


def test_open_work_grows_linearly_with_history(tmp_path, monkeypatch):
    small, large = tmp_path / "small", tmp_path / "large"
    small.mkdir()
    large.mkdir()
    _seed(small, 500)
    _seed(large, 2000)
    VoolMemory(runtime_home=str(small), agent_id="vool_chat").close()  # first-open migrations
    VoolMemory(runtime_home=str(large), agent_id="vool_chat").close()
    steps_small = _open_steps(monkeypatch, small)
    steps_large = _open_steps(monkeypatch, large)
    # 4x the history: linear work is about 4x; the per-row index scan was about 16x.
    assert steps_large <= 8 * max(steps_small, 1), (steps_small, steps_large)


def test_open_still_backfills_missing_index_rows(tmp_path):
    db = _seed(tmp_path, 40)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM memory_fts WHERE node_id IN ('node3', 'node4', 'node17')")
    conn.execute("DELETE FROM source_fts WHERE occurrence_id IN ('occ5', 'occ6')")
    # a withdrawn occurrence and an empty one must stay out of the index
    conn.execute("UPDATE source_occurrences SET status = 'withdrawn' WHERE occurrence_id = 'occ6'")
    conn.execute("UPDATE source_occurrences SET body = '' WHERE occurrence_id = 'occ9'")
    conn.execute("DELETE FROM source_fts WHERE occurrence_id = 'occ9'")
    conn.commit()
    conn.close()

    VoolMemory(runtime_home=str(tmp_path), agent_id="vool_chat").close()

    conn = sqlite3.connect(db)
    fts = dict(conn.execute("SELECT node_id, agent_id FROM memory_fts").fetchall())
    occ = {r[0] for r in conn.execute("SELECT occurrence_id FROM source_fts")}
    conn.close()
    assert len(fts) == 40
    # each repaired row keeps its own agent, not the opener's
    assert fts["node3"] == "other_agent"
    assert fts["node4"] == "vool_chat"
    assert fts["node17"] == "other_agent"
    assert "occ5" in occ
    assert "occ6" not in occ
    assert "occ9" not in occ
