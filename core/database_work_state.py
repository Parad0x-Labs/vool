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
            for key, _stamp in sorted(_ASKED.items(), key=lambda kv: kv[1])[: len(_ASKED) - _MAX_TRACKED]:
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


def database_catalog_block(max_chars: int = 2600) -> str:
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
                links: list[str] = []
                targets_of: dict[str, list[tuple[str, str]]] = {}
                shapes: dict[str, list[str]] = {}
                for (table,) in conn.execute(
                    "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
                ).fetchall()[:20]:
                    columns = [row[1] for row in conn.execute('pragma table_info("%s")' % table.replace('"', ""))]
                    flags = []
                    for column in columns[:30]:
                        if column.lower() == "id" or column.lower().endswith("_id"):
                            continue
                        try:
                            seen = [
                                r[0]
                                for r in conn.execute(
                                    'select distinct "%s" from "%s" limit 4' % (column.replace('"', ""), table.replace('"', ""))
                                )
                            ]
                        except Exception:
                            continue
                        if 0 < len(seen) <= 3 and all(isinstance(v, int) for v in seen):
                            flags.append("%s in {%s}" % (column, ",".join(str(v) for v in sorted(seen))))
                    tables.append("%s(%s)%s" % (table, ", ".join(columns[:30]), (" [values: " + "; ".join(flags) + "]") if flags else ""))
                    shapes[table] = columns
                for table, columns in shapes.items():
                    for column in columns:
                        stem = column[:-3] if column.lower().endswith("_id") else ""
                        target = next((t for t in (stem, stem + "s", stem + "es") if stem and t in shapes and t != table), "")
                        if target:
                            links.append("%s.%s = %s.id" % (table, column, target))
                            targets_of.setdefault(table, []).append((column, target))
            finally:
                conn.close()
            example = ""
            for table, pairs in targets_of.items():
                if len(pairs) < 2:
                    continue
                picks = []
                for _col, target in pairs[:2]:
                    label = next((c for c in shapes[target] if c.lower() not in ("id",) and not c.lower().endswith("_id")), "id")
                    picks.append("%s.%s" % (target, label))
                tail = [c for c in shapes[table] if c.lower() not in ("id",) and not c.lower().endswith("_id")][:1]
                who = next(
                    ("%s.name" % target for _c, target in pairs[:2] if "name" in [x.lower() for x in shapes[target]]),
                    picks[0],
                )
                joins = " ".join("JOIN %s ON %s.%s = %s.id" % (target, table, col, target) for col, target in pairs[:2])
                example = (
                    "; example (who holds what): SELECT %s FROM %s %s WHERE %s = '<name>'"
                    % (", ".join(picks + ["%s.%s" % (table, c) for c in tail]), table, joins, who)
                )
                break
            lines.append(
                "- database `%s`: %s%s"
                % (path.stem, "; ".join(tables) or "no tables", ("; joins: " + ", ".join(links[:8])) if links else "") + example
            )
        if not lines:
            return ""
        text = (
            "## The person's databases right now\n"
            + "\n".join(lines)
            + "\nUse these exact database, table and column names; never guess one. A phrase like "
            "\"my library db\" names the DATABASE `library`, not a table. A row-limited result is not a count: "
            "use SELECT COUNT(*) to count. A \"returned\"-style flag column means 0 = still out. "
            "Questions about the people, items or records in these tables (\"has Tomas given everything back\", "
            "\"which member joined most recently\") are answered by querying them: a first name is a value in a "
            "table (join through the id columns), so look it up instead of asking what is meant, and never use "
            "a process, file or web tool for them."
        )
        return text[:max_chars]
    except Exception:
        return ""


_NAME_TOKEN_RE = None


def suggest_database_query(question: str, max_chars: int = 600) -> str:
    """One concrete read-only call for a question, built from the person's own tables ("" when none fits).

    Run 16f: the model answered "I don't have access" to "list the members, newest first" even after being told
    to look; the prompt's examples did not land. A ready call with the real database and table names does: the
    nudge hands it over. A first name the person gives that exists in a `name` column becomes a lookup of that
    row, joined through the id columns when a table links to it. Read-only, bounded, never raises."""
    try:
        import re
        import sqlite3

        from core.plugin_tools import plugin_scratch_dir

        text = str(question or "")
        lowered = text.lower()
        root = plugin_scratch_dir("vool-database") / "databases"
        for path in sorted(p for p in root.glob("*.sqlite") if p.is_file())[:6]:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
            try:
                shapes: dict[str, list[str]] = {}
                for (table,) in conn.execute(
                    "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
                ).fetchall()[:20]:
                    shapes[table] = [r[1] for r in conn.execute('pragma table_info("%s")' % table.replace('"', ""))]
                # 1. a name the person gave, found in a name column
                for token in re.findall(r"\b[A-Z][a-z]{2,}\b", text):
                    for table, columns in shapes.items():
                        if "name" not in [c.lower() for c in columns]:
                            continue
                        hit = conn.execute(
                            'select count(*) from "%s" where lower(name) = lower(?)' % table.replace('"', ""), (token,)
                        ).fetchone()[0]
                        if not hit:
                            continue
                        linked = []
                        for other, other_columns in shapes.items():
                            for column in other_columns:
                                stem = column[:-3] if column.lower().endswith("_id") else ""
                                if stem and table in (stem, stem + "s", stem + "es") and other != table:
                                    linked.append((other, column))
                        if not linked:
                            sql = "SELECT * FROM %s WHERE name = '%s'" % (table, token)
                        else:
                            other, column = linked[0]
                            joins = [" JOIN %s ON %s.%s = %s.id" % (table, other, column, table)]
                            picks = ["%s.name" % table]
                            for col2 in shapes[other]:
                                stem2 = col2[:-3] if col2.lower().endswith("_id") else ""
                                tgt = next((t for t in (stem2, stem2 + "s", stem2 + "es") if stem2 and t in shapes and t not in (other, table)), "")
                                if tgt:
                                    label = next((c for c in shapes[tgt] if c.lower() != "id" and not c.lower().endswith("_id")), "id")
                                    joins.append(" JOIN %s ON %s.%s = %s.id" % (tgt, other, col2, tgt))
                                    picks.append("%s.%s" % (tgt, label))
                            picks += ["%s.%s" % (other, c) for c in shapes[other] if c.lower() != "id" and not c.lower().endswith("_id")][:2]
                            sql = "SELECT %s FROM %s%s WHERE %s.name = '%s'" % (", ".join(picks), other, "".join(joins), table, token)
                        return ("name=%s, sql=%s" % (path.stem, sql))[:max_chars]
                # 2. a table the person named, with the order they asked for
                for table, columns in shapes.items():
                    names = {table.lower(), table.lower().rstrip("s"), table.lower()[:-3] + "y" if table.lower().endswith("ies") else table.lower()}
                    if not any(re.search(r"\b%s(?:s|es)?\b" % re.escape(n), lowered) for n in names if n):
                        continue
                    order = ""
                    # An expiry or due date is the column a "latest expiry first" ask sorts by (Mac answer-quality
                    # row 3 on d5d044b0: "show my freezer items, the ones expiring last at the top").
                    date_col = next(
                        (c for c in columns if re.search(r"expir|best_before|use_by|due", c, re.I)
                         and re.search(r"\b(?:expir\w*|due|best before|use by)\b", lowered)),
                        "",
                    ) or next((c for c in columns if re.search(r"join|date|created|added|year|time", c, re.I)), "")
                    if date_col and re.search(
                        r"\b(newest|latest|recent|most recently|furthest)\b|\blast\s+(?:at|on)\s+(?:the\s+)?top\b"
                        r"|\b(?:expir\w*|due)\s+last\b", lowered
                    ):
                        order = " ORDER BY %s DESC" % date_col
                    elif date_col and re.search(
                        r"\b(oldest|earliest|soonest)\b|\b(?:expir\w*|due)\s+(?:first|soonest|next)\b", lowered
                    ):
                        order = " ORDER BY %s ASC" % date_col
                    return ("name=%s, sql=SELECT * FROM %s%s" % (path.stem, table, order))[:max_chars]
                # 3. no table named, but a column is: "who joined most recently" -> the table with `joined`
                for table, columns in shapes.items():
                    hit = next(
                        (
                            c for c in columns
                            if c.lower() != "id" and not c.lower().endswith("_id") and len(c) >= 4
                            and re.search(r"\b%s" % re.escape(c.lower()[:5]), lowered)
                        ),
                        "",
                    )
                    if not hit:
                        continue
                    descending = re.search(r"\b(newest|latest|most recent(?:ly)?|largest|biggest|highest|most)\b", lowered)
                    ascending = re.search(r"\b(oldest|earliest|soonest|smallest|lowest|least)\b", lowered)
                    if not (descending or ascending):
                        continue
                    order = " ORDER BY %s %s" % (hit, "ASC" if ascending and not descending else "DESC")
                    limit = " LIMIT 1" if re.search(r"\b(who|which|what)\b", lowered) and not re.search(r"\b(list|all|every)\b", lowered) else ""
                    return ("name=%s, sql=SELECT * FROM %s%s%s" % (path.stem, table, order, limit))[:max_chars]
            finally:
                conn.close()
        return ""
    except Exception:
        return ""
