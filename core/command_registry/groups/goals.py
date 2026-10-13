"""Goals group — read-only discovery and status for the explicit /goal workflow.

The goal LIFECYCLE controls (start/pause/resume/stop/retry) deliberately have no registry
command: they are chat-surface actions bound to the chatting session and its live mode
context, served through the chat ingress and the loopback-gated ``/api/goal/*`` routes. A
registry-dispatchable control would be a second, session-loose door into another chat's
goal. What the palette and API get is the truthful read: the chat's current goal state.
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
class GoalStatusInput:
    session_id: str = ""


def _probe_goal_store(context: dict) -> tuple[bool, str]:
    try:
        from storage.db import get_connection

        conn = get_connection()
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_goals'")
        return (True, "") if cur.fetchone() else (False, "goal store not created yet")
    except Exception as exc:
        return False, f"control database unreachable: {exc}"


def _handle_status(inp, ctx):
    from core.goal_workflow import goal_public_view, load_latest_goal_for_session

    session_id = str(getattr(inp, "session_id", "") or "").strip() or str(
        (getattr(ctx, "context", None) or {}).get("session_id") or ""
    ).strip()
    if not session_id:
        return HandlerOk(
            data={"goal": None, "note": "no session given; pass session_id or run from a chat surface"},
            summary="no goal session given",
        )
    goal = load_latest_goal_for_session(session_id)
    view = goal_public_view(goal) if goal else None
    summary = (
        f"goal {view['state']}: {view['completed']}/{view['total']} steps"
        if view
        else "no goal in this chat"
    )
    return HandlerOk(data={"goal": view}, summary=summary)


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="goals", description="explicit /goal workflow: chat-bound goal status"))
    reg.add(
        CommandSpec(
            command_id="goals.status",
            group="goals",
            description="Show the chat's explicit goal: state, plan progress, current step",
            aliases=("goal",),
            input_schema=GoalStatusInput,
            effects="read_only",
            capabilities=frozenset({"control_plane.read"}),
            handler=Handler("core.command_registry.groups.goals:_handle_status"),
            availability=Availability("core.command_registry.groups.goals:_probe_goal_store"),
            exit_codes=(0, 2, 10),
            model_offerable=True,
        )
    )
