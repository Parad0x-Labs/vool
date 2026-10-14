"""v0.7.2: a PIN, card number or password typed in chat is never written to VOOL's run records.

THE MEASURED DEFECT (main 48dfe06, served daemon, fresh home)
-------------------------------------------------------------
v0.7.1 keeps typed secrets out of memory and out of every model prompt, but after
"Remember that my bank card PIN is 4821." the raw PIN was still written to run records on disk:
``runtime_checkpoints`` (request, source context, state, outcome), ``obligation_sets.snapshot_json``
and ``context_access_log.report_json``. None reaches a model, but each is plain text on disk.

THE CONTRACT UNDER TEST
-----------------------
* Over the real ``/api/chat`` door, no row of the runtime database holds the typed value, while
  the same records are still written (checkpoints, plan, access report exist for the turns).
* A turn with no secret is recorded exactly as before: its plain words are in its records.
"""
from __future__ import annotations

import sqlite3
import time

from tests.test_secret_context_withheld_v071 import _chat_like_the_page, _served_home

TURNS = (
    "Remember that my bank card PIN is 4821.",
    "What is my bank card PIN?",
    "My email password is Qx7-plover-91. Also explain entropy briefly.",
    "My sister lives in Lisbon.",
)
SECRETS = ("PIN is 4821", "Qx7-plover-91")
RECORD_TABLES = ("runtime_checkpoints", "obligation_sets", "context_access_log")


def _rows_holding(conn, needle: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        for column in columns:
            try:
                count = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{column}" AS TEXT) LIKE ?', (f"%{needle}%",)
                ).fetchone()[0]
            except sqlite3.DatabaseError:
                continue
            if count:
                found[f"{table}.{column}"] = count
    return found


def test_no_run_record_holds_a_typed_secret_and_plain_turns_are_still_recorded():
    with _served_home() as (home, daemon, _local, _paid):
        answers = _chat_like_the_page(daemon, TURNS)
        time.sleep(2.0)
    assert "didn't save your PIN" in answers[0], answers[0]

    conn = sqlite3.connect(next((home / "data").glob("vool_web0_v2*.db")))
    try:
        for table in RECORD_TABLES:
            assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] > 0, (
                f"{table} was never written, so this test proves nothing"
            )
        leaks = {secret: _rows_holding(conn, secret) for secret in SECRETS}
        plain = _rows_holding(conn, "My sister lives in Lisbon")
    finally:
        conn.close()
    assert leaks == {secret: {} for secret in SECRETS}, leaks
    assert any(key.startswith(RECORD_TABLES) for key in plain), f"the plain turn is no longer recorded: {plain}"
