"""Local meter and user limit for the X API, which X bills on the user's own account.

X charges per request and per post read. VOOL counts every request it sends with the user's keys,
the posts and accounts each answer carried, and the actions it sent (posts, likes, follows), per
calendar month (UTC). Nothing leaves the machine: the meter is a small JSON file under the VOOL
data folder.

Reads reserve their worst case before the request goes out (``reserve_reads``) and settle to what X
actually returned afterwards, and an action is counted before it is sent (``reserve_action``), both
under one lock, so parallel calls cannot run past a limit between the check and the count.
The user sets a monthly limit on items read, posts and accounts together (Settings > API Keys > X). Reads are refused BEFORE the
request is sent once the limit is reached, and page sizes are clamped to what is left, so VOOL can
never run past the number the user chose. A missing or unreadable meter fails closed: VOOL refuses
reads rather than guess the count is zero. ``0`` means "no reads at all"; ``None`` means no limit.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from core.runtime_paths import data_path

_FILE = "x_api_usage.json"
DEFAULT_MONTHLY_READS = 1000
DEFAULT_MONTHLY_ACTIONS = 50
MAX_LIMIT = 10_000_000
_lock = threading.RLock()
_reserved_reads = 0  # in-flight worst cases, held until each read settles


class UsageUnreadable(Exception):
    pass


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
        history.append({k: state.get(k) for k in ("month", "requests", "posts_read", "users_read", "other_read", "actions")})
        fresh = _blank(month, state["limits"])
        fresh["history"] = history
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
    reads = sum(int(state.get(k) or 0) for k in ("posts_read", "users_read", "other_read")) + _reserved_reads
    action_limit = _limit(limits.get("monthly_actions", DEFAULT_MONTHLY_ACTIONS))
    return {
        "ok": True, "month": state["month"], "requests": int(state.get("requests") or 0),
        "posts_read": int(state.get("posts_read") or 0), "users_read": int(state.get("users_read") or 0),
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
        return (f"Your X action limit for {snap['month']} is used up ({snap['actions']} of "
                f"{snap['monthly_actions']}). Nothing was sent to X. You can raise it in Settings > API Keys > X.")
    return ""


def _bump(**counts: int) -> None:
    with _lock:
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


def reserve_reads(worst_case: int) -> str:
    """Hold ``worst_case`` reads against the limit before a request is sent. '' when held, else
    the refusal to show. Every successful reserve must be settled with ``release_reads``."""
    global _reserved_reads
    worst_case = max(1, int(worst_case))
    with _lock:
        snap = snapshot()
        if not snap.get("ok"):
            return snap["message"]
        left = snap["reads_left"]
        if left is not None and worst_case > left:
            if left <= 0:
                return refusal() or "Your X read limit for this month is used up. Nothing was sent to X."
            return (f"That X read could return up to {worst_case} posts and accounts, and only {left} are left under "
                    "your monthly limit. Nothing was sent to X. Ask for fewer, or raise the limit in Settings > API Keys > X.")
        _reserved_reads += worst_case
    return ""


def release_reads(worst_case: int) -> None:
    global _reserved_reads
    with _lock:
        _reserved_reads = max(0, _reserved_reads - max(1, int(worst_case)))


def reserve_action() -> str:
    """Count one action before it is sent. '' when counted, else the refusal to show."""
    with _lock:
        refused = action_refusal()
        if refused:
            return refused
        record_action()
    return ""


def unreserve_action() -> None:
    """Give back an action that was counted but provably never left this machine."""
    with _lock:
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

    with _lock:
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
    """Start the meter again (keeps the limits when they are readable)."""
    with _lock:
        try:
            limits = _load()["limits"]
        except UsageUnreadable:
            limits = None
        _save(_blank(_month(), limits))
    return {"ok": True, **snapshot()}


__all__ = [
    "DEFAULT_MONTHLY_ACTIONS",
    "DEFAULT_MONTHLY_READS",
    "action_refusal",
    "reads_left",
    "record_action",
    "record_read",
    "record_request",
    "refusal",
    "release_reads",
    "reserve_action",
    "reserve_reads",
    "reset_meter",
    "set_limits",
    "snapshot",
    "unreserve_action",
]
