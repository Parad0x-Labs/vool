"""The explicit /goal workflow: one durable, chat-bound goal per chat.

This module OWNS the goal aggregate and its controller. It deliberately owns nothing else:
execution rides the existing agent turn path (`core.web.api.runtime.run_agent`), permission
rides the existing mode authority (`core.mode_permission_policy`), cancellation rides the
existing live-turn registry (`core.live_turns`), and progress facts ride the existing runtime
event ledger (`core.runtime_task_events.emit_runtime_event` -> ``runtime_session_events``).
There is no second scheduler, no second permission authority, and no goal-private receipt
store: step turns commit their own canonical transcript rows, checkpoints and receipts
through the same seams every chat turn uses.

What is durable here is ONLY the goal record the mission names: explicit objective,
acceptance criteria, the plan revision with stable step identifiers and dependencies, the
goal lifecycle state, and references (turn ids) back to the canonical attempts. Tool
receipts, spend accounting, worker ownership and checkpoint state stay in their existing
owners and are referenced, never copied.

State machine (goal.state)::

    queued -> planning -> running -> completed | failed | cancelled | blocked
                       running -> waiting_approval -> running | failed | cancelled
                       running -> paused -> running
                       running -> interrupted -> running | cancelled
                       running -> blocked -> running | cancelled

``blocked`` is a non-terminal hold the operator can resolve (missing budget or capability);
it is deliberately NOT ``waiting_approval``, which is reserved for a live permission ask.

Worker ownership: every spawn claims a monotone durable ``worker_epoch``. A worker records
its epoch and every durable write it makes is fenced against the current epoch — a worker
overtaken by a successor (revise, resume after reconnect) exits without writing. ``_WORKERS``
is keyed by goal id, but a worker's unregister only removes ITS OWN registration, never a
successor's.

Step states live on the plan's steps: pending, running, waiting_approval, completed, failed,
blocked, skipped, cancelled, interrupted. A model saying "done" can never complete a step:
completion requires the step's turn to have ended fulfilled AND the step's declared
verification (an actual executed tool effect recorded in the runtime event ledger) when one
is declared.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

GOAL_EVENT_TYPE = "goal_updated"

#: Goal lifecycle states. Terminal: completed, failed, cancelled. Holding: paused,
#: interrupted, waiting_approval, blocked.
GOAL_STATES = (
    "queued",
    "planning",
    "running",
    "waiting_approval",
    "paused",
    "interrupted",
    "blocked",
    "completed",
    "failed",
    "cancelled",
)
TERMINAL_GOAL_STATES = frozenset({"completed", "failed", "cancelled"})

STEP_STATES = (
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "blocked",
    "skipped",
    "cancelled",
    "interrupted",
)

#: Attempts per step are bounded: one dispatch, one bounded repair re-dispatch after a failed
#: verification. A goal is never an unlimited loop.
MAX_STEP_ATTEMPTS = 2

#: Bounded plan size. The planner may not mint an unbounded work program.
MAX_PLAN_STEPS = 8

#: Total bounded dispatch budget per goal (planner + steps + repairs). Crossing it fails the
#: goal truthfully instead of looping.
MAX_GOAL_TURNS = 20

_LOCK = threading.RLock()


class WorkerFencedError(RuntimeError):
    """A stale worker tried to write after a successor claimed the goal's epoch."""

    code = "VOOL_E_GOAL_WORKER_FENCED"


# ---------------------------------------------------------------------------
# Parsing the composer text
# ---------------------------------------------------------------------------

_GOAL_WORD = re.compile(r"^/goal\b\s*", re.IGNORECASE)

#: The explicit CONVERSATIONAL goal-instruction form: "goal: do X instead" (no slash). This
#: is the deterministic, unambiguous way a normal chat message addresses the active goal.
_GOAL_INSTRUCTION_PREFIX = re.compile(r"^goal\s*[:：]\s*", re.IGNORECASE)

#: Reference + correction cues for the AMBIGUOUS case. A message that references the
#: goal/plan and carries a correction verb, without the explicit prefix, is likely about the
#: goal — it asks ONE concise question instead of mutating anything. No classifier, no paid
#: call: two closed-vocabulary regular expressions.
_GOAL_REFERENCE_CUE = re.compile(r"\b(goal|plan|steps?)\b", re.IGNORECASE)
_CORRECTION_CUE = re.compile(
    r"\b(revise|change|update|instead|drop|remove|rename|reorder|adjust|extend|add|swap|replace|scrub|abort)\b",
    re.IGNORECASE,
)


def parse_goal_reference(text: str) -> dict[str, str] | None:
    """Classify a plain (non-slash) chat message's goal reference, or None.

    Returns ``{"kind": "instruction", "arg": ...}`` for the explicit ``goal:`` prefix;
    ``{"kind": "ambiguous"}`` when the message references the goal/plan with a correction
    cue but no prefix (the caller asks ONE concise question; nothing is mutated); None for
    ordinary chat. Only ever consulted when the chat HAS an active goal.
    """
    raw = str(text or "").strip()
    if not raw or raw.startswith("/"):
        return None
    if _GOAL_INSTRUCTION_PREFIX.match(raw):
        return {"kind": "instruction", "arg": _GOAL_INSTRUCTION_PREFIX.sub("", raw, count=1).strip()}
    if _GOAL_REFERENCE_CUE.search(raw) and _CORRECTION_CUE.search(raw):
        return {"kind": "ambiguous"}
    return None


def parse_goal_command(text: str) -> dict[str, str] | None:
    """Split a composer message starting with ``/goal`` into its sub-command and argument.

    Returns None when the message is not a goal command. ``sub`` is one of ``""`` (bare
    /goal), ``status``, ``pause``, ``resume``, ``stop``, ``revise``, ``start``.
    """
    raw = str(text or "").strip()
    match = _GOAL_WORD.match(raw)
    if not match:
        return None
    rest = raw[match.end():].strip()
    lowered = rest.lower()
    for word in ("status", "pause", "resume", "stop"):
        if lowered == word or lowered.startswith(word + " ") or lowered.startswith(word + "\n"):
            return {"sub": word, "arg": rest[len(word):].strip()}
    if lowered.startswith("revise ") or lowered.startswith("revise\n"):
        return {"sub": "revise", "arg": rest[len("revise"):].strip()}
    if lowered.startswith("start "):
        return {"sub": "start", "arg": rest[len("start"):].strip()}
    return {"sub": "", "arg": rest}


# ---------------------------------------------------------------------------
# Durable store (one row per goal; the storage/migration registration pattern)
# ---------------------------------------------------------------------------

_GOAL_COLUMNS = (
    "goal_id",
    "session_id",
    "project_id",
    "objective",
    "acceptance",
    "plan_json",
    "state",
    "revision",
    "current_step_id",
    "created_at",
    "updated_at",
    "ended_at",
    "started_at",
    "paused_at",
    "paused_seconds",
    "created_turn_id",
    "last_reason",
    "model",
    "exec_context_json",
    "worker_epoch",
)


def _conn():
    from storage.db import get_connection

    return get_connection()


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_goals (
            goal_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            objective TEXT NOT NULL,
            acceptance TEXT NOT NULL DEFAULT '',
            plan_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'queued',
            revision INTEGER NOT NULL DEFAULT 0,
            current_step_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            started_at REAL,
            paused_at REAL,
            paused_seconds REAL NOT NULL DEFAULT 0,
            created_turn_id TEXT NOT NULL DEFAULT '',
            last_reason TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            exec_context_json TEXT NOT NULL DEFAULT '{}',
            worker_epoch INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_goals_session ON chat_goals(session_id, created_at DESC)"
    )
    # Evolution of THIS module's own table (the storage/migrations pattern for additive
    # columns on an already-shipped table): add missing columns in place, idempotently.
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(chat_goals)").fetchall()}
    if "worker_epoch" not in existing:
        conn.execute("ALTER TABLE chat_goals ADD COLUMN worker_epoch INTEGER NOT NULL DEFAULT 0")


def _row_to_goal(row) -> dict[str, Any]:
    goal = {key: row[idx] for idx, key in enumerate(_GOAL_COLUMNS)}
    try:
        goal["plan"] = json.loads(goal.pop("plan_json") or "{}")
    except Exception:
        goal["plan"] = {}
    try:
        goal["exec_context"] = json.loads(goal.pop("exec_context_json") or "{}")
    except Exception:
        goal["exec_context"] = {}
    return goal


def save_goal(goal: dict[str, Any], *, expected_epoch: int | None = None) -> None:
    """Persist the full goal aggregate. The caller holds the authoritative in-memory copy.

    ``expected_epoch`` fences a worker's writes: when the durable row has moved to a NEWER
    epoch (a successor claimed it via revise/resume), the stale write raises
    WorkerFencedError instead of clobbering the successor's state.
    """
    conn = _conn()
    _ensure_table(conn)
    conn.execute("BEGIN IMMEDIATE")
    if expected_epoch is not None:
        row = conn.execute(
            "SELECT worker_epoch FROM chat_goals WHERE goal_id = ?", (str(goal.get("goal_id") or ""),)
        ).fetchone()
        if row is not None and int(row[0] or 0) != int(expected_epoch):
            conn.rollback()
            conn.close()
            raise WorkerFencedError(
                f"goal {goal.get('goal_id')} moved to epoch {row[0]}; worker holds epoch {expected_epoch}"
            )
    payload = {key: goal.get(key) for key in _GOAL_COLUMNS if key in goal}
    payload["plan_json"] = json.dumps(goal.get("plan") or {}, ensure_ascii=False)
    payload["exec_context_json"] = json.dumps(goal.get("exec_context") or {}, ensure_ascii=False)
    columns = [key for key in _GOAL_COLUMNS]
    values = [payload.get(key) for key in columns]
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO chat_goals ({','.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    conn.close()


def load_goal(goal_id: str) -> dict[str, Any] | None:
    conn = _conn()
    _ensure_table(conn)
    row = conn.execute(
        f"SELECT {','.join(_GOAL_COLUMNS)} FROM chat_goals WHERE goal_id = ?", (str(goal_id or ""),)
    ).fetchone()
    return _row_to_goal(row) if row is not None else None


def load_active_goal_for_session(session_id: str) -> dict[str, Any] | None:
    """The chat's one non-terminal goal, or None. One explicit goal per chat at a time."""
    conn = _conn()
    _ensure_table(conn)
    rows = conn.execute(
        f"SELECT {','.join(_GOAL_COLUMNS)} FROM chat_goals WHERE session_id = ? ORDER BY created_at DESC LIMIT 5",
        (str(session_id or ""),),
    ).fetchall()
    for row in rows:
        goal = _row_to_goal(row)
        if str(goal.get("state")) not in TERMINAL_GOAL_STATES:
            return goal
    return None


def load_latest_goal_for_session(session_id: str) -> dict[str, Any] | None:
    conn = _conn()
    _ensure_table(conn)
    row = conn.execute(
        f"SELECT {','.join(_GOAL_COLUMNS)} FROM chat_goals WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (str(session_id or ""),),
    ).fetchone()
    return _row_to_goal(row) if row is not None else None


def reset_goal_state_for_tests() -> None:
    """Tests only: drop every goal row. A process with real goals must never call this."""
    conn = _conn()
    _ensure_table(conn)
    conn.execute("DELETE FROM chat_goals")
    conn.commit()
    with _LOCK:
        _WORKERS.clear()


# ---------------------------------------------------------------------------
# Plan model
# ---------------------------------------------------------------------------

def new_goal_id() -> str:
    return f"goal-{uuid.uuid4().hex[:20]}"


def new_step_id(index: int) -> str:
    return f"step-{index + 1}-{uuid.uuid4().hex[:6]}"


def _clean_depends(values: Any, known: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item) in known]


def build_plan(steps_input: list[dict[str, Any]], *, provenance: str) -> dict[str, Any]:
    """Validate and normalize a plan. Raises ValueError when the shape is unusable.

    Each normalized step carries a stable id, a title, optional details, declared
    dependencies (already-known ids only), and an optional machine-checkable verification:
    ``{"tool": <tool name>, "path_contains": <substring>}`` — evidence read from the runtime
    event ledger of the step's own turn, never from model prose.
    """
    if not isinstance(steps_input, list) or not steps_input:
        raise ValueError("a plan needs at least one step")
    if len(steps_input) > MAX_PLAN_STEPS:
        raise ValueError(f"a plan is bounded to {MAX_PLAN_STEPS} steps")
    ids: list[str] = []
    cleaned: list[dict[str, Any]] = []
    for index, raw in enumerate(steps_input):
        if not isinstance(raw, dict):
            raise ValueError("each plan step must be an object")
        title = str(raw.get("title") or raw.get("name") or "").strip()
        if not title:
            raise ValueError("each plan step needs a title")
        step_id = str(raw.get("id") or "").strip() or new_step_id(index)
        if step_id in ids:
            raise ValueError(f"duplicate plan step id: {step_id}")
        ids.append(step_id)
        verify_raw = raw.get("verify") if isinstance(raw.get("verify"), dict) else None
        verify = ""
        if verify_raw:
            verify = json.dumps(
                {
                    "tool": str(verify_raw.get("tool") or "").strip(),
                    "path_contains": str(verify_raw.get("path_contains") or "").strip(),
                    **({"answer_contains": str(verify_raw["answer_contains"]).strip()} if verify_raw.get("answer_contains") else {}),
                },
                ensure_ascii=False,
            )
        cleaned.append(
            {
                "id": step_id,
                "title": title[:300],
                "details": str(raw.get("details") or raw.get("description") or "").strip()[:2000],
                "verify": verify,
                "state": "pending",
                "reason": "",
                "attempts": 0,
                "turn_id": "",
                "checkpoint_id": "",
            }
        )
    known = set(ids)
    for raw, step in zip(steps_input, cleaned, strict=True):
        deps = _clean_depends(raw.get("depends_on") or raw.get("depends"), known)
        if step["id"] in deps:
            raise ValueError("a plan step cannot depend on itself")
        step["depends_on"] = deps
    # Acyclicity: Kahn's algorithm over the declared edges.
    indegree = {step_id: 0 for step_id in ids}
    for step in cleaned:
        for _dep in step["depends_on"]:
            indegree[step["id"]] += 1
    ready = [step_id for step_id, count in indegree.items() if count == 0]
    seen = 0
    while ready:
        current = ready.pop()
        seen += 1
        for step in cleaned:
            if current in step["depends_on"]:
                indegree[step["id"]] -= 1
                if indegree[step["id"]] == 0:
                    ready.append(step["id"])
    if seen != len(ids):
        raise ValueError("plan dependencies contain a cycle")
    return {"revision": 1, "provenance": str(provenance), "steps": cleaned}


def parse_planner_answer(answer: str) -> list[dict[str, Any]] | None:
    """Extract the planner's strict JSON plan from an answer, or None.

    The planner is asked to reply with ONLY a JSON object; fenced or bare are both accepted.
    Anything unparsable yields None — the caller then derives an truthful single-step plan
    instead of inventing structure the model did not state.
    """
    text = str(answer or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1) if fenced else None, text]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            steps = payload.get("steps")
            if isinstance(steps, list) and steps:
                return steps
    return None


def derived_single_step_plan(objective: str) -> dict[str, Any]:
    """The truthful fallback when no planner produced structure: ONE explicit step."""
    return build_plan(
        [{"id": "step-1", "title": objective[:300], "details": ""}],
        provenance="derived",
    )


def completed_step_count(plan: dict[str, Any]) -> tuple[int, int]:
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps:
        return (0, 0)
    done = sum(1 for step in steps if step.get("state") == "completed")
    return (done, len(steps))


# ---------------------------------------------------------------------------
# Public projection (the ONE shape the card, the commands and Activity share)
# ---------------------------------------------------------------------------

def goal_public_view(goal: dict[str, Any]) -> dict[str, Any]:
    """The compact canonical goal state served to the card and the registry commands.

    This is a projection of the durable record, not a second status store: the card renders
    this, Activity renders the referenced events, and both read the same row.
    """
    plan = goal.get("plan") if isinstance(goal.get("plan"), dict) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    done, total = completed_step_count(plan)
    now = time.time()
    started_at = float(goal.get("started_at") or 0.0)
    paused_seconds = float(goal.get("paused_seconds") or 0.0)
    paused_at = float(goal.get("paused_at") or 0.0)
    elapsed = 0.0
    if started_at:
        end = float(goal.get("ended_at") or 0.0) or now
        effective_pause = paused_seconds + ((now - paused_at) if paused_at and not goal.get("ended_at") else 0.0)
        elapsed = max(0.0, end - started_at - effective_pause)
    return {
        "goal_id": str(goal.get("goal_id") or ""),
        "session_id": str(goal.get("session_id") or ""),
        "project_id": str(goal.get("project_id") or ""),
        "objective": str(goal.get("objective") or ""),
        "acceptance": str(goal.get("acceptance") or ""),
        "state": str(goal.get("state") or ""),
        "revision": int(goal.get("revision") or 0),
        "plan_provenance": str(plan.get("provenance") or ""),
        "current_step_id": str(goal.get("current_step_id") or ""),
        "steps": [
            {
                "id": step.get("id"),
                "title": step.get("title"),
                "details": step.get("details"),
                "state": step.get("state"),
                "reason": step.get("reason"),
                "depends_on": step.get("depends_on") or [],
                "turn_id": step.get("turn_id") or "",
            }
            for step in steps
        ],
        "completed": done,
        "total": total,
        "has_plan": bool(steps),
        "started_at": started_at,
        "updated_at": float(goal.get("updated_at") or 0.0),
        "ended_at": float(goal.get("ended_at") or 0.0),
        "paused": bool(goal.get("paused_at")) and str(goal.get("state")) == "paused",
        "elapsed_seconds": round(elapsed, 3),
        "reason": str(goal.get("last_reason") or ""),
    }


# ---------------------------------------------------------------------------
# Event emission (through the ONE runtime event owner)
# ---------------------------------------------------------------------------

def emit_goal_event(source_context: dict[str, Any] | None, goal: dict[str, Any], message: str) -> dict[str, Any]:
    """Emit one durable, user-safe goal event through the runtime event owner.

    The payload carries the compact public view; the typed event channel allowlists
    ``goal_updated`` -> ``goal.updated``. Emission goes to the durable ledger always, and to
    any registered live stream sink via the context's stream id.
    """
    from core.runtime_task_events import emit_runtime_event

    view = goal_public_view(goal)
    emit_runtime_event(
        source_context,
        event_type=GOAL_EVENT_TYPE,
        message=str(message or "")[:300],
        details={"goal": view},
    )
    return view


# ---------------------------------------------------------------------------
# Active worker registry (pause/stop controls reach the live worker here)
# ---------------------------------------------------------------------------

class _GoalWorker:
    def __init__(self, goal_id: str, session_id: str, *, epoch: int = 0) -> None:
        self.goal_id = str(goal_id)
        self.session_id = str(session_id)
        self.epoch = int(epoch)
        self.pause_requested = threading.Event()
        self.resume_requested = threading.Event()
        self.stop_requested = threading.Event()
        # Revise fencing: stop-like (no new dispatch) but the settled outcome NEVER writes
        # goal-level state — the successor owns the goal; only the step's own settled truth
        # is recorded before exit.
        self.fence_requested = threading.Event()
        self.current_turn_id = ""
        # One-shot, in-memory ONLY (never persisted): the pending-approval token a retry
        # resume applies to the exact waiting step's next dispatch.
        self.pending_approval_token = ""
        self.pending_step_id = ""
        self.lock = threading.Lock()
        self.finished = threading.Event()

    def set_current_turn(self, turn_id: str) -> None:
        with self.lock:
            self.current_turn_id = str(turn_id or "")

    def current_step_turn(self) -> str:
        with self.lock:
            return str(self.current_turn_id or "")

    def arm_approval_token(self, step_id: str, token: str) -> None:
        with self.lock:
            self.pending_step_id = str(step_id or "")
            self.pending_approval_token = str(token or "")

    def take_approval_token(self, step_id: str) -> str:
        """Consume the armed approval token ONCE, and only for the step it was armed for."""
        with self.lock:
            if str(step_id or "") != self.pending_step_id:
                return ""
            token = self.pending_approval_token
            self.pending_approval_token = ""
            self.pending_step_id = ""
            return token


_WORKERS: dict[str, _GoalWorker] = {}


def register_worker(worker: _GoalWorker) -> None:
    with _LOCK:
        _WORKERS[worker.goal_id] = worker


def unregister_worker(goal_id: str, worker: _GoalWorker | None = None) -> None:
    """Release a worker's registration. With ``worker`` given, only THAT worker's own
    registration is removed — a stale worker finishing late must never unregister its
    successor (the revise/resume fencing law)."""
    with _LOCK:
        current = _WORKERS.get(str(goal_id))
        if current is None:
            if worker is not None:
                worker.finished.set()
            return
        if worker is not None and current is not worker:
            worker.finished.set()
            return
        current.finished.set()
        _WORKERS.pop(str(goal_id), None)


def active_worker(goal_id: str) -> _GoalWorker | None:
    with _LOCK:
        return _WORKERS.get(str(goal_id))


def claim_worker_epoch(goal_id: str) -> int:
    """Atomically claim the NEXT worker epoch for this goal and return it. The durable
    increment is what fences an overtaken worker's writes (save_goal expected_epoch)."""
    conn = _conn()
    try:
        _ensure_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE chat_goals SET worker_epoch = COALESCE(worker_epoch, 0) + 1 WHERE goal_id = ?", (str(goal_id),))
        row = conn.execute("SELECT worker_epoch FROM chat_goals WHERE goal_id = ?", (str(goal_id),)).fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def request_pause(goal_id: str) -> dict[str, Any]:
    """Pause: no NEW goal step begins after acknowledgement; the in-flight step turn is
    cancelled through the existing live-turn owner and is ACCOUNTED until it settles — the
    worker classifies the settled outcome (the step's own state) when the turn returns; this
    control never pre-writes a step state it has not observed."""
    goal = load_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "no such goal"}
    if str(goal.get("state")) in TERMINAL_GOAL_STATES:
        return {"ok": False, "error": f"goal already {goal.get('state')}"}
    if str(goal.get("state")) == "paused":
        return {"ok": True, "state": "paused", "already": True}
    worker = active_worker(goal_id)
    now = time.time()
    goal["paused_at"] = now
    goal["state"] = "paused"
    goal["updated_at"] = now
    goal["last_reason"] = "paused by operator"
    save_goal(goal)
    if worker is not None:
        worker.pause_requested.set()
        turn_id = worker.current_step_turn()
        if turn_id:
            from core.live_turns import request_cancel

            request_cancel(goal.get("session_id") or "", turn_id)
    emit_goal_event(_goal_event_context(goal), goal, "Goal paused")
    return {"ok": True, "state": "paused"}


def request_stop(goal_id: str) -> dict[str, Any]:
    """Stop: acknowledges cancellation, prevents new dispatch, and leaves already-dispatched
    side effects as unresolved-for-reconciliation (their receipts stay in the canonical
    stores; nothing is claimed undone). The in-flight worker keeps ownership of its turn
    until it settles — this control never unregisters it early and never pre-writes the
    step's terminal state; the worker classifies the settled outcome."""
    goal = load_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "no such goal"}
    previous_state = str(goal.get("state"))
    if previous_state in TERMINAL_GOAL_STATES:
        return {"ok": False, "error": f"goal already {previous_state}"}
    worker = active_worker(goal_id)
    now = time.time()
    if goal.get("paused_at"):
        paused_at = float(goal.get("paused_at") or 0.0)
        goal["paused_seconds"] = float(goal.get("paused_seconds") or 0.0) + max(0.0, now - paused_at)
        goal["paused_at"] = None
    goal["state"] = "cancelled"
    goal["ended_at"] = now
    goal["updated_at"] = now
    goal["last_reason"] = "stopped by operator"
    if worker is None:
        # No live worker will settle an in-flight turn: freeze any recorded running step as
        # cancelled so nothing is left claiming to run.
        _freeze_running_steps(goal, "cancelled", "goal stopped by operator")
    save_goal(goal)
    if worker is not None:
        worker.stop_requested.set()
        turn_id = worker.current_step_turn()
        if turn_id:
            from core.live_turns import request_cancel

            request_cancel(goal.get("session_id") or "", turn_id)
    emit_goal_event(_goal_event_context(goal), goal, "Goal stopped")
    return {"ok": True, "state": "cancelled"}


def request_resume(goal_id: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resume re-checks current mode/authority through the NEXT dispatch: step turns carry no
    pinned mode, so the authority resolves the chat's LIVE mode on every consult — a mode
    change made while paused takes effect before the next action.

    Interrupted steps are reconciled against their recorded turn's receipts BEFORE any
    re-dispatch: a step whose declared verification already holds in its own turn's ledger
    events is COMPLETED (the effect committed before the interruption) and is never replayed;
    a step whose expected tool ran but could not be verified is a RECONCILIATION NEED
    (blocked, truthful reason) — never blindly replayed; only a step with no recorded effect
    evidence returns to pending for a fresh dispatch.
    """
    goal = load_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "no such goal"}
    state = str(goal.get("state"))
    if state in TERMINAL_GOAL_STATES:
        return {"ok": False, "error": f"goal already {state}"}
    if state not in ("paused", "interrupted", "blocked"):
        return {"ok": True, "state": state, "already": True}
    worker = active_worker(goal_id)
    if worker is not None and worker.pause_requested.is_set() and not worker.finished.is_set():
        # Keep accounting the cancelled turn. Its owner will launch one successor only
        # after settlement; an immediate Resume must not orphan or overlap the workers.
        worker.resume_requested.set()
        return {"ok": True, "state": state, "settling": True}
    now = time.time()
    if goal.get("paused_at"):
        paused_at = float(goal.get("paused_at") or 0.0)
        goal["paused_seconds"] = float(goal.get("paused_seconds") or 0.0) + max(0.0, now - paused_at)
        goal["paused_at"] = None
    goal["state"] = "running"
    goal["updated_at"] = now
    goal["last_reason"] = ""
    from core.goal_executor import reconcile_interrupted_step

    for step in goal.get("plan", {}).get("steps", []):
        if step.get("state") == "interrupted":
            reconcile_interrupted_step(goal, step)
        elif step.get("state") == "blocked" and "reconcile" not in str(step.get("reason") or ""):
            # A budget/capability hold resumes optimistically; the next dispatch re-consults
            # the live authority and re-blocks truthfully if the need persists.
            step["state"] = "pending"
            step["reason"] = ""
    save_goal(goal)
    emit_goal_event(_goal_event_context(goal), goal, "Goal resumed")
    resume_callback = _RESUME_CALLBACKS.get(str(goal_id))
    if resume_callback is not None:
        resume_callback()
    return {"ok": True, "state": "running"}


def request_revise(goal_id: str, new_objective: str, *, source: str = "chat") -> dict[str, Any]:
    """Revise the goal's objective and re-derive the plan, monotonically.

    Fences the active worker first (epoch bump — the old worker exits without writing),
    preserves completed work (completed steps carry over with their identities, receipts and
    turn references), supersedes only pending obligations, bumps the revision monotonically,
    and writes durable revision provenance. Exactly ONE successor worker may then be spawned
    by the caller; the fence makes an old worker's late writes and unregister harmless.
    """
    goal = load_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "no such goal"}
    if str(goal.get("state")) in TERMINAL_GOAL_STATES:
        return {"ok": False, "error": f"goal already {goal.get('state')}"}
    clean_objective = str(new_objective or "").strip()[:4000]
    if not clean_objective:
        return {"ok": False, "error": "an objective is required"}
    worker = active_worker(goal_id)
    if worker is not None:
        # Fence the old worker: no new dispatch, and its settled outcome records only the
        # STEP truth (never goal state). Wait bounded for the in-flight turn to settle so
        # the rewrite below starts from the settled facts — an effect the turn committed is
        # recorded before the plan is superseded.
        worker.fence_requested.set()
        turn_id = worker.current_step_turn()
        if turn_id:
            from core.live_turns import request_cancel

            request_cancel(goal.get("session_id") or "", turn_id)
        if not worker.finished.wait(timeout=10.0):
            return {"ok": False, "error": "the active step is still settling; revise again in a moment"}
    goal = load_goal(goal_id) or goal
    previous_plan = goal.get("plan") if isinstance(goal.get("plan"), dict) else {}
    previous_revision = int(goal.get("revision") or 0)
    carried = [
        step
        for step in (previous_plan.get("steps") or [])
        if isinstance(step, dict) and step.get("state") == "completed"
    ]
    history = list(previous_plan.get("history") or [])
    history.append(
        {
            "revision": previous_revision,
            "objective": str(goal.get("objective") or "")[:4000],
            "provenance": str(previous_plan.get("provenance") or ""),
            "completed_step_ids": [str(step.get("id")) for step in carried],
            "superseded_at": time.time(),
        }
    )
    # Only the last few revisions are retained in the row (bounded history); the canonical
    # receipts stay in the runtime stores regardless.
    goal["plan"] = {
        "revision": 0,  # re-derived; build_plan stamps 1, so carry the monotone counter after
        "provenance": f"revised:{source}",
        "history": history[-5:],
        "carried_steps": carried,
        "dispatches": int(previous_plan.get("dispatches") or 0),
    }
    goal["objective"] = clean_objective
    goal["state"] = "queued"
    goal["current_step_id"] = ""
    goal["worker_epoch"] = int(goal.get("worker_epoch") or 0) + 1
    goal["updated_at"] = time.time()
    goal["last_reason"] = f"objective revised from {source}; completed steps carried"
    save_goal(goal)
    emit_goal_event(_goal_event_context(goal), goal, "Goal revised")
    return {"ok": True, "state": "queued", "worker_epoch": int(goal.get("worker_epoch") or 0)}


#: Served-layer callback: after a resume, the HTTP layer re-arms the goal worker. The store
#: layer never spawns threads itself; the web adapter registers the callback when it serves
#: the goal.
_RESUME_CALLBACKS: dict[str, Callable[[], None]] = {}


def set_resume_callback(goal_id: str, callback: Callable[[], None] | None) -> None:
    if callback is None:
        _RESUME_CALLBACKS.pop(str(goal_id), None)
    else:
        _RESUME_CALLBACKS[str(goal_id)] = callback


def _goal_event_context(goal: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_session_id": str(goal.get("session_id") or ""),
        "cancel_turn_id": str(goal.get("created_turn_id") or ""),
    }


def _freeze_running_steps(goal: dict[str, Any], state: str, reason: str) -> None:
    for step in goal.get("plan", {}).get("steps", []):
        if step.get("state") in ("running", "waiting_approval"):
            step["state"] = state
            if state != "completed":
                step["reason"] = reason


# ---------------------------------------------------------------------------
# Startup reconciliation: a goal recorded running whose worker died with the process is
# truthfully interrupted — never silently resurrected as running, never left "running" forever.
# ---------------------------------------------------------------------------

def reconcile_on_startup() -> int:
    conn = _conn()
    _ensure_table(conn)
    rows = conn.execute(
        f"SELECT {','.join(_GOAL_COLUMNS)} FROM chat_goals WHERE state IN ('running','planning','queued','waiting_approval')"
    ).fetchall()
    reconciled = 0
    now = time.time()
    for row in rows:
        goal = _row_to_goal(row)
        if active_worker(goal.get("goal_id") or "") is not None:
            continue
        goal["state"] = "interrupted"
        goal["updated_at"] = now
        goal["last_reason"] = "interrupted by restart"
        _freeze_running_steps(goal, "interrupted", "interrupted by restart")
        save_goal(goal)
        reconciled += 1
    return reconciled


def reconcile_goal_if_orphaned(goal_id: str) -> bool:
    """One-goal truthful hydration: a goal recorded mid-dispatch (running/planning/queued)
    whose worker is NOT registered reads as interrupted. Holding states (paused,
    waiting_approval) legitimately have no worker; interrupted and terminal goals are never
    resurrected here — only an explicit resume/stop moves them."""
    goal = load_goal(goal_id)
    if goal is None:
        return False
    if str(goal.get("state")) not in ("running", "planning", "queued"):
        return False
    if active_worker(goal_id) is not None:
        return False
    now = time.time()
    goal["state"] = "interrupted"
    goal["updated_at"] = now
    goal["last_reason"] = "interrupted: no active worker"
    _freeze_running_steps(goal, "interrupted", "interrupted: no active worker")
    save_goal(goal)
    return True
