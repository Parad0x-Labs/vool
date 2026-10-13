"""The goal controller's execution loop: dispatches the plan through the EXISTING turn path.

This module adds no execution authority of its own. Every goal step is one ordinary agent
turn dispatched through ``core.web.api.runtime.run_agent`` — the same gate (MF-15 session
serialization), the same mode authority (re-consulted per tool call from the chat's LIVE
session record — step contexts pin no mode), the same transcript seal (A7
``finalize_answer``), the same checkpoints and receipts. The controller's job is only
sequencing (dependencies), truth-keeping (step states from the turn's own outcome payload
and the runtime event ledger), and bounded repair (one re-dispatch after a failed
verification, inside a bounded total-turn budget).

A model saying "done" never completes a step: completion requires the step's turn to have
ended fulfilled AND, when the step declares a verification, the declared effect/evidence to
actually appear in the step turn's recorded runtime events or committed answer. A refusal, a
provider non-answer, or a budget/capability block can never read as completion or as an
approval wait.

Workers are EPOCH-FENCED: a worker records the epoch it claimed and every durable write is
checked against the goal's current epoch — a worker overtaken by revise/resume exits without
writing and its late unregister never removes its successor.
"""

from __future__ import annotations

import json
import time
from typing import Any

from core.goal_workflow import (
    MAX_GOAL_TURNS,
    MAX_STEP_ATTEMPTS,
    WorkerFencedError,
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
#: step turn. The operating mode is deliberately NOT carried: step turns pin no mode, so the
#: authority resolves the chat's LIVE session mode on every consult — a mode change mid-goal
#: applies to the next action, including after pause/resume.
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
        "operating_mode",
        "operating_mode_revision",
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
        "revision": int(goal.get("revision") or 0),
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
        expected = []
        if verify.get("tool"):
            expected.append(f"the tool call '{verify['tool']}' actually completes")
        if verify.get("path_contains"):
            expected.append(f"a written path containing '{verify['path_contains']}'")
        if verify.get("answer_contains"):
            expected.append(f"your final answer stating '{verify['answer_contains']}'")
        if expected:
            lines.append(
                "This step is verified only by real recorded evidence: "
                + " and ".join(expected)
                + ". Make sure that exact evidence exists before you finish."
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
    verify = {
        "tool": str(raw.get("tool") or "").strip(),
        "path_contains": str(raw.get("path_contains") or "").strip(),
        "answer_contains": str(raw.get("answer_contains") or "").strip(),
    }
    if not any(verify.values()):
        return None
    return verify


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


def verification_holds(step: dict[str, Any], events: list[dict[str, Any]], *, answer: str = "") -> tuple[bool, str]:
    """Check the step's declared verification against its turn's ACTUAL recorded evidence.

    Reads the runtime event ledger and the turn's committed answer only — the same rows
    Activity renders. No model prose verdict is consulted, so a model cannot advance the
    counter by saying done. An expected tool that only FAILED never satisfies verification.
    """
    verify = _verify_of(step)
    if not verify:
        return True, ""
    expected_tool = str(verify.get("tool") or "").strip()
    expected_path = str(verify.get("path_contains") or "").strip()
    expected_answer = str(verify.get("answer_contains") or "").strip()
    if expected_answer:
        if expected_answer.lower() not in str(answer or "").lower():
            return False, f"verification not met: the turn's answer does not state '{expected_answer}'"
    if not expected_tool and not expected_path:
        return True, ""
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


#: Failure codes that mean a LIVE permission ask (the only approval-shaped block). Any other
#: blocked code — budget, capability, provider — is a hold the operator resolves elsewhere;
#: it is never shown or resumed as an approval.
_APPROVAL_FAILURE_CODES = frozenset({"approval_required"})

#: task_outcome values that can never complete a step, whatever prose the answer carries.
_NON_SUCCESS_TASK_OUTCOMES = frozenset({"failed", "provider_did_not_answer", "refused", "blocked"})


def _classify_turn_outcome(result: dict[str, Any], cancel_fired: bool) -> tuple[str, str]:
    """Map a step turn's own outcome truth to a step state. Never invents success.

    Authoritative precedence: cancellation, then the typed task outcome, then the
    fulfillment receipt, then — only when no outcome truth exists at all — the transport
    status plus an actually committed answer. Prose cannot manufacture completion: a turn
    whose typed outcome names a failure or a non-answer is failed even when its text is
    nonempty, and a block whose failure codes are not an approval wait is a resource hold,
    not an approval.
    """
    if cancel_fired:
        return "interrupted", "step cancelled while running"
    outcome = str(result.get("task_outcome") or "").strip()
    fulfillment = result.get("fulfillment_outcome")
    status = ""
    reason_bits: list[str] = []
    codes: list[str] = []
    if isinstance(fulfillment, dict):
        status = str(fulfillment.get("fulfillment_status") or "").strip()
        codes = [str(code) for code in (fulfillment.get("failure_codes") or [])]
        if codes:
            reason_bits.append(",".join(codes[:4]))
    if result.get("error"):
        reason_bits.append(str(result.get("error"))[:200])
    if status == "fulfilled" and outcome in {"", "success"}:
        return "completed", ""
    if outcome == "pending_approval" or (status == "blocked" and set(codes) & _APPROVAL_FAILURE_CODES):
        return "waiting_approval", "; ".join(reason_bits) or "waiting for your approval"
    if status == "blocked":
        # A budget/capability/provider hold: honest non-terminal block, never an approval.
        return "blocked", "; ".join(reason_bits) or "blocked: required budget or capability unavailable"
    if outcome and outcome != "success":
        return "failed", "; ".join(reason_bits) or f"turn outcome: {outcome}"
    if status == "cancelled":
        return "cancelled", "turn cancelled"
    if status == "partially_fulfilled":
        return "failed", "; ".join(reason_bits) or "turn ended partially fulfilled"
    if status == "failed":
        return "failed", "; ".join(reason_bits) or "turn failed"
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
"depends_on" lists step ids that must complete first; "verify" declares the concrete recorded \
evidence that proves the step happened — a completed tool call, a path substring, or text your \
final answer must state (omit verify for a pure research/answer step). Do not call any tools; \
planning only.

Goal: {objective}
"""


def run_planner_turn(runtime, goal: dict[str, Any], base_context: dict[str, Any], model: str, turn_id: str, *, session_id: str = "") -> tuple[list[dict[str, Any]] | None, str]:
    """One bounded planning turn through the existing execution path.

    The plan is model-authored PROPOSAL only: validation (size, unique ids, acyclic
    dependencies) happens here, and dispatch/status/completion stay controller-owned. The
    turn is a REAL registered turn (cancellable through the live-turn owner) and honors the
    pause/stop flags. On any planner failure the caller derives the honest single-step plan.
    """
    from core.live_turns import register_turn, unregister_turn
    from core.web.api.runtime import run_agent

    prompt = _PLANNER_PROMPT.format(objective=str(goal.get("objective") or "").strip()[:2000])
    context = step_source_context(
        base_context, goal=goal, step={"id": "planning"}, attempt=0, turn_id=turn_id
    )
    register_turn(str(session_id or goal.get("session_id") or ""), turn_id)
    try:
        result = run_agent(runtime, prompt, session_id=str(session_id or goal.get("session_id") or ""), source_context=context) or {}
    except Exception as exc:
        return None, f"planner turn failed: {str(exc)[:200]}"
    finally:
        unregister_turn(str(session_id or goal.get("session_id") or ""), turn_id)
    answer = str(result.get("response") or result.get("content") or "")
    steps = parse_planner_answer(answer)
    if steps is None:
        return None, "planner returned no parsable plan"
    return steps, ""


def reconcile_interrupted_step(goal: dict[str, Any], step: dict[str, Any]) -> None:
    """Reconcile ONE interrupted step against its recorded turn's receipts — in place.

    Completed evidence (verification already holds in the step turn's own ledger events) →
    completed, never replayed. Expected-tool activity without verifiable completion →
    blocked as an unresolved reconciliation need. No effect evidence at all → pending (a
    fresh dispatch cannot duplicate anything)."""
    turn_id = str(step.get("turn_id") or "")
    session_id = str(goal.get("session_id") or "")
    if _verify_of(step) and turn_id:
        events = _step_events(session_id, turn_id)
        holds, reason = verification_holds(step, events)
        if holds:
            step["state"] = "completed"
            step["reason"] = "effect already committed before the interruption"
            return
        expected_tool = str(_verify_of(step).get("tool") or "")
        ran = [
            event
            for event in events
            if str(event.get("event_type") or "") in ("tool_executed", "tool_failed")
            and (not expected_tool or str(event.get("tool_name") or "") == expected_tool)
        ]
        if ran:
            step["state"] = "blocked"
            step["reason"] = "unresolved settlement: the interrupted turn's effect needs reconciliation before any re-dispatch"
            return
    step["state"] = "pending"
    step["reason"] = ""


# ---------------------------------------------------------------------------
# The worker loop
# ---------------------------------------------------------------------------

def execute_goal(
    runtime,
    goal_id: str,
    *,
    base_context: dict[str, Any],
    model: str,
) -> None:
    """Run the goal to a terminal or holding state. Runs in the served goal worker thread.

    Holding states (paused / waiting_approval / interrupted / blocked) exit the loop; an
    explicit control (resume / retry) re-enters through the served adapter. Cancel/pause
    flags are honored before every dispatch AND after the in-flight turn settles — the
    settled outcome is always classified and persisted before the worker exits, so pause or
    stop can never orphan an in-flight effect. Total dispatches are bounded
    (``MAX_GOAL_TURNS``); every durable write is epoch-fenced.
    """
    from core.live_turns import register_turn, request_cancel, unregister_turn

    goal = load_goal(goal_id)
    if goal is None or str(goal.get("state")) in ("completed", "failed", "cancelled"):
        unregister_worker(goal_id, active_worker(goal_id))
        return
    session_id = str(goal.get("session_id") or "")
    worker = active_worker(goal_id)
    epoch = int(worker.epoch) if worker is not None else None

    def _save(state: str | None = None, reason: str | None = None) -> None:
        if state:
            goal["state"] = state
        if reason is not None:
            goal["last_reason"] = str(reason)[:400]
        goal["updated_at"] = time.time()
        save_goal(goal, expected_epoch=epoch)

    def _emit(message: str) -> None:
        emit_goal_event(_worker_event_context(base_context, goal), goal, message)

    def _fenced() -> bool:
        """True when a successor claimed the goal (revise/resume) — exit without writing."""
        if epoch is None:
            return False
        current = load_goal(goal_id) or {}
        return int(current.get("worker_epoch") or 0) != epoch

    total_dispatches = 0

    try:
        if not goal.get("started_at"):
            # The elapsed clock starts at the worker's first real entry — real run
            # timestamps, never a client-provided value. Pause intervals are subtracted.
            goal["started_at"] = time.time()
            _save()

        # ---- Planning -----------------------------------------------------
        plan = goal.get("plan") if isinstance(goal.get("plan"), dict) else {}
        steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
        carried_steps = plan.get("carried_steps") if isinstance(plan.get("carried_steps"), list) else []
        if not steps:
            _save("planning", "deriving the plan")
            _emit("Goal planning: deriving steps")
            if _fenced():
                return
            planner_steps, planner_error = run_planner_turn(
                runtime, goal, base_context, model, f"{goal_id}:planning:a{int(goal.get('worker_epoch') or 0)}",
                session_id=session_id,
            )
            if _paused_or_stopped(worker):
                # The planner turn is accounted like any dispatch: settle it honestly.
                return
            if planner_steps is None:
                planner_steps = [{"id": "step-1", "title": str(goal.get("objective") or "")[:300]}]
                planner_error = (planner_error or "planner returned no parsable plan") + "; derived single-step plan"
            try:
                new_plan = build_plan(planner_steps, provenance=str((plan or {}).get("provenance") or "model"))
            except ValueError as exc:
                new_plan = derived_single_step_plan(str(goal.get("objective") or ""))
                planner_error = f"planner plan rejected ({exc}); derived single-step plan"
            # Monotone revision: a revise carries the previous counter forward; a first
            # plan starts at 1. Completed steps carried by a revise keep their identities,
            # and the durable revision provenance history travels with every replan.
            previous_revision = int(goal.get("revision") or 0)
            new_plan["revision"] = previous_revision + 1 if (carried_steps or previous_revision) else 1
            prior_history = plan.get("history") if isinstance(plan.get("history"), list) else []
            if prior_history:
                new_plan["history"] = prior_history
            if carried_steps:
                existing_ids = {step["id"] for step in new_plan["steps"]}
                for carried in carried_steps:
                    if carried.get("id") not in existing_ids:
                        new_plan["steps"].insert(0, dict(carried))
            goal["plan"] = new_plan
            goal["revision"] = new_plan["revision"]
            _save("running", planner_error or "")
            _emit("Plan ready" if not planner_error else planner_error)

        # ---- Execution loop -------------------------------------------------
        while True:
            if _paused_or_stopped(worker):
                return
            if _fenced():
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
                now = time.time()
                if total and done == total:
                    goal["state"] = "completed"
                    goal["ended_at"] = now
                    goal["updated_at"] = now
                    goal["last_reason"] = ""
                    goal["current_step_id"] = ""
                    save_goal(goal, expected_epoch=epoch)
                    _emit("Goal completed")
                else:
                    unresolved = [s for s in steps if s.get("state") in ("failed", "blocked")]
                    goal["state"] = "failed"
                    goal["ended_at"] = now
                    goal["updated_at"] = now
                    goal["last_reason"] = (
                        f"step {unresolved[0].get('id')} {unresolved[0].get('state')}: {unresolved[0].get('reason') or 'unresolved'}"
                        if unresolved
                        else "no dispatchable steps"
                    )
                    save_goal(goal, expected_epoch=epoch)
                    _emit("Goal blocked: " + str(goal.get("last_reason")))
                return

            if total_dispatches >= MAX_GOAL_TURNS:
                now = time.time()
                goal["state"] = "failed"
                goal["ended_at"] = now
                goal["last_reason"] = f"dispatch budget exhausted after {total_dispatches} turns"
                save_goal(goal, expected_epoch=epoch)
                _emit("Goal failed: dispatch budget exhausted")
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
            save_goal(goal, expected_epoch=epoch)
            total_dispatches += 1
            index = steps.index(step) + 1
            _emit(f"Step started: {step.get('title')}")

            repair = attempt > 1
            prompt = _step_prompt(goal, step, index=index, total=len(steps), repair=repair)
            approval_token = worker.take_approval_token(str(step.get("id"))) if worker is not None else ""
            context = step_source_context(
                base_context, goal=goal, step=step, attempt=attempt, turn_id=step_turn_id,
                approval_token=approval_token,
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

            # ALWAYS classify the settled turn — pause/stop never orphan an in-flight effect.
            state, reason = _classify_turn_outcome(result, cancel_event.is_set())
            events = _step_events(session_id, step_turn_id)
            answer_text = str(result.get("response") or result.get("content") or "")
            holds, verify_reason = verification_holds(step, events, answer=answer_text)
            if state == "completed" and not holds:
                state, reason = "failed", verify_reason
            if state == "interrupted" and holds:
                # The in-flight turn was cancelled AFTER its declared effect verifiably
                # committed: the step is completed by its own evidence — recording
                # interrupted here would invite a replay that duplicates a real effect.
                state, reason = "completed", ""
            if state == "waiting_approval":
                approval_id = _latest_approval_id(session_id, step_turn_id)
                if approval_id:
                    step["approval_id"] = approval_id
            step["state"] = state
            step["reason"] = reason
            step["answer_excerpt"] = str(result.get("response") or result.get("content") or "")[:500]
            goal["updated_at"] = time.time()

            if _fenced():
                return
            if state == "completed":
                goal["current_step_id"] = ""
                # Preserve the control's state: a pause/stop that landed mid-turn is kept
                # (re-read fresh — the worker's copy predates the control's write).
                fresh = load_goal(goal_id) or goal
                if str(fresh.get("state")) in ("paused", "cancelled"):
                    goal["state"] = str(fresh.get("state"))
                    goal["paused_at"] = fresh.get("paused_at")
                    goal["paused_seconds"] = fresh.get("paused_seconds")
                    save_goal(goal, expected_epoch=epoch)
                    _emit(f"Step completed: {step.get('title')}")
                    return
                save_goal(goal, expected_epoch=epoch)
                _emit(f"Step completed: {step.get('title')}")
                if _paused_or_stopped(worker):
                    return
                continue
            if state == "waiting_approval":
                goal["state"] = "waiting_approval"
                save_goal(goal, expected_epoch=epoch)
                _emit(f"Step waiting for approval: {step.get('title')}")
                return
            if state == "interrupted":
                # Pause/stop landed mid-turn: the step records the settled truth, and the
                # GOAL keeps the control's state (re-read fresh — the worker's copy predates
                # the pause write). A fenced worker (revise) records the STEP truth only and
                # leaves every goal-level state decision to its successor.
                if worker is not None and worker.fence_requested.is_set():
                    save_goal(goal, expected_epoch=epoch)
                    return
                fresh = load_goal(goal_id) or goal
                if worker is not None and worker.stop_requested.is_set():
                    goal["state"] = "cancelled"
                    goal["ended_at"] = time.time()
                    goal["last_reason"] = "stopped during step"
                    save_goal(goal, expected_epoch=epoch)
                    _emit("Goal cancelled during step: " + str(step.get("title")))
                elif str(fresh.get("state")) in ("paused", "cancelled"):
                    goal["state"] = str(fresh.get("state"))
                    goal["paused_at"] = fresh.get("paused_at")
                    goal["paused_seconds"] = fresh.get("paused_seconds")
                    save_goal(goal, expected_epoch=epoch)
                else:
                    goal["state"] = "interrupted"
                    goal["last_reason"] = reason
                    save_goal(goal, expected_epoch=epoch)
                    _emit("Goal interrupted: " + reason)
                return
            if state == "cancelled":
                goal["state"] = "cancelled"
                goal["ended_at"] = time.time()
                save_goal(goal, expected_epoch=epoch)
                _emit("Goal cancelled during step: " + str(step.get("title")))
                return
            if state == "blocked":
                goal["state"] = "blocked"
                goal["last_reason"] = f"step {step.get('id')} blocked: {reason}"
                save_goal(goal, expected_epoch=epoch)
                _emit("Goal blocked: " + str(goal.get("last_reason")))
                return
            # failed: bounded repair, then honest failure
            if attempt < MAX_STEP_ATTEMPTS:
                step["state"] = "pending"
                step["reason"] = reason
                save_goal(goal, expected_epoch=epoch)
                _emit(f"Step verification failed; scheduling repair: {step.get('title')}")
                if _paused_or_stopped(worker):
                    return
                continue
            goal["state"] = "failed"
            goal["ended_at"] = time.time()
            goal["last_reason"] = f"step {step.get('id')} failed: {reason}"
            save_goal(goal, expected_epoch=epoch)
            _emit("Goal failed: " + str(goal.get("last_reason")))
            return
    except WorkerFencedError:
        # A successor (revise/resume) owns the goal now; the stale worker exits silently.
        return
    finally:
        unregister_worker(goal_id, worker)


def _paused_or_stopped(worker) -> bool:
    if worker is None:
        return False
    return (
        worker.pause_requested.is_set()
        or worker.stop_requested.is_set()
        or worker.fence_requested.is_set()
    )


def _latest_approval_id(session_id: str, turn_id: str) -> str:
    """The approval the step's OWN turn minted, from the durable ledger events."""
    for event in _step_events(session_id, turn_id):
        approval = event.get("approval") if isinstance(event.get("approval"), dict) else {}
        approval_id = str(approval.get("approval_id") or event.get("approval_id") or "").strip()
        if approval_id:
            return approval_id
    return ""


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
    """The real inline recovery for a waiting step: resume the EXACT authorized attempt.

    The token is resolved by the served layer from the goal's recorded pending approval (the
    same approval the step's turn minted, bound to the exact action and arguments by the
    existing consult); it is armed on the worker and applied ONCE to the waiting step's next
    dispatch. Refuses honestly when the goal is not in a holding state or the recorded
    approval is not currently pending.
    """
    goal = load_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "no such goal"}
    state = str(goal.get("state"))
    if state not in ("waiting_approval", "paused", "interrupted"):
        return {"ok": False, "error": f"goal is {state}; retry applies to a holding state"}
    step_id = str(goal.get("current_step_id") or "")
    waiting = None
    for step in goal.get("plan", {}).get("steps", []):
        if step.get("id") == step_id and step.get("state") in ("waiting_approval", "running", "interrupted"):
            waiting = step
            break
    if waiting is None:
        return {"ok": False, "error": "no waiting step to resume"}
    approval_id = str(waiting.get("approval_id") or "").strip()
    token = str(approval_token or "").strip()
    resolved = ""
    if approval_id:
        from core.mode_permission_policy import pending_approval_record

        record = pending_approval_record(str(goal.get("session_id") or ""), approval_id)
        if record is None:
            return {
                "ok": False,
                "error": "the recorded approval is no longer pending (expired, used, or resolved); resume the step fresh with /goal resume",
            }
        resolved = str(record.get("approval_id") or "")
    if not token and resolved:
        token = resolved
    if not token:
        return {"ok": False, "error": "no pending approval is recorded for this step; run /goal resume to re-dispatch it"}
    waiting["state"] = "pending"
    waiting["reason"] = ""
    goal["state"] = "running"
    goal["updated_at"] = time.time()
    goal["last_reason"] = ""
    save_goal(goal)
    from core.goal_workflow import emit_goal_event as _emit

    _emit(_goal_event_context_for(goal), goal, "Step re-dispatched after approval")
    return {"ok": True, "state": "running", "approval_token_used": bool(token), "pending_step_id": step_id, "pending_approval_token": token}


def _goal_event_context_for(goal: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_session_id": str(goal.get("session_id") or ""),
        "cancel_turn_id": str(goal.get("created_turn_id") or ""),
    }
