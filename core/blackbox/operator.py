"""Operator-facing entry points. ``rollback_turn`` mints a bound single-use token and sends the
rollback across ``execute_authorized_runtime_tool`` -- the same permission authority every tool
call crosses -- so an operator in MANUAL mode is asked for approval exactly like a model would be.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.blackbox.authority import DIRECTIVE_KEY, mint_rollback_authorization
from core.blackbox.store import BlackboxStore, default_store


def rollback_turn(
    turn_id: str,
    *,
    workspace_root: Path | str,
    session_id: str = "",
    operator: str = "operator",
    source_context: dict[str, Any] | None = None,
    task_id: str = "",
    store: BlackboxStore | None = None,
    authority_token: str | None = None,
) -> Any:
    """``authority_token`` is a scope from ``grant_internal_authority`` (bounded, expiring,
    workspace-bound). Without one the ordinary mode matrix decides -- and both Manual and Auto
    PROMPT for a delete-class effect, so an unattended operator surface must mint its own scope
    and say so (see ``__main__``)."""
    from core.authorized_tool_execution import execute_authorized_runtime_tool

    root = Path(workspace_root).expanduser().resolve()
    store = store or default_store()
    token = mint_rollback_authorization(turn_id=turn_id, workspace_root=root, operator=operator, store=store)
    context = dict(source_context or {})
    context["workspace"] = str(root)
    context.setdefault("session_id", session_id or f"operator:{operator}")
    if session_id:
        context["session_id"] = session_id
    context[DIRECTIVE_KEY] = {"turn_id": str(turn_id), "authorization": token, "operator": str(operator)}
    return execute_authorized_runtime_tool(
        "workspace.rollback_last_change",
        {},
        task_id=task_id or f"rollback:{turn_id}",
        source_context=context,
        authority_token=authority_token,
    )


def list_turns(*, workspace_root: Path | str | None = None, store: BlackboxStore | None = None) -> list[dict[str, Any]]:
    store = store or default_store()
    root = str(Path(workspace_root).expanduser().resolve()) if workspace_root else ""
    entries = store.entries()
    records = store.effect_records(entries)
    out: list[dict[str, Any]] = []
    for summary in sorted(store.turn_index(entries).values(), key=lambda t: t.first_seq):
        if root and summary.root != root:
            continue
        effects = [records[e] for e in summary.effect_ids if e in records]
        out.append(
            {
                "turn_id": summary.turn_id,
                "root": summary.root,
                "first_seq": summary.first_seq,
                "sessions": sorted(summary.sessions),
                "effects": [
                    {"effect_id": r.effect_id, "path": r.path, "operation": r.intended.get("operation"),
                     "outcome": r.outcome or "open", "before_sha256": r.before.sha256,
                     "after_sha256": (r.after.sha256 if r.after else "")}
                    for r in effects
                ],
                "pruned": summary.pruned,
                "rolled_back": summary.rolled_back,
                "is_rollback": summary.is_rollback,
            }
        )
    return out


def status(*, store: BlackboxStore | None = None) -> dict[str, Any]:
    return (store or default_store()).status()


def verify(*, store: BlackboxStore | None = None) -> dict[str, Any]:
    report = (store or default_store()).verify()
    return {"ok": report.ok, "reason": report.reason, "entries": report.entries, "head_count": report.head_count,
            "first_bad_seq": report.first_bad_seq, "last_hash": report.last_hash}


__all__ = ["list_turns", "rollback_turn", "status", "verify"]
