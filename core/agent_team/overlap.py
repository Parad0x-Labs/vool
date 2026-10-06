"""The overlap watch: who touched whose files, across every process an agent started.

Three layers, each tagged in the evidence it produces:

1. ``gate``  — a write through VOOL's own file tools asks the lease first
   (:mod:`core.agent_team.gate`); a write into another lineage's claim is refused before it lands,
   and the attempt is recorded here.
2. ``open_file`` / ``cwd`` — each tick, every lineage member's open files and working directory.
   Linux reports the open mode, so a file open for writing in another agent's claim is an
   overlap at once (``open_file_write``). macOS reports no mode: there an open file is only a
   candidate, confirmed by layer 3. A working directory inside another agent's claim is a warning.
3. ``change_scan`` — ``(mtime_ns, size, inode)`` of every claimed path plus the workspace's
   ``.git/hooks`` and ``.git/config``, diffed each tick. A change is attributed to the lineage
   with the strongest evidence on it (gate > open file > cwd). If the strongest evidence belongs
   to a lineage other than the path's owner, that is an overlap. With no evidence at all, a
   running owner is presumed to have made its own change — unless a stray (an orphaned process
   created after the team started, in no lineage) shows evidence in that claim, or the owner is
   not running (paused, ended, not started), in which case an *unattributed change* is raised:
   the culprit is unknown and the alert says so.

Any change under ``.git/hooks`` or to ``.git/config`` raises an alert: hooks run code on the next
commit, and no agent's claim covers them.

Ownership is the deepest claim: while a sub-agent holds ``a/x/``, its parent's own writes there
are an overlap, because ``a/x/`` is not the parent's to write until the sub-agent is done.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agent_team.lineage import Member, open_paths

_STRENGTH = {"gate": 3, "open_file_write": 3, "open_file": 2, "cwd": 1}
_GIT_HOOKS = (".git", "hooks")
_GIT_CONFIG = (".git", "config")


@dataclass(frozen=True)
class Evidence:
    layer: str
    agent_id: str
    path: str
    pid: int = 0
    relation: str = ""
    cmdline: str = ""
    at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer, "agent_id": self.agent_id, "path": self.path, "pid": self.pid,
            "relation": self.relation, "cmdline": self.cmdline,
        }


@dataclass
class Finding:
    kind: str                  # overlap | unattributed | git_internal | cwd_warning | gate_block
    owner: str = ""            # agent whose claim the path is in ("" for .git internals)
    intruder: str = ""         # agent whose lineage reached in ("" when unknown)
    paths: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    suspects: list[str] = field(default_factory=list)  # agents that could not be ruled out
    strays: list[Member] = field(default_factory=list)


def _rel(workspace: Path, path: str) -> str:
    try:
        return Path(path).relative_to(workspace).as_posix()
    except ValueError:
        return ""


def _inside(rel: str, claim: str) -> bool:
    if not rel:
        return False
    a, b = rel.split("/"), claim.split("/")
    return len(a) >= len(b) and a[: len(b)] == b


class ScopeScanner:
    """``(mtime_ns, size, inode)`` per file under the watched roots."""

    def __init__(self, workspace: Path, *, max_files: int = 50_000) -> None:
        self.workspace = workspace
        self.max_files = max_files
        self.truncated = False

    def capture(self, rel_roots: Iterable[str]) -> dict[str, tuple[int, int, int]]:
        out: dict[str, tuple[int, int, int]] = {}
        self.truncated = False
        for rel in sorted(set(rel_roots)):
            root = self.workspace / rel
            try:
                st = root.lstat()
            except OSError:
                continue
            if not root.is_dir() or root.is_symlink():
                out[rel] = (st.st_mtime_ns, st.st_size, st.st_ino)
                continue
            stack = [root]
            while stack:
                current = stack.pop()
                try:
                    entries = list(os.scandir(current))
                except OSError:
                    continue
                for entry in entries:
                    if len(out) >= self.max_files:
                        self.truncated = True
                        return out
                    try:
                        est = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    out[Path(entry.path).relative_to(self.workspace).as_posix()] = (
                        est.st_mtime_ns, est.st_size, est.st_ino,
                    )
        return out


def diff_snapshots(old: Mapping[str, tuple], new: Mapping[str, tuple]) -> list[tuple[str, str]]:
    changes: list[tuple[str, str]] = []
    for path, sig in new.items():
        before = old.get(path)
        if before is None:
            changes.append((path, "added"))
        elif before != sig:
            changes.append((path, "modified"))
    for path in old:
        if path not in new:
            changes.append((path, "deleted"))
    return sorted(changes)


class OverlapWatch:
    def __init__(self, workspace: str | os.PathLike[str], *, window_ticks: int = 3,
                 clock: Callable[[], float] = time.monotonic, linux_modes: bool | None = None) -> None:
        self.workspace = Path(os.path.realpath(workspace))
        self.window_ticks = max(1, int(window_ticks))
        self._clock = clock
        self._scanner = ScopeScanner(self.workspace)
        self._claims: dict[str, tuple[str, ...]] = {}
        self._parents: dict[str, str] = {}
        self._snapshot: dict[str, dict[str, tuple[int, int, int]]] | None = None
        self._history: list[list[Evidence]] = []
        self._gate_notes: list[Evidence] = []
        self._warned_cwd: set[tuple[str, str]] = set()
        self.linux_modes = sys.platform.startswith("linux") if linux_modes is None else linux_modes
        self.has_git = (self.workspace / ".git").is_dir()

    # ------------------------------------------------------------- claims
    def set_claims(self, claims: Mapping[str, Iterable[str]], parents: Mapping[str, str]) -> None:
        self._claims = {a: tuple(c) for a, c in claims.items() if c}
        self._parents = dict(parents)

    def owner_of(self, rel: str) -> str:
        """The agent with the deepest claim covering ``rel`` ("" when unclaimed)."""
        best, depth = "", -1
        for agent_id, claims in self._claims.items():
            for claim in claims:
                if _inside(rel, claim) and claim.count("/") > depth:
                    best, depth = agent_id, claim.count("/")
        return best

    def _roots(self) -> list[str]:
        roots = [c for claims in self._claims.values() for c in claims]
        if self.has_git:
            roots += ["/".join(_GIT_HOOKS), "/".join(_GIT_CONFIG)]
        return roots

    # --------------------------------------------------------------- gate
    def gate_note(self, agent_id: str, path: str, *, allowed: bool) -> None:
        rel = _rel(self.workspace, os.path.realpath(path))
        self._gate_notes.append(Evidence("gate", agent_id, rel or path, at=self._clock(),
                                         relation="VOOL file tool", cmdline="allowed" if allowed else "refused"))

    # -------------------------------------------------------------- sample
    def _sample(self, lineages: Mapping[str, Mapping[int, Member]]) -> list[Evidence]:
        now = self._clock()
        found: list[Evidence] = []
        for agent_id, members in lineages.items():
            for member in members.values():
                files, cwd = open_paths(member)
                proc_modes: dict[str, str] = {}
                if self.linux_modes:
                    proc_modes = _linux_modes(member)
                for path in files:
                    rel = _rel(self.workspace, path)
                    if not rel:
                        continue
                    layer = "open_file"
                    if proc_modes.get(path, "") in {"w", "a", "r+", "a+", "w+"}:
                        layer = "open_file_write"
                    found.append(Evidence(layer, agent_id, rel, member.pid, member.relation(), member.cmdline, now))
                if cwd:
                    rel = _rel(self.workspace, cwd)
                    if rel or cwd == str(self.workspace):
                        found.append(Evidence("cwd", agent_id, rel, member.pid, member.relation(), member.cmdline, now))
        return found

    def _stray_evidence(self, strays: Iterable[Member]) -> list[tuple[Member, str]]:
        out: list[tuple[Member, str]] = []
        for stray in strays:
            files, cwd = open_paths(stray)
            for path in [*files, cwd]:
                rel = _rel(self.workspace, path) if path else ""
                if rel and self.owner_of(rel):
                    out.append((stray, rel))
        return out

    # ---------------------------------------------------------------- tick
    def tick(
        self,
        lineages: Mapping[str, Mapping[int, Member]],
        strays: Iterable[Member],
        running: Mapping[str, bool],
        *,
        resample: Callable[[], Mapping[str, Mapping[int, Member]]] | None = None,
    ) -> list[Finding]:
        """One watch pass: sample evidence, then diff the scopes. Returns findings."""
        findings: list[Finding] = []
        sample = self._sample(lineages)
        self._history.append(sample)
        self._history = self._history[-self.window_ticks:]
        gate_notes, self._gate_notes = self._gate_notes, []
        for note in gate_notes:
            if note.cmdline == "refused":
                findings.append(Finding("gate_block", owner=self.owner_of(note.path), intruder=note.agent_id,
                                        paths=[note.path], evidence=[note]))

        # Linux: a file open for WRITING in another agent's claim is an overlap now.
        for ev in sample:
            if ev.layer != "open_file_write":
                continue
            owner = self.owner_of(ev.path)
            if owner and owner != ev.agent_id:
                findings.append(Finding("overlap", owner=owner, intruder=ev.agent_id, paths=[ev.path], evidence=[ev]))

        # Working directory inside another agent's claim: a warning, once per pair.
        for ev in sample:
            if ev.layer != "cwd" or not ev.path:
                continue
            owner = self.owner_of(ev.path)
            if owner and owner != ev.agent_id and (owner, ev.agent_id) not in self._warned_cwd:
                self._warned_cwd.add((owner, ev.agent_id))
                findings.append(Finding("cwd_warning", owner=owner, intruder=ev.agent_id, paths=[ev.path], evidence=[ev]))

        roots = sorted(set(self._roots()))
        current = {root: self._scanner.capture([root]) for root in roots}
        previous, self._snapshot = self._snapshot, current
        if previous is None:
            return findings
        changes: list[tuple[str, str]] = []
        seen: set[str] = set()
        for root in roots:
            if root not in previous:
                continue  # a newly watched claim: this tick is its baseline, not a change
            for path, kind in diff_snapshots(previous[root], current[root]):
                if path not in seen:
                    seen.add(path)
                    changes.append((path, kind))
        if not changes:
            return findings

        evidence = [ev for tick in self._history for ev in tick] + gate_notes
        need_resample = any(not self._exact(evidence, path) for path, _ in changes)
        if need_resample and resample is not None:
            # Burst: a writer that keeps a file open between writes is caught here even if it
            # opened the file after this tick's first sample.
            for _ in range(3):
                evidence += self._sample(resample())
                time.sleep(0.03)
        stray_hits = self._stray_evidence(strays) if strays else []

        by_pair: dict[tuple[str, str, str], Finding] = {}
        for path, _kind in changes:
            parts = tuple(path.split("/"))
            if parts[:2] == _GIT_HOOKS or parts[:2] == _GIT_CONFIG:
                exact = [ev for ev in evidence if ev.path == path and ev.layer in _STRENGTH and _STRENGTH[ev.layer] >= 2]
                suspects = sorted({ev.agent_id for ev in exact}) or sorted(a for a, r in running.items() if r)
                key = ("git_internal", "", ",".join(suspects))
                finding = by_pair.setdefault(key, Finding("git_internal", suspects=suspects,
                                                          intruder=suspects[0] if len(suspects) == 1 else ""))
                finding.paths.append(path)
                finding.evidence.extend(exact or [Evidence("change_scan", "", path, at=self._clock())])
                continue
            owner = self.owner_of(path)
            if not owner:
                continue
            ranked = self._rank(evidence, path, owner)
            if ranked:
                top_strength = _STRENGTH[ranked[0].layer]
                top = [ev for ev in ranked if _STRENGTH[ev.layer] == top_strength]
                intruders = sorted({ev.agent_id for ev in top if ev.agent_id != owner})
                if intruders:
                    for intruder in intruders:
                        key = ("overlap", owner, intruder)
                        finding = by_pair.setdefault(key, Finding("overlap", owner=owner, intruder=intruder))
                        finding.paths.append(path)
                        finding.evidence.extend(ev for ev in top if ev.agent_id == intruder)
                        finding.evidence.append(Evidence("change_scan", "", path, at=self._clock()))
                continue
            strays_here = [s for s, rel in stray_hits if _inside(rel, _claim_root(self._claims, owner, path))]
            if running.get(owner) and not strays_here:
                continue  # the owner's own change
            key = ("unattributed", owner, "")
            finding = by_pair.setdefault(key, Finding("unattributed", owner=owner))
            finding.paths.append(path)
            finding.strays.extend(s for s in strays_here if s not in finding.strays)
            finding.evidence.append(Evidence("change_scan", "", path, at=self._clock()))
        findings.extend(by_pair.values())
        return findings

    @staticmethod
    def _exact(evidence: Iterable[Evidence], path: str) -> bool:
        return any(ev.path == path and ev.layer in {"gate", "open_file", "open_file_write"} for ev in evidence)

    def _rank(self, evidence: Iterable[Evidence], path: str, owner: str) -> list[Evidence]:
        claim_root = _claim_root(self._claims, owner, path)
        out = []
        for ev in evidence:
            exact = ev.layer in {"gate", "open_file", "open_file_write"} and ev.path == path
            working_there = ev.layer == "cwd" and bool(ev.path) and _inside(ev.path, claim_root)
            if exact or working_there:
                out.append(ev)
        return sorted(out, key=lambda ev: -_STRENGTH[ev.layer])


def _claim_root(claims: Mapping[str, tuple[str, ...]], owner: str, path: str) -> str:
    best = ""
    for claim in claims.get(owner, ()):
        if _inside(path, claim) and len(claim) > len(best):
            best = claim
    return best or path


def _linux_modes(member: Member) -> dict[str, str]:
    from core.agent_team.lineage import verified_process

    proc = verified_process(member.ident)
    if proc is None:
        return {}
    try:
        return {os.path.realpath(f.path): str(getattr(f, "mode", "") or "") for f in proc.open_files()}
    except Exception:  # unreadable now (exited, permission): no mode evidence, never a crash
        return {}


__all__ = ["Evidence", "Finding", "OverlapWatch", "ScopeScanner", "diff_snapshots"]
