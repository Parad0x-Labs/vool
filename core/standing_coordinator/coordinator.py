"""The standing coordinator: watch, plan, act once, remember.

One ``tick`` looks at every watched team through its port, plans with
:func:`core.standing_coordinator.planner.plan`, and executes each planned action at most
once (claim, act, record the port's receipt). A port that throws makes that team's tick
a recorded error, never a guess about the team.

``decide`` is the other way in: the user's answer to a waiting agent. It is recorded
first (with who answered and where), then delivered. With ``remember`` set it becomes a
standing decision that later agents asking the same topic get without asking again,
except for requests whose kind is in ``ALWAYS_ASK_KINDS`` or that are flagged
irreversible: those are answered one at a time, always by the user.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from core.standing_coordinator import planner
from core.standing_coordinator.model import (
    Action,
    AgentState,
    PortReceipt,
    StandingCoordinatorError,
    TeamSnapshot,
    WatchPolicy,
)
from core.standing_coordinator.notifier import NotificationCentreNotifier, Notifier
from core.standing_coordinator.ports import TeamPort, resolve_port
from core.standing_coordinator.store import SCOPE_ALL, CoordinatorStore

logger = logging.getLogger(__name__)

REMEMBER_CHOICES = ("no", "team", "always")

_RECEIPT_STATUS = {"applied": "done", "unsupported": "unsupported", "refused": "refused", "not_found": "not_found"}


def _receipt_status(receipt: Any) -> str:
    if not isinstance(receipt, PortReceipt):
        return "failed"
    return _RECEIPT_STATUS.get(receipt.effect, "failed")


class StandingCoordinator:
    def __init__(
        self,
        store: CoordinatorStore | None = None,
        *,
        notifier: Notifier | None = None,
        port_resolver: Callable[[str], TeamPort] = resolve_port,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store if store is not None else CoordinatorStore(clock=clock)
        self.notifier = notifier if notifier is not None else NotificationCentreNotifier()
        self._resolve = port_resolver
        self._clock = clock

    # ---------------------------------------------------------------- watches
    def watch(self, team_id: str, policy: WatchPolicy | None = None, *, session_id: str = "") -> dict[str, Any]:
        """Start watching a team the port knows. An unknown team is refused, not watched blind."""
        port = self._resolve(team_id)
        snapshot = port.snapshot(team_id)
        if snapshot is None:
            raise StandingCoordinatorError(f"no such team: {team_id}")
        chosen = policy or WatchPolicy()
        created = self.store.add_watch(team_id, chosen, session_id=session_id or snapshot.session_id)
        return {"ok": True, "team_id": team_id, "created": created, "policy": chosen.as_row()}

    def unwatch(self, team_id: str, *, reason: str = "user") -> dict[str, Any]:
        return {"ok": True, "team_id": team_id, "ended": self.store.end_watch(team_id, reason)}

    # ------------------------------------------------------------------- tick
    def tick(self, now: float | None = None) -> dict[str, Any]:
        moment = float(now if now is not None else self._clock())
        taken: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for watch in self.store.active_watches():
            team_id = watch["team_id"]
            try:
                port = self._resolve(team_id)
                snapshot = port.snapshot(team_id)
            except Exception as exc:
                errors.append({"team_id": team_id, "error": f"{type(exc).__name__}: {exc}"})
                self.store.append_event(team_id, "port_error", error=f"{type(exc).__name__}: {exc}")
                continue
            if snapshot is None:
                taken.extend(self._vanished(watch))
                continue
            session_id = watch.get("session_id") or snapshot.session_id
            retrying = False
            for action in planner.plan(snapshot, watch["policy"], self.store, moment):
                if action.kind == "end_watch" and retrying:
                    continue  # the final report did not land; the watch stays until it does
                result = self._execute(action, port, snapshot, session_id)
                if result is not None:
                    retrying = retrying or result["status"] == "retry"
                    taken.append(result)
        return {"ok": not errors, "at": moment, "actions": taken, "errors": errors}

    def _vanished(self, watch: dict[str, Any]) -> list[dict[str, Any]]:
        team_id = watch["team_id"]
        action = Action(
            key=f"vanished:{team_id}", kind="report", team_id=team_id, section="needs",
            text=("The team's registry no longer lists this team, so I stopped watching it. "
                  "Its own record is the place to check what it finished."),
            payload={"title": "Team no longer visible"},
        )
        out = []
        result = self._execute(action, None, None, watch.get("session_id") or "")
        if result is not None:
            out.append(result)
        self.store.end_watch(team_id, "vanished")
        return out

    def _execute(self, action: Action, port: TeamPort | None, snapshot: TeamSnapshot | None,
                 session_id: str) -> dict[str, Any] | None:
        store = self.store
        if not store.claim(action.key, team_id=action.team_id, kind=action.kind, agent_id=action.agent_id,
                           covers=action.covers):
            return None
        status = "failed"
        detail: dict[str, Any] = {}
        try:
            if action.kind == "end_watch":
                store.end_watch(action.team_id, str(action.payload.get("reason") or "planned"))
                status = "done"
            elif action.kind in {"report", "ask_user", "escalate"}:
                kind = action.kind
                body = action.text
                if "stops" in action.payload:
                    # Written now, from the stops' receipts, so it says what actually happened.
                    statuses = {row["action_key"]: row["status"]
                                for row in store.actions(action.team_id, kind="stop")}
                    body = planner.stop_report_text(action.payload, statuses)
                result = self.notifier.notify(
                    key=action.key, team_id=action.team_id, session_id=session_id, kind=kind,
                    title=str(action.payload.get("title") or "Team update"), body=body,
                    section=action.section,
                )
                status = "done" if result.get("ok") else "failed"
                detail = {"notifier": result}
                if status == "done":
                    store.append_event(action.team_id, "reported", key=action.key, kind=kind,
                                       digest=action.payload.get("digest") or (planner.digest_of(snapshot) if snapshot else ""),
                                       text=body)
            elif port is None:
                status, detail = "failed", {"error": "no port"}
            elif action.kind == "nudge":
                receipt = port.nudge(action.team_id, action.agent_id, action.text)
                status, detail = _receipt_status(receipt), {"receipt": getattr(receipt, "__dict__", str(receipt))}
            elif action.kind == "answer":
                receipt = port.answer(action.team_id, action.agent_id, str(action.payload["request_id"]),
                                      action.text, str(action.payload["decision_id"]))
                status, detail = _receipt_status(receipt), {"receipt": getattr(receipt, "__dict__", str(receipt))}
            elif action.kind == "pause":
                receipt = port.pause(action.team_id, action.agent_id)
                status, detail = _receipt_status(receipt), {"receipt": getattr(receipt, "__dict__", str(receipt))}
            elif action.kind == "stop":
                receipt = port.stop(action.team_id, action.agent_id)
                status, detail = _receipt_status(receipt), {"receipt": getattr(receipt, "__dict__", str(receipt))}
            else:
                detail = {"error": f"unknown action kind {action.kind}"}
        except Exception as exc:  # the effect's outcome is what the port said, or this error
            logger.exception("standing coordinator action %s failed", action.key)
            status, detail = "failed", {"error": f"{type(exc).__name__}: {exc}"}
        if status == "failed" and action.kind in {"report", "ask_user", "escalate"}:
            # The notification centre deduplicates on this same key, so retrying next tick
            # cannot file the item twice; holding the claim would lose the question.
            store.release(action.key, action.covers)
            store.append_event(action.team_id, "notify_failed", key=action.key, detail=detail)
            return {"key": action.key, "kind": action.kind, "agent_id": action.agent_id, "status": "retry"}
        store.finish(action.key, status, **detail)
        store.append_event(action.team_id, "action", key=action.key, kind=action.kind,
                           agent_id=action.agent_id, status=status, payload=action.payload)
        return {"key": action.key, "kind": action.kind, "agent_id": action.agent_id, "status": status}

    # -------------------------------------------------------------- decisions
    def decide(self, team_id: str, request_id: str, answer: str, *, remember: str = "no",
               source: str = "") -> dict[str, Any]:
        """Record the user's answer to one waiting request, then hand it over."""
        if remember not in REMEMBER_CHOICES:
            raise StandingCoordinatorError(f"remember must be one of {REMEMBER_CHOICES}")
        if not str(answer or "").strip():
            raise StandingCoordinatorError("an answer is required")
        port = self._resolve(team_id)
        snapshot = port.snapshot(team_id)
        if snapshot is None:
            raise StandingCoordinatorError(f"no such team: {team_id}")
        target = next((a for a in snapshot.agents if a.state is AgentState.WAITING and a.request is not None
                       and a.request.request_id == request_id), None)
        if target is None or target.request is None:
            raise StandingCoordinatorError("no agent in this team is waiting on that request")
        request = target.request
        standing = remember != "no"
        note = ""
        if standing and request.must_ask_user:
            standing = False
            note = ("Recorded for this request only: this kind of request is always asked, "
                    "so it is never answered from memory.")
        decision = self.store.record_decision(
            scope=SCOPE_ALL if remember == "always" else team_id,
            topic_key=request.topic_key or f"request:{request_id}",
            answer=answer, standing=standing, question=request.question, source=source,
        )
        self.store.append_event(team_id, "decision_recorded", decision_id=decision["decision_id"],
                                request_id=request_id, standing=standing, topic_key=decision["topic_key"])
        delivered = [self._deliver(port, snapshot, target.agent_id, request_id, decision)]
        if standing:
            for sibling in snapshot.agents:
                req = sibling.request
                if (sibling is target or sibling.state is not AgentState.WAITING or req is None
                        or req.must_ask_user or req.topic_key != request.topic_key):
                    continue
                delivered.append(self._deliver(port, snapshot, sibling.agent_id, req.request_id, decision))
        return {"ok": True, "decision": decision, "delivered": delivered, "note": note}

    def _deliver(self, port: TeamPort, snapshot: TeamSnapshot, agent_id: str, request_id: str,
                 decision: dict[str, Any]) -> dict[str, Any]:
        action = Action(
            key=f"answer:{snapshot.team_id}:{agent_id}:{request_id}:{decision['decision_id']}", kind="answer", team_id=snapshot.team_id,
            agent_id=agent_id, text=decision["answer"],
            payload={"request_id": request_id, "decision_id": decision["decision_id"], "from_memory": False},
        )
        result = self._execute(action, port, snapshot, snapshot.session_id)
        if result is None:
            return {"agent_id": agent_id, "request_id": request_id, "status": "already_answered"}
        return {"agent_id": agent_id, "request_id": request_id, "status": result["status"]}

    def forget(self, decision_id: str) -> dict[str, Any]:
        return {"ok": True, "forgotten": self.store.forget_decision(decision_id)}

    # ----------------------------------------------------------------- status
    def status(self, team_id: str) -> dict[str, Any]:
        watch = self.store.watch(team_id)
        if watch is None:
            raise StandingCoordinatorError(f"this team is not watched: {team_id}")
        snapshot = None
        error = ""
        try:
            snapshot = self._resolve(team_id).snapshot(team_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        policy = watch["policy"]
        out: dict[str, Any] = {
            "ok": True,
            "team_id": team_id,
            "watching": watch["ended_at"] is None,
            "end_reason": watch["end_reason"],
            "policy": policy.as_row(),
            "snapshot_error": error,
            "events": self.store.events(team_id)[-50:],
        }
        if snapshot is not None:
            out["line"] = planner.status_line(snapshot, policy)
            out["spend"] = snapshot.total_spend().as_row()
            out["agents"] = [
                {"agent_id": a.agent_id, "label": a.label, "state": a.state.value,
                 "last_progress_at": a.last_progress_at, "spend": a.spend.as_row(),
                 "waiting_on": ({"request_id": a.request.request_id, "question": a.request.question,
                                 "always_ask": a.request.must_ask_user} if a.request else None)}
                for a in snapshot.agents
            ]
        return out


__all__ = ["REMEMBER_CHOICES", "StandingCoordinator"]
