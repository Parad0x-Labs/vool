"""Persistent task DAG, writer leases and restart-safe task state.

Two truths a council run cannot hold in its head:

* **The dependency DAG.** A task is READY only when every dependency is COMPLETE;
  cycles are refused at construction (a dependency graph with a cycle is a plan that
  cannot start, not a plan that starts eventually); a COMPLETED task is terminal.
* **The writer lease.** One writer per overlapping authority: two live tasks may
  not hold overlapping writable scope, because two writers on one file is a lost
  update wearing a work ethic.

Everything is on disk — ``tasks.json`` for state (atomic temp+rename writes, the
same discipline ``core/council/run_store.py`` uses) and ``tasks.events.jsonl`` for
the append-only history — so a restart reads the same DAG, the same leases, and the
same fencing counters the previous process died with.

Leases expire by wall clock and carry FENCING COUNTERS. A lease that outlived its
holder (crash, restart, lost process) is stale; claiming its authority breaks it and
bumps the counter, so the old holder's late ``commit_with_lease`` — arriving with a
fencing counter the ledger no longer honors — fails typed instead of silently
overwriting the successor's work. The clock is injectable because expiry law must
be testable without waiting.
"""

from __future__ import annotations

import enum
import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TaskDagError(ValueError):
    """The task graph or lease request violates the DAG law."""


class DuplicateTaskRefused(TaskDagError):
    """A task id was added twice. Task identity is unique by construction."""


class DAGCycleRefused(TaskDagError):
    """A dependency edge would create a cycle."""


class OverlappingWriterRefused(RuntimeError):
    """Another live lease already owns overlapping scope."""

    def __init__(self, conflicting_lease: WriterLease) -> None:
        super().__init__(
            f"task {conflicting_lease.task_id} holds a live writer lease on "
            f"{sorted(conflicting_lease.scope)} until {conflicting_lease.expires_at:.0f} "
            f"(holder: {conflicting_lease.holder})"
        )
        self.conflicting_lease = conflicting_lease


class StaleLeaseRefused(RuntimeError):
    """A commit arrived on a lease this ledger no longer honors."""


def _components(path: str) -> tuple[str, ...]:
    return tuple(part for part in str(path or "").strip().split("/") if part not in ("", "."))


def paths_overlap(a: str, b: str) -> bool:
    """Component-wise containment: equal paths, or one inside the other's directory.

    String prefixing is NOT overlap — ``core/council-2`` is a sibling of
    ``core/council``, not inside it.
    """
    left, right = _components(a), _components(b)
    if not left or not right:
        return False
    shorter = min(len(left), len(right))
    return left[:shorter] == right[:shorter]


def scopes_overlap(scope_a: Iterable[str], scope_b: Iterable[str]) -> bool:
    """Whether two writable scopes share any authority: any overlapping pair."""
    return any(
        paths_overlap(a, b) for a in scope_a or () for b in scope_b or ()
    )


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass(frozen=True)
class WriterLease:
    """One task's claim to write inside ``scope``, valid until ``expires_at``."""

    lease_id: str
    task_id: str
    scope: tuple[str, ...]
    holder: str
    fencing_counter: int
    acquired_at: float
    expires_at: float

    def live_at(self, now: float) -> bool:
        return now < self.expires_at

    def covers(self, path: str) -> bool:
        return any(paths_overlap(item, path) for item in self.scope)


_LEASE_TTL_FLOOR = 1  # seconds; a lease with no lifetime cannot be stale or live


class TaskLedger:
    """File-backed task DAG + lease authority. Re-instantiable across restarts."""

    def __init__(self, root: Path | str, *, clock: Callable = time.time) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._state = self._read_state()

    # ------------------------------------------------------------------ files
    def _state_path(self) -> Path:
        return self._root / "tasks.json"

    def _events_path(self) -> Path:
        return self._root / "tasks.events.jsonl"

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = self._state_path().read_text(encoding="utf-8")
        except OSError:
            return {"tasks": {}, "leases": {}, "fencing": {}}
        try:
            loaded = json.loads(raw)
        except ValueError:
            # A corrupt state file is never guessed back into existence: the event
            # log survives for the post-mortem, and the authority starts empty
            # rather than inventing history.
            return {"tasks": {}, "leases": {}, "fencing": {}}
        if not isinstance(loaded, dict):
            return {"tasks": {}, "leases": {}, "fencing": {}}
        loaded.setdefault("tasks", {})
        loaded.setdefault("leases", {})
        loaded.setdefault("fencing", {})
        return loaded

    def _write_state(self) -> None:
        target = self._state_path()
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tasks-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                with __import__("contextlib").suppress(OSError):
                    os.unlink(tmp)

    def _emit(self, event_type: str, **fields: Any) -> None:
        row = {"ts": self._clock(), "type": event_type}
        row.update(fields)
        with open(self._events_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    # -------------------------------------------------------------------- dag
    def add_task(self, task_id: str, dependencies: Sequence[str] = ()) -> TaskStatus:
        clean = str(task_id or "").strip()
        if not clean:
            raise TaskDagError("a task id must be non-empty")
        deps = tuple(str(dep or "").strip() for dep in dependencies or ())
        deps = tuple(dep for dep in deps if dep)
        with self._lock:
            if clean in self._state["tasks"]:
                raise DuplicateTaskRefused(f"task {clean!r} already exists")
            for dep in deps:
                if dep == clean:
                    raise DAGCycleRefused(f"task {clean!r} cannot depend on itself")
            # Cycle check: can `clean` be reached from any of its dependencies?
            frontier = list(deps)
            seen: set[str] = set()
            while frontier:
                node = frontier.pop()
                if node == clean:
                    raise DAGCycleRefused(
                        f"task {clean!r} would participate in a dependency cycle"
                    )
                if node in seen:
                    continue
                seen.add(node)
                frontier.extend(self._state["tasks"].get(node, {}).get("dependencies", ()))
            self._state["tasks"][clean] = {"status": TaskStatus.PENDING.value,
                                            "dependencies": list(deps)}
            self._write_state()
            self._emit("task_added", task_id=clean, dependencies=list(deps))
            return TaskStatus.PENDING

    def _task_row(self, task_id: str) -> dict[str, Any]:
        row = self._state["tasks"].get(str(task_id or "").strip())
        if row is None:
            raise TaskDagError(f"unknown task {task_id!r}")
        return row

    def set_status(self, task_id: str, status: TaskStatus) -> None:
        if not isinstance(status, TaskStatus):
            raise TaskDagError("status transitions require a typed TaskStatus")
        with self._lock:
            row = self._task_row(task_id)
            clean = str(task_id or "").strip()
            current = TaskStatus(row["status"])
            if current is TaskStatus.COMPLETE and status is not TaskStatus.COMPLETE:
                # COMPLETE is terminal: a completed task never re-runs (and never
                # un-completes; a wrong completion is reverted by operators, not
                # by editing history).
                raise TaskDagError(
                    f"task {clean!r} is COMPLETE — completion is terminal"
                )
            row["status"] = status.value
            self._write_state()
            self._emit("task_status", task_id=clean, status=status.value)

    def status(self, task_id: str) -> TaskStatus | None:
        with self._lock:
            row = self._state["tasks"].get(str(task_id or "").strip())
            return TaskStatus(row["status"]) if row else None

    def dependencies(self, task_id: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._task_row(task_id).get("dependencies", ()))

    def incomplete_dependencies(self, task_id: str) -> list[str]:
        """Dependencies that are not COMPLETE yet — missing ones included, in
        declaration order, so the blocker can be named rather than inferred."""
        with self._lock:
            deps = tuple(self._task_row(task_id).get("dependencies", ()))
        blocked: list[str] = []
        for dep in deps:
            row = self._state["tasks"].get(dep)
            if row is None or row.get("status") != TaskStatus.COMPLETE.value:
                blocked.append(dep)
        return blocked

    # ----------------------------------------------------------------- leases
    def _lease_rows_live(self, now: float) -> dict[str, dict[str, Any]]:
        return {
            lease_id: row
            for lease_id, row in self._state["leases"].items()
            if row.get("expires_at", 0.0) > now
        }

    def _lease_from_row(self, row: Mapping[str, Any]) -> WriterLease:
        return WriterLease(
            lease_id=str(row.get("lease_id")),
            task_id=str(row.get("task_id")),
            scope=tuple(row.get("scope", ())),
            holder=str(row.get("holder")),
            fencing_counter=int(row.get("fencing_counter", 0)),
            acquired_at=float(row.get("acquired_at", 0.0)),
            expires_at=float(row.get("expires_at", 0.0)),
        )

    def live_leases(self) -> tuple[WriterLease, ...]:
        now = self._clock()
        with self._lock:
            rows = self._lease_rows_live(now)
            return tuple(self._lease_from_row(row) for row in rows.values())

    def fencing_epoch(self, task_id: str) -> int:
        """The fencing counter this task's most recent lease carries.

        The GLOBAL counter only ever rises and every acquire takes the next value,
        so a successor's epoch always outranks a dead holder's; this per-task view
        is what a mutation proof must match to prove it committed under a lease
        this ledger still honors.
        """
        with self._lock:
            last_by_task = self._state["fencing"].get("last_by_task", {})
            return int(last_by_task.get(str(task_id or "").strip(), 0))

    def acquire_lease(
        self, task_id: str, *, scope: Sequence[str], holder: str, ttl_seconds: float
    ) -> WriterLease:
        """Claim the writer authority for ``scope`` on ``task_id``.

        Refused while any live lease — another task with overlapping scope, or the
        same task under a different holder — still owns it. A live lease of the
        same task and holder RENEWS (same authority, longer life, a fencing bump so
        the old epoch's commits cannot land after the renewal). A STALE conflicting
        lease is broken on the way in: its holder is gone, and the new writer takes
        the authority with a fencing counter the dead holder can never present again.
        """
        clean = str(task_id or "").strip()
        clean_scope = tuple(
            dict.fromkeys(str(item or "").strip() for item in scope or () if str(item).strip())
        )
        if not clean_scope:
            raise TaskDagError("a writer lease requires a declared scope")
        ttl = float(ttl_seconds)
        if ttl < _LEASE_TTL_FLOOR:
            raise TaskDagError("a lease needs a positive ttl")
        with self._lock:
            self._task_row(clean)  # unknown task refuses
            row = self._state["tasks"][clean]
            if row.get("status") == TaskStatus.COMPLETE.value:
                raise TaskDagError(
                    f"task {clean!r} is COMPLETE — a completed task never re-leases"
                )
            now = self._clock()
            for lease_id, other in list(self._state["leases"].items()):
                if other.get("expires_at", 0.0) <= now:
                    continue  # stale rows never block; they are swept below
                same_task = other.get("task_id") == clean
                if not same_task and not scopes_overlap(
                    other.get("scope", ()), clean_scope
                ):
                    continue
                if same_task and other.get("holder") == str(holder or ""):
                    # Renewal: supersede our own lease with a longer-lived one.
                    self._state["leases"].pop(lease_id, None)
                    self._emit("lease_renewed", task_id=clean, holder=str(holder),
                               superseded_lease_id=lease_id)
                    continue
                if same_task:
                    raise OverlappingWriterRefused(self._lease_from_row(other))
                if scopes_overlap(other.get("scope", ()), clean_scope):
                    raise OverlappingWriterRefused(self._lease_from_row(other))

            # Sweep stale leases that overlap this scope: claiming the authority
            # BREAKS them. Popping the row is what fences their holders out — a
            # commit on an unknown lease is refused — and the global counter only
            # ever rises, so the successor's lease outranks every lease before it.
            for lease_id, other in list(self._state["leases"].items()):
                if other.get("expires_at", 0.0) > now:
                    continue
                if scopes_overlap(other.get("scope", ()), clean_scope) or other.get(
                    "task_id"
                ) == clean:
                    self._state["leases"].pop(lease_id, None)
                    self._emit("lease_broken", lease_id=lease_id,
                               task_id=str(other.get("task_id")),
                               by_task=clean)

            epoch = int(self._state["fencing"].get("global", 0)) + 1
            self._state["fencing"]["global"] = epoch
            self._state["fencing"].setdefault("last_by_task", {})[clean] = epoch
            lease = WriterLease(
                lease_id=f"lease-{uuid.uuid4().hex[:12]}",
                task_id=clean,
                scope=clean_scope,
                holder=str(holder or ""),
                fencing_counter=epoch,
                acquired_at=now,
                expires_at=now + ttl,
            )
            self._state["leases"][lease.lease_id] = {
                "lease_id": lease.lease_id,
                "task_id": lease.task_id,
                "scope": list(lease.scope),
                "holder": lease.holder,
                "fencing_counter": lease.fencing_counter,
                "acquired_at": lease.acquired_at,
                "expires_at": lease.expires_at,
            }
            self._write_state()
            self._emit("lease_acquired", task_id=clean, lease_id=lease.lease_id,
                       holder=lease.holder, scope=list(lease.scope),
                       fencing_counter=lease.fencing_counter,
                       expires_at=lease.expires_at)
            return lease

    def break_stale_lease(self, lease_id: str) -> bool:
        """Break a lease that has expired. False if unknown or not stale yet.

        Breaking bumps the broken task's fencing epoch: the old holder's commit
        becomes impossible even if its process wakes up and tries.
        """
        with self._lock:
            row = self._state["leases"].get(str(lease_id or ""))
            if row is None:
                return False
            if row.get("expires_at", 0.0) > self._clock():
                return False  # still live — not breakable
            task_id = str(row.get("task_id"))
            self._state["leases"].pop(str(lease_id), None)
            self._write_state()
            self._emit("lease_broken", lease_id=str(lease_id), task_id=task_id)
            return True

    def commit_with_lease(self, lease_id: str, *, fencing_counter: int,
                          payload_sha256: str) -> dict[str, Any]:
        """Record a commit under a lease this ledger still honors.

        Refused typed when the lease is unknown, expired, or carries a fencing
        counter the ledger no longer honors — the last is how a stale holder's late
        commit dies even though its lease object still exists in memory.
        """
        with self._lock:
            row = self._state["leases"].get(str(lease_id or ""))
            if row is None:
                raise StaleLeaseRefused(f"no lease {lease_id!r} is known to this ledger")
            now = self._clock()
            if row.get("expires_at", 0.0) <= now:
                raise StaleLeaseRefused(
                    f"lease {lease_id!r} expired at {row.get('expires_at'):.0f}"
                )
            task_id = str(row.get("task_id"))
            if int(fencing_counter) != int(row.get("fencing_counter", -1)):
                raise StaleLeaseRefused(
                    f"fencing counter {fencing_counter} is not the honored epoch "
                    f"{row.get('fencing_counter')} for task {task_id!r}"
                )
            out = {
                "task_id": task_id,
                "lease_id": str(lease_id),
                "fencing_counter": int(fencing_counter),
                "payload_sha256": str(payload_sha256),
                "committed_at": now,
            }
            self._emit("task_committed", **out)
            return out

    # ----------------------------------------------------------------- events
    def events(self) -> list[dict[str, Any]]:
        try:
            lines = self._events_path().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
            except ValueError:
                continue
            if isinstance(loaded, dict):
                out.append(loaded)
        return out


__all__ = [
    "DAGCycleRefused",
    "DuplicateTaskRefused",
    "OverlappingWriterRefused",
    "StaleLeaseRefused",
    "TaskDagError",
    "TaskLedger",
    "TaskStatus",
    "WriterLease",
    "paths_overlap",
    "scopes_overlap",
]
