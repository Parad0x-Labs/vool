import time

from storage.db import get_connection


def _init_pin_table() -> None:
    """Ensures chunk pin tracking exists in SQLite."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS local_chunk_pins (
                chunk_hash TEXT PRIMARY KEY,
                pinned_at TEXT,
                last_accessed_at TEXT,
                access_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    finally:
        conn.close()

def pin_chunk(chunk_hash: str) -> None:
    """Records that we officially host this chunk."""
    _init_pin_table()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO local_chunk_pins (chunk_hash, pinned_at, last_accessed_at, access_count)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(chunk_hash) DO UPDATE SET
                last_accessed_at=excluded.pinned_at,
                access_count=access_count + 1
        """, (chunk_hash, now_iso, now_iso))
        conn.commit()
    finally:
        conn.close()

def unpin_chunk(chunk_hash: str) -> None:
    """Removes a pin. (The CAS file remains until a garbage collector cleans it)."""
    _init_pin_table()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM local_chunk_pins WHERE chunk_hash = ?", (chunk_hash,))
        conn.commit()
    finally:
        conn.close()

def get_pinned_chunks(limit: int = 100) -> list[str]:
    """Returns a list of chunk hashes we are currently pinning, ordered by least-recently used."""
    _init_pin_table()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT chunk_hash FROM local_chunk_pins ORDER BY last_accessed_at ASC LIMIT ?", (limit,)).fetchall()
        return [r["chunk_hash"] for r in rows]
    finally:
        conn.close()
