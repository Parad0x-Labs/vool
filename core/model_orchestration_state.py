from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.enum_compat import StrEnum
from storage.db import get_connection


class ModelOrchestrationState(StrEnum):
    LOCAL_SELECTED = "LOCAL_SELECTED"
    LOCAL_RUNNING = "LOCAL_RUNNING"
    LOCAL_VERIFYING = "LOCAL_VERIFYING"
    LOCAL_FAILED = "LOCAL_FAILED"
    ESCALATION_EVALUATING = "ESCALATION_EVALUATING"
    ESCALATION_PROPOSED = "ESCALATION_PROPOSED"
    AWAITING_USER_APPROVAL = "AWAITING_USER_APPROVAL"
    PAID_REQUEST_PREPARING = "PAID_REQUEST_PREPARING"
    PAID_RUNNING = "PAID_RUNNING"
    PAID_VALIDATING = "PAID_VALIDATING"
    PAID_FAILED = "PAID_FAILED"
    RETURNING_LOCAL = "RETURNING_LOCAL"
    LOCAL_RESUMED = "LOCAL_RESUMED"
    TASK_VERIFYING = "TASK_VERIFYING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNING = "COMPLETED_WITH_WARNING"
    CANCELLED = "CANCELLED"
    FAILED_SAFE = "FAILED_SAFE"


_TERMINAL = {
    ModelOrchestrationState.COMPLETED,
    ModelOrchestrationState.COMPLETED_WITH_WARNING,
    ModelOrchestrationState.CANCELLED,
    ModelOrchestrationState.FAILED_SAFE,
}
_ALLOWED: dict[ModelOrchestrationState, set[ModelOrchestrationState]] = {
    ModelOrchestrationState.LOCAL_SELECTED: {ModelOrchestrationState.LOCAL_RUNNING, ModelOrchestrationState.CANCELLED},
    ModelOrchestrationState.LOCAL_RUNNING: {
        ModelOrchestrationState.LOCAL_VERIFYING,
        ModelOrchestrationState.LOCAL_FAILED,
        ModelOrchestrationState.ESCALATION_EVALUATING,
        ModelOrchestrationState.CANCELLED,
    },
    ModelOrchestrationState.LOCAL_VERIFYING: {
        ModelOrchestrationState.LOCAL_SELECTED,
        ModelOrchestrationState.TASK_VERIFYING,
        ModelOrchestrationState.LOCAL_FAILED,
        ModelOrchestrationState.ESCALATION_EVALUATING,
    },
    ModelOrchestrationState.LOCAL_FAILED: {
        ModelOrchestrationState.LOCAL_SELECTED,
        ModelOrchestrationState.ESCALATION_EVALUATING,
        ModelOrchestrationState.FAILED_SAFE,
    },
    ModelOrchestrationState.ESCALATION_EVALUATING: {
        ModelOrchestrationState.ESCALATION_PROPOSED,
        ModelOrchestrationState.LOCAL_SELECTED,
        ModelOrchestrationState.FAILED_SAFE,
    },
    ModelOrchestrationState.ESCALATION_PROPOSED: {
        ModelOrchestrationState.AWAITING_USER_APPROVAL,
        ModelOrchestrationState.PAID_REQUEST_PREPARING,
        ModelOrchestrationState.LOCAL_SELECTED,
        ModelOrchestrationState.CANCELLED,
    },
    ModelOrchestrationState.AWAITING_USER_APPROVAL: {
        ModelOrchestrationState.PAID_REQUEST_PREPARING,
        ModelOrchestrationState.LOCAL_SELECTED,
        ModelOrchestrationState.CANCELLED,
    },
    ModelOrchestrationState.PAID_REQUEST_PREPARING: {
        ModelOrchestrationState.PAID_RUNNING,
        ModelOrchestrationState.PAID_FAILED,
        ModelOrchestrationState.RETURNING_LOCAL,
        ModelOrchestrationState.CANCELLED,
    },
    ModelOrchestrationState.PAID_RUNNING: {
        ModelOrchestrationState.PAID_VALIDATING,
        ModelOrchestrationState.PAID_FAILED,
        ModelOrchestrationState.RETURNING_LOCAL,
        ModelOrchestrationState.CANCELLED,
    },
    ModelOrchestrationState.PAID_VALIDATING: {
        ModelOrchestrationState.RETURNING_LOCAL,
        # An explicitly pinned paid model may itself produce the terminal answer. There is no
        # remaining local handoff in that shape; validation proceeds directly to task verification.
        ModelOrchestrationState.TASK_VERIFYING,
        ModelOrchestrationState.PAID_FAILED,
        ModelOrchestrationState.CANCELLED,
    },
    ModelOrchestrationState.PAID_FAILED: {ModelOrchestrationState.RETURNING_LOCAL, ModelOrchestrationState.FAILED_SAFE},
    ModelOrchestrationState.RETURNING_LOCAL: {ModelOrchestrationState.LOCAL_RESUMED, ModelOrchestrationState.FAILED_SAFE},
    ModelOrchestrationState.LOCAL_RESUMED: {
        ModelOrchestrationState.LOCAL_RUNNING,
        ModelOrchestrationState.TASK_VERIFYING,
        ModelOrchestrationState.COMPLETED_WITH_WARNING,
    },
    ModelOrchestrationState.TASK_VERIFYING: {
        ModelOrchestrationState.COMPLETED,
        ModelOrchestrationState.COMPLETED_WITH_WARNING,
        ModelOrchestrationState.LOCAL_FAILED,
    },
}


@dataclass(frozen=True)
class ModelTransition:
    transition_id: str
    run_id: str
    task_id: str
    turn_id: str
    subtask_id: str
    from_state: str
    to_state: str
    source_model: str
    target_model: str
    reason: str
    context_capsule_ref: str
    spend_estimate_usd: float
    actual_spend_usd: float
    policy_decision: str
    verification_result: str
    created_at: str
    version: int


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_orchestration_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                subtask_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                active_lane TEXT NOT NULL DEFAULT '',
                active_model TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, turn_id, subtask_id)
            );
            CREATE TABLE IF NOT EXISTS model_orchestration_transitions (
                transition_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                subtask_id TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                source_model TEXT NOT NULL,
                target_model TEXT NOT NULL,
                reason TEXT NOT NULL,
                context_capsule_ref TEXT NOT NULL DEFAULT '',
                spend_estimate_usd REAL NOT NULL DEFAULT 0,
                actual_spend_usd REAL NOT NULL DEFAULT 0,
                policy_decision TEXT NOT NULL DEFAULT '',
                verification_result TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                version INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES model_orchestration_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_model_orchestration_scope
              ON model_orchestration_runs(task_id, turn_id, subtask_id);
            """
        )
        conn.commit()
    finally:
        conn.close()


class ModelOrchestrationStore:
    def create_run(
        self,
        *,
        task_id: str,
        turn_id: str,
        subtask_id: str = "",
        session_id: str = "",
        lane: str,
        model_id: str,
        reason: str,
    ) -> str:
        if not str(task_id).strip() or not str(turn_id).strip():
            raise ValueError("task_id and turn_id are required")
        _init_tables()
        run_id = f"model-run-{uuid.uuid4().hex}"
        now = _utcnow()
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO model_orchestration_runs
                (run_id, task_id, turn_id, subtask_id, session_id, state, active_lane, active_model, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (run_id, task_id, turn_id, subtask_id, session_id, ModelOrchestrationState.LOCAL_SELECTED, lane, model_id, now, now),
            )
            self._insert_transition(
                conn,
                run_id=run_id,
                task_id=task_id,
                turn_id=turn_id,
                subtask_id=subtask_id,
                from_state="",
                to_state=ModelOrchestrationState.LOCAL_SELECTED,
                source_model="",
                target_model=model_id,
                reason=reason,
                version=0,
            )
            conn.commit()
            return run_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def transition(
        self,
        run_id: str,
        to_state: ModelOrchestrationState | str,
        *,
        source_model: str,
        target_model: str,
        reason: str,
        context_capsule_ref: str = "",
        spend_estimate_usd: float = 0.0,
        actual_spend_usd: float = 0.0,
        policy_decision: str = "",
        verification_result: str = "",
        metadata: dict[str, Any] | None = None,
        expected_version: int | None = None,
        active_lane: str | None = None,
    ) -> ModelTransition:
        _init_tables()
        target_state = ModelOrchestrationState(to_state)
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM model_orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = ModelOrchestrationState(row["state"])
            if current in _TERMINAL or target_state not in _ALLOWED.get(current, set()):
                raise ValueError(f"invalid model transition: {current} -> {target_state}")
            version = int(row["version"]) + 1
            if expected_version is not None and int(row["version"]) != int(expected_version):
                raise RuntimeError("model orchestration state changed concurrently")
            now = _utcnow()
            conn.execute(
                """UPDATE model_orchestration_runs SET state = ?, active_lane = ?, active_model = ?,
                version = ?, updated_at = ? WHERE run_id = ? AND version = ?""",
                (target_state, active_lane if active_lane is not None else row["active_lane"], target_model, version, now, run_id, row["version"]),
            )
            transition = self._insert_transition(
                conn,
                run_id=run_id,
                task_id=row["task_id"],
                turn_id=row["turn_id"],
                subtask_id=row["subtask_id"],
                from_state=current,
                to_state=target_state,
                source_model=source_model,
                target_model=target_model,
                reason=reason,
                context_capsule_ref=context_capsule_ref,
                spend_estimate_usd=spend_estimate_usd,
                actual_spend_usd=actual_spend_usd,
                policy_decision=policy_decision,
                verification_result=verification_result,
                metadata=metadata,
                version=version,
            )
            conn.commit()
            return transition
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def current(self, run_id: str) -> dict[str, Any] | None:
        _init_tables()
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM model_orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def recover_interrupted_paid_runs(self) -> tuple[str, ...]:
        _init_tables()
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT run_id, active_model FROM model_orchestration_runs
                WHERE state IN (?, ?, ?)""",
                (
                    ModelOrchestrationState.PAID_REQUEST_PREPARING,
                    ModelOrchestrationState.PAID_RUNNING,
                    ModelOrchestrationState.PAID_VALIDATING,
                ),
            ).fetchall()
        finally:
            conn.close()
        recovered: list[str] = []
        for row in rows:
            self.transition(
                row["run_id"],
                ModelOrchestrationState.RETURNING_LOCAL,
                source_model=row["active_model"],
                target_model="",
                reason="restart_paid_call_not_resubmitted",
                policy_decision="billing_reconciliation_required",
                verification_result="unverified_after_restart",
            )
            self.transition(
                row["run_id"],
                ModelOrchestrationState.LOCAL_RESUMED,
                source_model=row["active_model"],
                target_model="",
                reason="restart_safe_local_restore",
                policy_decision="no_paid_retry",
                verification_result="requires_local_reverification",
                active_lane="LOCAL_DAILY",
            )
            recovered.append(row["run_id"])
        return tuple(recovered)

    @staticmethod
    def _insert_transition(conn, **values: Any) -> ModelTransition:
        transition = ModelTransition(
            transition_id=f"model-transition-{uuid.uuid4().hex}",
            run_id=str(values["run_id"]),
            task_id=str(values["task_id"]),
            turn_id=str(values["turn_id"]),
            subtask_id=str(values.get("subtask_id") or ""),
            from_state=str(values.get("from_state") or ""),
            to_state=str(values["to_state"]),
            source_model=str(values.get("source_model") or ""),
            target_model=str(values.get("target_model") or ""),
            reason=str(values.get("reason") or ""),
            context_capsule_ref=str(values.get("context_capsule_ref") or ""),
            spend_estimate_usd=float(values.get("spend_estimate_usd") or 0.0),
            actual_spend_usd=float(values.get("actual_spend_usd") or 0.0),
            policy_decision=str(values.get("policy_decision") or ""),
            verification_result=str(values.get("verification_result") or ""),
            created_at=_utcnow(),
            version=int(values["version"]),
        )
        conn.execute(
            """INSERT INTO model_orchestration_transitions
            (transition_id, run_id, task_id, turn_id, subtask_id, from_state, to_state, source_model,
             target_model, reason, context_capsule_ref, spend_estimate_usd, actual_spend_usd,
             policy_decision, verification_result, metadata_json, created_at, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                transition.transition_id, transition.run_id, transition.task_id, transition.turn_id,
                transition.subtask_id, transition.from_state, transition.to_state,
                transition.source_model, transition.target_model, transition.reason,
                transition.context_capsule_ref, transition.spend_estimate_usd, transition.actual_spend_usd,
                transition.policy_decision, transition.verification_result,
                json.dumps(values.get("metadata") or {}, sort_keys=True), transition.created_at, transition.version,
            ),
        )
        return transition


__all__ = ["ModelOrchestrationState", "ModelOrchestrationStore", "ModelTransition"]
