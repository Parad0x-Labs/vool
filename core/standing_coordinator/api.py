"""Owner-facing surface: watch, unwatch, status, decide, decisions, forget.

Each function returns ``(http_status, body)`` like ``core.council.api``. Input is typed
and unknown fields are refused, so a mistyped request never runs on defaults.
"""

from __future__ import annotations

import threading
from typing import Any

from core.standing_coordinator.coordinator import REMEMBER_CHOICES, StandingCoordinator
from core.standing_coordinator.model import StandingCoordinatorError, WatchPolicy

_LOCK = threading.Lock()
_DEFAULT: StandingCoordinator | None = None


def default_coordinator() -> StandingCoordinator:
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is None:
            _DEFAULT = StandingCoordinator()
        return _DEFAULT


def set_default_coordinator_for_tests(coordinator: StandingCoordinator | None) -> None:
    global _DEFAULT
    with _LOCK:
        _DEFAULT = coordinator


def _refuse_unknown(body: dict[str, Any], allowed: set[str]) -> tuple[int, dict[str, Any]] | None:
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "body must be an object"}
    unknown = set(body) - allowed
    if unknown:
        return 400, {"ok": False, "error": f"unknown fields: {sorted(unknown)}"}
    return None


def _guard(fn, *args, **kwargs) -> tuple[int, dict[str, Any]]:
    try:
        return 200, fn(*args, **kwargs)
    except StandingCoordinatorError as exc:
        text = str(exc)
        code = 404 if text.startswith(("no such team", "this team is not watched", "no agent")) else 400
        return code, {"ok": False, "error": text}


def watch(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    refused = _refuse_unknown(body, {"team_id", "policy", "session_id"})
    if refused:
        return refused
    team_id = str(body.get("team_id") or "").strip()
    try:
        policy = WatchPolicy.from_row(body.get("policy")) if body.get("policy") is not None else WatchPolicy()
    except StandingCoordinatorError as exc:
        return 400, {"ok": False, "error": str(exc)}
    return _guard(default_coordinator().watch, team_id, policy, session_id=str(body.get("session_id") or ""))


def unwatch(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    refused = _refuse_unknown(body, {"team_id"})
    if refused:
        return refused
    return _guard(default_coordinator().unwatch, str(body.get("team_id") or "").strip())


def status(team_id: str) -> tuple[int, dict[str, Any]]:
    return _guard(default_coordinator().status, str(team_id or "").strip())


def watches() -> tuple[int, dict[str, Any]]:
    rows = []
    for row in default_coordinator().store.active_watches():
        rows.append({"team_id": row["team_id"], "session_id": row["session_id"], "created_at": row["created_at"],
                     "policy": row["policy"].as_row()})
    return 200, {"ok": True, "watches": rows}


def decide(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    refused = _refuse_unknown(body, {"team_id", "request_id", "answer", "remember", "source"})
    if refused:
        return refused
    remember = str(body.get("remember") or "no")
    if remember not in REMEMBER_CHOICES:
        return 400, {"ok": False, "error": f"remember must be one of {list(REMEMBER_CHOICES)}"}
    return _guard(
        default_coordinator().decide,
        str(body.get("team_id") or "").strip(), str(body.get("request_id") or "").strip(),
        str(body.get("answer") or ""), remember=remember, source=str(body.get("source") or ""),
    )


def decisions(include_superseded: bool = False) -> tuple[int, dict[str, Any]]:
    rows = default_coordinator().store.decisions(include_superseded=bool(include_superseded))
    return 200, {"ok": True, "decisions": rows}


def forget(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    refused = _refuse_unknown(body, {"decision_id"})
    if refused:
        return refused
    result = default_coordinator().forget(str(body.get("decision_id") or "").strip())
    return (200 if result["forgotten"] else 404), result


__all__ = ["decide", "decisions", "default_coordinator", "forget", "status", "unwatch", "watch", "watches"]
