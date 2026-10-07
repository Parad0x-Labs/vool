"""Council runs seen as a one-agent team, so a long council run can be watched today.

Team id ``council:<run_id>``. Everything comes from the council's own run store: the
state file for the state, the append-only ledger for the time of the last recorded
event, the seat reports' measured usage for spend. A council run is a single agent
here; its seats have their own bounded attempts inside the orchestrator.

What a council run supports: ``stop`` (through the council API, which acts only on runs
this process holds). A seat turn cannot take a message mid-turn and a paused run is
resumed through its own card, so ``nudge``, ``answer`` and ``pause`` say
``unsupported``; the coordinator then puts the matter to the user instead.

Spend is what providers reported on each seat turn: a lower bound when a provider
reported nothing, never an estimate.
"""

from __future__ import annotations

from typing import Any

from core.standing_coordinator.model import (
    AgentSnapshot,
    AgentState,
    DecisionRequest,
    PortReceipt,
    SpendView,
    TeamSnapshot,
)

_PREFIX = "council:"

_STATE_MAP = {
    "converged": AgentState.DONE,
    "no_convergence": AgentState.PARTIAL,
    "stopped": AgentState.STOPPED,
    "failed": AgentState.FAILED,
    "gate0_refused": AgentState.FAILED,
    "needs_attention": AgentState.WAITING,
}


def _run_id(team_id: str) -> str:
    text = str(team_id or "")
    return text[len(_PREFIX):] if text.startswith(_PREFIX) else ""


def _spend(state: dict[str, Any]) -> SpendView:
    cost, tokens, calls = 0.0, 0, 0
    for round_row in state.get("rounds") or []:
        for report in (round_row or {}).get("reports") or []:
            calls += 1
            usage = (report or {}).get("usage") or {}
            for name in ("prompt_tokens", "output_tokens"):
                value = usage.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    tokens += value
            usd = usage.get("usd_actual")
            if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd > 0:
                cost += float(usd)
    return SpendView(cost_usd=cost, tokens=tokens, calls=calls)


class CouncilRunPort:
    def _store(self, team_id: str):
        from core.council.run_store import CouncilRunStore

        run_id = _run_id(team_id)
        if not run_id:
            return None
        try:
            store = CouncilRunStore(run_id)
        except ValueError:
            return None
        return store if store.run_id == run_id else None

    def snapshot(self, team_id: str) -> TeamSnapshot | None:
        store = self._store(team_id)
        state = store.read_state() if store is not None else None
        if store is None or state is None:
            return None
        raw_state = str(state.get("state") or "")
        mapped = _STATE_MAP.get(raw_state, AgentState.RUNNING)
        events = store.read_events()
        last_ts = None
        if events:
            last = events[-1].get("ts")
            if isinstance(last, (int, float)) and not isinstance(last, bool):
                last_ts = float(last)
        started = state.get("started_at")
        started_at = float(started) if isinstance(started, (int, float)) and not isinstance(started, bool) else (
            last_ts if last_ts is not None else float(state.get("updated_at") or 0.0))
        outcome = state.get("outcome") or {}
        request = None
        if mapped is AgentState.WAITING:
            blocking = ", ".join(str((row or {}).get("seat_id") or "?") for row in outcome.get("blocking_seats") or [])
            request = DecisionRequest(
                request_id=f"{store.run_id}:needs_attention:{outcome.get('round_no') or state.get('round_no') or 0}",
                question=(f"The council is paused: seat(s) {blocking or 'unknown'} produced no report. "
                          "Retry a seat, point it at another model, or disable it, from the council card."),
                kind="council_review",
                irreversible=True,
            )
        problem = str(state.get("problem") or "").strip().splitlines()
        agent = AgentSnapshot(
            agent_id=store.run_id,
            label="Council run",
            state=mapped,
            started_at=started_at,
            last_progress_at=last_ts,
            request=request,
            spend=_spend(state),
            summary=str(outcome.get("detail") or outcome.get("result") or "") if mapped is not AgentState.RUNNING else "",
        )
        return TeamSnapshot(
            team_id=team_id,
            agents=(agent,),
            started_at=started_at,
            title=f"Council: {problem[0][:80]}" if problem else "Council run",
            session_id=str(state.get("chat_session") or ""),
        )

    def nudge(self, team_id: str, agent_id: str, text: str) -> PortReceipt:
        return PortReceipt.unsupported("a council seat turn cannot take a message mid-turn")

    def answer(self, team_id: str, agent_id: str, request_id: str, answer: str, decision_id: str) -> PortReceipt:
        return PortReceipt.unsupported("a paused council run is resumed from its own card")

    def pause(self, team_id: str, agent_id: str) -> PortReceipt:
        return PortReceipt.unsupported("a council run has no pause; it can be stopped")

    def stop(self, team_id: str, agent_id: str) -> PortReceipt:
        from core.council import api as council_api

        run_id = _run_id(team_id)
        if not run_id or agent_id != run_id:
            return PortReceipt(False, "not_found", "not this port's run")
        code, body = council_api.stop(run_id)
        if code == 200:
            # A live run only takes the request here ("stopping"); it ends when its thread notices.
            # Until a snapshot shows it ended, the stop is pending, never done.
            return PortReceipt.applied("stopped") if body.get("stopped") else PortReceipt.pending("stopping")
        return PortReceipt(False, "not_found", str(body.get("error") or code))


__all__ = ["CouncilRunPort"]
