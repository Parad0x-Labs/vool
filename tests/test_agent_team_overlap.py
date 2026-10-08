"""Agent teams: overlap alerts, pause and advice, on real processes, files and signals.

Every agent here is a real process started by the coordinator (``tests/agent_team_agent.py``);
every pause is a real SIGSTOP checked through the kernel's process status; every overlap is a
real write by a real child, grandchild or detached daemon. Nothing stands in for a process.

Acceptance ids from the design: O1-O6 (detection), P1-P3 (pause), R1-R2 (advice).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from core.agent_team import gate
from core.agent_team.advice import AgentView, recommend
from core.agent_team.coordinator import TeamCoordinator

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process signals")

AGENT = str(Path(__file__).with_name("agent_team_agent.py"))
LIMITS = {"max_usd": 1.0, "max_tokens": 100_000, "max_calls": 20, "wall_clock_seconds": 120}
TICK = 0.25


def _team(tmp_path: Path, *, git: bool = False, **kwargs) -> tuple[TeamCoordinator, Path]:
    ws = tmp_path / "ws"
    for d in ("a", "b", "a/x"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    if git:
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "b" / "session.py").write_text("SESSION = 1\n")
    (ws / "a" / "x" / "store.py").write_text("STORE = 1\n")
    team = TeamCoordinator(tmp_path / "team", workspace=ws, team_limits=LIMITS, tick_seconds=TICK, **kwargs)
    return team, ws


def _agent(key: str, title: str, importance: str, claims: list[str], *argv: str, **extra) -> dict:
    return {"key": key, "objective": title, "title": title, "importance": importance, "claims": claims,
            "command": [sys.executable, AGENT, *argv], "limits": dict(LIMITS), **extra}


def _ticks_until(team: TeamCoordinator, predicate, *, timeout: float = 40.0) -> tuple[list[dict], int]:
    alerts: list[dict] = []
    ticks = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alerts += team.tick()
        ticks += 1
        if predicate(alerts):
            return alerts, ticks
        time.sleep(TICK)
    raise AssertionError(f"condition not reached in {timeout}s; alerts={alerts}; status={team.status()}")


def _statuses(team: TeamCoordinator, title: str) -> list[str]:
    row = next(r for r in team.registry.agents() if r["title"] == title)
    out = []
    for member in team._members(row["agent_id"]):
        try:
            out.append(psutil.Process(member.pid).status())
        except psutil.Error:
            out.append("gone")
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def team_factory(tmp_path):
    made: list[TeamCoordinator] = []

    def make(**kwargs):
        team, ws = _team(tmp_path, **kwargs)
        made.append(team)
        return team, ws

    yield make
    for team in made:
        try:
            team.stop()
        finally:
            team.close()


def _o1_start(team: TeamCoordinator, ws: Path, *, scrub: bool = False, intruder_wall: float = 120) -> None:
    intruder = _agent("refactor", "Session store refactor", "normal", ["a"], "grandchild_writer",
                      str(ws / "b" / "session.py"), "6", *(["scrub"] if scrub else []))
    intruder["limits"] = {**LIMITS, "wall_clock_seconds": intruder_wall}
    team.start([
        _agent("login", "Login redirect fix", "high", ["b"], "own_writer", str(ws / "b" / "out"), "8", "1"),
        intruder,
    ])


def test_o1_grandchild_daemon_overlap_is_caught_paused_and_advised(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws)
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    alert = next(a for a in alerts if a["kind"] == "overlap")
    text = alert["alert"]
    # names the intruding process by command line and relation
    assert "b/session.py" in text
    assert "Session store refactor · normal" in text
    assert re.search(r"(grandchild|descendant|daemon)", text)
    assert "daemon line" not in text and "python" in text
    # the intruder's WHOLE lineage is stopped in the kernel, the owner keeps running
    assert _statuses(team, "Session store refactor") and set(_statuses(team, "Session store refactor")) == {"stopped"}
    assert "stopped" not in _statuses(team, "Login redirect fix")
    rec = alert["recommendation"]
    login = next(r for r in team.registry.agents() if r["title"] == "Login redirect fix")
    assert rec["keep"] == login["agent_id"] and rec["rule"] == "intruder"
    # B finishes its own work while A stays frozen
    _ticks_until(team, lambda _a: team.registry.agent(login["agent_id"])["state"] == "done", timeout=60)
    assert set(_statuses(team, "Session store refactor")) == {"stopped"}
    assert len(list((ws / "b" / "out").iterdir())) == 8


def test_o2_env_scrubbed_reparented_daemon_is_never_silently_missed(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws, scrub=True)
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] in ("overlap", "unattributed") for x in a))
    hit = next(a for a in alerts if a["kind"] in ("overlap", "unattributed"))
    assert "b/session.py" in hit["alert"]
    if hit["kind"] == "unattributed":
        assert "could not tell which agent" in hit["alert"]
        assert "orphaned process" in hit["alert"]  # the stray daemon is named, and left alone


def test_o3_parent_writing_into_its_sub_agents_claim_keeps_the_sub_agent(team_factory):
    team, ws = team_factory()
    team.start([
        _agent("store", "Store migration", "normal", ["a"], "grandchild_writer", str(ws / "a" / "x" / "store.py"), "6"),
        _agent("index", "Store index rebuild", "normal", ["a/x"], "sleeper", "20", parent="store"),
    ])
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    alert = next(a for a in alerts if a["kind"] == "overlap")
    rows = {r["title"]: r for r in team.registry.agents()}
    assert alert["recommendation"]["keep"] == rows["Store index rebuild"]["agent_id"]
    assert alert["recommendation"]["pause"] == rows["Store migration"]["agent_id"]
    assert set(_statuses(team, "Store migration")) == {"stopped"}


def test_o4_gate_refuses_a_write_into_another_claim_before_it_lands(team_factory):
    team, ws = team_factory()
    team.start([
        _agent("login", "Login redirect fix", "high", ["b"], "sleeper", "20"),
        _agent("docs", "Docs update", "low", ["a"], "sleeper", "20"),
    ])
    docs = next(r for r in team.registry.agents() if r["title"] == "Docs update")
    session = "openclaw:" + "d" * 20
    gate.bind_session(session, team, docs["agent_id"])
    try:
        before = _sha(ws / "b" / "session.py")
        allowed, reason = gate.check_write(str(ws / "b" / "session.py"), session_id=session)
        assert not allowed and "Login redirect fix · high" in reason
        assert _sha(ws / "b" / "session.py") == before
        ok, _ = gate.check_write(str(ws / "a" / "notes.md"), session_id=session)
        assert ok
        # an ordinary chat (no agent binding) is untouched by the gate
        assert gate.check_write(str(ws / "b" / "session.py"), session_id="openclaw:" + "e" * 20) == (True, "")
        assert gate.env_for_session(session) == {"VOOL_AGENT_RUN": docs["run_token"]}
    finally:
        gate.unbind_session(session)
    team.tick()
    assert any(e["kind"] == "gate_block" for e in team.registry.events())


def test_o5_open_file_alone_is_no_alert_open_plus_change_is_overlap(team_factory):
    team, ws = team_factory()
    reader = [sys.executable, "-c",
              "import sys,time\nf=open(sys.argv[1])\nf.read()\ntime.sleep(30)", str(ws / "b" / "session.py")]
    team.start([
        _agent("login", "Login redirect fix", "high", ["b"], "sleeper", "30"),
        {**_agent("lint", "Lint checker", "low", ["a"], "sleeper", "1"), "command": reader},
    ])
    end = time.monotonic() + 3
    alerts: list[dict] = []
    while time.monotonic() < end:
        alerts += team.tick()
        time.sleep(TICK)
    assert not [a for a in alerts if a["kind"] in ("overlap", "unattributed")]
    (ws / "b" / "session.py").write_text("SESSION = 2\n")
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    alert = next(a for a in alerts if a["kind"] == "overlap")
    lint = next(r for r in team.registry.agents() if r["title"] == "Lint checker")
    assert alert["recommendation"]["pause"] == lint["agent_id"]


def test_o6_git_hook_write_alerts_and_pauses_the_writer(team_factory):
    team, ws = team_factory(git=True)
    team.tick()
    team.start([_agent("hooks", "Pre-commit setup", "normal", ["a"], "hook_writer",
                       str(ws / ".git" / "hooks" / "pre-commit"), "20")])
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "git_internal" for x in a))
    alert = next(a for a in alerts if a["kind"] == "git_internal")
    assert ".git/hooks/pre-commit" in alert["alert"]
    assert "Pre-commit setup · normal" in alert["alert"]
    assert set(_statuses(team, "Pre-commit setup")) == {"stopped"}


def test_p1_contested_bytes_do_not_change_between_pause_and_decision(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws)
    _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    before = _sha(ws / "b" / "session.py")
    end = time.monotonic() + 3
    while time.monotonic() < end:
        team.tick()
        time.sleep(TICK)
    assert _sha(ws / "b" / "session.py") == before


def test_p2_decide_swap_then_stop_the_paused_lineage(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws)
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    conflict = next(a for a in alerts if a["kind"] == "overlap")["conflict_id"]
    result = team.decide(conflict, "swap")
    assert result["ok"] and result["running"] == "Session store refactor · normal"
    assert "stopped" not in _statuses(team, "Session store refactor")
    assert set(_statuses(team, "Login redirect fix")) == {"stopped"}
    login = next(r for r in team.registry.agents() if r["title"] == "Login redirect fix")
    pids = [m.pid for m in team._members(login["agent_id"])]
    assert team.stop("Login redirect fix · high")["ok"]
    assert not any(psutil.pid_exists(p) and psutil.Process(p).status() != "zombie" for p in pids)
    assert team.registry.agent(login["agent_id"])["state"] == "stopped"


def test_p2_decide_stop_ends_only_the_paused_agent(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws)
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    conflict = next(a for a in alerts if a["kind"] == "overlap")["conflict_id"]
    refactor = next(r for r in team.registry.agents() if r["title"] == "Session store refactor")
    pids = [m.pid for m in team._members(refactor["agent_id"])]
    assert team.decide(conflict, "stop")["ok"]
    assert team.registry.agent(refactor["agent_id"])["state"] == "stopped"
    assert not any(psutil.pid_exists(p) and psutil.Process(p).status() != "zombie" for p in pids)
    assert "stopped" not in _statuses(team, "Login redirect fix")


def test_p3_paused_time_is_not_billed_and_a_running_cap_hit_is_partial(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws, intruder_wall=5)
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    refactor = next(r for r in team.registry.agents() if r["title"] == "Session store refactor")
    end = time.monotonic() + 7  # longer than its whole 5 s limit
    while time.monotonic() < end:
        team.tick()
        time.sleep(TICK)
    row = team.registry.agent(refactor["agent_id"])
    assert row["state"] == "paused" and row["running_seconds"] < 5
    conflict = next(a for a in alerts if a["kind"] == "overlap")["conflict_id"]
    team.decide(conflict, "swap")
    _ticks_until(team, lambda _a: team.registry.agent(refactor["agent_id"])["state"] in ("partial", "done", "failed"),
                 timeout=30)
    row = team.registry.agent(refactor["agent_id"])
    assert row["state"] == "partial"
    assert "partial" in row["result"]["relay"] and "wall_clock" in row["result"]["relay"]


def _view(agent_id, title, importance, done=0, planned=0, launched=0.0, claims=("x",)):
    return AgentView(agent_id, f"{title} · {importance}", title, importance, done, planned, launched, claims)


def test_r1_each_rule_decides_and_the_reason_names_it():
    a = _view("a", "Login redirect fix", "normal", 1, 4, 10.0, ("src/auth",))
    b = _view("b", "Session store refactor", "normal", 3, 4, 20.0, ("src/store",))
    rec = recommend(a, b, intruder="b")
    assert (rec.keep, rec.rule) == ("a", "intruder") and "reached into files owned by Login redirect fix" in rec.reason
    hi = _view("c", "Payment webhook fix", "critical", 0, 4, 30.0)
    rec = recommend(a, hi)
    assert (rec.keep, rec.rule) == ("c", "importance") and "critical" in rec.reason
    rec = recommend(a, b)
    assert (rec.keep, rec.rule) == ("b", "progress") and "3 of 4 steps done" in rec.reason
    same = _view("d", "Docs update", "normal", 1, 4, 5.0)
    rec = recommend(a, same)
    assert (rec.keep, rec.rule) == ("d", "start_order") and "started first" in rec.reason


def test_r1_importance_rises_with_dependents_on_real_agents(team_factory):
    team, ws = team_factory()
    team.start([
        _agent("schema", "Schema migration", "normal", ["a"], "sleeper", "5"),
        _agent("api", "API handlers", "normal", ["b"], "sleeper", "5", depends_on=["schema"]),
    ])
    rows = {r["title"]: r for r in team.registry.agents()}
    assert rows["Schema migration"]["effective_importance"] == "high"
    assert rows["Schema migration"]["display_name"] == "Schema migration · high"
    assert rows["API handlers"]["state"] == "pending"  # waits for its dependency


def test_r2_alert_is_readable_and_carries_no_internal_ids(team_factory):
    team, ws = team_factory()
    _o1_start(team, ws)
    alerts, _ = _ticks_until(team, lambda a: any(x["kind"] == "overlap" for x in a))
    text = next(a for a in alerts if a["kind"] == "overlap")["alert"]
    for row in team.registry.agents():
        assert row["agent_id"] not in text and row["run_token"] not in text
    assert not re.search(r"\b(ag|cf|team)-[0-9a-f]{6,}\b", text)
    assert "Login redirect fix · high" in text and "Session store refactor · normal" in text
    assert "recommended" in text and "stays paused" in text
    assert "keep it paused until Login redirect fix finishes (recommended)" in text
