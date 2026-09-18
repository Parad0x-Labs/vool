"""Tasks group — coding-task controls from the control plane.

Binds to ``core.control_plane.queue_views`` / ``core.control_plane.runtime_views``
— the same reads the control-plane workspace renders from. Read-only for this
milestone: the operator sees the queue and the live coding sessions.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerOk,
)


@dataclass(frozen=True)
class QueueInput:
    limit: int = 32


@dataclass(frozen=True)
class SessionsInput:
    limit: int = 32
    event_limit: int = 12


def _table_exists(conn, table: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _probe_control_db(context: dict) -> tuple[bool, str]:
    try:
        from storage.db import get_connection

        conn = get_connection()
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        return (True, "") if cur.fetchone() else (False, "control database has no tables yet")
    except Exception as exc:
        return False, f"control database unreachable: {exc}"


def _handle_queue(inp, ctx):
    from core.control_plane.queue_views import load_open_task_offers
    from storage.db import get_connection

    limit = max(1, min(int(inp.limit or 32), 200))
    conn = get_connection()
    offers = load_open_task_offers(conn, limit=limit, table_exists_fn=_table_exists)
    return HandlerOk(
        data={"count": len(offers), "offers": offers},
        summary=f"{len(offers)} open coding-task offer(s)",
    )


def _handle_sessions(inp, ctx):
    from core.control_plane.runtime_views import load_runtime_sessions
    from storage.db import get_connection

    conn = get_connection()
    sessions = load_runtime_sessions(conn, limit=int(inp.limit or 32), event_limit=int(inp.event_limit or 12))
    return HandlerOk(
        data={"count": len(sessions), "sessions": sessions},
        summary=f"{len(sessions)} runtime coding session(s)",
    )


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="tasks", description="coding-task controls: the control-plane queue and live sessions"))
    reg.add(
        CommandSpec(
            command_id="tasks.queue",
            group="tasks",
            description="Show open coding-task offers from the control plane",
            aliases=("tasks",),
            input_schema=QueueInput,
            effects="read_only",
            capabilities=frozenset({"control_plane.read"}),
            handler=Handler("core.command_registry.groups.tasks:_handle_queue"),
            availability=Availability("core.command_registry.groups.tasks:_probe_control_db"),
            exit_codes=(0, 2, 10),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="tasks.sessions",
            group="tasks",
            description="Show live runtime coding sessions and their checkpoints",
            input_schema=SessionsInput,
            effects="read_only",
            capabilities=frozenset({"control_plane.read"}),
            handler=Handler("core.command_registry.groups.tasks:_handle_sessions"),
            availability=Availability("core.command_registry.groups.tasks:_probe_control_db"),
            exit_codes=(0, 2, 10),
        )
    )
