"""Standing coordinator laws, through the real store, planner and executor.

The team is a labelled in-memory port double (the Agent Teams registry is not on main);
it records every call the coordinator makes, so each law is checked on the effects that
actually reached the team and the user, not on the plan.
"""

from __future__ import annotations

import pytest

from core.council.cost_ladder import SpendCeilings
from core.standing_coordinator import (
    AgentSnapshot,
    AgentState,
    DecisionRequest,
    PortReceipt,
    SpendView,
    StandingCoordinator,
    TeamSnapshot,
    WatchPolicy,
)
from core.standing_coordinator.model import StandingCoordinatorError
from core.standing_coordinator.notifier import MemoryNotifier
from core.standing_coordinator.store import CoordinatorStore

T0 = 1_000_000.0
TEAM = "team:t1"


class FakeTeamPort:
    """Labelled double of the Agent Teams registry: holds agents, records every call."""

    def __init__(self) -> None:
        self.agents: dict[str, AgentSnapshot] = {}
        self.calls: list[tuple] = []
        self.nudge_effect = "applied"
        self.present = True
        self.raises = False
        self.team_spend: SpendView | None = None

    def put(self, agent_id: str, state: AgentState = AgentState.RUNNING, **fields) -> None:
        base = {"agent_id": agent_id, "state": state, "started_at": T0, "label": agent_id.upper()}
        base.update(fields)
        self.agents[agent_id] = AgentSnapshot(**base)

    def snapshot(self, team_id: str):
        if self.raises:
            raise RuntimeError("registry offline")
        if not self.present:
            return None
        return TeamSnapshot(team_id=team_id, agents=tuple(self.agents.values()), started_at=T0,
                            title="Refactor", session_id="chat-1", spend=self.team_spend)

    def nudge(self, team_id, agent_id, text):
        self.calls.append(("nudge", agent_id, text))
        return PortReceipt(self.nudge_effect == "applied", self.nudge_effect)

    def answer(self, team_id, agent_id, request_id, answer, decision_id):
        self.calls.append(("answer", agent_id, request_id, answer, decision_id))
        return PortReceipt.applied()

    def pause(self, team_id, agent_id):
        self.calls.append(("pause", agent_id))
        return PortReceipt.applied()

    def stop(self, team_id, agent_id):
        self.calls.append(("stop", agent_id))
        return PortReceipt.applied()

    def kinds(self, kind):
        return [c for c in self.calls if c[0] == kind]


@pytest.fixture()
def rig(tmp_path):
    port = FakeTeamPort()
    notifier = MemoryNotifier()
    clock = {"now": T0}
    store = CoordinatorStore(tmp_path / "c.sqlite3", clock=lambda: clock["now"])

    def make():
        return StandingCoordinator(CoordinatorStore(tmp_path / "c.sqlite3", clock=lambda: clock["now"]),
                                   notifier=notifier, port_resolver=lambda _tid: port, clock=lambda: clock["now"])

    coordinator = StandingCoordinator(store, notifier=notifier, port_resolver=lambda _tid: port,
                                      clock=lambda: clock["now"])

    class Rig:
        pass

    r = Rig()
    r.port, r.notifier, r.clock, r.coordinator, r.make, r.store = port, notifier, clock, coordinator, make, store

    def at(seconds):
        clock["now"] = T0 + seconds
        return r.coordinator.tick()

    r.at = at
    return r


def policy(**kw):
    base = {"stall_after_seconds": 600, "nudge_gap_seconds": 300, "max_nudges": 2, "report_every_seconds": 900}
    base.update(kw)
    return WatchPolicy(**base)


# ------------------------------------------------------------------------- stalls
def test_stalled_agent_is_nudged_twice_then_escalated_once_and_progress_resets(rig):
    rig.port.put("a", last_progress_at=T0)
    rig.coordinator.watch(TEAM, policy())
    rig.at(599)
    assert rig.port.kinds("nudge") == []
    rig.at(600)
    rig.at(700)  # inside the gap: no second nudge
    assert len(rig.port.kinds("nudge")) == 1
    assert "10 min" in rig.port.kinds("nudge")[0][2]
    rig.at(900)
    assert len(rig.port.kinds("nudge")) == 2
    rig.at(1100)
    assert [n for n in rig.notifier.items if n["kind"] == "escalate"] == []  # inside the gap after nudge 2
    rig.at(1200)
    escalations = [n for n in rig.notifier.items if n["kind"] == "escalate"]
    assert len(escalations) == 1 and escalations[0]["section"] == "needs"
    assert "recommend pausing" in escalations[0]["body"]
    rig.at(5000)
    assert len([n for n in rig.notifier.items if n["kind"] == "escalate"]) == 1
    assert len(rig.port.kinds("nudge")) == 2
    assert rig.port.kinds("pause") == []  # recommending is not pausing
    # Progress resumes, then stops again: the count starts over for the new quiet spell.
    rig.port.put("a", last_progress_at=T0 + 5000)
    rig.at(5000 + 600)
    assert len(rig.port.kinds("nudge")) == 3


def test_agent_that_cannot_take_a_nudge_is_escalated_on_the_next_tick(rig):
    rig.port.nudge_effect = "unsupported"
    rig.port.put("a")
    rig.coordinator.watch(TEAM, policy())
    rig.at(600)
    rig.at(630)
    assert len(rig.port.kinds("nudge")) == 1
    [esc] = [n for n in rig.notifier.items if n["kind"] == "escalate"]
    assert "cannot take a message" in esc["body"]


def test_auto_pause_policy_pauses_the_stalled_agent(rig):
    rig.port.put("a")
    rig.coordinator.watch(TEAM, policy(max_nudges=0, auto_pause_stalled=True))
    rig.at(900)
    assert rig.port.kinds("pause") == [("pause", "a")]
    assert "I paused it" in rig.notifier.items[-1]["body"]


def test_waiting_and_paused_agents_are_not_nudged(rig):
    rig.port.put("w", AgentState.WAITING, request=DecisionRequest("r1", "Which DB?", topic_key="db"))
    rig.port.put("p", AgentState.PAUSED)
    rig.coordinator.watch(TEAM, policy())
    rig.at(5000)
    assert rig.port.kinds("nudge") == []


# ---------------------------------------------------------------------- decisions
def test_questions_are_batched_answered_once_and_remembered_for_later_agents(rig):
    ask = dict(topic_key="dependency.add.httpx", options=("yes", "no"))
    rig.port.put("a", AgentState.WAITING, request=DecisionRequest("ra", "May I add httpx?", **ask))
    rig.port.put("b", AgentState.WAITING, request=DecisionRequest("rb", "Can I add httpx?", **ask))
    rig.coordinator.watch(TEAM, policy())
    rig.at(30)
    rig.at(60)
    [question] = [n for n in rig.notifier.items if n["kind"] == "ask_user"]
    assert "A asks" in question["body"] and "B asks" in question["body"] and question["section"] == "needs"

    result = rig.coordinator.decide(TEAM, "ra", "yes", remember="team", source="chat-1 message 7")
    assert [d["status"] for d in result["delivered"]] == ["done", "done"]
    decision_id = result["decision"]["decision_id"]
    assert {(c[1], c[3], c[4]) for c in rig.port.kinds("answer")} == {("a", "yes", decision_id), ("b", "yes", decision_id)}

    # A third agent asks the same thing later: answered from memory, the user is not asked.
    rig.port.put("a", AgentState.RUNNING, last_progress_at=T0 + 100)
    rig.port.put("b", AgentState.RUNNING, last_progress_at=T0 + 100)
    rig.port.put("c", AgentState.WAITING, request=DecisionRequest("rc", "Add httpx?", **ask))
    rig.at(120)
    assert rig.port.kinds("answer")[-1] == ("answer", "c", "rc", "yes", decision_id)
    assert len([n for n in rig.notifier.items if n["kind"] == "ask_user"]) == 1
    rig.at(150)  # the registry has not caught up yet: still not answered twice
    assert len(rig.port.kinds("answer")) == 3


def test_new_question_resends_the_open_list_but_an_asked_one_alone_is_not_repeated(rig):
    rig.port.put("a", AgentState.WAITING, request=DecisionRequest("ra", "Q1?", topic_key="t1"))
    rig.coordinator.watch(TEAM, policy())
    rig.at(30)
    rig.at(60)
    assert len([n for n in rig.notifier.items if n["kind"] == "ask_user"]) == 1
    rig.port.put("b", AgentState.WAITING, request=DecisionRequest("rb", "Q2?", topic_key="t2"))
    rig.at(90)
    asks = [n for n in rig.notifier.items if n["kind"] == "ask_user"]
    assert len(asks) == 2 and "Q1?" in asks[1]["body"] and "Q2?" in asks[1]["body"]


def test_irreversible_requests_are_never_answered_from_memory(rig):
    rig.store.record_decision(scope="*", topic_key="git.push.main", answer="yes", standing=True)
    rig.port.put("a", AgentState.WAITING,
                 request=DecisionRequest("ra", "Push to main?", topic_key="git.push.main", kind="git_push"))
    rig.coordinator.watch(TEAM, policy())
    rig.at(30)
    assert rig.port.kinds("answer") == []
    assert [n["kind"] for n in rig.notifier.items] == ["ask_user"]
    result = rig.coordinator.decide(TEAM, "ra", "yes", remember="always")
    assert result["decision"]["standing"] is False and "always asked" in result["note"]
    assert rig.port.kinds("answer")[0][1:3] == ("a", "ra")


def test_a_request_without_a_topic_can_only_be_answered_by_the_user(rig):
    rig.port.put("a", AgentState.WAITING, request=DecisionRequest("ra", "Which of these two files?"))
    rig.coordinator.watch(TEAM, policy())
    rig.at(30)
    assert rig.port.kinds("answer") == []
    result = rig.coordinator.decide(TEAM, "ra", "the second", remember="team")
    assert result["decision"]["standing"] is False


def test_forgotten_decision_sends_the_next_question_to_the_user(rig):
    decision = rig.store.record_decision(scope=TEAM, topic_key="db", answer="sqlite", standing=True)
    assert rig.coordinator.forget(decision["decision_id"])["forgotten"] is True
    rig.port.put("a", AgentState.WAITING, request=DecisionRequest("ra", "Which DB?", topic_key="db"))
    rig.coordinator.watch(TEAM, policy())
    rig.at(30)
    assert rig.port.kinds("answer") == []
    assert [n["kind"] for n in rig.notifier.items] == ["ask_user"]


def test_newer_standing_decision_supersedes_and_keeps_the_old_row(rig):
    first = rig.store.record_decision(scope=TEAM, topic_key="db", answer="sqlite", standing=True)
    second = rig.store.record_decision(scope=TEAM, topic_key="db", answer="postgres", standing=True)
    assert rig.store.lookup_decision("db", TEAM)["decision_id"] == second["decision_id"]
    old = rig.store.decision(first["decision_id"])
    assert old["superseded_by"] == second["decision_id"] and old["answer"] == "sqlite"


def test_decide_refuses_a_request_nobody_is_waiting_on(rig):
    rig.port.put("a")
    rig.coordinator.watch(TEAM, policy())
    with pytest.raises(StandingCoordinatorError, match="waiting on that request"):
        rig.coordinator.decide(TEAM, "nope", "yes")


# ------------------------------------------------------------------------- spend
def test_team_cap_warns_once_then_stops_every_unfinished_agent_once(rig):
    ceilings = SpendCeilings(max_calls=1000, max_tokens=10**7, max_cost_usd=2.0, wall_clock_seconds=10**6)
    rig.port.put("a", spend=SpendView(cost_usd=0.9))
    rig.port.put("b", spend=SpendView(cost_usd=0.75))
    rig.port.put("done", AgentState.DONE, spend=SpendView(cost_usd=0.0))
    rig.coordinator.watch(TEAM, policy(ceilings=ceilings))
    rig.at(30)
    rig.at(60)
    warns = [n for n in rig.notifier.items if "82%" in n["body"]]
    assert len(warns) == 1
    rig.port.put("a", spend=SpendView(cost_usd=1.3))
    rig.at(90)
    rig.at(120)
    assert sorted(c[1] for c in rig.port.kinds("stop")) == ["a", "b"]
    stops = [n for n in rig.notifier.items if n["section"] == "needs" and "cost cap" in n["body"]]
    assert len(stops) == 1 and "$2.05 of $2.00" in stops[0]["body"]


def test_wall_clock_cap_stops_the_team(rig):
    ceilings = SpendCeilings(max_calls=1000, max_tokens=10**7, max_cost_usd=100.0, wall_clock_seconds=3600)
    rig.port.put("a", last_progress_at=T0 + 3500)
    rig.coordinator.watch(TEAM, policy(ceilings=ceilings))
    rig.at(3600)
    assert rig.port.kinds("stop") == [("stop", "a")]
    assert "wall clock cap" in rig.notifier.items[-1]["body"]


def test_registry_team_total_wins_over_the_sum_of_agents(rig):
    ceilings = SpendCeilings(max_calls=1000, max_tokens=10**7, max_cost_usd=1.0, wall_clock_seconds=10**6)
    rig.port.put("a", spend=SpendView(cost_usd=0.1))
    rig.port.team_spend = SpendView(cost_usd=1.0)  # includes a finished agent the registry dropped
    rig.coordinator.watch(TEAM, policy(ceilings=ceilings))
    rig.at(30)
    assert rig.port.kinds("stop") == [("stop", "a")]


# -------------------------------------------------------------------- reporting
def test_finished_agents_reported_once_and_team_end_ends_the_watch(rig):
    rig.port.put("a", last_progress_at=T0 + 10)
    rig.port.put("b", last_progress_at=T0 + 10)
    rig.coordinator.watch(TEAM, policy())
    rig.port.put("a", AgentState.DONE, summary="Moved 3 modules; tests pass.")
    rig.at(60)
    rig.at(90)
    milestones = [n for n in rig.notifier.items if n["title"] == "Agent update"]
    assert len(milestones) == 1 and "A: done. Its report: Moved 3 modules" in milestones[0]["body"]
    rig.port.put("b", AgentState.FAILED, summary="Could not reach the API.")
    rig.at(120)
    [final] = [n for n in rig.notifier.items if n["title"] == "Team finished"]
    assert "partial" in final["body"] and "B: failed" in final["body"]
    assert final["session_id"] == "chat-1"
    assert rig.store.watch(TEAM)["end_reason"] == "team_finished"
    count = len(rig.notifier.items)
    rig.at(5000)
    assert len(rig.notifier.items) == count


def test_periodic_report_only_when_something_changed(rig):
    rig.port.put("a", last_progress_at=T0)
    rig.port.put("b", last_progress_at=T0)
    rig.coordinator.watch(TEAM, policy(stall_after_seconds=10**5))
    rig.at(899)
    assert rig.notifier.items == []
    rig.at(900)
    assert [n["title"] for n in rig.notifier.items] == ["Team progress"]
    rig.at(1800)
    assert len(rig.notifier.items) == 1  # nothing changed: silence
    rig.at(2000)
    rig.port.put("b", AgentState.PAUSED)
    rig.at(2000)
    assert len(rig.notifier.items) == 2  # changed and past the interval since the last report
    rig.port.put("a", AgentState.PAUSED)
    rig.at(2300)
    assert len(rig.notifier.items) == 2  # changed again, but inside the interval
    rig.at(2900)
    assert len(rig.notifier.items) == 3 and "2 paused" in rig.notifier.items[-1]["body"]


# ---------------------------------------------------------- failure and restart
def test_restart_resumes_without_repeating_anything(rig):
    rig.port.put("a")
    rig.port.put("w", AgentState.WAITING, request=DecisionRequest("rw", "Q?", topic_key="q"))
    rig.coordinator.watch(TEAM, policy())
    rig.at(600)
    before_calls, before_items = list(rig.port.calls), list(rig.notifier.items)
    rig.coordinator = rig.make()  # a fresh process over the same file
    rig.at(620)
    assert rig.port.calls == before_calls and rig.notifier.items == before_items
    rig.at(900)
    assert len(rig.port.kinds("nudge")) == 2


def test_action_interrupted_by_a_crash_is_marked_uncertain_and_never_replayed(rig):
    rig.port.put("a")
    rig.coordinator.watch(TEAM, policy())
    rig.clock["now"] = T0 + 600
    key = f"nudge:{TEAM}:a:{T0:.3f}:1"
    assert rig.store.claim(key, team_id=TEAM, kind="nudge", agent_id="a")  # crash right after the claim
    rig.clock["now"] = T0 + 800
    assert rig.store.reconcile() == 1
    rig.at(810)
    assert rig.port.kinds("nudge") == []
    assert rig.store.last_event(TEAM, "action_uncertain")["fields"]["action_key"] == key


def test_failed_notification_is_retried_and_the_watch_waits_for_the_final_report(rig):
    class Flaky(MemoryNotifier):
        fail = True

        def notify(self, **kw):
            if self.fail:
                raise OSError("disk full")
            return super().notify(**kw)

    rig.coordinator.notifier = notifier = Flaky()
    rig.port.put("a", AgentState.DONE)
    rig.coordinator.watch(TEAM, policy())
    rig.at(30)
    assert notifier.items == [] and rig.store.watch(TEAM)["ended_at"] is None
    notifier.fail = False
    rig.at(60)
    assert [n["title"] for n in notifier.items] == ["Team finished"]
    assert rig.store.watch(TEAM)["ended_at"] is not None


def test_port_error_is_recorded_and_nothing_is_guessed(rig):
    rig.port.put("a")
    rig.coordinator.watch(TEAM, policy())
    rig.port.raises = True
    result = rig.at(5000)
    assert result["ok"] is False and rig.port.calls == [] and rig.notifier.items == []
    assert rig.store.last_event(TEAM, "port_error")["fields"]["error"] == "RuntimeError: registry offline"


def test_vanished_team_is_reported_once_and_unwatched(rig):
    rig.port.put("a")
    rig.coordinator.watch(TEAM, policy())
    rig.port.present = False
    rig.at(30)
    rig.at(60)
    assert [n["title"] for n in rig.notifier.items] == ["Team no longer visible"]
    assert rig.store.watch(TEAM)["end_reason"] == "vanished"


def test_unknown_team_and_bad_policy_are_refused(rig):
    rig.port.present = False
    with pytest.raises(StandingCoordinatorError, match="no such team"):
        rig.coordinator.watch(TEAM)
    with pytest.raises(StandingCoordinatorError, match="at least 30 seconds"):
        WatchPolicy(stall_after_seconds=1)
    with pytest.raises(StandingCoordinatorError, match="unknown policy fields"):
        WatchPolicy.from_row({"stall_after": 60})
    with pytest.raises(StandingCoordinatorError, match="invalid team ceilings"):
        WatchPolicy.from_row({"ceilings": {"max_calls": 1}})
    with pytest.raises(StandingCoordinatorError, match="names no request"):
        AgentSnapshot(agent_id="x", state=AgentState.WAITING, started_at=T0)
