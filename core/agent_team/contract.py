"""One agent's written brief, and the plan-level law every brief is checked against.

A contract says what the agent is for (objective → task title), how much it matters
(importance), which paths it may write (claims), how it runs (a command, or a model turn), what
it may spend (limits) and who started it (parent). The plan is refused before anything launches
when:

* a title is generic, or two agents would show the same name after disambiguation;
* two agents that are not parent and child claim overlapping paths (one writer per file);
* a sub-agent claims anything outside its parent's claim;
* the agent-start depth passes the ceiling (default 2, hard ceiling 3);
* a claim escapes the workspace (absolute, ``..``, or a symlink out);
* any limit is missing or unbounded;
* a dependency names an agent that is not in the plan, or the dependencies form a cycle.

Importance is the stated priority raised one level per agent that depends on this one, capped
at critical: an agent others are waiting on matters more than its own label says.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from core.agent_team import names
from core.agent_team.limits import Limits
from core.council.task_dag import paths_overlap, scopes_overlap

DEFAULT_MAX_DEPTH = 2
HARD_MAX_DEPTH = 3
KINDS = ("process", "model")
MODES = ("read", "write")


class ContractRefused(ValueError):
    """The plan cannot start. The message is user-facing: it names tasks, not ids."""


def normalize_claim(claim: str) -> str:
    clean = str(claim or "").strip().replace("\\", "/")
    if not clean:
        raise ContractRefused("an empty path cannot be claimed")
    if clean.startswith("/") or (len(clean) > 1 and clean[1] == ":"):
        raise ContractRefused(f"claim {claim!r} must be relative to the workspace")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ContractRefused(f"claim {claim!r} escapes the workspace")
    return "/".join(parts)


def claim_inside(child: str, parent: str) -> bool:
    """``child`` is ``parent`` or sits inside it, component-wise."""
    c, p = child.split("/"), parent.split("/")
    return len(c) >= len(p) and c[: len(p)] == p


@dataclass(frozen=True)
class AgentContract:
    key: str
    objective: str
    limits: Limits
    title: str = ""
    importance: str = "normal"
    kind: str = "process"
    mode: str = "write"
    claims: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    cwd: str = ""
    model: str = ""
    prompt: str = ""
    parent: str = ""
    depends_on: tuple[str, ...] = ()
    planned_steps: int = 0
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, default_limits: Limits | None = None) -> AgentContract:
        if not isinstance(raw, Mapping):
            raise ContractRefused("each agent needs a brief (an object)")
        limits_raw = raw.get("limits")
        limits = Limits.from_dict(limits_raw) if limits_raw is not None else default_limits
        if limits is None:
            raise ContractRefused("each agent needs limits: max_usd, max_tokens, max_calls, wall_clock_seconds")
        command = raw.get("command") or ()
        if isinstance(command, str):
            raise ContractRefused("command must be a list of arguments, not a shell string")
        return cls(
            key=str(raw.get("key") or raw.get("title") or raw.get("objective") or "").strip(),
            objective=str(raw.get("objective") or "").strip(),
            limits=limits,
            title=str(raw.get("title") or "").strip(),
            importance=str(raw.get("importance") or "normal"),
            kind=str(raw.get("kind") or ("model" if raw.get("model") and not command else "process")),
            mode=str(raw.get("mode") or ("write" if raw.get("claims") else "read")),
            claims=tuple(str(c) for c in raw.get("claims") or ()),
            command=tuple(str(c) for c in command),
            cwd=str(raw.get("cwd") or ""),
            model=str(raw.get("model") or ""),
            prompt=str(raw.get("prompt") or ""),
            parent=str(raw.get("parent") or ""),
            depends_on=tuple(str(d) for d in raw.get("depends_on") or ()),
            planned_steps=int(raw.get("planned_steps") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "objective": self.objective, "limits": self.limits.to_dict(),
            "title": self.title, "importance": self.importance, "kind": self.kind,
            "mode": self.mode, "claims": list(self.claims), "command": list(self.command),
            "cwd": self.cwd, "model": self.model, "prompt": self.prompt, "parent": self.parent,
            "depends_on": list(self.depends_on), "planned_steps": self.planned_steps,
        }


@dataclass(frozen=True)
class ValidatedAgent:
    contract: AgentContract
    title: str
    importance: str          # stated
    effective_importance: str  # raised by dependents
    display_name: str
    depth: int               # 1 = started by the chat; 2 = started by an agent; ...


def _check_workspace_claim(workspace: Path, claim: str) -> None:
    root = workspace.resolve()
    target = (root / claim)
    # Resolve the deepest existing ancestor: a claim through a symlink that leaves the
    # workspace is an escape even if the leaf does not exist yet.
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    resolved = probe.resolve()
    if resolved != root and root not in resolved.parents:
        raise ContractRefused(f"claim {claim!r} resolves outside the workspace")


def validate_plan(
    raw_agents: Sequence[AgentContract],
    *,
    workspace: str | os.PathLike[str],
    existing: Mapping[str, ValidatedAgent] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[ValidatedAgent]:
    """Check a plan against the team law. ``existing`` are agents already running in the team
    (their claims still hold leases; their keys may be parents or dependencies)."""
    if not raw_agents:
        raise ContractRefused("a plan needs at least one agent")
    if not 1 <= int(max_depth) <= HARD_MAX_DEPTH:
        raise ContractRefused(f"max depth must be between 1 and {HARD_MAX_DEPTH}")
    workspace_path = Path(workspace)
    existing = dict(existing or {})
    by_key: dict[str, AgentContract] = {}
    for agent in raw_agents:
        if not agent.objective:
            raise ContractRefused("each agent needs an objective")
        if not agent.key:
            raise ContractRefused("each agent needs a key")
        if agent.key in by_key or agent.key in existing:
            raise ContractRefused(f"two agents share the key {agent.key!r}")
        if agent.kind not in KINDS:
            raise ContractRefused(f"kind must be one of {KINDS}")
        if agent.mode not in MODES:
            raise ContractRefused(f"mode must be one of {MODES}")
        if agent.kind == "process" and not agent.command:
            raise ContractRefused(f"{agent.title or agent.key}: a process agent needs a command")
        if agent.kind == "model" and not agent.model:
            raise ContractRefused(f"{agent.title or agent.key}: a model agent needs a model")
        if agent.mode == "write" and not agent.claims:
            raise ContractRefused(f"{agent.title or agent.key}: a write agent must claim the paths it writes")
        by_key[agent.key] = agent

    # Normalize claims and titles.
    normalized: dict[str, AgentContract] = {}
    for key, agent in by_key.items():
        claims = tuple(dict.fromkeys(normalize_claim(c) for c in agent.claims))
        for claim in claims:
            _check_workspace_claim(workspace_path, claim)
        if agent.cwd:
            cwd = normalize_claim(agent.cwd)
            _check_workspace_claim(workspace_path, cwd)
        title = names.validate_title(agent.title or names.title_from_objective(agent.objective))
        importance = names.normalize_importance(agent.importance)
        normalized[key] = replace(agent, claims=claims, title=title, importance=importance)

    def parent_of(key: str) -> tuple[str, Any] | None:
        parent_key = normalized[key].parent if key in normalized else existing[key].contract.parent
        if not parent_key:
            return None
        if parent_key in normalized:
            return parent_key, normalized[parent_key]
        if parent_key in existing:
            return parent_key, existing[parent_key].contract
        raise ContractRefused(f"{normalized[key].title}: its parent is not in this team")

    def depth_of(key: str, seen: tuple[str, ...] = ()) -> int:
        if key in seen:
            raise ContractRefused("agents cannot be their own ancestors")
        if key in existing and key not in normalized:
            return existing[key].depth
        parent = parent_of(key)
        return 1 if parent is None else 1 + depth_of(parent[0], (*seen, key))

    depths = {key: depth_of(key) for key in normalized}
    for key, depth in depths.items():
        if depth > max_depth:
            raise ContractRefused(
                f"{normalized[key].title}: agents may start agents only {max_depth - 1} level(s) "
                f"deep here (hard ceiling {HARD_MAX_DEPTH}); this one would be at depth {depth}"
            )

    # A sub-agent's claim sits inside its parent's claim.
    for key, agent in normalized.items():
        parent = parent_of(key)
        if parent is None:
            continue
        parent_claims = parent[1].claims
        for claim in agent.claims:
            if not any(claim_inside(claim, pc) for pc in parent_claims):
                raise ContractRefused(
                    f"{agent.title}: it claims {claim}, which is outside what its parent "
                    f"({parent[1].title}) holds"
                )

    # One writer per file: overlapping claims are refused unless one agent is the other's
    # ancestor (a sub-lease inside the parent's lease).
    def ancestors(key: str) -> set[str]:
        out: set[str] = set()
        current = key
        while True:
            parent = parent_of(current) if (current in normalized or current in existing) else None
            if parent is None:
                return out
            out.add(parent[0])
            current = parent[0]

    pool: dict[str, AgentContract] = {**{k: v.contract for k, v in existing.items()}, **normalized}
    keys = list(pool)
    for i, left in enumerate(keys):
        for right in keys[i + 1:]:
            if left not in normalized and right not in normalized:
                continue
            a, b = pool[left], pool[right]
            if a.mode != "write" or b.mode != "write":
                continue
            if not scopes_overlap(a.claims, b.claims):
                continue
            if left in ancestors(right) or right in ancestors(left):
                continue
            shared = sorted({x for x in a.claims for y in b.claims if paths_overlap(x, y)})
            raise ContractRefused(
                f"{a.title} and {b.title} both claim {', '.join(shared)}; only one agent may "
                "write a file. Split the paths or make one a sub-task of the other."
            )

    # Dependencies: known keys, no cycles.
    known = set(pool)
    for agent in normalized.values():
        for dep in agent.depends_on:
            if dep not in known:
                raise ContractRefused(f"{agent.title} depends on {dep!r}, which is not in this team")
    state: dict[str, int] = {}

    def visit(key: str) -> None:
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            raise ContractRefused("the plan's dependencies form a cycle; it could never start")
        state[key] = 1
        for dep in pool[key].depends_on:
            visit(dep)
        state[key] = 2

    for key in normalized:
        visit(key)

    dependents: dict[str, int] = {k: 0 for k in pool}
    for agent in pool.values():
        for dep in agent.depends_on:
            dependents[dep] = dependents.get(dep, 0) + 1

    titles = [(normalized[k].title, names.raise_importance(normalized[k].importance, dependents.get(k, 0)),
               normalized[k].claims) for k in normalized]
    taken = [v.display_name for v in existing.values()]
    display = names.assign_display_names(titles, taken=taken)

    out: list[ValidatedAgent] = []
    for (key, agent), (_title, effective, _claims), name in zip(normalized.items(), titles, display, strict=True):
        out.append(ValidatedAgent(
            contract=agent,
            title=agent.title,
            importance=agent.importance,
            effective_importance=effective,
            display_name=name,
            depth=depths[key],
        ))
    return out


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "HARD_MAX_DEPTH",
    "AgentContract",
    "ContractRefused",
    "ValidatedAgent",
    "claim_inside",
    "normalize_claim",
    "validate_plan",
]
