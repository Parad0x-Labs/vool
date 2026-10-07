"""Pure planning: given what the registry says and what was already done, what to do now.

No effects happen here. The planner reads the store (what was claimed before, the
user's live decisions) and returns keyed :class:`Action` rows. The executor claims each
key before acting, so a plan can be recomputed any number of times, after any restart,
without anything happening twice.

Order inside one tick, highest first:
  1. team budget: at the cap every unfinished agent is stopped; near it, one warning;
  2. waiting agents: answered from a live standing decision, else put to the user in one
     batched question (a request is asked once; a new one re-sends the open list);
  3. stalled agents: nudged up to ``max_nudges`` times, then escalated to the user once
     with a pause recommendation (or paused, when the policy says so);
  4. milestones: agents that finished since the last report, in one report;
  5. the team: when every agent has ended, a final report and the watch ends;
  6. a periodic digest, only when something changed since the last report.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core.standing_coordinator.model import (
    Action,
    AgentSnapshot,
    AgentState,
    DecisionRequest,
    TeamSnapshot,
    WatchPolicy,
)
from core.standing_coordinator.store import CoordinatorStore

_SUMMARY_CHARS = 160


def _h(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _mark(value: float) -> str:
    return f"{float(value):.3f}"


def _name(agent: AgentSnapshot) -> str:
    return agent.label or agent.agent_id


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60.0))


def _action_status(store: CoordinatorStore, team_id: str, key: str) -> str | None:
    for row in store.actions(team_id, key_prefix=key):
        if row["action_key"] == key:
            return str(row["status"])
    return None


def digest_of(team: TeamSnapshot) -> str:
    """What a report is about. Unchanged digest, no report."""
    spend = team.total_spend()
    return _h({
        "agents": sorted((a.agent_id, a.state.value, a.request.request_id if a.request else "") for a in team.agents),
        "cost_cents": int(spend.cost_usd * 100),
    })


def status_line(team: TeamSnapshot, policy: WatchPolicy) -> str:
    counts: dict[str, int] = {}
    for agent in team.agents:
        counts[agent.state.value] = counts.get(agent.state.value, 0) + 1
    parts = [f"{n} {state}" for state, n in sorted(counts.items())]
    spend = team.total_spend()
    money = f"${spend.cost_usd:.2f}"
    if policy.ceilings is not None:
        money += f" of ${policy.ceilings.max_cost_usd:.2f}"
    label = team.title or team.team_id
    return f"{label}: {len(team.agents)} agents ({', '.join(parts) or 'none'}), spent {money}."


def _question_text(requests: list[tuple[AgentSnapshot, DecisionRequest]]) -> str:
    lines = ["Agents are waiting on you:"]
    for agent, request in requests:
        options = f" Options: {', '.join(request.options)}." if request.options else ""
        lines.append(f"- {_name(agent)} asks: {request.question.strip()}{options}")
    return "\n".join(lines)


_STOP_NOT_DONE = {
    "refused": "the team refused the stop",
    "unsupported": "this team cannot be stopped from here",
    "not_found": "the team no longer lists it",
    "failed": "the stop failed",
}


def stop_report_text(payload: dict, statuses: dict[str, str]) -> str:
    """The cap report, from what each stop's receipt says happened.

    Only an applied stop is reported as stopped. Refused, unsupported, missing and failed stops are
    named with their reason; a stop with no final receipt (in flight, or left by a crash) is named as
    unknown. Each of those agents may still be running and spending.
    """
    stopped: list[str] = []
    not_done: list[str] = []
    unknown: list[str] = []
    for key, name in dict(payload.get("stops") or {}).items():
        status = statuses.get(key, "")
        if status == "done":
            stopped.append(name)
        elif status in _STOP_NOT_DONE:
            not_done.append(f"{name} ({_STOP_NOT_DONE[status]})")
        else:
            unknown.append(name)
    parts = [f"The team reached its {payload.get('axis') or 'spend'} cap."]
    if stopped:
        parts.append(f"I stopped {len(stopped)} unfinished agent(s): {', '.join(stopped)}. "
                     "Their work so far is kept and labelled partial.")
    if not_done:
        parts.append(f"I could not stop {len(not_done)}: {'; '.join(not_done)}. They may still be running and spending.")
    if unknown:
        parts.append(f"I don't know whether {len(unknown)} stopped: {', '.join(unknown)}. "
                     "The stop has no receipt yet, so check them before relying on the cap.")
    if payload.get("status_line"):
        parts.append(str(payload["status_line"]))
    return " ".join(parts)


def plan(team: TeamSnapshot, policy: WatchPolicy, store: CoordinatorStore, now: float) -> list[Action]:
    tid = team.team_id
    actions: list[Action] = []

    # 1. Team budget ------------------------------------------------------------------
    stopping = False
    ceilings = policy.ceilings
    if ceilings is not None:
        spend = team.total_spend()
        ratios = {
            "cost": spend.cost_usd / ceilings.max_cost_usd,
            "tokens": spend.tokens / ceilings.max_tokens,
            "calls": spend.calls / ceilings.max_calls,
            "wall_clock": max(0.0, now - team.started_at) / ceilings.wall_clock_seconds,
        }
        axis, worst = max(ratios.items(), key=lambda item: item[1])
        if worst >= 1.0 and not team.all_terminal:
            stopping = True
            live = [a for a in team.agents if not a.terminal]
            stops = {}
            for agent in live:
                key = f"stop:{tid}:{agent.agent_id}:budget"
                stops[key] = _name(agent)
                actions.append(Action(key=key, kind="stop", team_id=tid,
                                      agent_id=agent.agent_id, payload={"reason": f"team_{axis}_cap"}))
            # The report's words are written from the stops' recorded receipts when it is sent
            # (see `stop_report_text`), never from what was planned: a refused stop is not a stop.
            actions.append(Action(
                key=f"budget_stop:{tid}", kind="report", team_id=tid, section="needs",
                payload={"digest": digest_of(team), "title": "Team at its cap", "stops": stops,
                         "axis": axis.replace("_", " "), "status_line": status_line(team, policy)},
            ))
        elif worst >= policy.spend_warn_fraction:
            actions.append(Action(
                key=f"spend_warn:{tid}", kind="report", team_id=tid,
                text=(f"The team has used {int(worst * 100)}% of its {axis.replace('_', ' ')} cap. "
                      f"It stops at 100%. " + status_line(team, policy)),
                payload={"title": "Team near its cap"},
            ))

    # 2. Waiting agents -----------------------------------------------------------------
    to_ask: list[tuple[AgentSnapshot, DecisionRequest]] = []
    if not stopping:
        for agent in team.agents:
            if agent.state is not AgentState.WAITING or agent.request is None:
                continue
            request = agent.request
            prefix = f"answer:{tid}:{agent.agent_id}:{request.request_id}:"
            tries = store.actions(tid, kind="answer", agent_id=agent.agent_id, key_prefix=prefix)
            if any(row["status"] == "done" for row in tries):
                continue  # handed over; the registry has not caught up yet
            answer_key = f"{prefix}mem"
            decision = None
            if not any(row["action_key"] == answer_key for row in tries) and not request.must_ask_user:
                decision = store.lookup_decision(request.topic_key, tid)
            if decision is not None:
                actions.append(Action(
                    key=answer_key, kind="answer", team_id=tid, agent_id=agent.agent_id,
                    text=decision["answer"],
                    payload={"request_id": request.request_id, "decision_id": decision["decision_id"],
                             "topic_key": request.topic_key, "from_memory": True},
                ))
            else:
                to_ask.append((agent, request))
        fresh = [r for _, r in to_ask if _action_status(store, tid, f"asked:{tid}:{r.request_id}") is None]
        if fresh:
            ids = sorted(r.request_id for _, r in to_ask)
            actions.append(Action(
                key=f"ask:{tid}:{_h(ids)}", kind="ask_user", team_id=tid, section="needs",
                text=_question_text(to_ask),
                payload={"request_ids": ids, "title": "Agents need your decision"},
                covers=tuple(f"asked:{tid}:{rid}" for rid in ids),
            ))

    # 3. Stalled agents -----------------------------------------------------------------
    if not stopping:
        for agent in team.agents:
            if agent.state is not AgentState.RUNNING:
                continue
            mark = agent.progress_mark()
            quiet = now - mark
            if quiet < policy.stall_after_seconds:
                continue
            prefix = f"nudge:{tid}:{agent.agent_id}:{_mark(mark)}:"
            nudges = store.actions(tid, kind="nudge", agent_id=agent.agent_id, key_prefix=prefix)
            cannot = any(row["status"] in {"unsupported", "not_found"} for row in nudges)
            count = len(nudges)
            last_at = nudges[-1]["created_at"] if nudges else mark + policy.stall_after_seconds
            if not cannot and count < policy.max_nudges:
                if count == 0 or now - last_at >= policy.nudge_gap_seconds:
                    actions.append(Action(
                        key=f"{prefix}{count + 1}", kind="nudge", team_id=tid, agent_id=agent.agent_id,
                        text=(f"Status check from the team coordinator: no progress has been recorded for "
                              f"{_minutes(quiet)} min. Continue with your current step, or report it as "
                              f"blocked or needing a decision."),
                    ))
                continue
            if cannot or now - last_at >= policy.nudge_gap_seconds:
                tried = "it cannot take a message mid-task" if cannot else f"{count} nudge(s) changed nothing"
                advice = "I paused it." if policy.auto_pause_stalled else "I recommend pausing it."
                actions.append(Action(
                    key=f"escalate:{tid}:{agent.agent_id}:{_mark(mark)}", kind="escalate", team_id=tid,
                    agent_id=agent.agent_id, section="needs",
                    text=(f"{_name(agent)} has made no progress for {_minutes(quiet)} min and {tried}. "
                          f"{advice} Its spend so far: ${agent.spend.cost_usd:.2f}."),
                    payload={"title": "An agent is stuck", "recommendation": "pause"},
                ))
                if policy.auto_pause_stalled:
                    actions.append(Action(key=f"pause:{tid}:{agent.agent_id}:{_mark(mark)}", kind="pause",
                                          team_id=tid, agent_id=agent.agent_id))

    # 4. Milestones ---------------------------------------------------------------------
    ended = [a for a in team.agents if a.terminal
             and _action_status(store, tid, f"agent_end:{tid}:{a.agent_id}:{a.state.value}") is None]
    finishing = team.all_terminal

    # 5. The team -----------------------------------------------------------------------
    if finishing:
        lines = [f"All agents have ended. {status_line(team, policy)}"]
        if any(a.state is not AgentState.DONE for a in team.agents):
            lines.append("The result is partial: not every agent finished its task.")
        for agent in team.agents:
            said = f" Its report: {agent.summary.strip()[:_SUMMARY_CHARS]}" if agent.summary.strip() else ""
            lines.append(f"- {_name(agent)}: {agent.state.value}.{said}")
        actions.append(Action(
            key=f"team_done:{tid}", kind="report", team_id=tid, text="\n".join(lines),
            payload={"digest": digest_of(team), "title": "Team finished"},
            covers=tuple(f"agent_end:{tid}:{a.agent_id}:{a.state.value}" for a in ended),
        ))
        actions.append(Action(key=f"end_watch:{tid}", kind="end_watch", team_id=tid,
                              payload={"reason": "team_finished"}))
        return actions
    if ended:
        lines = []
        for agent in ended:
            said = f" Its report: {agent.summary.strip()[:_SUMMARY_CHARS]}" if agent.summary.strip() else ""
            lines.append(f"- {_name(agent)}: {agent.state.value}.{said}")
        lines.append(status_line(team, policy))
        keys = tuple(f"agent_end:{tid}:{a.agent_id}:{a.state.value}" for a in ended)
        actions.append(Action(
            key=f"milestone:{tid}:{_h(keys)}", kind="report", team_id=tid, text="\n".join(lines),
            payload={"digest": digest_of(team), "title": "Agent update"}, covers=keys,
        ))
        return actions

    # 6. Periodic digest ----------------------------------------------------------------
    digest = digest_of(team)
    last = store.last_event(tid, "reported")
    last_digest = (last or {}).get("fields", {}).get("digest")
    last_ts = float((last or {}).get("ts") or 0.0)
    watch = store.watch(tid) or {}
    since = last_ts or float(watch.get("created_at") or now)
    if digest != last_digest and now - since >= policy.report_every_seconds and not any(
        a.kind in {"report", "ask_user", "escalate"} for a in actions
    ):
        actions.append(Action(
            key=f"digest:{tid}:{digest}", kind="report", team_id=tid, text=status_line(team, policy),
            payload={"digest": digest, "title": "Team progress"},
        ))
    return actions


__all__ = ["digest_of", "plan", "status_line"]
