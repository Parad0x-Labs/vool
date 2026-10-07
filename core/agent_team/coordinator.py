"""The team coordinator: plain code, no model, no tokens.

It owns the contracts, the leases (claims), the limits, the registry and the overlap watch. It
launches each agent with a run token, watches every process the agent's lineage spawns, pauses
on overlap with a recommendation, checks each report against what the agent actually did, caps
what goes back to the chat, and survives its own restart by re-adopting only processes that are
provably the ones it started.

It signals only processes in a registered lineage, verified by ``(pid, create_time)`` at the
moment of signalling, never its own process or any ancestor, never anything by name.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import psutil

from core.agent_team import advice, gate, policy
from core.agent_team.contract import AgentContract, ContractRefused, ValidatedAgent, validate_plan
from core.agent_team.limits import BudgetRefused, Limits, TeamBudget
from core.agent_team.lineage import (
    ENV_TOKEN,
    AgentRoot,
    LineageTracker,
    Member,
    ProcId,
    describe,
    identify,
    verified_process,
)
from core.agent_team.overlap import Finding, OverlapWatch, ScopeScanner, diff_snapshots
from core.agent_team.registry import END_STATES, LIVE_STATES, Registry
from core.agent_team.report import RESULT_TOKEN_CAP, check_report, relay_line

LEFTOVER_GRACE_SECONDS = 10.0
STOP_GRACE_SECONDS = 3.0


def _now() -> float:
    return time.time()


class TeamCoordinator:
    def __init__(
        self,
        team_dir: str | os.PathLike[str],
        *,
        workspace: str | os.PathLike[str] | None = None,
        team_limits: Limits | Mapping[str, Any] | None = None,
        tick_seconds: float = 1.0,
        auto_resume_recommended: bool = True,
        max_depth: int = 2,
        chat_text: str = "",
        model_runner: Any = None,
        output_ceiling: int = 2048,
        on_alert: Any = None,
    ) -> None:
        self.team_dir = Path(team_dir)
        self.registry = Registry(self.team_dir)
        self.tick_seconds = float(tick_seconds)
        self.auto_resume_recommended = bool(auto_resume_recommended)
        self.max_depth = int(max_depth)
        self.chat_text = str(chat_text or "")
        self.model_runner = model_runner
        self.output_ceiling = int(output_ceiling)
        #: Called with each new alert (overlap, git internals, unattributed change) so it reaches
        #: the user as it happens, not when they next ask for status.
        self.on_alert = on_alert
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._model_threads: dict[str, threading.Thread] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._responses: dict[str, Any] = {}
        self._launch_snapshots: dict[str, dict[str, tuple]] = {}
        self._root_exited_at: dict[str, float] = {}
        self._outside_writes: dict[str, set[str]] = {}
        self._stopping: set[str] = set()
        self._cap_hit: dict[str, str] = {}
        self._loop: threading.Thread | None = None
        self._loop_stop = threading.Event()
        self.recovered: dict[str, list[str]] = {"adopted": [], "lost": []}

        me = identify(os.getpid())
        team = self.registry.team()
        if team is None:
            if workspace is None or team_limits is None:
                raise ContractRefused("a new team needs a workspace and team limits")
            limits = team_limits if isinstance(team_limits, Limits) else Limits.from_dict(dict(team_limits))
            self.team_id = f"team-{uuid.uuid4().hex[:12]}"
            self.workspace = Path(os.path.realpath(workspace))
            self.registry.create_team(self.team_id, workspace=str(self.workspace), limits=limits.to_dict(),
                                      coordinator=(me.pid, me.create_time) if me else (os.getpid(), 0.0))
            self.team_limits = limits
            fresh = True
        else:
            self.team_id = team["team_id"]
            self.workspace = Path(team["workspace"])
            self.team_limits = Limits.from_dict(team["limits"])
            self.registry.set_team(self.team_id, coordinator_pid=os.getpid(),
                                   coordinator_create_time=me.create_time if me else 0.0)
            fresh = False
        self.budget = TeamBudget(self.team_limits)
        self.tracker = LineageTracker()
        self.watch = OverlapWatch(self.workspace)
        self.scanner = ScopeScanner(self.workspace)
        if not fresh:
            self._recover()

    # ================================================================ helpers
    def _agent_dir(self, agent_id: str) -> Path:
        path = self.team_dir / "agents" / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_token(self, agent_id: str) -> str:
        row = self.registry.agent(agent_id)
        return row["run_token"] if row else ""

    def _views(self) -> dict[str, advice.AgentView]:
        out = {}
        for row in self.registry.agents():
            out[row["agent_id"]] = advice.AgentView(
                agent_id=row["agent_id"], display_name=row["display_name"], title=row["title"],
                effective_importance=row["effective_importance"], progress_done=row["progress_done"],
                progress_planned=row["progress_planned"], launched_at=row["launched_at"] or 0.0,
                claims=tuple(row["contract"].get("claims") or ()),
            )
        return out

    def _validated_existing(self) -> dict[str, ValidatedAgent]:
        out: dict[str, ValidatedAgent] = {}
        for row in self.registry.agents():
            if row["state"] in END_STATES and row["state"] not in ("needs_decision",):
                # Ended agents release their claims; their keys stay valid as dependencies.
                contract = AgentContract.from_dict({**row["contract"], "claims": [], "mode": "read",
                                                    "command": row["contract"].get("command") or ["true"]})
            else:
                contract = AgentContract.from_dict(row["contract"])
            out[row["key"]] = ValidatedAgent(contract, row["title"], row["importance"],
                                             row["effective_importance"], row["display_name"], row["depth"])
        return out

    def _key_to_id(self) -> dict[str, str]:
        return {row["key"]: row["agent_id"] for row in self.registry.agents()}

    # ================================================================== start
    def start(self, plan: Sequence[AgentContract | Mapping[str, Any]], *, parent_token: str = "") -> list[dict[str, Any]]:
        """Validate a plan and launch every agent whose dependencies are done.

        ``parent_token``: an agent asking the team to start sub-agents presents its own run token;
        the sub-agents' ``parent`` must be that agent and the depth ceiling applies."""
        contracts = [p if isinstance(p, AgentContract) else AgentContract.from_dict(p) for p in plan]
        with self._lock:
            if parent_token:
                caller = next((r for r in self.registry.agents() if r["run_token"] == parent_token), None)
                if caller is None or caller["state"] not in LIVE_STATES:
                    raise ContractRefused("only a running agent of this team can start sub-agents")
                for contract in contracts:
                    if contract.parent != caller["key"]:
                        raise ContractRefused("an agent can only start its own sub-agents")
            validated = validate_plan(contracts, workspace=self.workspace, existing=self._validated_existing(),
                                      max_depth=self.max_depth)
            keys = self._key_to_id()
            created = []
            for item in validated:
                agent_id = f"ag-{uuid.uuid4().hex[:10]}"
                keys[item.contract.key] = agent_id
                parent_id = keys.get(item.contract.parent, "") if item.contract.parent else ""
                self.registry.add_agent({
                    "agent_id": agent_id, "team_id": self.team_id, "key": item.contract.key,
                    "display_name": item.display_name, "title": item.title, "importance": item.importance,
                    "effective_importance": item.effective_importance, "depth": item.depth,
                    "parent_id": parent_id, "contract": item.contract.to_dict(), "state": "pending",
                    "run_token": uuid.uuid4().hex, "progress_planned": int(item.contract.planned_steps or 0),
                    "spend": {}, "result": {},
                })
                self.budget.add_agent(agent_id, item.contract.limits)
                self.registry.event(self.team_id, "agent_planned", agent_id, name=item.display_name,
                                    claims=list(item.contract.claims))
                created.append({"agent": item.display_name, "state": "pending"})
            self._refresh_effective_importance()
            self._launch_ready()
            return [self._public_row(self.registry.agent(keys[c.key])) for c in contracts]

    def _refresh_effective_importance(self) -> None:
        from core.agent_team.names import raise_importance

        rows = self.registry.agents()
        dependents: dict[str, int] = {}
        for row in rows:
            for dep in row["contract"].get("depends_on") or ():
                dependents[dep] = dependents.get(dep, 0) + 1
        for row in rows:
            effective = raise_importance(row["importance"], dependents.get(row["key"], 0))
            if effective != row["effective_importance"]:
                self.registry.update_agent(row["agent_id"], effective_importance=effective)

    def _launch_ready(self) -> None:
        rows = self.registry.agents()
        state_by_key = {r["key"]: r["state"] for r in rows}
        for row in rows:
            if row["state"] != "pending":
                continue
            deps = row["contract"].get("depends_on") or ()
            if any(state_by_key.get(d) in ("failed", "stopped", "lost", "refused") for d in deps):
                self._finish(row["agent_id"], forced_state="refused",
                             note="a task it depends on did not finish, so it was not started")
                continue
            if all(state_by_key.get(d) == "done" for d in deps):
                self._launch(row)

    def _launch(self, row: dict[str, Any]) -> None:
        contract = AgentContract.from_dict(row["contract"])
        agent_id = row["agent_id"]
        adir = self._agent_dir(agent_id)
        # Intent BEFORE the process exists: a coordinator that dies between these two lines
        # finds `launching` + the run token on restart and searches for the token instead of
        # relaunching. No path through recovery can start the same agent twice.
        self.registry.update_agent(agent_id, state="launching")
        snapshot = self.scanner.capture(contract.claims) if contract.claims else {}
        self._launch_snapshots[agent_id] = snapshot
        (adir / "launch_snapshot.json").write_text(json.dumps({k: list(v) for k, v in snapshot.items()}))
        for name in ("result.json", "status.jsonl", "PAUSE"):
            (adir / name).unlink(missing_ok=True)
        if contract.kind == "process":
            self._launch_process(row, contract, adir)
        else:
            self._launch_model(row, contract, adir)

    def _child_env(self, row: dict[str, Any], contract: AgentContract, adir: Path, decision: str = "") -> dict[str, str]:
        extra = {
            ENV_TOKEN: row["run_token"],
            "VOOL_AGENT_STATUS": str(adir / "status.jsonl"),
            "VOOL_AGENT_RESULT": str(adir / "result.json"),
            "VOOL_AGENT_PAUSE": str(adir / "PAUSE"),
            "VOOL_AGENT_DEPTH": str(row["depth"]),
            "VOOL_AGENT_TEAM": str(self.team_dir),
            "VOOL_AGENT_NAME": row["display_name"],
            "VOOL_AGENT_CLAIMS": os.pathsep.join(contract.claims),
        }
        if decision:
            extra["VOOL_AGENT_DECISION"] = decision
        return policy.child_environment(os.environ, extra=extra)

    def _launch_process(self, row: dict[str, Any], contract: AgentContract, adir: Path, decision: str = "") -> None:
        agent_id = row["agent_id"]
        self._root_exited_at.pop(agent_id, None)
        self._stopping.discard(agent_id)
        self._cap_hit.pop(agent_id, None)
        cwd = self.workspace / contract.cwd if contract.cwd else self.workspace
        with open(adir / "stdout.log", "ab") as out, open(adir / "stderr.log", "ab") as err:
            try:
                proc = subprocess.Popen(
                    list(contract.command), cwd=str(cwd), env=self._child_env(row, contract, adir, decision),
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True,
                )
            except OSError as exc:
                self._finish(agent_id, forced_state="failed", note=f"could not start: {exc}")
                return
        ident = identify(proc.pid)
        self._procs[agent_id] = proc
        self.registry.update_agent(agent_id, state="running", pid=proc.pid,
                                   create_time=ident.create_time if ident else 0.0, launched_at=_now())
        self.budget.clock_for(agent_id).start()
        self.registry.event(self.team_id, "agent_launched", agent_id, pid=proc.pid, agent_kind="process")

    def _launch_model(self, row: dict[str, Any], contract: AgentContract, adir: Path, decision: str = "") -> None:
        agent_id = row["agent_id"]
        if self.model_runner is None:
            self._finish(agent_id, forced_state="refused", note="no model runner is attached to this team")
            return
        if contract.mode == "write" and not gate.INSTALLED:
            self._finish(agent_id, forced_state="refused",
                         note="write-mode model agents need VOOL's file tools to ask the write gate first; "
                              "that hook is not installed, so this agent was not started")
            return
        from core.agent_team import model_agent

        session_id = model_agent.agent_session_id(self.team_id, agent_id)
        prompt = model_agent.build_prompt(
            display_name=row["display_name"], objective=contract.objective, claims=contract.claims,
            mode=contract.mode, constraints=policy.brief_constraints(self.chat_text),
            extra=contract.prompt, decision=decision,
        )
        cancel = threading.Event()
        self._cancel[agent_id] = cancel
        gate.bind_session(session_id, self, agent_id)
        self.registry.update_agent(agent_id, state="running", session_id=session_id, launched_at=_now())
        self.budget.clock_for(agent_id).start()
        self.registry.event(self.team_id, "agent_launched", agent_id, agent_kind="model", model=contract.model,
                            session_id=session_id)

        def _run() -> None:
            outcome: dict[str, Any] = {}
            try:
                tokens, usd = model_agent.reservation_for(contract.model, prompt, self.output_ceiling)
                while self._is_paused(agent_id) and not cancel.is_set():
                    time.sleep(0.05)
                if cancel.is_set():
                    outcome = {"stopped": True}
                    return
                reservation = self.budget.reserve(agent_id, tokens=tokens, usd=usd)
                self.registry.event(self.team_id, "spend_reserved", agent_id, tokens=tokens, usd=usd)
                try:
                    turn = self.model_runner.run(
                        session_id=session_id, model=contract.model, prompt=prompt, mode=contract.mode,
                        turn_id=f"{agent_id}-{uuid.uuid4().hex[:6]}", cancel=cancel,
                        on_response=lambda resp: self._responses.__setitem__(agent_id, resp),
                        workspace=str(self.workspace),
                    )
                except Exception:
                    self.budget.settle(reservation, tokens=tokens, usd=usd)
                    raise
                if turn.usage_complete:
                    self.budget.settle(reservation, tokens=turn.prompt_tokens + turn.output_tokens,
                                       usd=turn.usd if turn.usd is not None else usd)
                else:
                    self.budget.settle(reservation, tokens=tokens, usd=usd)
                parsed = model_agent.parse_result_line(turn.text)
                (adir / "result_full.txt").write_text(turn.text)
                outcome = {"text": turn.text, "parsed": parsed, "receipts": turn.receipts,
                           "model_actual": turn.model_actual, "usage_complete": turn.usage_complete}
            except BudgetRefused as exc:
                self._cap_hit[agent_id] = exc.axis
                outcome = {"refused": f"{exc.scope} {exc.axis} limit: {exc.detail}"}
            except model_agent.ModelAgentRefused as exc:
                outcome = {"refused": str(exc)}
            except Exception as exc:  # transport fault: a failed agent, typed
                outcome = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                gate.unbind_session(session_id)
                (adir / "model_outcome.json").write_text(json.dumps(outcome, default=str))

        thread = threading.Thread(target=_run, name=f"agent-{agent_id}", daemon=True)
        self._model_threads[agent_id] = thread
        thread.start()

    # =============================================================== gate API
    def gate_write(self, agent_id: str, abs_path: str) -> tuple[bool, str]:
        rel = ""
        with contextlib.suppress(ValueError):
            rel = Path(abs_path).relative_to(self.workspace).as_posix()
        with self._lock:
            self._sync_claims()
            owner = self.watch.owner_of(rel) if rel else ""
            allowed = bool(rel) and owner == agent_id
            self.watch.gate_note(agent_id, abs_path, allowed=allowed)
            row = self.registry.agent(agent_id)
            name = row["display_name"] if row else "this agent"
            if allowed:
                return True, ""
            if owner:
                other = self.registry.agent(owner)
                return False, (f"{name} may not write {rel}: it belongs to "
                               f"{other['display_name'] if other else 'another agent'}")
            return False, f"{name} may not write {rel or abs_path}: it is outside its claimed paths"

    # ================================================================== state
    def _is_paused(self, agent_id: str) -> bool:
        row = self.registry.agent(agent_id)
        return bool(row and row["state"] == "paused")

    def _roots(self) -> list[AgentRoot]:
        roots = []
        for row in self.registry.agents():
            if row["state"] in ("pending",):
                continue
            root = ProcId(row["pid"], row["create_time"]) if row["pid"] else None
            roots.append(AgentRoot(row["agent_id"], root, row["run_token"], row["launched_at"] or _now()))
        return roots

    def _sync_claims(self) -> None:
        claims: dict[str, tuple[str, ...]] = {}
        parents: dict[str, str] = {}
        for row in self.registry.agents():
            if row["state"] in END_STATES and row["state"] != "needs_decision":
                continue
            if row["contract"].get("mode") == "write":
                claims[row["agent_id"]] = tuple(row["contract"].get("claims") or ())
            parents[row["agent_id"]] = row["parent_id"]
        self.watch.set_claims(claims, parents)

    # =================================================================== tick
    def tick(self) -> list[dict[str, Any]]:
        """One coordinator step. Returns the alerts raised in this step."""
        alerts: list[dict[str, Any]] = []
        with self._lock:
            self._sync_claims()
            roots = self._roots()
            lineages, strays = self.tracker.scan(roots)
            rows = {r["agent_id"]: r for r in self.registry.agents()}
            for agent_id, row in rows.items():
                if row["state"] not in ("running", "paused", "launching"):
                    continue
                self._read_progress(agent_id)
                if row["contract"].get("kind") == "process":
                    self._check_process(agent_id, row, lineages.get(agent_id, {}))
                else:
                    self._check_model(agent_id, row)
            rows = {r["agent_id"]: r for r in self.registry.agents()}
            running = {a: r["state"] == "running" for a, r in rows.items()}
            findings = self.watch.tick(lineages, strays, running,
                                       resample=lambda: self.tracker.scan(self._roots())[0])
            for finding in findings:
                alert = self._handle(finding, lineages)
                if alert:
                    alerts.append(alert)
                    if self.on_alert is not None:
                        try:
                            self.on_alert(alert)
                        except Exception as exc:  # delivery failed: say so in the trail, keep watching
                            self.registry.event(self.team_id, "alert_delivery_failed", "",
                                                conflict_id=alert.get("conflict_id"), error=f"{type(exc).__name__}: {exc}")
            self._launch_ready()
            for agent_id, row in rows.items():
                if row["state"] in ("running", "paused"):
                    self.registry.update_agent(
                        agent_id, running_seconds=self.budget.clock_for(agent_id).elapsed(),
                        spend=self.budget.snapshot(agent_id),
                    )
        return alerts

    def _read_progress(self, agent_id: str) -> None:
        path = self._agent_dir(agent_id) / "status.jsonl"
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            done, planned = payload.get("done"), payload.get("planned")
            fields: dict[str, Any] = {}
            if isinstance(done, int):
                fields["progress_done"] = max(0, done)
            if isinstance(planned, int):
                fields["progress_planned"] = max(0, planned)
            self.registry.update_agent(agent_id, **fields)
            return

    def _check_process(self, agent_id: str, row: dict[str, Any], members: Mapping[int, Member]) -> None:
        proc = self._procs.get(agent_id)
        root_alive: bool
        exit_code: int | None = None
        exit_observed = False
        if proc is not None:
            code = proc.poll()
            root_alive = code is None
            if code is not None:
                exit_code, exit_observed = code, True
        else:  # re-adopted after a restart: we are not its parent and cannot read its exit code
            root_alive = verified_process(ProcId(row["pid"], row["create_time"])) is not None if row["pid"] else False
        if row["state"] == "running" and self.budget.wall_clock_exhausted(agent_id):
            self._cap_hit[agent_id] = "wall_clock"
            self._terminate_lineage(agent_id)
            self._finish(agent_id, exit_code=exit_code, exit_observed=exit_observed)
            return
        if root_alive:
            return
        others = [m for m in members.values() if m.depth != 0]
        if others:
            started = self._root_exited_at.setdefault(agent_id, time.monotonic())
            if row["state"] == "paused":
                return  # a paused lineage is the user's to decide; it is not left over
            if time.monotonic() - started < LEFTOVER_GRACE_SECONDS:
                return
            self.registry.event(self.team_id, "leftovers_stopped", agent_id, processes=describe({m.pid: m for m in others}))
            self._terminate_lineage(agent_id)
        self._finish(agent_id, exit_code=exit_code, exit_observed=exit_observed)

    def _check_model(self, agent_id: str, row: dict[str, Any]) -> None:
        thread = self._model_threads.get(agent_id)
        if thread is None:
            if row["state"] in ("running", "paused"):
                # A model turn whose thread died with a previous coordinator.
                self._finish(agent_id, forced_state="lost", note="its chat turn was in flight when the coordinator restarted")
            return
        if row["state"] == "running" and self.budget.wall_clock_exhausted(agent_id):
            self._cap_hit[agent_id] = "wall_clock"
            self._cancel_model(agent_id)
        if thread.is_alive():
            return
        self._finish(agent_id)

    def _cancel_model(self, agent_id: str) -> None:
        event = self._cancel.get(agent_id)
        if event is not None:
            event.set()
        response = self._responses.get(agent_id)
        if response is not None:
            with contextlib.suppress(Exception):
                response.close()

    # ============================================================== signalling
    def _members(self, agent_id: str) -> list[Member]:
        lineages, _ = self.tracker.scan(self._roots())
        return list(lineages.get(agent_id, {}).values())

    def _signal(self, member: Member, sig: int) -> bool:
        if member.pid in self.tracker.protected:
            return False
        proc = verified_process(member.ident)
        if proc is None:
            return False
        try:
            proc.send_signal(sig)  # psutil re-checks the identity before signalling
            return True
        except (psutil.Error, OSError):
            return False

    def _freeze(self, agent_ids: Iterable[str]) -> dict[str, list[int]]:
        """SIGSTOP every verified member of each lineage, re-scanning until no new member
        appears (a process stopped cannot fork, so this converges)."""
        ids = list(dict.fromkeys(agent_ids))
        frozen: dict[str, set[int]] = {a: set() for a in ids}
        for agent_id in ids:
            (self._agent_dir(agent_id) / "PAUSE").write_text("paused by the team coordinator\n")
        for _ in range(8):
            lineages, _strays = self.tracker.scan(self._roots())
            new = 0
            for agent_id in ids:
                for member in lineages.get(agent_id, {}).values():
                    if member.pid in frozen[agent_id]:
                        continue
                    if self._signal(member, signal.SIGSTOP):
                        frozen[agent_id].add(member.pid)
                        new += 1
            if not new:
                break
        for agent_id in ids:
            row = self.registry.agent(agent_id)
            if row and row["state"] in ("running", "launching"):
                self.registry.update_agent(agent_id, state="paused")
            with contextlib.suppress(KeyError):
                self.budget.clock_for(agent_id).pause()
        return {a: sorted(p) for a, p in frozen.items()}

    def _thaw(self, agent_id: str) -> list[int]:
        resumed = []
        for member in self._members(agent_id):
            if self._signal(member, signal.SIGCONT):
                resumed.append(member.pid)
        (self._agent_dir(agent_id) / "PAUSE").unlink(missing_ok=True)
        row = self.registry.agent(agent_id)
        if row and row["state"] == "paused":
            self.registry.update_agent(agent_id, state="running", pause_reason="")
        with contextlib.suppress(KeyError):
            self.budget.clock_for(agent_id).resume()
        return resumed

    def _terminate_lineage(self, agent_id: str) -> None:
        self._stopping.add(agent_id)
        self._freeze([agent_id])
        members = self._members(agent_id)
        for member in members:
            self._signal(member, signal.SIGTERM)
            self._signal(member, signal.SIGCONT)
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not any(verified_process(m.ident) for m in members):
                break
            time.sleep(0.05)
        for member in members:
            self._signal(member, signal.SIGKILL)
        proc = self._procs.get(agent_id)
        if proc is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=STOP_GRACE_SECONDS)

    # ================================================================ findings
    def _handle(self, finding: Finding, lineages: Mapping[str, Mapping[int, Member]]) -> dict[str, Any] | None:
        views = self._views()
        evidence = [ev.to_dict() for ev in finding.evidence]
        if finding.kind == "overlap" and finding.owner and finding.intruder:
            pair = sorted([finding.owner, finding.intruder])
            existing = next((c for c in self.registry.conflicts("open") if sorted(c["agents"]) == pair), None)
            if existing is not None:
                paths = sorted(set(existing["paths"]) | set(finding.paths))
                self.registry.update_conflict(existing["conflict_id"], paths=paths,
                                              evidence=existing["evidence"] + evidence)
                paused = existing["recommendation"]["pause"]
                self._freeze([paused])  # catch anything the paused lineage forked since
                self._outside_writes.setdefault(finding.intruder, set()).update(finding.paths)
                return None
            frozen = self._freeze(pair)
            rec = advice.recommend(views[finding.owner], views[finding.intruder], intruder=finding.intruder)
            resumed: list[int] = []
            if self.auto_resume_recommended:
                resumed = self._thaw(rec.keep)
            paused_row = self.registry.agent(rec.pause)
            if paused_row and paused_row["state"] not in END_STATES:
                self.registry.update_agent(rec.pause, state="paused", pause_reason="overlap")
            self._outside_writes.setdefault(finding.intruder, set()).update(finding.paths)
            alert_text = advice.overlap_alert(paths=finding.paths, evidence=evidence, views=self._views(),
                                              recommendation=rec, auto_resumed=self.auto_resume_recommended)
            return self._record_conflict("overlap", pair, finding.paths, evidence, rec.to_dict(), alert_text,
                                         frozen=frozen, resumed=resumed)
        if finding.kind == "git_internal":
            suspects = [s for s in finding.suspects if s in views]
            frozen = self._freeze(suspects) if suspects else {}
            for s in suspects:
                self.registry.update_agent(s, state="paused", pause_reason="git_internal")
            alert_text = advice.git_internal_alert(paths=finding.paths, evidence=evidence, views=views, suspects=suspects)
            return self._record_conflict("git_internal", suspects, finding.paths, evidence,
                                         {"keep": "", "pause": suspects, "rule": "git_internal",
                                          "reason": "git hooks and config are never part of a task's claim"},
                                         alert_text, frozen=frozen, resumed=[])
        if finding.kind == "unattributed":
            owner = views.get(finding.owner)
            row = self.registry.agent(finding.owner) if finding.owner else None
            strays = [{"pid": s.pid, "cmdline": s.cmdline} for s in finding.strays]
            alert_text = advice.unattributed_alert(owner=owner, paths=finding.paths, strays=strays,
                                                   owner_running=bool(row and row["state"] == "running"))
            return self._record_conflict("unattributed", [finding.owner] if finding.owner else [], finding.paths,
                                         evidence + [{"layer": "stray", **s} for s in strays],
                                         {"keep": "", "pause": "", "rule": "unknown", "reason": "culprit unknown"},
                                         alert_text, frozen={}, resumed=[], state="notice")
        if finding.kind in ("gate_block", "cwd_warning"):
            self.registry.event(self.team_id, finding.kind, finding.intruder, owner=finding.owner,
                                paths=finding.paths, evidence=evidence)
            return None
        return None

    def _record_conflict(self, kind: str, agents: list[str], paths: list[str], evidence: list[dict],
                         recommendation: dict, alert_text: str, *, frozen: Mapping, resumed: list[int],
                         state: str = "open") -> dict[str, Any]:
        conflict_id = f"cf-{uuid.uuid4().hex[:10]}"
        self.registry.add_conflict({
            "conflict_id": conflict_id, "team_id": self.team_id, "created_at": _now(), "kind": kind, "state": state,
            "agents": agents, "paths": sorted(set(paths)), "evidence": evidence,
            "recommendation": recommendation, "alert": alert_text, "decision": {},
        })
        self.registry.event(self.team_id, f"conflict_{kind}", "", conflict_id=conflict_id, agents=agents,
                            paths=paths, frozen=dict(frozen), resumed=resumed, recommendation=recommendation)
        return {"conflict_id": conflict_id, "kind": kind, "alert": alert_text, "recommendation": recommendation,
                "frozen": dict(frozen), "resumed": resumed}

    # ================================================================== finish
    def _finish(self, agent_id: str, *, exit_code: int | None = None, exit_observed: bool = False,
                forced_state: str = "", note: str = "") -> None:
        row = self.registry.agent(agent_id)
        if row is None or row["state"] in END_STATES:
            return
        adir = self._agent_dir(agent_id)
        contract = AgentContract.from_dict(row["contract"])
        result_payload: dict[str, Any] = {}
        text = ""
        receipts = 0
        if contract.kind == "process":
            try:
                result_payload = json.loads((adir / "result.json").read_text())
            except (OSError, ValueError):
                result_payload = {}
            text = str(result_payload.get("summary") or "")
            if not text:
                try:
                    tail = (adir / "stdout.log").read_text(errors="replace")[-4000:]
                except OSError:
                    tail = ""
                text = tail.strip()
        else:
            try:
                outcome = json.loads((adir / "model_outcome.json").read_text())
            except (OSError, ValueError):
                outcome = {}
            if outcome.get("refused"):
                forced_state = forced_state or ("partial" if self._cap_hit.get(agent_id) else "refused")
                note = note or str(outcome["refused"])
            elif outcome.get("error"):
                # A stream closed by our own cap or stop is that cap or stop, not a fault.
                if self._cap_hit.get(agent_id):
                    forced_state = forced_state or "partial"
                elif agent_id in self._stopping:
                    forced_state = forced_state or "stopped"
                else:
                    forced_state = forced_state or "failed"
                    note = note or str(outcome["error"])
            elif outcome.get("stopped"):
                forced_state = forced_state or "stopped"
            result_payload = dict(outcome.get("parsed") or {})
            text = str(result_payload.get("summary") or outcome.get("text") or "")
            receipts = int(outcome.get("receipts") or 0)
            if not forced_state and not result_payload:
                # The turn ended without the agent's typed RESULT line: what came back is VOOL's
                # own reply (often a refusal), not the agent's report. Never shown as done.
                forced_state = "unverified"
                note = note or "its turn ended without a RESULT line, so its answer is not taken as a report"
                text = text[:600]
            # The turn's end is observed in this process (its thread returned); a refusal or a
            # transport fault is already a forced state, never an unobserved exit.
            exit_observed = True
            exit_code = 0
        snapshot = self._launch_snapshots.get(agent_id)
        if snapshot is None:
            try:
                raw = json.loads((adir / "launch_snapshot.json").read_text())
                snapshot = {k: tuple(v) for k, v in raw.items()}
            except (OSError, ValueError):
                snapshot = {}
        observed = []
        if contract.claims:
            observed = [p for p, _ in diff_snapshots(snapshot, self.scanner.capture(contract.claims))]
        stopped = agent_id in self._stopping and not self._cap_hit.get(agent_id)
        verdict = check_report(
            stated_status=str(result_payload.get("status") or ""),
            claimed_changes=result_payload.get("changed") or (),
            observed_changes=observed,
            changed_outside_claim=sorted(self._outside_writes.get(agent_id, set())),
            exit_code=exit_code, exit_observed=exit_observed, text=text, receipts=receipts,
            cap_hit=self._cap_hit.get(agent_id, ""), stopped=stopped,
        )
        if forced_state and forced_state not in ("stopped",):
            verdict.state = forced_state
        if note:
            verdict.notes.append(note)
        full = adir / "result_full.txt"
        if not full.exists():
            full.write_text(text)
        pointer = os.path.relpath(full, self.team_dir)
        relayed, truncated = relay_line(display_name=row["display_name"], verdict=verdict, summary=text,
                                        pointer=pointer, cap=RESULT_TOKEN_CAP)
        question = str(result_payload.get("question") or "")
        result = {"relay": relayed, "truncated": truncated, "verdict": verdict.to_dict(),
                  "observed_changes": observed, "question": question,
                  "options": list(result_payload.get("options") or []), "full_result": pointer,
                  "session_id": row.get("session_id") or ""}
        clock = self.budget.clock_for(agent_id)
        clock.pause()
        self.registry.update_agent(agent_id, state=verdict.state, ended_at=_now(), exit_code=exit_code,
                                   result=result, running_seconds=clock.elapsed(),
                                   spend=self.budget.snapshot(agent_id))
        self.registry.event(self.team_id, "agent_finished", agent_id, state=verdict.state, relay=relayed)
        (adir / "PAUSE").unlink(missing_ok=True)
        self._procs.pop(agent_id, None)
        self._release_waiters(agent_id)

    def _release_waiters(self, agent_id: str) -> None:
        """Conflicts decided as 'wait until the kept agent finishes' resume the paused one now."""
        for conflict in self.registry.conflicts("waiting"):
            if conflict["decision"].get("wait_for") == agent_id:
                paused = conflict["decision"].get("paused")
                if paused:
                    self._thaw(paused)
                self.registry.update_conflict(conflict["conflict_id"], state="resolved")
                self.registry.event(self.team_id, "conflict_resolved", paused or "",
                                    conflict_id=conflict["conflict_id"], how="kept agent finished")

    # ================================================================ public API
    def stop(self, agent: str = "") -> dict[str, Any]:
        """Stop one agent (by display name, title or key) or, with no name, every live agent."""
        with self._lock:
            targets = [r for r in self.registry.agents((*LIVE_STATES, "launching")) if
                       not agent or agent in (r["display_name"], r["title"], r["key"], r["agent_id"])]
            if agent and not targets:
                return {"ok": False, "error": f"no running agent is called {agent!r}"}
            stopped = []
            for row in targets:
                if row["state"] == "pending":
                    self._finish(row["agent_id"], forced_state="stopped", note="stopped before it started")
                elif row["contract"].get("kind") == "model":
                    self._stopping.add(row["agent_id"])
                    self._cancel_model(row["agent_id"])
                    self._thaw(row["agent_id"])
                    thread = self._model_threads.get(row["agent_id"])
                    if thread is not None:
                        thread.join(timeout=STOP_GRACE_SECONDS)
                    self._finish(row["agent_id"], forced_state="stopped")
                else:
                    self._terminate_lineage(row["agent_id"])
                    self._finish(row["agent_id"], exit_code=None, exit_observed=False, forced_state="stopped")
                stopped.append(row["display_name"])
            for conflict in self.registry.conflicts("open") + self.registry.conflicts("waiting"):
                if any(a in [t["agent_id"] for t in targets] for a in conflict["agents"]):
                    survivors = [a for a in conflict["agents"] if a not in [t["agent_id"] for t in targets]]
                    for a in survivors:
                        survivor = self.registry.agent(a)
                        if survivor and survivor["state"] == "paused":
                            self._thaw(a)
                    self.registry.update_conflict(conflict["conflict_id"], state="resolved",
                                                  decision={"stopped": [t["agent_id"] for t in targets]})
            return {"ok": True, "stopped": stopped}

    def decide(self, conflict_id: str, choice: str) -> dict[str, Any]:
        """Answer an open overlap: ``wait`` (keep the recommendation; the paused agent resumes when
        the kept one finishes), ``swap`` (run the paused one, pause the other), ``stop`` (stop the
        paused one), or ``resume`` (git-internal alerts: resume the paused agents)."""
        with self._lock:
            conflict = next((c for c in self.registry.conflicts() if c["conflict_id"] == conflict_id), None)
            if conflict is None or conflict["state"] not in ("open",):
                return {"ok": False, "error": "that alert is not open any more"}
            rec = conflict["recommendation"]
            choice = str(choice or "").strip().lower()
            if conflict["agents"] and rec.get("rule") == "git_internal":
                if choice == "stop":
                    for a in conflict["agents"]:
                        self._terminate_lineage(a)
                        self._finish(a, forced_state="stopped", note="stopped after writing git internals")
                elif choice == "resume":
                    for a in conflict["agents"]:
                        self._thaw(a)
                else:
                    return {"ok": False, "error": "choose resume or stop"}
                self.registry.update_conflict(conflict_id, state="resolved", decision={"choice": choice})
                return {"ok": True, "choice": choice}
            keep, paused = rec["keep"], rec["pause"]
            if choice == "swap":
                self._freeze([keep])
                self.registry.update_agent(keep, state="paused", pause_reason="overlap")
                self._thaw(paused)
                keep, paused = paused, keep
                choice = "wait"
            if choice == "wait":
                if not self.auto_resume_recommended:
                    self._thaw(keep)
                keep_row = self.registry.agent(keep)
                if keep_row and keep_row["state"] in END_STATES:
                    self._thaw(paused)
                    self.registry.update_conflict(conflict_id, state="resolved",
                                                  decision={"choice": "wait", "kept": keep, "paused": paused})
                else:
                    self.registry.update_conflict(conflict_id, state="waiting",
                                                  decision={"choice": "wait", "wait_for": keep, "paused": paused})
            elif choice == "stop":
                self._terminate_lineage(paused)
                self._finish(paused, forced_state="stopped", note="stopped after an overlap")
                if not self.auto_resume_recommended:
                    self._thaw(keep)
                self.registry.update_conflict(conflict_id, state="resolved", decision={"choice": "stop", "stopped": paused})
            else:
                return {"ok": False, "error": "choose wait, swap or stop"}
            self.registry.event(self.team_id, "conflict_decided", "", conflict_id=conflict_id, choice=choice,
                                kept=keep, paused=paused)
            kept_row, paused_row = self.registry.agent(keep), self.registry.agent(paused)
            return {"ok": True, "choice": choice, "running": kept_row["display_name"] if kept_row else "",
                    "paused": paused_row["display_name"] if paused_row else ""}

    def answer(self, agent: str, decision: str) -> dict[str, Any]:
        """Answer an agent that returned ``needs_decision``: it runs again with the decision."""
        with self._lock:
            row = next((r for r in self.registry.agents(["needs_decision"]) if
                        agent in (r["display_name"], r["title"], r["key"], r["agent_id"])), None)
            if row is None:
                return {"ok": False, "error": f"no agent called {agent!r} is waiting for a decision"}
            contract = AgentContract.from_dict(row["contract"])
            adir = self._agent_dir(row["agent_id"])
            self.registry.update_agent(row["agent_id"], state="launching", ended_at=None, result={})
            for name in ("result.json", "model_outcome.json", "result_full.txt"):
                (adir / name).unlink(missing_ok=True)
            self.registry.event(self.team_id, "decision_answered", row["agent_id"], decision=decision)
            if contract.kind == "process":
                self._launch_process(row, contract, adir, decision=decision)
            else:
                self._launch_model(row, contract, adir, decision=decision)
            return {"ok": True, "agent": row["display_name"]}

    def pending_question(self) -> str:
        """All agents waiting on the user, batched into ONE question."""
        waiting = self.registry.agents(["needs_decision"])
        if not waiting:
            return ""
        if len(waiting) == 1:
            row = waiting[0]
            q = row["result"].get("question") or "it needs your decision to continue"
            opts = row["result"].get("options") or []
            return f"{row['display_name']} asks: {q}" + (f" Options: {' / '.join(opts)}." if opts else "")
        lines = [f"{len(waiting)} agents need a decision before they can go on:"]
        for i, row in enumerate(waiting, 1):
            q = row["result"].get("question") or "needs your decision"
            opts = row["result"].get("options") or []
            lines.append(f"{i}. {row['display_name']}: {q}" + (f" ({' / '.join(opts)})" if opts else ""))
        lines.append("Answer each by number in one reply.")
        return "\n".join(lines)

    def _public_row(self, row: dict[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {}
        spend = row.get("spend") or {}
        planned = row["progress_planned"]
        return {
            "agent": row["display_name"],
            "state": row["state"],
            "progress": f"{row['progress_done']}/{planned}" if planned else "",
            "spent_usd": spend.get("usd", 0.0),
            "tokens": spend.get("tokens", 0),
            "running_seconds": round(float(row.get("running_seconds") or 0.0), 1),
            "result": (row.get("result") or {}).get("relay", ""),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            agents = [self._public_row(r) for r in self.registry.agents()]
            alerts = [{"alert_id": c["conflict_id"], "kind": c["kind"],
                       "text": c["alert"], "state": c["state"]}
                      for c in self.registry.conflicts() if c["state"] in ("open", "waiting", "notice")]
            lines = [f"{a['agent']}: {a['state']}" + (f", {a['progress']} steps" if a["progress"] else "")
                     + (f", ${a['spent_usd']:.4f}" if a["spent_usd"] else "") for a in agents]
            return {"ok": True, "agents": agents, "alerts": alerts, "lines": lines,
                    "question": self.pending_question(), "team_spend": self.budget.snapshot()}

    def team_result(self) -> str:
        """One compact result for the chat model: each agent's capped relay line."""
        rows = self.registry.agents()
        return "\n".join((r.get("result") or {}).get("relay", f"{r['display_name']}: {r['state']}.") for r in rows)

    def all_ended(self) -> bool:
        return all(r["state"] in END_STATES for r in self.registry.agents())

    def run_until_ended(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.tick()
            if self.all_ended():
                return True
            time.sleep(self.tick_seconds)
        return self.all_ended()

    def start_loop(self) -> None:
        if self._loop is not None:
            return

        def _run() -> None:
            while not self._loop_stop.is_set():
                try:
                    self.tick()
                except Exception as exc:  # the loop must survive a bad tick; the event says why
                    self.registry.event(self.team_id, "tick_error", "", error=f"{type(exc).__name__}: {exc}")
                self._loop_stop.wait(self.tick_seconds)

        self._loop = threading.Thread(target=_run, name=f"team-{self.team_id}", daemon=True)
        self._loop.start()

    def close(self) -> None:
        self._loop_stop.set()
        if self._loop is not None:
            self._loop.join(timeout=5)
        self.registry.close()

    # ================================================================ recovery
    def _recover(self) -> None:
        """Re-adopt agents whose recorded process is still the same process; report the rest lost.
        Never relaunch: a restart must not double-run an agent or double-spend its budget."""
        for row in self.registry.agents():
            agent_id = row["agent_id"]
            contract = AgentContract.from_dict(row["contract"])
            self.budget.add_agent(agent_id, contract.limits, carried_seconds=float(row["running_seconds"] or 0.0),
                                  spent=row.get("spend") or {})
            if row["state"] not in ("running", "paused", "launching"):
                continue
            if contract.kind == "model":
                self._finish(agent_id, forced_state="lost",
                             note="its chat turn was in flight when the coordinator restarted; see its chat")
                self.recovered["lost"].append(row["display_name"])
                continue
            ident = ProcId(row["pid"], row["create_time"]) if row["pid"] else None
            alive = ident is not None and verified_process(ident) is not None
            if not alive and row["state"] == "launching":
                # Died between "launching" and recording the pid: look for the token.
                found = self._find_by_token(row["run_token"], row["launched_at"] or 0.0)
                if found is not None:
                    ident, alive = found, True
                    self.registry.update_agent(agent_id, pid=found.pid, create_time=found.create_time,
                                               state="running")
            if alive and ident is not None:
                self.recovered["adopted"].append(row["display_name"])
                self.registry.event(self.team_id, "agent_readopted", agent_id, pid=ident.pid)
                if row["state"] != "paused":
                    self.budget.clock_for(agent_id).start()
            else:
                self._finish(agent_id, forced_state="lost",
                             note="its process was gone (or its pid now belongs to another process) after the "
                                  "coordinator restarted; it was not relaunched")
                self.recovered["lost"].append(row["display_name"])

    def _find_by_token(self, token: str, since: float) -> ProcId | None:
        for proc in psutil.process_iter(attrs=("pid", "create_time")):
            try:
                if proc.info["create_time"] < since - 5:
                    continue
                if proc.environ().get(ENV_TOKEN) == token:
                    return ProcId(proc.info["pid"], proc.info["create_time"])
            except (psutil.Error, OSError, SystemError, ValueError):
                continue
        return None


__all__ = ["TeamCoordinator"]
