"""Agent teams seen through the coordinator's five calls.

Team id ``team:<dir>`` is one chat's whole team; ``team:<dir>:<seq>`` is one batch of it: the
agents planned at or after event ``seq`` of the team's registry. A chat that starts agents again
after its last batch ended gets a new batch, so every start has its own watch and its own final
report. ``<dir>`` is the team's folder under ``<data>/agent_teams/`` (16 hex characters).

Everything is read from the team's own registry (``team.sqlite3``), opened for one look and
closed again: the agents' states and typed results, their metered spend, the event trail and each
agent's status file. Nothing is guessed. An agent with no event and no status line since it
started is judged on its start time, and a registry state this port does not know makes the look
fail (a recorded port error), never a made-up state.

What a team supports, through the chat's own door (:mod:`core.agent_team.service`):
  * ``answer``: an agent that stopped on a decision runs again with the user's answer;
  * ``stop``: one agent, through the team's coordinator, which acts only on processes it can
    prove it started; an agent waiting on a decision has its question withdrawn.
An agent process cannot take a message mid-task and the team has no pause the coordinator may
use, so ``nudge`` and ``pause`` say ``unsupported`` and a stuck agent is put to the user with a
stop recommendation instead.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from core.standing_coordinator.model import (
    TERMINAL_STATES,
    AgentSnapshot,
    AgentState,
    DecisionRequest,
    PortReceipt,
    SpendView,
    TeamSnapshot,
)

PREFIX = "team"
_DIR = re.compile(r"^[0-9a-f]{16}$")
_TITLE_CHARS = 80

_STATE_MAP = {
    "pending": AgentState.QUEUED,
    "launching": AgentState.RUNNING,
    "running": AgentState.RUNNING,
    "paused": AgentState.PAUSED,
    "needs_decision": AgentState.WAITING,
    "done": AgentState.DONE,
    "partial": AgentState.PARTIAL,
    "stopped": AgentState.STOPPED,
    "failed": AgentState.FAILED,
    "refused": AgentState.FAILED,  # not started: a task it depends on did not finish
    "lost": AgentState.FAILED,  # its process was gone after a restart; never relaunched
    "unverified": AgentState.FAILED,  # its turn ended without a typed result
}


def batch_team_id(team_dir_name: str, since_seq: int) -> str:
    """The watch id for the agents a chat's team planned at or after registry event ``since_seq``."""
    return f"{PREFIX}:{team_dir_name}:{int(since_seq)}"


def team_id_prefix(team_dir_name: str) -> str:
    return f"{PREFIX}:{team_dir_name}"


def _teams_root() -> Path:
    # Read-only on purpose: core.agent_team.service.teams_root() creates the folder.
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "agent_teams"


def _locate(team_id: str) -> tuple[Path, int] | None:
    text = str(team_id or "")
    head = f"{PREFIX}:"
    if not text.startswith(head):
        return None
    name, sep, since_text = text[len(head):].partition(":")
    if not _DIR.match(name):
        return None
    since = 0
    if sep:
        # One spelling per batch, so two ids can never watch the same agents twice.
        if not since_text.isdigit() or str(int(since_text)) != since_text or int(since_text) < 1:
            return None
        since = int(since_text)
    return _teams_root() / name, since


def _session_of(path: Path) -> str:
    """The chat the team belongs to, only when it hashes to this very folder."""
    try:
        raw = (path / "session.txt").read_text()
    except OSError:
        return ""
    for candidate in (raw, raw.strip()):
        if candidate and hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16] == path.name:
            return candidate
    return ""


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if value > 0 else 0.0


def _summary(row: dict[str, Any], raw_state: str, state: AgentState) -> str:
    """The agent's relayed result without its "<name>: <state>." head (the report prints that)."""
    relay = str((row.get("result") or {}).get("relay") or "").strip()
    head = f"{row['display_name']}: {raw_state}."
    if relay.startswith(head):
        relay = relay[len(head):].strip()
    if raw_state != state.value:
        # The registry's own word, when the coordinator's state is coarser ("lost" is "failed").
        relay = f"{raw_state}. {relay}".strip()
    return relay


class AgentTeamPort:
    def snapshot(self, team_id: str) -> TeamSnapshot | None:
        located = _locate(team_id)
        if located is None:
            return None
        path, since = located
        if not (path / "team.sqlite3").is_file():
            return None  # never create a registry by looking for one
        from core.agent_team.registry import Registry

        registry = Registry(path)
        try:
            team = registry.team()
            rows = registry.agents()
            events = registry.events()
        finally:
            registry.close()
        if team is None:
            return None
        planned_seq: dict[str, int] = {}
        planned_ts: dict[str, float] = {}
        last_ts: dict[str, float] = {}
        answered: dict[str, int] = {}
        for event in events:
            agent_id = str(event.get("agent_id") or "")
            if not agent_id:
                continue
            ts = float(event.get("ts") or 0.0)
            if event["kind"] == "agent_planned" and agent_id not in planned_seq:
                planned_seq[agent_id] = int(event["seq"])
                planned_ts[agent_id] = ts
            last_ts[agent_id] = max(last_ts.get(agent_id, 0.0), ts)
            if event["kind"] == "decision_answered":
                answered[agent_id] = answered.get(agent_id, 0) + 1
        members = [r for r in rows if planned_seq.get(r["agent_id"], 0) >= since] if since else rows
        if not members:
            return None
        agents = []
        for row in members:
            agent_id = row["agent_id"]
            raw_state = str(row.get("state") or "")
            if raw_state not in _STATE_MAP:
                raise ValueError(f"agent {agent_id} has a state this port does not know: {raw_state!r}")
            state = _STATE_MAP[raw_state]
            result = row.get("result") or {}
            started_at = float(row.get("launched_at") or planned_ts.get(agent_id) or team["created_at"])
            marks = [last_ts[agent_id]] if agent_id in last_ts else []
            try:
                marks.append((path / "agents" / agent_id / "status.jsonl").stat().st_mtime)
            except OSError:
                pass
            request = None
            if state is AgentState.WAITING:
                question = str(result.get("question") or "").strip() or "it needs your decision to continue"
                options = tuple(str(o) for o in (result.get("options") or ()) if str(o).strip())
                # A new id for each time it stops to ask, so an answered question is never asked again
                # and the next one is never mistaken for it.
                request = DecisionRequest(request_id=f"{agent_id}:ask:{answered.get(agent_id, 0) + 1}",
                                          question=question, options=options)
            spend = row.get("spend") or {}
            agents.append(AgentSnapshot(
                agent_id=agent_id,
                state=state,
                started_at=started_at,
                label=str(row.get("display_name") or agent_id),
                last_progress_at=max(marks) if marks else None,
                request=request,
                spend=SpendView(cost_usd=_number(spend.get("usd")), tokens=int(_number(spend.get("tokens"))),
                                calls=int(_number(spend.get("calls")))),
                summary=_summary(row, raw_state, state) if state in TERMINAL_STATES else "",
            ))
        titles = [str(r.get("title") or r.get("display_name") or r["agent_id"]) for r in members]
        named = titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " and " + titles[-1]
        title = f"Agent team for {named}"
        if len(title) > _TITLE_CHARS:
            title = title[: _TITLE_CHARS - 1].rstrip(" ,") + "…"
        return TeamSnapshot(
            team_id=team_id,
            agents=tuple(agents),
            started_at=min(a.started_at for a in agents),
            title=title,
            session_id=_session_of(path),
            pause_supported=False,
        )

    def nudge(self, team_id: str, agent_id: str, text: str) -> PortReceipt:
        return PortReceipt.unsupported("an agent cannot take a message mid-task")

    def pause(self, team_id: str, agent_id: str) -> PortReceipt:
        return PortReceipt.unsupported("an agent team has no pause from here; an agent can be stopped")

    def _member(self, team_id: str, agent_id: str) -> tuple[AgentSnapshot | None, str]:
        snapshot = self.snapshot(team_id)
        located = _locate(team_id)
        if snapshot is None or located is None:
            return None, ""
        agent = next((a for a in snapshot.agents if a.agent_id == agent_id), None)
        return agent, _session_of(located[0])

    def answer(self, team_id: str, agent_id: str, request_id: str, answer: str, decision_id: str) -> PortReceipt:
        agent, session = self._member(team_id, agent_id)
        if agent is None:
            return PortReceipt(False, "not_found", "this team lists no such agent")
        if agent.state is not AgentState.WAITING or agent.request is None or agent.request.request_id != request_id:
            return PortReceipt(False, "refused", "that agent is not waiting on this question any more")
        if not session:
            return PortReceipt(False, "refused", "the team's chat is not recorded, so its door cannot be found")
        from core.agent_team import service

        result = service.answer(session, agent_id, answer)
        if result.get("ok"):
            return PortReceipt.applied("it runs again with the decision")
        return PortReceipt(False, "refused", str(result.get("error") or "the team refused the answer"))

    def stop(self, team_id: str, agent_id: str) -> PortReceipt:
        agent, session = self._member(team_id, agent_id)
        if agent is None:
            return PortReceipt(False, "not_found", "this team lists no such agent")
        if agent.terminal:
            return PortReceipt.applied(f"it had already ended ({agent.state.value})")
        if not session:
            return PortReceipt(False, "refused", "the team's chat is not recorded, so its door cannot be found")
        from core.agent_team import service

        result = service.stop(session, agent_id)
        if not result.get("ok"):
            return PortReceipt(False, "refused", str(result.get("error") or "the team refused the stop"))
        after, _ = self._member(team_id, agent_id)
        if after is not None and after.terminal:
            return PortReceipt.applied("stopped")
        return PortReceipt.pending("stopping")


__all__ = ["PREFIX", "AgentTeamPort", "batch_team_id", "team_id_prefix"]
