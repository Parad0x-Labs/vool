"""Which processes belong to which agent: children, grandchildren and detached daemons.

An agent's lineage is:

* its root process (the one the coordinator started, recorded as ``(pid, create_time)``);
* every process whose parent chain reaches a lineage member (``tree``);
* every process created after the team started, by the same user, whose environment carries the
  agent's run token (``token``). This is what catches a daemon that double-forked, called
  ``setsid`` and was reparented to launchd/init: its parent chain no longer leads anywhere, but
  it inherited the token.

Membership is sticky per process identity. A process once seen in a lineage stays in it while it
lives, even if it later ``exec``s with a scrubbed environment (``env -i``) or is reparented —
the same ``(pid, create_time)`` is the same process.

A process is identified by ``(pid, create_time)``. A pid whose create time changed is a different
process: it is never treated as a member and never signalled. The coordinator's own process and
all of its ancestors are protected and never become members, whatever they carry.

Processes created after the team started, by the same user, reparented to pid 1 and in no
lineage are ``strays``: they may be an env-scrubbed daemon an agent left behind. They are
evidence for alerts only. Nothing outside a lineage is ever signalled.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import psutil

ENV_TOKEN = "VOOL_AGENT_RUN"
_CT_TOLERANCE = 0.01


@dataclass(frozen=True)
class ProcId:
    pid: int
    create_time: float


@dataclass(frozen=True)
class Member:
    pid: int
    create_time: float
    ppid: int
    depth: int | None  # 0 = root, 1 = child, 2 = grandchild; None = detached, depth unknown
    cmdline: str
    via: str           # root | tree | token

    @property
    def ident(self) -> ProcId:
        return ProcId(self.pid, self.create_time)

    def relation(self) -> str:
        if self.depth is None:
            return "detached descendant (reparented daemon)"
        return relation_for_depth(self.depth)


def relation_for_depth(depth: int) -> str:
    if depth <= 0:
        return "main process"
    if depth == 1:
        return "child process"
    if depth == 2:
        return "grandchild process"
    if depth == 3:
        return "great-grandchild process"
    return f"descendant process ({depth} levels down)"


def identify(pid: int) -> ProcId | None:
    try:
        return ProcId(int(pid), psutil.Process(int(pid)).create_time())
    except (psutil.Error, ValueError):
        return None


def verified_process(ident: ProcId) -> psutil.Process | None:
    """The live process for ``ident``, or None when it is gone or the pid was reused."""
    try:
        proc = psutil.Process(int(ident.pid))
        if abs(proc.create_time() - float(ident.create_time)) > _CT_TOLERANCE:
            return None
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return proc
    except psutil.Error:
        return None


def protected_pids() -> frozenset[int]:
    """This process and every ancestor: never members, never signalled."""
    out = {os.getpid(), 0, 1}
    try:
        for parent in psutil.Process(os.getpid()).parents():
            out.add(parent.pid)
    except psutil.Error:
        pass
    return frozenset(out)


def _cmdline(proc: psutil.Process) -> str:
    try:
        parts = proc.cmdline()
    except psutil.Error:
        parts = []
    if not parts:
        try:
            parts = [proc.name()]
        except psutil.Error:
            parts = ["?"]
    text = " ".join(" ".join(parts).split())
    return text if len(text) <= 240 else text[:237] + "..."


@dataclass(frozen=True)
class AgentRoot:
    agent_id: str
    root: ProcId | None
    token: str
    started_at: float


class LineageTracker:
    """Keeps per-agent lineages across ticks. One instance per coordinator."""

    def __init__(self, *, protected: Iterable[int] | None = None) -> None:
        self._protected = frozenset(protected) if protected is not None else protected_pids()
        self._uid = os.getuid() if hasattr(os, "getuid") else None
        # (pid, create_time) -> (agent_id, member)
        self._known: dict[ProcId, tuple[str, Member]] = {}
        self._env_checked: dict[ProcId, str] = {}

    @property
    def protected(self) -> frozenset[int]:
        return self._protected

    def _token_of(self, proc: psutil.Process, ident: ProcId) -> str:
        if ident in self._env_checked:
            return self._env_checked[ident]
        try:
            token = str(proc.environ().get(ENV_TOKEN) or "")
        except psutil.Error:
            token = ""
        self._env_checked[ident] = token
        return token

    def scan(self, agents: Iterable[AgentRoot]) -> tuple[dict[str, dict[int, Member]], list[Member]]:
        """One process-table pass. Returns ``(lineages, strays)``."""
        roots = [a for a in agents]
        by_token = {a.token: a for a in roots if a.token}
        since = min((a.started_at for a in roots), default=0.0) - 1.0

        table: dict[int, tuple[psutil.Process, ProcId, int]] = {}
        children: dict[int, list[int]] = {}
        for proc in psutil.process_iter(attrs=("pid", "ppid", "create_time", "uids")):
            info = proc.info
            try:
                pid, ppid, ct = int(info["pid"]), int(info.get("ppid") or 0), float(info["create_time"] or 0)
            except (TypeError, ValueError, KeyError):
                continue
            if pid in self._protected:
                continue
            if self._uid is not None:
                uids = info.get("uids")
                if uids is not None and getattr(uids, "real", self._uid) != self._uid:
                    continue
            ident = ProcId(pid, ct)
            table[pid] = (proc, ident, ppid)
            children.setdefault(ppid, []).append(pid)

        owner: dict[int, tuple[str, int | None, str]] = {}  # pid -> (agent_id, depth, via)

        # 1. sticky members that are still the same process
        for ident, (agent_id, member) in list(self._known.items()):
            row = table.get(ident.pid)
            if row is None or abs(row[1].create_time - ident.create_time) > _CT_TOLERANCE:
                self._known.pop(ident, None)
                continue
            owner[ident.pid] = (agent_id, member.depth, member.via)

        # 2. roots
        for agent in roots:
            if agent.root is None:
                continue
            row = table.get(agent.root.pid)
            if row is None or abs(row[1].create_time - agent.root.create_time) > _CT_TOLERANCE:
                continue
            owner[agent.root.pid] = (agent.agent_id, 0, "root")

        # 3. token carriers (created after the team started)
        for pid, (proc, ident, _ppid) in table.items():
            if ident.create_time < since:
                continue
            token = self._token_of(proc, ident)
            if token and token in by_token:
                agent = by_token[token]
                current = owner.get(pid)
                if current is None or current[0] != agent.agent_id:
                    owner[pid] = (agent.agent_id, current[1] if current and current[0] == agent.agent_id else None, "token")

        # 4. tree: every descendant of a member belongs to that member's agent (unless it carries
        #    a different agent's token, assigned above).
        queue = deque(owner.keys())
        while queue:
            parent_pid = queue.popleft()
            agent_id, depth, _via = owner[parent_pid]
            for child_pid in children.get(parent_pid, ()):
                if child_pid in owner:
                    known_agent, known_depth, via = owner[child_pid]
                    if known_agent == agent_id and known_depth is None and depth is not None:
                        owner[child_pid] = (agent_id, depth + 1, via)
                        queue.append(child_pid)
                    continue
                owner[child_pid] = (agent_id, None if depth is None else depth + 1, "tree")
                queue.append(child_pid)

        lineages: dict[str, dict[int, Member]] = {a.agent_id: {} for a in roots}
        for pid, (agent_id, depth, via) in owner.items():
            proc, ident, ppid = table[pid]
            previous = self._known.get(ident)
            if previous is not None and previous[0] == agent_id:
                member = previous[1]
                if member.depth is None and depth is not None:
                    member = Member(pid, ident.create_time, ppid, depth, member.cmdline, member.via)
                elif member.ppid != ppid:
                    member = Member(pid, ident.create_time, ppid, member.depth, member.cmdline, member.via)
            else:
                member = Member(pid, ident.create_time, ppid, depth, _cmdline(proc), via)
            self._known[ident] = (agent_id, member)
            lineages.setdefault(agent_id, {})[pid] = member

        strays: list[Member] = []
        for pid, (proc, ident, ppid) in table.items():
            if pid in owner or ident.create_time < since or ppid not in (0, 1):
                continue
            strays.append(Member(pid, ident.create_time, ppid, None, _cmdline(proc), "stray"))
        return lineages, strays

    def members_of(self, agent_id: str) -> list[Member]:
        return [m for (aid, m) in self._known.values() if aid == agent_id]


def open_paths(member: Member) -> tuple[list[str], str]:
    """``(open file paths, cwd)`` for a verified member; empty when unreadable or gone."""
    proc = verified_process(member.ident)
    if proc is None:
        return [], ""
    files: list[str] = []
    try:
        files = [os.path.realpath(f.path) for f in proc.open_files()]
    except psutil.Error:
        files = []
    try:
        cwd = os.path.realpath(proc.cwd())
    except psutil.Error:
        cwd = ""
    return files, cwd


def describe(members: Mapping[int, Member]) -> list[dict]:
    return [
        {"pid": m.pid, "relation": m.relation(), "cmdline": m.cmdline, "via": m.via}
        for m in sorted(members.values(), key=lambda m: (m.depth is None, m.depth or 0, m.pid))
    ]


__all__ = [
    "ENV_TOKEN",
    "AgentRoot",
    "LineageTracker",
    "Member",
    "ProcId",
    "describe",
    "identify",
    "open_paths",
    "protected_pids",
    "relation_for_depth",
    "verified_process",
]
