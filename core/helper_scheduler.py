from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psutil

from core import audit_logger
from storage.db import get_connection


@dataclass
class SchedulerConfig:
    max_concurrent_mesh_tasks: int = 2
    max_cpu_percent: float = 85.0
    max_memory_percent: float = 85.0
    reserve_capacity_for_local_user: bool = True


class HelperScheduler:
    """
    Manages local execution capacity for mesh tasks versus local user requests.
    Prevents the node from accepting mesh work if it would starve the local user.
    """
    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig()

    def _table_exists(self, conn: Any, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (str(table),),
        ).fetchone()
        return bool(row)

    def _get_active_mesh_assignments(self) -> int:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM task_assignments
                WHERE status = 'active'
                """
            ).fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            conn.close()

    def _get_active_user_tasks(self) -> int:
        conn = get_connection()
        try:
            total = 0
            if self._table_exists(conn, "runtime_checkpoints"):
                row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt
                    FROM runtime_checkpoints
                    WHERE status IN ('running', 'pending_approval')
                    """
                ).fetchone()
                total += int(row["cnt"]) if row else 0
            if self._table_exists(conn, "operator_action_requests"):
                row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt
                    FROM operator_action_requests
                    WHERE status IN ('requested', 'pending', 'approved', 'running')
                    """
                ).fetchone()
                total += int(row["cnt"]) if row else 0
            if self._table_exists(conn, "local_tasks"):
                row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt
                    FROM local_tasks
                    WHERE outcome IN ('pending', 'running', 'in_progress')
                    """
                ).fetchone()
                total += int(row["cnt"]) if row else 0
            return total
        finally:
            conn.close()

    def can_accept_mesh_task(self) -> bool:
        """
        Determines if the node has the physical and configured capacity
        to accept and run a new helper task.
        """
        active_mesh = self._get_active_mesh_assignments()

        # Hard configured cap
        if active_mesh >= self.config.max_concurrent_mesh_tasks:
            return False

        # Soft capability cap (prioritize local user)
        if self.config.reserve_capacity_for_local_user:
            active_user = self._get_active_user_tasks()
            # If the user is actively waiting on tasks, we throttle mesh acceptance
            # depending on total capacity.
            available = self.config.max_concurrent_mesh_tasks - active_user
            if active_mesh >= max(0, available):
                return False

        # System resource limits
        cpu = psutil.cpu_percent(interval=0.1)
        if cpu > self.config.max_cpu_percent:
            audit_logger.log(
                "mesh_task_rejected_resources",
                target_id="cpu",
                target_type="system",
                details={"cpu_percent": cpu, "threshold": self.config.max_cpu_percent}
            )
            return False

        mem = psutil.virtual_memory().percent
        if mem > self.config.max_memory_percent:
            audit_logger.log(
                "mesh_task_rejected_resources",
                target_id="memory",
                target_type="system",
                details={"mem_percent": mem, "threshold": self.config.max_memory_percent}
            )
            return False

        return True

    def adjust_advertised_capacity(self, current_capacity: int) -> int:
        """
        Dynamically adjusts the advertised capacity for CAPABILITY_ADs based on load.
        """
        active_mesh = self._get_active_mesh_assignments()
        target = self.config.max_concurrent_mesh_tasks - active_mesh

        if self.config.reserve_capacity_for_local_user:
            active_user = self._get_active_user_tasks()
            target -= active_user

        return max(0, min(self.config.max_concurrent_mesh_tasks, target))
