"""A routine's schedule: when it comes due, VOOL works out the list and tells the person.

It never runs anything by itself. At the chosen time (every day, or one day a week, in this computer's
local time) the poller makes the same list "Try it" shows, and when there is something to do it adds
one notification that opens the routine's confirm screen. The person still looks at the list and
chooses Run. A time missed while VOOL was closed is caught up once, when VOOL next runs; a routine
just given a schedule waits for its next time instead of firing for one already past.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.routines.model import RoutineError

_LOG = logging.getLogger(__name__)
TICK_SECONDS = 60.0
_LOCK = threading.Lock()


def _state_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "routines" / "schedule.json"


def _read() -> dict[str, dict[str, str]]:
    """Per routine: the schedule last seen (as text) and the last time slot handled."""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): {"schedule": str(row.get("schedule") or ""), "slot": str(row.get("slot") or "")}
            for key, row in data.items() if isinstance(row, dict)}


def _write(state: dict[str, dict[str, str]]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def latest_slot(schedule: dict[str, Any], now: datetime) -> str:
    """The most recent time this schedule came due at or before ``now`` (local), or "" when it has none."""
    every = str((schedule or {}).get("every") or "")
    if every not in {"daily", "weekly"}:
        return ""
    hour, minute = (int(part) for part in str(schedule.get("time") or "09:00").split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if every == "weekly":
        slot -= timedelta(days=(now.weekday() - int(schedule.get("weekday") or 0)) % 7)
    if slot > now:
        slot -= timedelta(days=1 if every == "daily" else 7)
    return slot.isoformat(timespec="minutes")


def _notify(routine: dict[str, Any], slot: str, *, changes: int, problem: str = "") -> None:
    from core.operator import notification_center

    name = routine["name"]
    if problem:
        title = f"{name} could not get ready"
        body = problem
    else:
        title = f"{name} is ready to run"
        body = (f"{changes} change is ready. Open My routines to look at it and run it." if changes == 1 else
                f"{changes} changes are ready. Open My routines to look at them and run them.")
    notification_center.record_item(
        dedupe_key=f"routine:{routine['id']}:{slot}",
        source_kind="background_run",
        title=title,
        body=body,
        # "Needs you": nothing runs until the person looks at the list and chooses Run.
        payload={"kind": "routine", "section": "needs", "routine_id": routine["id"], "name": name,
                 "changes": changes, "problem": bool(problem), "open": f"/routines?run={routine['id']}"},
    )


def tick(now: datetime | None = None) -> list[dict[str, Any]]:
    """One pass over the saved routines; what it prepared, for the log and the tests."""
    from core.routines import store
    from core.routines.plan import plan

    now = now or datetime.now()
    done: list[dict[str, Any]] = []
    with _LOCK:
        state = _read()
        changed = False
        alive = set()
        for routine in store.all_routines():
            alive.add(routine["id"])
            slot = latest_slot(routine["schedule"], now)
            if not slot:
                if routine["id"] in state:
                    state.pop(routine["id"])
                    changed = True
                continue
            shape = json.dumps(routine["schedule"], sort_keys=True)
            seen = state.get(routine["id"])
            if seen is None or seen["schedule"] != shape:
                # Newly scheduled, or the schedule changed: wait for its next time, never fire for one
                # already past.
                state[routine["id"]] = {"schedule": shape, "slot": slot}
                changed = True
                continue
            if seen["slot"] >= slot:
                continue
            state[routine["id"]] = {"schedule": shape, "slot": slot}
            changed = True
            try:
                prepared = plan(store.get(routine["id"]))
            except RoutineError as exc:
                _notify(routine, slot, changes=0, problem=exc.message)
                done.append({"routine_id": routine["id"], "slot": slot, "problem": exc.code})
                continue
            changes = len(prepared["operations"])
            if changes or prepared["needs_you"] or prepared["stop"]:
                _notify(routine, slot, changes=changes)
            done.append({"routine_id": routine["id"], "slot": slot, "changes": changes})
        for gone in set(state) - alive:
            state.pop(gone)
            changed = True
        if changed:
            _write(state)
    return done


class _Poller:
    def __init__(self, tick_seconds: float = TICK_SECONDS) -> None:
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="vool-routine-schedule", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                tick()
            except Exception:
                _LOG.exception("routine schedule pass failed; the next pass tries again")
            self._stop.wait(self.tick_seconds)


_DEFAULT: _Poller | None = None
_DEFAULT_LOCK = threading.Lock()


def start_default_poller() -> None:
    """Start the one process-wide schedule poller. Called by the served boot; safe to call twice."""
    global _DEFAULT

    with _DEFAULT_LOCK:
        if _DEFAULT is not None and _DEFAULT._thread is not None and _DEFAULT._thread.is_alive():
            return
        _DEFAULT = _Poller()
        _DEFAULT.start()


def stop_default_poller() -> None:
    global _DEFAULT

    with _DEFAULT_LOCK:
        if _DEFAULT is not None:
            _DEFAULT.stop()
        _DEFAULT = None


__all__ = ["TICK_SECONDS", "latest_slot", "start_default_poller", "stop_default_poller", "tick"]
