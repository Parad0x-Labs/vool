"""Local meter and user limit for the X API, which X bills on the user's own account.

X charges per request and per post read. VOOL counts every request it sends with the user's keys,
the posts and accounts each answer carried, and the actions it sent (posts, likes, follows), per
calendar month (UTC). Nothing leaves the machine: the meter is a small JSON file under the VOOL
data folder.

Reads reserve their worst case before the request goes out (``reserve_reads``) and settle to what X
actually returned afterwards, and an action is counted before it is sent (``reserve_action``), both
under one lock, so parallel calls cannot run past a limit between the check and the count. The
held reads live in the meter file next to the counts, under a lock file shared by every VOOL
process, so two processes cannot each hold the same reads. A read whose answer was lost or cut off
after it went out keeps its whole held amount as an unsettled read (``record_uncertain_read``), and
so does a hold left behind by a process that ended before it settled.
The user sets a monthly limit on items read, posts and accounts together (Settings > API Keys > X). Reads are refused BEFORE the
request is sent once the limit is reached, and page sizes are clamped to what is left, so VOOL can
never run past the number the user chose. A missing or unreadable meter fails closed: VOOL refuses
reads rather than guess the count is zero. ``0`` means "no reads at all"; ``None`` means no limit.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from core.runtime_paths import data_path

_FILE = "x_api_usage.json"
DEFAULT_MONTHLY_READS = 1000
DEFAULT_MONTHLY_ACTIONS = 50
MAX_LIMIT = 10_000_000
_lock = threading.RLock()
_lock_depth = threading.local()
_READ_KEYS = ("posts_read", "users_read", "other_read", "uncertain_read")
LOCK_WAIT_S = 10.0
HOLD_STALE_S = 3600  # no read stays in flight this long; an older hold is from an ended process


class UsageUnreadable(Exception):
    pass


@contextmanager
def _meter_lock():
    """This process's lock plus the lock file every VOOL process shares. Waits briefly for another
    process to finish its update; raises LockUnavailable when it cannot be had."""
    from core.cross_process_lock import LockUnavailable, PublicationLock

    with _lock:
        if getattr(_lock_depth, "held", False):
            yield
            return
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            held = PublicationLock(_path().with_suffix(".lock"))
            try:
                held.__enter__()
                break
            except LockUnavailable:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        _lock_depth.held = True
        try:
            yield
        finally:
            _lock_depth.held = False
            held.__exit__(None, None, None)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _settle_ended_holds(state: dict[str, Any]) -> None:
    """Holds of processes that ended (or that are too old to be in flight) may have been served by
    X: they stay counted as unsettled reads instead of vanishing."""
    holds = state.get("holds") if isinstance(state.get("holds"), dict) else {}
    now = time.time()
    for key, hold in list(holds.items()):
        try:
            pid, amount, at = int(key), int(hold.get("n") or 0), float(hold.get("at") or 0)
        except (TypeError, ValueError, AttributeError):
            pid, amount, at = 0, 0, 0.0
        if pid == os.getpid() and now - at < HOLD_STALE_S:
            continue
        if pid > 0 and pid != os.getpid() and _alive(pid) and now - at < HOLD_STALE_S:
            continue
        holds.pop(key, None)
        state["uncertain_read"] = int(state.get("uncertain_read") or 0) + max(0, amount)
    state["holds"] = holds


def _held(state: dict[str, Any]) -> int:
    holds = state.get("holds") if isinstance(state.get("holds"), dict) else {}
    total = 0
    for hold in holds.values():
        try:
            total += max(0, int(hold.get("n") or 0))
        except (TypeError, ValueError, AttributeError):
            continue
    return total


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _path():
    return data_path(_FILE)


def _blank(month: str, limits: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"month": month, "requests": 0, "posts_read": 0, "users_read": 0, "other_read": 0, "actions": 0,
            "by_endpoint": {}, "limits": dict(limits or {
                "monthly_reads": DEFAULT_MONTHLY_READS,
                "monthly_actions": DEFAULT_MONTHLY_ACTIONS})}


def _load() -> dict[str, Any]:
    path = _path()
    month = _month()
    if not path.exists():
        return _blank(month)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UsageUnreadable() from None
    if not isinstance(state, dict) or not isinstance(state.get("limits"), dict):
        raise UsageUnreadable()
    if state.get("month") != month:
        history = list(state.get("history") or [])[-11:]
        history.append({k: state.get(k) for k in ("month", "requests", *_READ_KEYS, "actions")})
        fresh = _blank(month, state["limits"])
        fresh["history"] = history
        if isinstance(state.get("holds"), dict):
            fresh["holds"] = state["holds"]  # reads still in flight across the month change
        return fresh
    return state


def _save(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _limit(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, min(MAX_LIMIT, int(value)))
    except (TypeError, ValueError):
        return 0  # an unreadable limit fails closed


def snapshot() -> dict[str, Any]:
    """This month's counts and the user's limits (for Settings and the x.usage tool)."""
    with _lock:
        try:
            state = _load()
        except UsageUnreadable:
            return {"ok": False, "state": "unreadable",
                    "message": "VOOL could not read its X usage meter, so X reads are paused. Reset the meter in Settings > API Keys > X."}
    limits = state["limits"]
    read_limit = _limit(limits.get("monthly_reads", DEFAULT_MONTHLY_READS))
    reads = sum(int(state.get(k) or 0) for k in _READ_KEYS) + _held(state)
    action_limit = _limit(limits.get("monthly_actions", DEFAULT_MONTHLY_ACTIONS))
    return {
        "ok": True, "month": state["month"], "requests": int(state.get("requests") or 0),
        "posts_read": int(state.get("posts_read") or 0), "users_read": int(state.get("users_read") or 0),
        "uncertain_read": int(state.get("uncertain_read") or 0),
        "actions": int(state.get("actions") or 0),
        "monthly_reads": read_limit, "monthly_actions": action_limit,
        "reads": reads,
        "reads_left": None if read_limit is None else max(0, read_limit - reads),
        "actions_left": None if action_limit is None else max(0, action_limit - int(state.get("actions") or 0)),
        "by_endpoint": dict(state.get("by_endpoint") or {}),
        "history": list(state.get("history") or []),
    }


def reads_left() -> int | None:
    snap = snapshot()
    if not snap.get("ok"):
        return 0
    return snap["reads_left"]


def refusal() -> str:
    """A plain refusal when the next read must not go out; '' when it may."""
    snap = snapshot()
    if not snap.get("ok"):
        return snap["message"]
    if snap["reads_left"] is not None and snap["reads_left"] <= 0:
        return (f"Your X read limit for {snap['month']} is used up ({snap['reads']} of "
                f"{snap['monthly_reads']} posts and accounts read). Nothing was sent to X. You can raise the limit in "
                "Settings > API Keys > X.")
    return ""


def action_refusal() -> str:
    snap = snapshot()
    if not snap.get("ok"):
        return snap["message"]
    if snap["actions_left"] is not None and snap["actions_left"] <= 0:
        return (f"Your X send limit for {snap['month']} is used up ({snap['actions']} of "
                f"{snap['monthly_actions']}). Nothing was sent to X. You can raise it in Settings > API Keys > X.")
    return ""


def _bump(**counts: int) -> None:
    from core.cross_process_lock import LockUnavailable

    try:
        with _meter_lock():
            _bump_locked(counts)
    except LockUnavailable:
        # Another process kept the meter locked past the wait. The read's hold is still in the
        # file, so it stays counted and is settled as an unsettled read once this process ends.
        return


def _bump_locked(counts: dict[str, Any]) -> None:
    try:
        state = _load()
    except UsageUnreadable:
        return  # refusal() already stops further reads; never overwrite an unreadable meter
    endpoint = counts.pop("endpoint", None)
    for key, value in counts.items():
        state[key] = int(state.get(key) or 0) + int(value)
    if endpoint:
        by = state.setdefault("by_endpoint", {})
        by[endpoint] = int(by.get(endpoint) or 0) + 1
    _save(state)


def _endpoint_family(path: str) -> str:
    parts = [p for p in str(path).split("/") if p]
    # /2/users/123/tweets -> users/:id/tweets ; ids never stored
    return "/".join(":id" if p.isdigit() else p for p in parts[1:4])


def record_request(path: str) -> None:
    _bump(requests=1, endpoint=_endpoint_family(path))


def record_read(*, posts: int = 0, users: int = 0, other: int = 0) -> None:
    if posts or users or other:
        _bump(posts_read=max(0, posts), users_read=max(0, users), other_read=max(0, other))


def record_uncertain_read(amount: int) -> None:
    """A read went out and its answer was lost or unusable: X may have served all it held, so the
    held amount counts against the limit."""
    if int(amount or 0) > 0:
        _bump(uncertain_read=int(amount))


def reserve_reads(worst_case: int) -> str:
    """Hold ``worst_case`` reads against the limit before a request is sent. '' when held, else
    the refusal to show. Every successful reserve must be settled with ``release_reads``."""
    from core.cross_process_lock import LockUnavailable

    worst_case = max(1, int(worst_case))
    try:
        with _meter_lock():
            try:
                state = _load()
            except UsageUnreadable:
                return snapshot()["message"]
            _settle_ended_holds(state)
            _save(state)
            snap = snapshot()
            if not snap.get("ok"):
                return snap["message"]
            left = snap["reads_left"]
            if left is not None and worst_case > left:
                if left <= 0:
                    return refusal() or "Your X read limit for this month is used up. Nothing was sent to X."
                return (f"That X read could return up to {worst_case} posts and accounts, and only {left} are left under "
                        "your monthly limit. Nothing was sent to X. Ask for fewer, or raise the limit in Settings > API Keys > X.")
            holds = state.setdefault("holds", {})
            mine = holds.get(str(os.getpid())) if isinstance(holds.get(str(os.getpid())), dict) else {}
            holds[str(os.getpid())] = {"n": max(0, int(mine.get("n") or 0)) + worst_case, "at": time.time()}
            _save(state)
    except LockUnavailable:
        return "VOOL could not update its X usage meter just now. Nothing was sent to X. Try again."
    return ""


def release_reads(worst_case: int) -> None:
    from core.cross_process_lock import LockUnavailable

    try:
        with _meter_lock():
            try:
                state = _load()
            except UsageUnreadable:
                return
            holds = state.get("holds") if isinstance(state.get("holds"), dict) else {}
            mine = holds.get(str(os.getpid()))
            if isinstance(mine, dict):
                left = max(0, int(mine.get("n") or 0) - max(1, int(worst_case)))
                if left:
                    holds[str(os.getpid())] = {**mine, "n": left}
                else:
                    holds.pop(str(os.getpid()), None)
            state["holds"] = holds
            _save(state)
    except LockUnavailable:
        return  # the hold stays in the file and is settled once this process ends


def reserve_action() -> str:
    """Count one action before it is sent. '' when counted, else the refusal to show."""
    from core.cross_process_lock import LockUnavailable

    try:
        with _meter_lock():
            refused = action_refusal()
            if refused:
                return refused
            record_action()
    except LockUnavailable:
        return "VOOL could not update its X usage meter just now. Nothing was sent to X. Try again."
    return ""


def unreserve_action() -> None:
    """Give back an action that was counted but provably never left this machine."""
    from core.cross_process_lock import LockUnavailable

    try:
        with _meter_lock():
            _unreserve_locked()
    except LockUnavailable:
        return  # keeping the count is the safe side


def _unreserve_locked() -> None:
    try:
        state = _load()
    except UsageUnreadable:
        return
    state["actions"] = max(0, int(state.get("actions") or 0) - 1)
    state["requests"] = max(0, int(state.get("requests") or 0) - 1)
    _save(state)


def record_action() -> None:
    _bump(actions=1, requests=1)


def set_limits(*, monthly_reads: Any = "keep", monthly_actions: Any = "keep") -> dict[str, Any]:
    """Change the user's limits. A value of None (or "") removes that limit."""
    def parse(value: Any) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError
            value = int(value)
        if isinstance(value, str) and not value.strip().isdigit():
            raise ValueError
        number = int(value)
        if number < 0 or number > MAX_LIMIT:
            raise ValueError
        return number

    from core.cross_process_lock import LockUnavailable

    try:
        with _meter_lock():
            return _set_limits_locked(parse, monthly_reads, monthly_actions)
    except LockUnavailable:
        return {"ok": False, "state": "busy",
                "message": "VOOL could not update its X usage meter just now, so your limits were not changed. Try again."}


def _set_limits_locked(parse, monthly_reads: Any, monthly_actions: Any) -> dict[str, Any]:
    try:
        state = _load()
    except UsageUnreadable:
        # Saving limits must never zero a meter VOOL could not read: that would hand back the
        # whole month's budget without the user choosing to.
        return {"ok": False, "state": "unreadable",
                "message": "VOOL could not read its X usage count, so your limits were not changed. "
                           "Use \"Start counting from zero\" first if you want to reset it."}
    limits = dict(state.get("limits") or {})
    try:
        if monthly_reads != "keep":
            limits["monthly_reads"] = parse(monthly_reads)
        if monthly_actions != "keep":
            limits["monthly_actions"] = parse(monthly_actions)
    except (TypeError, ValueError):
        return {"ok": False, "state": "invalid",
                "message": f"Use a whole number from 0 to {MAX_LIMIT:,}, or leave it empty for no limit."}
    state["limits"] = limits
    _save(state)
    return {"ok": True, **snapshot()}


def reset_meter() -> dict[str, Any]:
    """Start the meter again (keeps the limits when they are readable, and the reads still in
    flight, which settle against the new count)."""
    from core.cross_process_lock import LockUnavailable

    try:
        with _meter_lock():
            try:
                old = _load()
                limits, holds = old["limits"], old.get("holds")
            except UsageUnreadable:
                limits, holds = None, None
            fresh = _blank(_month(), limits)
            if isinstance(holds, dict):
                fresh["holds"] = holds
            _save(fresh)
    except LockUnavailable:
        return {"ok": False, "state": "busy",
                "message": "VOOL could not update its X usage meter just now, so it was not reset. Try again."}
    return {"ok": True, **snapshot()}


__all__ = [
    "DEFAULT_MONTHLY_ACTIONS",
    "DEFAULT_MONTHLY_READS",
    "action_refusal",
    "reads_left",
    "record_action",
    "record_read",
    "record_request",
    "record_uncertain_read",
    "refusal",
    "release_reads",
    "reserve_action",
    "reserve_reads",
    "reset_meter",
    "set_limits",
    "snapshot",
    "unreserve_action",
]
