"""The chat's door to agent teams: start, status, stop, decide, answer.

One team per chat session at a time. Teams live under ``<VOOL data dir>/agent_teams/<team>/``; the
registry there is the only state, so :func:`recover_all` at server boot re-opens every team that
still had live agents and lets each coordinator re-adopt or report them lost.

Every result is a small dict with user-facing words only (task names, states, alert text) — no
internal ids except the alert id the user's choice must name.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.agent_team.contract import ContractRefused
from core.agent_team.coordinator import TeamCoordinator
from core.agent_team.limits import Limits, LimitsRefused
from core.agent_team.registry import LIVE_STATES, Registry

_LOCK = threading.RLock()
_TEAMS: dict[str, TeamCoordinator] = {}


def teams_root() -> Path:
    from core.runtime_paths import active_data_dir

    root = active_data_dir() / "agent_teams"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _team_dir(session_id: str) -> Path:
    import hashlib

    return teams_root() / hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:16]


def _team_for(session_id: str) -> TeamCoordinator | None:
    path = _team_dir(session_id)
    with _LOCK:
        team = _TEAMS.get(path.name)
        if team is not None:
            return team
        if (path / "team.sqlite3").exists():
            team = TeamCoordinator(path, on_alert=_notifier(session_id))
            team.start_loop()
            _TEAMS[path.name] = team
            return team
        return None


_ALERT_TITLES = {
    "overlap": "Two agents touched the same files",
    "git_internal": "An agent wrote git internals",
    "unattributed": "Files changed and no agent can be tied to it",
}


def _notifier(session_id: str):
    """Push each alert into VOOL's notification center for this chat (a background run item)."""

    def notify(alert: Mapping[str, Any]) -> None:
        from core.operator import notification_center

        notification_center.record_item(
            dedupe_key=f"agent-team:{alert.get('conflict_id')}",
            source_kind="background_run",
            title=_ALERT_TITLES.get(str(alert.get("kind") or ""), "Agent team alert"),
            body=str(alert.get("alert") or ""),
            payload={"alert_id": alert.get("conflict_id"), "kind": alert.get("kind"),
                     "recommendation": alert.get("recommendation")},
            session_id=session_id,
        )

    return notify


def start(session_id: str, plan: Sequence[Mapping[str, Any]], *, team_limits: Mapping[str, Any],
          workspace: str, chat_text: str = "", base_url: str = "", tick_seconds: float = 1.0) -> dict[str, Any]:
    if not str(session_id or "").strip():
        return {"ok": False, "error": "agents run for a chat; no chat session was given"}
    try:
        limits = Limits.from_dict(dict(team_limits or {}))
    except LimitsRefused as exc:
        return {"ok": False, "error": str(exc)}
    with _LOCK:
        team = _team_for(session_id)
        if team is None:
            runner = None
            if base_url:
                from core.agent_team.model_agent import HttpChatRunner

                runner = HttpChatRunner(base_url)
            team = TeamCoordinator(_team_dir(session_id), workspace=workspace, team_limits=limits,
                                   tick_seconds=tick_seconds, chat_text=chat_text, model_runner=runner,
                                   on_alert=_notifier(session_id))
            (_team_dir(session_id) / "session.txt").write_text(str(session_id))
            team.start_loop()
            _TEAMS[_team_dir(session_id).name] = team
        try:
            rows = team.start(list(plan))
        except (ContractRefused, LimitsRefused) as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "agents": rows, "status": team.status()["lines"]}


def status(session_id: str) -> dict[str, Any]:
    team = _team_for(session_id)
    if team is None:
        return {"ok": True, "agents": [], "alerts": [], "lines": [], "question": ""}
    return team.status()


def stop(session_id: str, agent: str = "") -> dict[str, Any]:
    team = _team_for(session_id)
    if team is None:
        return {"ok": False, "error": "this chat has no agents"}
    return team.stop(agent)


def decide(session_id: str, alert_id: str, choice: str) -> dict[str, Any]:
    team = _team_for(session_id)
    if team is None:
        return {"ok": False, "error": "this chat has no agents"}
    return team.decide(alert_id, choice)


def answer(session_id: str, agent: str, decision: str) -> dict[str, Any]:
    team = _team_for(session_id)
    if team is None:
        return {"ok": False, "error": "this chat has no agents"}
    return team.answer(agent, decision)


def recover_all() -> list[dict[str, Any]]:
    """At server boot: re-open every team that still had live agents.

    Writes nothing when no team was ever started (the teams folder is not created here), and one team
    that cannot be read is reported and skipped, never allowed to stop the recovery of the others.
    """
    from core.runtime_paths import active_data_dir

    root = active_data_dir() / "agent_teams"
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/team.sqlite3")):
        try:
            registry = Registry(path.parent)
            try:
                live = registry.agents((*LIVE_STATES, "launching"))
            finally:
                registry.close()
            if not live:
                continue
            with _LOCK:
                if path.parent.name in _TEAMS:
                    continue
                try:
                    session_id = (path.parent / "session.txt").read_text().strip()
                except OSError:
                    session_id = ""
                team = TeamCoordinator(path.parent, on_alert=_notifier(session_id))
                team.start_loop()
                _TEAMS[path.parent.name] = team
            out.append({"team": path.parent.name, **team.recovered})
        except Exception as exc:  # one unreadable team never costs the others
            out.append({"team": path.parent.name, "error": f"{type(exc).__name__}: {exc}"})
    return out


def _forget_for_tests() -> None:
    with _LOCK:
        for team in _TEAMS.values():
            with contextlib.suppress(Exception):
                team.close()
        _TEAMS.clear()


__all__ = ["answer", "decide", "recover_all", "start", "status", "stop", "teams_root"]
