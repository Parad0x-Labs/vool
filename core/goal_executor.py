"""The goal controller's execution loop: dispatches the plan through the EXISTING turn path.

This module adds no execution authority of its own. Every goal step is one ordinary agent
turn dispatched through ``core.web.api.runtime.run_agent`` — the same gate (MF-15 session
serialization), the same mode authority (re-consulted per tool call), the same transcript
seal (A7 ``finalize_answer``), the same checkpoints and receipts. The controller's job is
only sequencing (dependencies), truth-keeping (step states from the turn's own outcome
payload and the runtime event ledger), and bounded repair (one re-dispatch after a failed
verification).

A model saying "done" never completes a step: completion requires the step's turn to have
ended ``fulfilled`` AND, when the step declares a verification, the declared tool effect to
actually appear in the step turn's recorded runtime events.
"""

from __future__ import annotations

import json
import time
import threading
from typing import Any, Callable

from core.goal_workflow import (
    MAX_STEP_ATTEMPTS,
    build_plan,
    completed_step_count,
    emit_goal_event,
    load_goal,
    new_step_id,
    parse_planner_answer,
    derived_single_step_plan,
    save_goal,
    unregister_worker,
    active_worker,
)

#: Keys dropped from the ingressing turn's context before a step turn reuses it. Stream
#: plumbing is per-turn; the goal ingress turn's queue/bypass/tokens must never leak into a
#: step turn. The mode itself is NOT pinned here: step turns omit an explicit mode so the
#: authority resolves the chat's LIVE mode on every consult — a mode change mid-goal applies
#: to the next action.
_STEP_CONTEXT_DROP = frozenset(
    {
        "runtime_event_stream_id",
        "cancel_event",
        "bypass_token",
        "mode_approval_token",
        "price_wait_queue_id",
        "goal_step",
        "attachment_delivery",
        "attachment_turn_id",
    }
)


def step_source_context(base_context: dict[str, Any] | None, *, goal: dict[str, Any], step: dict[str, Any], attempt: int, turn_id: str, approval_token: str = "") -> dict[str, Any]:
    context = {
        key: value
        for key, value in dict(base_context or {}).items()
        if key not in _STEP_CONTEXT_DROP
    }
    context["cancel_turn_id"] = str(turn_id)
    context["surface"] = "goal"
    context["goal_step"] = {
        "goal_id": str(goal.get("goal_id") or ""),
        "step_id": str(step.get("id") or ""),
        "attempt": int(attempt),
    }
    if approval_token:
        context["mode_approval_token"] = str(approval_token)
    return context


def _step_prompt(goal: dict[str, Any], step: dict[str, Any], *, index: int, total: int, repair: bool) -> str:
    objective = str(goal.get("objective") or "").strip()
    lines = [
        f"You are executing one step of an explicit goal in this chat.",
        f"Goal: {objective}",
        f"Step {index} of {total}: {step.get('title')}",
    ]
    details = str(step.get("details") or "").strip()
    if details:
        lines.append(f"Step details: {details}")
    acceptance = str(goal.get("acceptance") or "").strip()
    if acceptance:
        lines.append(f"Goal acceptance criteria: {acceptance}")
    verify = _verify_of(step)
    if verify:
        lines.append(
            "This step is verified by whether the tool call '"
            + str(verify.get("tool") or "")
            + "' actually completes"
            + (
                " with a path containing '" + str(verify.get("path_contains")) + "'"
                if verify.get("path_contains")
                else ""
            )
            + ". Make sure that exact effect happens as part of this step."
        )
    if repair:
        lines.append(
            "A previous attempt at this step did not satisfy its verification. Diagnose why and "
            "complete the step for real this time."
        )
    lines.append("Work only on this step. When finished, end your reply with a one-line summary of what was actually done.")
    return "\n".join(lines)


def _verify_of(step: dict[str, Any]) -> dict[str, Any] | None:
    raw = step.get("verify")
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    tool = str(raw.get("tool") or "").strip()
    path_contains = str(raw.get("path_contains") or "").strip()
    if not tool and not path_contains:
        return None
    return {"tool": tool, "path_contains": path_contains}


def _step_events(session_id: str, turn_id: str, *, after_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    from core.runtime_continuity import list_runtime_session_events

    matched: list[dict[str, Any]] = []
    cursor = max(0, int(after_seq))
    for _ in range(20):  # bounded sweep; a step turn emits far fewer than 4000 events
        rows = list_runtime_session_events(session_id, after_seq=cursor, limit=200)
        if not rows:
            break
        cursor = max(int(row.get("seq") or cursor) for row in rows)
        matched.extend(row for row in rows if str(row.get("client_turn_id") or "") == str(turn_id))
        if len(rows) < 200 or len(matched) >= limit:
            break
    return matched[:limit]


def verification_holds(step: dict[str, Any], events: list[dict[str, Any]]) -> tuple[bool, str]:
    """Check the step's declared verification against its turn's ACTUAL recorded events.

    Reads the runtime event ledger only — the same rows Activity renders. No model prose is
    consulted, so a model cannot advance the counter by saying done.
    """
    verify = _verify_of(step)
    if not verify:
        return True, ""
    expected_tool = str(verify.get("tool") or "").strip()
    expected_path = str(verify.get("path_contains") or "").strip()
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in ("tool_executed", "tool_failed"):
            continue
        tool_name = str(event.get("tool_name") or "").strip()
        if expected_tool and tool_name != expected_tool:
            continue
        if event_type == "tool_failed":
            continue
        if expected_path:
            path_value = str(event.get("path") or event.get("file_path") or event.get("target_path") or "")
            if expected_path.lower() not in path_value.lower():
                continue
        return True, ""
    return False, (
        f"verification not met: expected tool '{expected_tool or 'any'}'"
        + (f" writing a path containing '{expected_path}'" if expected_path else "")
        + " was not recorded as completed for this step"
    )


def _classify_turn_outcome(result: dict[str, Any], cancel_fired: bool) -> tuple[str, str]:
    """Map a step turn's own outcome truth to a step state. Never invents success."""
    if cancel_fired:
        return "interrupted", "step cancelled while running"
    outcome = str(result.get("task_outcome") or "").strip()
    if outcome == "pending_approval":
        return "waiting_approval", "waiting for your approval"
    fulfillment = result.get("fulfillment_outcome")
    status = ""
    reason_bits: list[str] = []
    if isinstance(fulfillment, dict):
        status = str(fulfillment.get("fulfillment_status") or "").strip()
        codes = fulfillment.get("failure_codes") or []
        if codes:
            reason_bits.append(",".join(str(code) for code in codes[:4]))
    if result.get("error"):
        reason_bits.append(str(result.get("error"))[:200])
    if status == "fulfilled":
        return "completed", ""
    if status == "cancelled":
        return "cancelled", "turn cancelled"
    if status == "blocked":
        return "waiting_approval", "; ".join(reason_bits) or "turn blocked pending approval"
    if status == "partially_fulfilled":
        return "failed", "; ".join(reason_bits) or "turn ended partially fulfilled"
    if status == "failed":
        return "failed", "; ".join(reason_bits) or "turn failed"
    # No fulfillment truth at all: fall back to the transport-level status the terminal task
    # event reads, plus ONE honest requirement — a completed step must have actually produced
    # an answer. An empty result with no error and no outcome truth is failed-honest, never
    # an invented completion.
    transport_status = str(result.get("status") or "").strip().lower()
    if result.get("error") or transport_status in {"failed", "error"}:
        return "failed", "; ".join(reason_bits) or "turn failed"
    if str(result.get("response") or result.get("content") or "").strip():
        return "completed", ""
    return "failed", "turn produced no outcome evidence and no answer"


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

_PLANNER_PROMPT = """You are the planner for an explicit goal in this chat. Reply with ONLY a \
JSON object — no prose before or after — shaped exactly like this:

```json
{{"steps": [{{"id": "step-1", "title": "short imperative title", "details": "what to do", \
"depends_on": [], "verify": {{"tool": "tool-name", "path_contains": "substring-of-path"}}}}]}}
```

Rules: at most 8 steps; each step is one coherent unit of real work in this chat's workspace; \
"depends_on" lists step ids that must complete first; "verify" declares the concrete tool call \
that proves the step happened (omit it for a pure research/answer step). Do not call any tools; \
planning only.

Goal: {objective}
"""


def run_planner_turn(runtime, goal: dict[str, Any], base_context: dict[str, Any], model: str, turn_id: str) -> tuple[list[dict[str, Any]] | None, str]:
    """One bounded planning turn through the existing execution path.

    The plan is model-authored PROPOSAL only: validation (size, unique ids, acyclic
    dependencies) happens here, and dispatch/status/completion stay controller-owned. On any
    planner failure the caller derives the honest single-step plan.
    """
    from core.web.api.runtime import run_agent

    prompt = _PLANNER_PROMPT.format(objective=str(goal.get("objective") or "").strip()[:2000])
    context = step_source_context(
        base_context, goal=goal, step={"id": "planning"}, attempt=0, turn_id=turn_id
    )
    try:
        result = run_agent(runtime, prompt, session_id=str(goal.get("session_id") or ""), source_context=context)
    except Exception as exc:
        return None, f"planner turn failed: {str(exc)[:200]}"
    answer = str(result.get("response") or result.get("content") or "")
    steps = parse_planner_answer(answer)
    if steps is None:
        return None, "planner returned no parsable plan"
    return steps, ""


# ---------------------------------------------------------------------------
# The worker loop
# ---------------------------------------------------------------------------

def execute_goal(
    runtime,
    goal_id: str,
    *,
    base_context: dict[str, Any],
    model: str,
    revision: int = 0,
) -> None:
    """Run the goal to a terminal or holding state. Runs in the served goal worker thread.

    Holding states (paused / waiting_approval / interrupted) exit the loop; an explicit
    control (resume / retry) re-enters through the served adapter. Cancel/pause flags are
    checked before every dispatch; the in-flight turn is stopped through the live-turn owner.
    """
    from core.live_turns import register_turn, request_cancel, unregister_turn

    goal = load_goal(goal_id)
    if goal is None or str(goal.get("state")) in ("completed", "failed", "cancelled"):
        unregister_worker(goal_id)
        return
    if not goal.get("started_at"):
        # The elapsed clock starts at the worker's first real entry — real run timestamps,
        # never a client-provided value. Pause intervals are subtracted from it.
        goal["started_at"] = time.time()
        save_goal(goal)
    session_id = str(goal.get("session_id") or "")
    worker = active_worker(goal_id)

    def _paused_or_stopped() -> bool:
        if worker is None:
            return False
        return worker.pause_requested.is_set() or worker.stop_requested.is_set()

    def _save(state: str | None = None, reason: str | None = None) -> None:
        if state:
            goal["state"] = state
        if reason is not None:
            goal["last_reason"] = str(reason)[:400]
        goal["updated_at"] = time.time()
        save_goal(goal)

    def _emit(message: str) -> None:
        emit_goal_event(_worker_event_context(base_context, goal), goal, message)

    try:
        # ---- Planning -----------------------------------------------------
        plan = goal.get("plan") if isinstance(goal.get("plan"), dict) else {}
        steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
        if not steps:
            _save("planning", "deriving the plan")
            _emit("Goal planning: deriving steps")
            planner_steps, planner_error = run_planner_turn(
                runtime, goal, base_context, model, f"{goal_id}:planning:1"
            )
            if _paused_or_stopped():
                return
            if planner_steps is not None:
                try:
                    goal["plan"] = build_plan(planner_steps, provenance="model")
                    goal["revision"] = 1
                    planner_error = ""
                except ValueError as exc:
                    goal["plan"] = derived_single_step_plan(str(goal.get("objective") or ""))
                    goal["revision"] = 1
                    planner_error = f"planner plan rejected ({exc}); derived single-step plan"
            else:
                goal["plan"] = derived_single_step_plan(str(goal.get("objective") or ""))
                goal["revision"] = 1
                if planner_error:
                    planner_error = f"{planner_error}; derived single-step plan"
            _save("running", planner_error or "")
            _emit("Plan ready" if not planner_error else planner_error)

        # ---- Execution loop -------------------------------------------------
        while True:
            if _paused_or_stopped():
                return
            goal = load_goal(goal_id) or goal
            if str(goal.get("state")) in ("paused", "cancelled"):
                return
            plan = goal.get("plan") if isinstance(goal.get("plan"), dict) else {}
            steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
            by_id = {step.get("id"): step for step in steps}
            # A failed/blocked/cancelled dependency fails the dependents visibly.
            for step in steps:
                if step.get("state") != "pending":
                    continue
                deps = step.get("depends_on") or []
                bad = [by_id[d] for d in deps if d in by_id and by_id[d].get("state") in ("failed", "blocked", "cancelled")]
                if bad:
                    step["state"] = "blocked"
                    step["reason"] = f"dependency {bad[0].get('id')} did not complete"
            done, total = completed_step_count(plan)
            next_step = None
            for step in steps:
                if step.get("state") != "pending":
                    continue
                deps = step.get("depends_on") or []
                if all(d in by_id and by_id[d].get("state") == "completed" for d in deps):
                    next_step = step
                    break
            if next_step is None:
                if total and done == total:
                    now = time.time()
                    goal["state"] = "completed"
                    goal["ended_at"] = now
                    goal["updated_at"] = now
                    goal["last_reason"] = ""
                    goal["current_step_id"] = ""
                    save_goal(goal)
                    _emit("Goal completed")
                else:
                    failed = [s for s in steps if s.get("state") in ("failed", "blocked")]
                    now = time.time()
                    goal["state"] = "failed"
                    goal["ended_at"] = now
                    goal["updated_at"] = now
                    goal["last_reason"] = (
                        f"step {failed[0].get('id')} {failed[0].get('state')}: {failed[0].get('reason') or 'unresolved'}"
                        if failed
                        else "no dispatchable steps"
                    )
                    save_goal(goal)
                    _emit("Goal blocked: " + str(goal.get("last_reason")))
                return

            step = next_step
            attempt = int(step.get("attempts") or 0) + 1
            step["attempts"] = attempt
            step["state"] = "running"
            step["reason"] = ""
            step_turn_id = f"{goal_id}:{step.get('id')}:a{attempt}"
            step["turn_id"] = step_turn_id
            goal["current_step_id"] = str(step.get("id"))
            goal["state"] = "running"
            goal["updated_at"] = time.time()
            save_goal(goal)
            index = steps.index(step) + 1
            _emit(f"Step started: {step.get('title')}")

            repair = attempt > 1
            prompt = _step_prompt(goal, step, index=index, total=len(steps), repair=repair)
            context = step_source_context(
                base_context, goal=goal, step=step, attempt=attempt, turn_id=step_turn_id
            )
            cancel_event = register_turn(session_id, step_turn_id)
            if worker is not None:
                worker.set_current_turn(step_turn_id)
            from core.web.api.runtime import run_agent

            try:
                result = run_agent(runtime, prompt, session_id=session_id, source_context=context) or {}
            except Exception as exc:
                result = {"error": f"step turn failed: {str(exc)[:200]}", "status": "failed"}
            finally:
                unregister_turn(session_id, step_turn_id)
                if worker is not None:
                    worker.set_current_turn("")
            if _paused_or_stopped():
                # request_pause/request_stop already re-froze this step; leave their state.
                return

            state, reason = _classify_turn_outcome(result, cancel_event.is_set())
            if state == "completed":
                events = _step_events(session_id, step_turn_id)
                holds, verify_reason = verification_holds(step, events)
                if not holds:
                    state, reason = "failed", verify_reason
            step["state"] = state
            step["reason"] = reason
            goal["updated_at"] = time.time()
            if state == "completed":
                goal["current_step_id"] = ""
                save_goal(goal)
                _emit(f"Step completed: {step.get('title')}")
                continue
            if state == "waiting_approval":
                goal["state"] = "waiting_approval"
                save_goal(goal)
                _emit(f"Step waiting for approval: {step.get('title')}")
                return
            if state == "interrupted":
                goal["state"] = "interrupted"
                goal["last_reason"] = reason
                save_goal(goal)
                _emit("Goal interrupted: " + reason)
                return
            if state == "cancelled":
                goal["state"] = "cancelled"
                goal["ended_at"] = time.time()
                save_goal(goal)
                _emit("Goal cancelled during step: " + str(step.get("title")))
                return
            # failed: bounded repair, then honest failure
            if attempt < MAX_STEP_ATTEMPTS:
                step["state"] = "pending"
                step["reason"] = reason
                save_goal(goal)
                _emit(f"Step verification failed; scheduling repair: {step.get('title')}")
                continue
            goal["state"] = "failed"
            goal["ended_at"] = time.time()
            goal["last_reason"] = f"step {step.get('id')} failed: {reason}"
            save_goal(goal)
            _emit("Goal failed: " + str(goal.get("last_reason")))
            return
    finally:
        unregister_worker(goal_id)


def _worker_event_context(base_context: dict[str, Any], goal: dict[str, Any]) -> dict[str, Any]:
    """The event context for goal lifecycle events: the chat ledger, plus the live goal stream
    sink when the ingressing turn's stream is still attached."""
    context = {
        "runtime_session_id": str(goal.get("session_id") or ""),
        "cancel_turn_id": str(goal.get("created_turn_id") or ""),
    }
    stream_id = str((base_context or {}).get("runtime_event_stream_id") or "")
    if stream_id:
        context["runtime_event_stream_id"] = stream_id
    return context


def retry_waiting_step(goal_id: str, *, approval_token: str = "") -> dict[str, Any]:
    """The real inline recovery for a waiting step: re-dispatch it carrying the chat's
    approval token through the SAME ``mode_approval_token`` seam every chat turn uses.
    Refuses honestly when the goal is not in a resumable holding state."""
    goal = load_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "no such goal"}
    state = str(goal.get("state"))
    if state not in ("waiting_approval", "paused", "interrupted"):
        return {"ok": False, "error": f"goal is {state}; retry applies to a holding state"}
    step_id = str(goal.get("current_step_id") or "")
    for step in goal.get("plan", {}).get("steps", []):
        if step.get("id") == step_id and step.get("state") in ("waiting_approval", "running"):
            step["state"] = "pending"
            step["reason"] = ""
            break
    goal["state"] = "running"
    goal["updated_at"] = time.time()
    goal["last_reason"] = ""
    save_goal(goal)
    from core.goal_workflow import emit_goal_event as _emit

    _emit(_goal_event_context_for(goal), goal, "Step re-dispatched after approval")
    return {"ok": True, "state": "running", "approval_token_used": bool(approval_token)}


def _goal_event_context_for(goal: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_session_id": str(goal.get("session_id") or ""),
        "cancel_turn_id": str(goal.get("created_turn_id") or ""),
    }
