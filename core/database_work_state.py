"""Durable evidence that a chat session is in the middle of database work.

"how many orders are shipped" and "and how many are pending?" name no database, so the wording
gates cannot see that they belong to the database conversation the person is having (served
run5: the same follow-up was answered by the tool in one run and by the tools-less lane in the
next). The signal is read from the execution ledger, never from the words: a vool-database tool
this session ran recently. Consumers follow core.email_work_state: the chat-lane policy releases
the turn, the planner admits the tool lane, the offer seats the READ-ONLY tools. Mutating tools are
never seated by session state; they still need a named ask, approval and the permission gates.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

RECENT_DATABASE_WORK_SECONDS = 30 * 60

_READ_ONLY_SEATS = (
    "vool-database.query",
    "vool-database.schema",
    "vool-database.connect",
    "vool-database.explain",
)


def _registered(intents: tuple[str, ...]) -> tuple[str, ...]:
    try:
        from core.tool_registry import registered_tools

        have = {str(c.intent) for c in registered_tools() if str(c.source or "").startswith("plugin:")}
    except Exception:
        return ()
    return tuple(i for i in intents if i in have)


# A session whose earlier turn ASKED for database work stays a database conversation even when
# that turn ended without a tool call (served run9: "list the members" died, and the next
# follow-up "does Yara have anything checked out right now" named no table). Process memory,
# bounded; the executed-tool ledger below covers restarts for sessions that ran a tool.
_ASKED: dict[str, float] = {}
_MAX_TRACKED = 256


def _session_of(source_context: dict[str, Any] | None) -> str:
    context = source_context if isinstance(source_context, dict) else {}
    return str(context.get("runtime_session_id") or context.get("session_id") or "").strip()


def note_database_demand(source_context: dict[str, Any] | None) -> None:
    """Remember that this session asked for database work. Never raises."""
    try:
        session = _session_of(source_context)
        if not session:
            return
        now = time.time()
        _ASKED[session] = now
        if len(_ASKED) > _MAX_TRACKED:
            for key, stamp in sorted(_ASKED.items(), key=lambda kv: kv[1])[: len(_ASKED) - _MAX_TRACKED]:
                _ASKED.pop(key, None)
    except Exception:
        pass


def _asked_recently(session: str) -> bool:
    stamp = _ASKED.get(session)
    return stamp is not None and time.time() - stamp <= RECENT_DATABASE_WORK_SECONDS


def active_database_work_intents(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """The read-only database tools a session with recent database work should keep; () otherwise. Never raises."""
    try:
        context = source_context if isinstance(source_context, dict) else {}
        session = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
        if not session:
            return ()
        seats = _registered(_READ_ONLY_SEATS)
        if not seats:
            return ()
        if _asked_recently(session):
            return seats
        from core.execution_truth import KIND_TOOL, recent_session_facts

        newest = next(
            (f for f in recent_session_facts(session, name_prefix="vool-database.") if f.in_category(KIND_TOOL)),
            None,
        )
        if newest is None:
            return ()
        stamp = datetime.fromisoformat(str(newest.created_at))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if time.time() - stamp.timestamp() > RECENT_DATABASE_WORK_SECONDS:
            return ()
        return seats
    except Exception:
        return ()


__all__ = ["RECENT_DATABASE_WORK_SECONDS", "active_database_work_intents"]


def database_catalog_block(max_chars: int = 1800) -> str:
    """The person's disposable databases, tables and columns as one compact block ("" when none).

    Handed to the model beside the database skill so it uses the real names instead of
    guessing them (served run9: `publication_year`, `plant_name`, "the library db" read as a table).
    Read-only; bounded; never raises.
    """
    try:
        import sqlite3

        from core.plugin_tools import plugin_scratch_dir

        root = plugin_scratch_dir("vool-database") / "databases"
        files = sorted(p for p in root.glob("*.sqlite") if p.is_file())[:12]
        lines: list[str] = []
        for path in files:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
            try:
                tables = []
                for (table,) in conn.execute(
                    "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
                ).fetchall()[:20]:
                    columns = [row[1] for row in conn.execute('pragma table_info("%s")' % table.replace('"', ""))]
                    tables.append("%s(%s)" % (table, ", ".join(columns[:30])))
            finally:
                conn.close()
            lines.append("- database `%s`: %s" % (path.stem, "; ".join(tables) or "no tables"))
        if not lines:
            return ""
        text = (
            "## The person's databases right now\n"
            + "\n".join(lines)
            + "\nUse these exact database, table and column names; never guess one. A phrase like "
            "\"my library db\" names the DATABASE `library`, not a table. A row-limited result is not a count: "
            "use SELECT COUNT(*) to count. A \"returned\"-style flag column means 0 = still out."
        )
        return text[:max_chars]
    except Exception:
        return ""
