from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core import policy_engine
from core.contribution_proof import append_contribution_proof_receipt
from core.task_capsule import TaskCapsule
from network.signer import get_local_peer_id, sign
from storage.db import execute_query


class AssistCoordinator:
    """
    Manages the creation, assignment, and rewarding of tasks on the Assist Mesh.
    Enforces strict sanitization to prevent context leakage.
    """

    @staticmethod
    def create_task_capsule(
        task_record: dict[str, Any],
        subtask_type: str,
        summary: str,
        task_type: str = "research",
        allowed_operations: list[str] | None = None,
        privacy_level: str = "strict",
    ) -> TaskCapsule:
        """
        Builds a safe TaskCapsule from a raw local task.
        Strips all local paths, secrets, and raw execution details.
        """
        if allowed_operations is None:
            allowed_operations = ["reason", "research", "compare", "rank", "summarize", "validate"]

        local_peer_id = get_local_peer_id()
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(hours=1)

        # Sanitize context heavily
        sanitized_context = {
            "problem_class": task_record.get("task_class", "unknown"),
            "environment_tags": {
                "os": task_record.get("environment_os", "unknown"),
                "runtime": task_record.get("environment_runtime", "unknown"),
            },
            "abstract_inputs": [],
            "known_constraints": ["No raw execution permitted.", "Return only structured advice."],
        }

        # The hash uniquely identifies the *content* being asked, preventing duplicate spam.
        raw_to_hash = json.dumps(sanitized_context, sort_keys=True) + summary
        capsule_hash = hashlib.sha256(raw_to_hash.encode("utf-8")).hexdigest()

        reward_hint = {
            "points": 10,
            "wnull_pending": 0
        }

        capsule_dict = {
            "capsule_id": str(uuid.uuid4()),
            "task_id": task_record.get("task_id", str(uuid.uuid4())),
            "parent_agent_id": local_peer_id,
            "task_type": task_type,
            "subtask_type": subtask_type,
            "summary": summary,
            "sanitized_context": sanitized_context,
            "allowed_operations": allowed_operations,
            "forbidden_operations": [
                "execute", "write_files", "access_db", "request_secrets", "persist_data", "install_packages", "call_shell"
            ],
            "privacy_level": privacy_level,
            "max_response_bytes": 8192,
            "deadline_ts": deadline.isoformat(),
            "reward_hint": reward_hint,
            "capsule_hash": capsule_hash,
            "signature": "", # Will fill below
        }

        # Create string to sign
        unsigned = dict(capsule_dict)
        unsigned.pop("signature", None)
        to_sign = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")

        capsule_dict["signature"] = sign(to_sign)

        return TaskCapsule.model_validate(capsule_dict)

    @staticmethod
    def calculate_reward(
        helpfulness_score: float,
        quality_score: float,
        parent_trust: float,
        helper_trust: float,
        task_complexity: float = 0.5,
        duplication_penalty: float = 0.0,
        harmful_penalty: float = 0.0,
    ) -> dict[str, int]:
        """
        Calculates proof-of-usefulness reward.
        Returns dict with points and wnull_pending.
        """
        if harmful_penalty > 0:
            return {"points": 0, "wnull_pending": 0}

        score = (
            (0.30 * helpfulness_score)
            + (0.20 * quality_score)
            + (0.15 * parent_trust)
            + (0.10 * helper_trust)
            + (0.10 * task_complexity)
            - (0.25 * duplication_penalty)
        )

        score = max(0.0, min(1.0, score))

        # Base multipliers - in the future this would be dynamic
        base_points = 20
        base_wnull = 5

        return {
            "points": int(base_points * score),
            "wnull_pending": int(base_wnull * score)
        }

    @staticmethod
    def settle_contribution(
        task_id: str,
        helper_peer_id: str,
        parent_peer_id: str,
        outcome: str,
        helpfulness_score: float,
        quality_score: float,
        helper_trust: float = 0.5,
        parent_trust: float = 0.5
    ) -> str:
        """
        Logs an assist to the contribution ledger.
        If accepted, calculates pending rewards and sets the fraud window.
        """
        reward = {"points": 0, "wnull_pending": 0}
        fraud_window_end = None
        now = datetime.now(timezone.utc)

        is_harmful = outcome in ("harmful", "rejected")

        if outcome == "accepted":
            reward = AssistCoordinator.calculate_reward(
                helpfulness_score=helpfulness_score,
                quality_score=quality_score,
                parent_trust=parent_trust,
                helper_trust=helper_trust,
                harmful_penalty=1.0 if is_harmful else 0.0
            )
            # Default to medium fraud window (24h)
            window_hours = float(policy_engine.get("rewards_and_farming.fraud_window_hours_medium", 24))
            fraud_window_end = (now + timedelta(hours=window_hours)).isoformat()

        entry_id = str(uuid.uuid4())
        finality_target = max(1, int(policy_engine.get("economics.contribution_finality_target_depth", 2) or 2))
        finality_state = "pending" if outcome == "accepted" else "rejected"

        execute_query(
            """
            INSERT INTO contribution_ledger (
                entry_id, task_id, helper_peer_id, parent_peer_id,
                contribution_type, outcome, helpfulness_score,
                points_awarded, wnull_pending, wnull_released,
                finality_state, finality_depth, finality_target,
                slashed_flag, fraud_window_end_ts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                task_id,
                helper_peer_id,
                parent_peer_id,
                "assist",
                "pending" if outcome == "accepted" else "rejected",
                helpfulness_score,
                reward["points"],
                reward["wnull_pending"],
                0, # released
                finality_state,
                0,
                finality_target,
                1 if is_harmful else 0,
                fraud_window_end,
                now.isoformat(),
                now.isoformat()
            )
        )

        append_contribution_proof_receipt(
            entry_id=entry_id,
            task_id=task_id,
            helper_peer_id=helper_peer_id,
            parent_peer_id=parent_peer_id,
            stage=finality_state,
            outcome="pending" if outcome == "accepted" else "rejected",
            finality_state=finality_state,
            finality_depth=0,
            finality_target=finality_target,
            points_awarded=int(reward["points"] or 0),
            challenge_reason="harmful_or_rejected" if is_harmful else "",
            evidence={"fraud_window_end_ts": fraud_window_end or ""},
            created_at=now.isoformat(),
        )

        return entry_id
