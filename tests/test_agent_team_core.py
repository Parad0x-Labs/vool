"""Agent teams: names, limits, kill-own-only, restart recovery, honesty, result cap, never-do,
batched decisions. Real processes and files throughout.

Acceptance ids from the design: N1, K1, K2, C1 (door level; the served model loop is in
``test_agent_team_model_loop.py``), H1, T1, plus build-plan step 5 (never-do, one question).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from core.agent_team import names
from core.agent_team.contract import AgentContract, ContractRefused, validate_plan
from core.agent_team.coordinator import TeamCoordinator
from core.agent_team.limits import BudgetRefused, Limits, LimitsRefused, TeamBudget
from core.agent_team.lineage import ENV_TOKEN, ProcId
from core.agent_team.policy import PUSH_DENIED_SCHEME, brief_constraints
from core.agent_team.report import RESULT_TOKEN_CAP, cap_text
from core.prompt_assembly_report import estimate_tokens

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process signals")

REPO = Path(__file__).resolve().parents[1]
AGENT = str(Path(__file__).with_name("agent_team_agent.py"))
LIMITS = {"max_usd": 1.0, "max_tokens": 100_000, "max_calls": 20, "wall_clock_seconds": 120}
TICK = 0.25


def _agent(key, title, importance, claims, *argv, **extra):
    return {"key": key, "objective": title, "title": title, "importance": importance, "claims": claims,
            "command": [sys.executable, AGENT, *argv], "limits": dict(LIMITS), **extra}


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    for d in ("a", "b", "c"):
        (root / d).mkdir(parents=True)
    return root


@pytest.fixture
def make_team(tmp_path, ws):
    made = []

    def make(name="team", **kwargs):
        team = TeamCoordinator(tmp_path / name, workspace=ws, team_limits=LIMITS, tick_seconds=TICK, **kwargs)
        made.append(team)
        return team

    yield make
    for team in made:
        try:
            team.stop()
        except Exception:
            pass
        team.close()


def _run(team, timeout=40.0):
    assert team.run_until_ended(timeout), team.status()


# ------------------------------------------------------------------------------------- N1
def test_n1_generic_names_are_refused_and_task_names_carry_importance(ws):
    for generic in ("agent", "Agent B", "worker-2", "subagent 1", "A", "helper", "task 3", "agent1"):
        assert names.is_generic(generic), generic
        with pytest.raises(names.NameRefused):
            names.validate_title(generic)
    assert names.display_name("Login redirect fix", "high") == "Login redirect fix · high"
    with pytest.raises(names.NameRefused):
        names.validate_title("Fix the login redirect after callback")  # six words
    assert names.title_from_objective("Please fix the login redirect after the OAuth callback. Then test.") == \
        "Fix login redirect after OAuth"
    lim = Limits(1, 1000, 5, 60)
    plan = [AgentContract(key="k1", objective="Docs update", limits=lim, claims=("a",), command=("true",)),
            AgentContract(key="k2", objective="Docs update", limits=lim, claims=("b",), command=("true",),
                          importance="low")]
    shown = [v.display_name for v in validate_plan(plan, workspace=ws)]
    assert shown == ["Docs update · normal", "Docs update · low"]
    plan[1] = AgentContract(key="k2", objective="Docs update", limits=lim, claims=("b",), command=("true",))
    shown = [v.display_name for v in validate_plan(plan, workspace=ws)]
    assert shown == ["Docs update (a/) · normal", "Docs update (b/) · normal"]
    with pytest.raises(ContractRefused):
        validate_plan([AgentContract(key="k", objective="x", title="worker 1", limits=lim, command=("true",))],
                      workspace=ws)


def test_plan_law_refuses_overlap_escape_depth_and_unbounded(ws):
    lim = Limits(1, 1000, 5, 60)
    one = AgentContract(key="p", objective="Parser rewrite", limits=lim, claims=("a",), command=("true",))
    with pytest.raises(ContractRefused, match="only one agent may write"):
        validate_plan([one, AgentContract(key="q", objective="Lexer cleanup", limits=lim, claims=("a/lex",),
                                          command=("true",))], workspace=ws)
    with pytest.raises(ContractRefused, match="escapes|relative"):
        validate_plan([AgentContract(key="e", objective="Escape attempt", limits=lim, claims=("../x",),
                                     command=("true",))], workspace=ws)
    (ws / "link").symlink_to("/")
    with pytest.raises(ContractRefused, match="outside the workspace"):
        validate_plan([AgentContract(key="s", objective="Symlink escape", limits=lim, claims=("link/etc",),
                                     command=("true",))], workspace=ws)
    child = AgentContract(key="c", objective="Lexer cleanup", limits=lim, claims=("b",), command=("true",), parent="p")
    with pytest.raises(ContractRefused, match="outside what its parent"):
        validate_plan([one, child], workspace=ws)
    chain = [one,
             AgentContract(key="c1", objective="Lexer cleanup", limits=lim, claims=("a/l",), command=("true",), parent="p"),
             AgentContract(key="c2", objective="Token table", limits=lim, claims=("a/l/t",), command=("true",), parent="c1")]
    with pytest.raises(ContractRefused, match="depth"):
        validate_plan(chain, workspace=ws, max_depth=2)
    assert len(validate_plan(chain, workspace=ws, max_depth=3)) == 3
    with pytest.raises(ContractRefused):
        validate_plan(chain, workspace=ws, max_depth=4)
    for bad in ({"max_usd": 0, "max_tokens": 1, "max_calls": 1, "wall_clock_seconds": 1},
                {"max_usd": float("inf"), "max_tokens": 1, "max_calls": 1, "wall_clock_seconds": 1},
                {"max_tokens": 1, "max_calls": 1, "wall_clock_seconds": 1}):
        with pytest.raises(LimitsRefused):
            Limits.from_dict(bad)


# ------------------------------------------------------------------------------------- K1
def test_k1_stop_never_touches_a_decoy_with_the_same_command_line(make_team):
    decoy = subprocess.Popen([sys.executable, AGENT, "sleeper", "30"], start_new_session=True)
    try:
        team = make_team()
        team.start([_agent("s", "Cache warmup", "low", ["a"], "sleeper", "30")])
        team.tick()
        row = team.registry.agents()[0]
        assert psutil.Process(row["pid"]).cmdline()[1:] == psutil.Process(decoy.pid).cmdline()[1:]
        assert team.stop()["stopped"] == ["Cache warmup · low"]
        assert decoy.poll() is None and psutil.Process(decoy.pid).status() != "stopped"
        assert not psutil.pid_exists(row["pid"]) or psutil.Process(row["pid"]).status() == "zombie"
    finally:
        decoy.terminate()
        decoy.wait(timeout=10)


def test_k1_recycled_pid_and_own_process_are_never_signalled(tmp_path, ws):
    decoy = subprocess.Popen([sys.executable, AGENT, "sleeper", "30"], start_new_session=True)
    try:
        team = TeamCoordinator(tmp_path / "t", workspace=ws, team_limits=LIMITS, tick_seconds=TICK)
        team.start([_agent("s", "Cache warmup", "low", ["a"], "sleeper", "2")])
        team.tick()
        rows = team.registry.agents()
        real_pid = rows[0]["pid"]
        # Disk says the agent is the decoy's pid, but from another process lifetime (recycled pid),
        # and a second agent claims to be THIS test process (pid and create time both true).
        team.registry.update_agent(rows[0]["agent_id"], pid=decoy.pid,
                                   create_time=psutil.Process(decoy.pid).create_time() - 100)
        me = psutil.Process(os.getpid())
        team.registry.add_agent({**{k: v for k, v in rows[0].items() if k not in ("agent_id", "key")},
                                 "agent_id": "ag-self", "key": "self", "pid": me.pid,
                                 "create_time": me.create_time(), "run_token": "self-token",
                                 "display_name": "Self check · low", "title": "Self check"})
        team.close()
        os.kill(real_pid, signal.SIGTERM)  # the agent this test's team started; recorded above
        recovered = TeamCoordinator(tmp_path / "t")
        assert "Cache warmup · low" in recovered.recovered["lost"]
        recovered.stop()
        recovered.close()
        assert decoy.poll() is None and psutil.Process(decoy.pid).status() != "stopped"
        # we are still here: the coordinator never signalled its own process
        assert psutil.Process(os.getpid()).status() != "stopped"
    finally:
        decoy.terminate()
        decoy.wait(timeout=10)


# ------------------------------------------------------------------------------------- K2
_COORDINATOR = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {repo!r})
    from core.agent_team.coordinator import TeamCoordinator
    team = TeamCoordinator({team!r}, workspace={ws!r}, team_limits={limits!r}, tick_seconds=0.25)
    team.start({plan!r})
    team.tick()
    print("ready", flush=True)
    while True:
        team.tick()
        time.sleep(0.25)
""")


def _token_count(token: str) -> int:
    count = 0
    for proc in psutil.process_iter(attrs=("pid",)):
        try:
            if proc.environ().get(ENV_TOKEN) == token and proc.status() != "zombie":
                count += 1
        except psutil.Error:
            continue
    return count


def test_k2_restart_readopts_only_the_same_processes_and_never_relaunches(tmp_path, ws):
    team_dir = tmp_path / "team"
    plan = [_agent("w", "Search index rebuild", "normal", ["a"], "sleeper", "40"),
            _agent("x", "Thumbnail cache", "low", ["b"], "sleeper", "40")]
    script = _COORDINATOR.format(repo=str(REPO), team=str(team_dir), ws=str(ws), limits=LIMITS, plan=plan)
    first = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
                             start_new_session=True)
    try:
        assert first.stdout.readline().strip() == "ready"
    finally:
        first.kill()  # the coordinator this test started (kill -9 mid-run)
        first.wait(timeout=20)
    team = TeamCoordinator(team_dir)
    try:
        rows = {r["title"]: r for r in team.registry.agents()}
        assert team.recovered == {"adopted": ["Search index rebuild · normal", "Thumbnail cache · low"], "lost": []}
        tokens = {t: rows[t]["run_token"] for t in rows}
        assert all(_token_count(tok) == 1 for tok in tokens.values())
        # one agent dies while no coordinator watches; a restart reports it lost, never relaunches it
        os.kill(rows["Thumbnail cache"]["pid"], signal.SIGKILL)
        team.close()
        time.sleep(0.5)
        again = TeamCoordinator(team_dir)
        try:
            assert again.recovered["lost"] == ["Thumbnail cache · low"]
            assert again.recovered["adopted"] == ["Search index rebuild · normal"]
            for _ in range(6):
                again.tick()
                time.sleep(TICK)
            assert _token_count(tokens["Search index rebuild"]) == 1
            assert _token_count(tokens["Thumbnail cache"]) == 0
            lost = again.registry.agent(rows["Thumbnail cache"]["agent_id"])
            assert lost["state"] == "lost" and "not relaunched" in lost["result"]["relay"]
            again.stop()
            adopted = again.registry.agent(rows["Search index rebuild"]["agent_id"])
            assert adopted["state"] == "stopped"
        finally:
            again.close()
    finally:
        pass


# ------------------------------------------------------------------------------------- C1
def test_c1_each_limit_refuses_the_reservation_before_the_call():
    budget = TeamBudget(Limits(max_usd=1.0, max_tokens=10_000, max_calls=3, wall_clock_seconds=60))
    budget.add_agent("tok", Limits(1.0, 1_000, 10, 60))
    budget.add_agent("usd", Limits(0.05, 10_000, 10, 60))
    budget.add_agent("calls", Limits(1.0, 10_000, 1, 60))
    budget.add_agent("clock", Limits(1.0, 10_000, 10, 0.2))
    with pytest.raises(BudgetRefused) as exc:
        budget.reserve("tok", tokens=1_001, usd=0.0)
    assert (exc.value.axis, exc.value.scope) == ("tokens", "agent")
    with pytest.raises(BudgetRefused) as exc:
        budget.reserve("usd", tokens=10, usd=0.06)
    assert exc.value.axis == "usd"
    r = budget.reserve("calls", tokens=10, usd=0.0)
    budget.settle(r, tokens=5, usd=0.0)
    with pytest.raises(BudgetRefused) as exc:
        budget.reserve("calls", tokens=10, usd=0.0)
    assert exc.value.axis == "calls"
    budget.clock_for("clock").start()
    time.sleep(0.3)
    with pytest.raises(BudgetRefused) as exc:
        budget.reserve("clock", tokens=10, usd=0.0)
    assert exc.value.axis == "wall_clock"
    # the TEAM ceiling binds across agents: 3 calls total, one used above
    budget.reserve("tok", tokens=10, usd=0.0)
    budget.reserve("usd", tokens=10, usd=0.0)
    with pytest.raises(BudgetRefused) as exc:
        budget.reserve("tok", tokens=10, usd=0.0)
    assert (exc.value.axis, exc.value.scope) == ("calls", "team")
    # refused reservations booked nothing
    assert budget.snapshot()["calls"] == 3


# ------------------------------------------------------------------------------------- H1
def test_h1_reports_are_checked_against_what_the_agent_did(make_team, ws):
    team = make_team()
    team.start([
        _agent("liar", "Parser bug fix", "normal", ["a"], "liar", "a/parser.py"),
        _agent("quiet", "Config audit", "normal", ["b"], "silent_writer", str(ws / "b" / "settings.toml")),
        _agent("crash", "Migration runner", "normal", ["c"], "fail", "3"),
    ])
    _run(team)
    rows = {r["title"]: r for r in team.registry.agents()}
    liar = rows["Parser bug fix"]["result"]
    assert liar["verdict"]["claimed_not_observed"] == ["a/parser.py"]
    assert liar["verdict"]["unsupported_claim"] and "claimed but not observed: a/parser.py" in liar["relay"]
    quiet = rows["Config audit"]["result"]
    assert quiet["verdict"]["changed_not_reported"] == ["b/settings.toml"]
    crash = rows["Migration runner"]
    assert crash["state"] == "failed" and "exited with code 3" in crash["result"]["relay"]


# ------------------------------------------------------------------------------------- T1
def test_t1_relayed_result_is_capped_and_the_full_text_stays_on_disk(make_team):
    team = make_team()
    team.start([_agent("big", "Changelog draft", "low", ["a"], "big_result", "3000")])
    _run(team)
    row = team.registry.agents()[0]
    relay = row["result"]["relay"]
    assert estimate_tokens(relay) <= RESULT_TOKEN_CAP and row["result"]["truncated"]
    full = team.team_dir / row["result"]["full_result"]
    assert full.read_text().count("word") == 3000
    assert row["result"]["full_result"] in relay
    assert estimate_tokens(team.team_result()) <= RESULT_TOKEN_CAP
    short, cut = cap_text("short answer")
    assert (short, cut) == ("short answer", False)


# --------------------------------------------------------------------------- never-do (step 5)
def test_children_inherit_never_do_push_rewrite_and_no_keys(make_team, ws, monkeypatch):
    subprocess.run(["git", "init", "-q", str(ws / "c")], check=True)
    subprocess.run(["git", "-C", str(ws / "c"), "remote", "add", "origin", str(ws / "remote.git")], check=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-not-real")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "not-real")
    team = make_team()
    team.start([_agent("env", "Release notes check", "normal", ["b"], "env_dump", str(ws / "b" / "env.txt"),
                       str(ws / "c"))])
    _run(team)
    env = dict(line.split("=", 1) for line in (ws / "b" / "env.txt").read_text().splitlines() if "=" in line)
    assert "OPENROUTER_API_KEY" not in env and "SOME_SERVICE_TOKEN" not in env
    assert env["PUSH_URL"] == f"{PUSH_DENIED_SCHEME}:{ws / 'remote.git'}"
    assert env["VOOL_AGENT_DEPTH"] == "1" and env[ENV_TOKEN]
    text = brief_constraints("Fix the importer. Never push anything to git.")
    assert "From the user: Never push anything to git." in text and "Never: git push." in text


def test_sub_agents_only_through_their_own_parent_token_and_depth(make_team):
    team = make_team()
    team.start([_agent("p", "Billing refactor", "normal", ["a"], "sleeper", "20")])
    parent = team.registry.agents()[0]
    with pytest.raises(ContractRefused, match="only a running agent"):
        team.start([_agent("s", "Invoice totals", "normal", ["a/inv"], "sleeper", "1", parent="p")],
                   parent_token="forged")
    team.start([_agent("s", "Invoice totals", "normal", ["a/inv"], "sleeper", "1", parent="p")],
               parent_token=parent["run_token"])
    child = next(r for r in team.registry.agents() if r["key"] == "s")
    with pytest.raises(ContractRefused, match="depth"):
        team.start([_agent("g", "Tax rounding", "normal", ["a/inv/tax"], "sleeper", "1", parent="s")],
                   parent_token=child["run_token"])


# ------------------------------------------------------------------- decisions (step 5)
def test_two_agents_needing_a_decision_produce_one_question(make_team):
    team = make_team()
    team.start([
        _agent("db", "Database upgrade", "high", ["a"], "ask", "Drop the legacy table?"),
        _agent("api", "API version bump", "normal", ["b"], "ask", "Remove the v1 endpoints?"),
    ])
    _run(team)
    question = team.status()["question"]
    assert question.startswith("2 agents need a decision")
    assert "Database upgrade · high: Drop the legacy table?" in question
    assert "API version bump · normal: Remove the v1 endpoints?" in question
    assert team.answer("Database upgrade", "yes, drop it")["ok"]
    assert team.answer("API version bump · normal", "no, keep v1")["ok"]
    _run(team)
    rows = {r["title"]: r for r in team.registry.agents()}
    assert rows["Database upgrade"]["state"] == "done" and "yes, drop it" in rows["Database upgrade"]["result"]["relay"]
    assert team.status()["question"] == ""


def test_registry_events_are_append_only(make_team):
    team = make_team()
    import sqlite3

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        team.registry._conn.execute("UPDATE events SET kind='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        team.registry._conn.execute("DELETE FROM events")
