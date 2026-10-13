"""Served /goal surface: the chat-ingress adapter and the owner-local control routes.

The chat ingress adapter turns a ``/goal`` composer message into a short, honest NDJSON turn
(usage text, status, control receipt, or goal creation) — the same wire shape every chat turn
uses, so the existing page renders it with no new transport. Goal execution then runs in a
detached worker thread (registered BEFORE the thread starts, so a control racing the first
instruction still lands); progress flows through the durable goal events in the runtime event
ledger, and the compact card reads the canonical state from ``/api/goal/state``.

No new authority: pause/stop reach the in-flight step turn through ``core.live_turns``,
resume/retry re-dispatch through the existing ``run_agent`` path (optionally carrying the
chat's own ``mode_approval_token``), and every state change is persisted in the durable goal
record before it is announced.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

from core.goal_executor import execute_goal, retry_waiting_step
from core.goal_workflow import (
    TERMINAL_GOAL_STATES,
    active_worker,
    goal_public_view,
    load_active_goal_for_session,
    load_goal,
    load_latest_goal_for_session,
    new_goal_id,
    parse_goal_command,
    request_pause,
    request_resume,
    request_stop,
    save_goal,
    unregister_worker,
    _GoalWorker,
    register_worker,
)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _chunk(model: str, content: str, *, done: bool = False) -> bytes:
    from core.web.api.runtime import ollama_stream_chunk

    return ollama_stream_chunk(model=model, content=content, created_at=_now_stamp(), done=done)


def _goal_event_line(view: dict[str, Any], message: str) -> bytes:
    payload = {
        "type": "goal.updated",
        "raw_type": "goal_updated",
        "stage": None,
        "summary": str(message or "")[:300],
        "tool": None,
        "status": None,
        "goal": view,
    }
    return (json.dumps({"vool_event": payload}, separators=(",", ":")) + "\n").encode("utf-8")


def spawn_goal_worker(
    runtime,
    goal: dict[str, Any],
    *,
    base_context: dict[str, Any],
    model: str,
) -> None:
    """Register the worker BEFORE the thread starts (the live-turn registration law), then run
    the goal loop detached. The response stream may end at any time; the worker's lifetime is
    the goal's, not the HTTP stream's."""
    worker = _GoalWorker(goal.get("goal_id") or "", goal.get("session_id") or "")
    register_worker(worker)
    thread = threading.Thread(
        target=_run_worker,
        args=(runtime, worker, str(goal.get("goal_id") or ""), dict(base_context or {}), str(model or "")),
        name=f"goal-worker-{str(goal.get('goal_id') or '')[-8:]}",
        daemon=True,
    )
    thread.start()


def _run_worker(runtime, worker: _GoalWorker, goal_id: str, base_context: dict[str, Any], model: str) -> None:
    try:
        execute_goal(runtime, goal_id, base_context=base_context, model=model)
    except Exception:
        # A worker crash must not leave the goal lying "running" forever: record the honest
        # failure. The exception detail stays in the server log, not in the goal record.
        import logging

        logging.getLogger("vool.goal").exception("goal worker failed (goal=%s)", goal_id)
        goal = load_goal(goal_id)
        if goal is not None and str(goal.get("state")) not in TERMINAL_GOAL_STATES:
            import time as _time

            goal["state"] = "failed"
            goal["ended_at"] = _time.time()
            goal["updated_at"] = _time.time()
            goal["last_reason"] = "goal worker failed; see server log"
            save_goal(goal)
    finally:
        unregister_worker(goal_id)


def _short_stream(lines: list[str], *, model: str, goal_view: dict[str, Any] | None = None) -> Iterator[bytes]:
    """One short local NDJSON turn: the control reply as content, a goal event, terminal."""
    for index, line in enumerate(lines):
        yield _chunk(model, line + ("\n" if index < len(lines) - 1 else ""))
    if goal_view is not None:
        yield _goal_event_line(goal_view, lines[-1] if lines else "")
    yield _chunk(model, "", done=True)


def _commit_goal_transcript(session_id: str, user_text: str, reply_text: str, source_context: dict[str, Any]) -> None:
    from core.persistent_memory import append_conversation_event

    context = dict(source_context or {})
    context.pop("transcript_commit_boundary", None)
    try:
        append_conversation_event(
            session_id=session_id,
            user_input=str(user_text),
            assistant_output=str(reply_text),
            source_context=context,
            response_class="goal_control",
            persist_now=True,
        )
    except Exception:
        pass  # transcript is best-effort for a control reply; the goal record is authoritative


def serve_goal_ingress(
    runtime,
    *,
    parsed: dict[str, str],
    user_text: str,
    session_id: str,
    source_context: dict[str, Any],
    model: str,
) -> tuple[int, Iterator[bytes], dict[str, Any]]:
    """Handle one ``/goal`` composer message. Returns (status, stream, meta).

    Only ever reached owner-local (the ingress branch checks); a remote peer's ``/goal`` text
    is ordinary message text and reaches the model as such.
    """
    sub = str(parsed.get("sub") or "")
    arg = str(parsed.get("arg") or "").strip()
    model_name = str(model or "vool")

    if sub in ("", "start") and not arg:
        reply = (
            "Set an explicit goal with: /goal <what should be achieved>.\n"
            "Controls: /goal status · /goal pause · /goal resume · /goal stop · /goal revise <new wording>.\n"
            "The goal runs under this chat's current operating mode; starting one never raises permissions."
        )
        return 200, _short_stream([reply], model=model_name), {"reply": reply}

    if sub == "status":
        goal = load_latest_goal_for_session(session_id)
        if goal is None:
            reply = "No goal in this chat yet. Start one with /goal <objective>."
            return 200, _short_stream([reply], model=model_name), {"reply": reply}
        view = goal_public_view(goal)
        done, total = view["completed"], view["total"]
        lines = [f"Goal: {view['objective']}", f"State: {view['state']}" + (f" — {view['reason']}" if view["reason"] else "")]
        if total:
            lines.append(f"Progress: {done}/{total} steps (plan revision {view['revision']}).")
        if view["current_step_id"]:
            current = next((s for s in view["steps"] if s.get("id") == view["current_step_id"]), None)
            if current:
                lines.append(f"Current step: {current['title']}")
        return 200, _short_stream(lines, model=model_name, goal_view=view), {"reply": "\n".join(lines)}

    if sub in ("pause", "resume", "stop"):
        goal = load_active_goal_for_session(session_id) if sub != "resume" else load_latest_goal_for_session(session_id)
        if goal is None:
            reply = f"No active goal to {sub}. Start one with /goal <objective>."
            return 200, _short_stream([reply], model=model_name), {"reply": reply}
        if sub == "pause":
            outcome = request_pause(goal.get("goal_id") or "")
        elif sub == "stop":
            outcome = request_stop(goal.get("goal_id") or "")
        else:
            outcome = request_resume(goal.get("goal_id") or "")
            if outcome.get("ok") and not outcome.get("already"):
                fresh = load_goal(goal.get("goal_id") or "")
                if fresh is not None and str(fresh.get("state")) not in TERMINAL_GOAL_STATES:
                    spawn_goal_worker(runtime, fresh, base_context=source_context, model=model_name)
        view = goal_public_view(load_goal(goal.get("goal_id") or "") or goal)
        if outcome.get("ok"):
            reply = f"Goal {sub}d. State: {view['state']}."
        else:
            reply = f"Could not {sub} the goal: {outcome.get('error')}"
        return 200, _short_stream([reply], model=model_name, goal_view=view), {"reply": reply}

    # ---- start / revise ------------------------------------------------------
    active = load_active_goal_for_session(session_id)
    if active is not None and sub != "revise":
        # One explicit goal per chat: a second /goal while one is active asks ONE concise
        # clarification instead of silently replacing (or doubling) the active goal.
        view = goal_public_view(active)
        reply = (
            f"A goal is already active in this chat: \"{view['objective']}\" ({view['state']}).\n"
            "Say /goal revise <changed goal> to revise it, /goal stop to finish it, or /goal status."
        )
        return 200, _short_stream([reply], model=model_name, goal_view=view), {"reply": reply}

    goal = load_active_goal_for_session(session_id)
    now = datetime.now(timezone.utc).timestamp()
    if sub == "revise":
        if goal is None:
            reply = "No goal to revise. Start one with /goal <objective>."
            return 200, _short_stream([reply], model=model_name), {"reply": reply}
        goal["objective"] = arg[:4000]
        goal["plan"] = {}
        goal["revision"] = 0
        goal["state"] = "queued"
        goal["current_step_id"] = ""
        goal["updated_at"] = now
        goal["last_reason"] = "objective revised; plan will be re-derived (completed steps are kept in history)"
        save_goal(goal)
        view = goal_public_view(goal)
        reply = "Goal revised. Re-deriving the plan; completed steps stay completed."
        spawn_goal_worker(runtime, goal, base_context=source_context, model=model_name)
        return 200, _short_stream([reply], model=model_name, goal_view=view), {"reply": reply}

    goal_id = new_goal_id()
    goal = {
        "goal_id": goal_id,
        "session_id": session_id,
        "project_id": str(source_context.get("project_id") or ""),
        "objective": arg[:4000],
        "acceptance": "",
        "plan": {},
        "state": "queued",
        "revision": 0,
        "current_step_id": "",
        "created_at": now,
        "updated_at": now,
        "ended_at": None,
        "started_at": None,
        "paused_at": None,
        "paused_seconds": 0.0,
        "created_turn_id": str(source_context.get("cancel_turn_id") or ""),
        "last_reason": "",
    }
    save_goal(goal)
    _persist_exec_binding(goal, source_context, model_name)
    view = goal_public_view(goal)
    reply = f"Goal created: {arg}\nDeriving the plan and starting the steps this chat's mode already permits. Progress shows on the goal card."
    spawn_goal_worker(runtime, goal, base_context=source_context, model=model_name)
    return 200, _short_stream([reply], model=model_name, goal_view=view), {"reply": reply}


def _persist_exec_binding(goal: dict[str, Any], source_context: dict[str, Any], model: str) -> None:
    """Bind the goal to the execution facts a resume/retry needs to re-dispatch into the SAME
    workspace: the chat's workspace roots, project binding and selected model. This is the
    goal's own binding, not duplicated execution state — mode, permissions and receipts stay
    with their existing owners."""
    from core.goal_workflow import save_goal as _save

    goal["model"] = str(model or "")
    goal["exec_context"] = {
        "workspace": str(source_context.get("workspace") or ""),
        "workspace_root": str(source_context.get("workspace_root") or ""),
        "project_id": str(source_context.get("project_id") or ""),
        "runtime_session_id": str(source_context.get("runtime_session_id") or goal.get("session_id") or ""),
        "owner_local": bool(source_context.get("owner_local", True)),
    }
    _save(goal)


def _rebuild_exec_context(goal: dict[str, Any]) -> dict[str, Any]:
    binding = goal.get("exec_context") if isinstance(goal.get("exec_context"), dict) else {}
    context = {
        "workspace": str(binding.get("workspace") or ""),
        "workspace_root": str(binding.get("workspace_root") or ""),
        "project_id": str(binding.get("project_id") or goal.get("project_id") or ""),
        "runtime_session_id": str(binding.get("runtime_session_id") or goal.get("session_id") or ""),
        "owner_local": bool(binding.get("owner_local", True)),
        "surface": "goal",
    }
    return context


def goal_state_payload(session_id: str) -> dict[str, Any]:
    """The canonical hydration snapshot for the card. An orphaned running goal (its worker
    died with a previous process and startup reconciliation has not run) reads honestly as
    interrupted — hydration never resurrects running."""
    from core.goal_workflow import reconcile_goal_if_orphaned

    goal = load_latest_goal_for_session(session_id)
    if goal is None:
        return {"goal": None}
    reconcile_goal_if_orphaned(goal.get("goal_id") or "")
    goal = load_goal(goal.get("goal_id") or "") or goal
    return {"goal": goal_public_view(goal)}


def goal_control(action: str, body: dict[str, Any], session_id: str) -> tuple[int, dict[str, Any]]:
    """Owner-local POST controls from the card: pause / resume / stop / retry.

    Chat-scoped: a control naming a goal that belongs to a DIFFERENT chat refuses, so one
    chat's card can never pause or stop another chat's goal."""
    goal_id = str(body.get("goal_id") or "").strip()
    goal = load_goal(goal_id) if goal_id else load_active_goal_for_session(session_id)
    if goal is None:
        return 404, {"ok": False, "error": "no such goal"}
    if str(goal.get("session_id") or "") != str(session_id or ""):
        return 403, {"ok": False, "error": "goal belongs to a different chat"}
    goal_id = str(goal.get("goal_id"))
    if action == "pause":
        outcome = request_pause(goal_id)
    elif action == "stop":
        outcome = request_stop(goal_id)
    elif action == "resume":
        outcome = request_resume(goal_id)
        if outcome.get("ok") and not outcome.get("already"):
            fresh = load_goal(goal_id)
            if fresh is not None and str(fresh.get("state")) not in TERMINAL_GOAL_STATES:
                spawn_goal_worker(None, fresh, base_context=_rebuild_exec_context(fresh), model=str(fresh.get("model") or ""))
        outcome = dict(outcome)
    elif action == "retry":
        approval_token = str(body.get("approval_token") or "").strip()[:128]
        outcome = retry_waiting_step(goal_id, approval_token=approval_token)
        if outcome.get("ok"):
            fresh = load_goal(goal_id)
            if fresh is not None:
                spawn_goal_worker(None, fresh, base_context=_rebuild_exec_context(fresh), model=str(fresh.get("model") or ""))
        outcome = dict(outcome)
    else:
        return 400, {"ok": False, "error": "unknown action"}
    fresh = load_goal(goal_id) or goal
    return (200 if outcome.get("ok") else 409), {**outcome, "goal": goal_public_view(fresh)}


def resume_goal_worker_for_session(runtime, session_id: str, base_context: dict[str, Any], model: str) -> None:
    """Re-arm a resumed goal's worker with the CURRENT request's context (used by the chat
    ingress resume path where the live session context is available)."""
    goal = load_active_goal_for_session(session_id)
    if goal is not None and active_worker(goal.get("goal_id") or "") is None:
        spawn_goal_worker(runtime, goal, base_context=base_context, model=model)
