"""Which agent to let run and which to keep paused, with the reason, in words a user can act on.

Order, first decisive rule wins:

1. **Intruder** — the agent whose processes reached into files another agent owns stays paused:
   "it reached into files owned by <task>".
2. **Importance** — the more important task runs. Importance is the stated level raised one step
   per agent that depends on it.
3. **Progress** — the agent further along runs, so less work is lost (steps done / planned, from
   the agent's own status channel).
4. **Start order** — the agent that started first runs.

The alert names tasks with their importance, the contested paths, which process touched them and
how (VOOL file tool, open file, working directory, change scan), and the options. It never shows
internal ids.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.agent_team.names import importance_rank

_LAYER_WORDS = {
    "gate": "tried to write through VOOL's file tool",
    "open_file_write": "had it open for writing",
    "open_file": "had it open",
    "cwd": "was working inside that folder",
    "change_scan": "changed on disk",
}


@dataclass(frozen=True)
class AgentView:
    agent_id: str
    display_name: str
    title: str
    effective_importance: str
    progress_done: int
    progress_planned: int
    launched_at: float
    claims: tuple[str, ...]

    @property
    def progress(self) -> float:
        if self.progress_planned <= 0:
            return 0.0
        return min(1.0, max(0.0, self.progress_done / self.progress_planned))

    def progress_words(self) -> str:
        if self.progress_planned <= 0:
            return "no progress reported"
        return f"{self.progress_done} of {self.progress_planned} steps done"


@dataclass(frozen=True)
class Recommendation:
    keep: str      # agent id to let run
    pause: str     # agent id to keep paused
    rule: str      # intruder | importance | progress | start_order
    reason: str    # one sentence, task names only

    def to_dict(self) -> dict[str, Any]:
        return {"keep": self.keep, "pause": self.pause, "rule": self.rule, "reason": self.reason}


def recommend(a: AgentView, b: AgentView, *, intruder: str = "") -> Recommendation:
    """Pick which of two agents keeps running."""
    if intruder in (a.agent_id, b.agent_id):
        keep, pause = (b, a) if intruder == a.agent_id else (a, b)
        claim = keep.claims[0] if keep.claims else "its files"
        return Recommendation(
            keep.agent_id, pause.agent_id, "intruder",
            f"{pause.title} reached into files owned by {keep.title}, which holds {claim}/",
        )
    ra, rb = importance_rank(a.effective_importance), importance_rank(b.effective_importance)
    if ra != rb:
        keep, pause = (a, b) if ra > rb else (b, a)
        return Recommendation(
            keep.agent_id, pause.agent_id, "importance",
            f"{keep.title} is {keep.effective_importance} importance; {pause.title} is "
            f"{pause.effective_importance}",
        )
    if abs(a.progress - b.progress) > 1e-9:
        keep, pause = (a, b) if a.progress > b.progress else (b, a)
        return Recommendation(
            keep.agent_id, pause.agent_id, "progress",
            f"{keep.title} is further along ({keep.progress_words()}) than {pause.title} "
            f"({pause.progress_words()}), so less work is lost",
        )
    keep, pause = (a, b) if a.launched_at <= b.launched_at else (b, a)
    return Recommendation(
        keep.agent_id, pause.agent_id, "start_order",
        f"both are {keep.effective_importance} importance with equal progress; {keep.title} started first",
    )


def _who(evidence: Sequence[Mapping[str, Any]], views: Mapping[str, AgentView]) -> list[str]:
    lines: list[str] = []
    seen: set[tuple] = set()
    for ev in evidence:
        agent_id = str(ev.get("agent_id") or "")
        layer = str(ev.get("layer") or "")
        if not agent_id or layer == "change_scan":
            continue
        key = (agent_id, ev.get("pid"), layer)
        if key in seen:
            continue
        seen.add(key)
        view = views.get(agent_id)
        name = view.display_name if view else "an agent that has finished"
        relation = str(ev.get("relation") or "process")
        command = str(ev.get("cmdline") or "").strip()
        command_words = f" (`{command[:120]}`)" if command and layer != "gate" else ""
        lines.append(f"{name}'s {relation}{command_words} {_LAYER_WORDS.get(layer, layer)}: {ev.get('path')}")
    return lines


def overlap_alert(
    *,
    paths: Sequence[str],
    evidence: Sequence[Mapping[str, Any]],
    views: Mapping[str, AgentView],
    recommendation: Recommendation,
    auto_resumed: bool,
) -> str:
    keep = views[recommendation.keep]
    pause = views[recommendation.pause]
    shown = ", ".join(f"`{p}`" for p in list(paths)[:6])
    more = f" and {len(paths) - 6} more" if len(paths) > 6 else ""
    lines = [f"Two agents touched the same files: {shown}{more}."]
    lines.extend(f"- {line}" for line in _who(evidence, views))
    if auto_resumed:
        lines.append(f"Paused both, then resumed **{keep.display_name}** (recommended: {recommendation.reason}; "
                     f"{keep.progress_words()}).")
    else:
        lines.append(f"Paused both. Recommended: let **{keep.display_name}** run ({recommendation.reason}).")
    lines.append(f"**{pause.display_name}** stays paused until you decide.")
    lines.append(
        f"Options: keep it paused until {keep.title} finishes (recommended) / swap, run {pause.title} "
        f"instead / stop {pause.title}."
    )
    return "\n".join(lines)


def unattributed_alert(*, owner: AgentView | None, paths: Sequence[str], strays: Sequence[Mapping[str, Any]],
                       owner_running: bool) -> str:
    shown = ", ".join(f"`{p}`" for p in list(paths)[:6])
    whose = f"{owner.display_name}'s files" if owner else "claimed files"
    lines = [f"Something changed {whose} and I could not tell which agent did it: {shown}."]
    for stray in strays[:4]:
        lines.append(f"- An orphaned process (`{str(stray.get('cmdline') or '')[:120]}`) had it open or was "
                     "working there; it belongs to no agent, so I did not touch it.")
    if owner is not None and not owner_running:
        lines.append(f"{owner.title} was not running at the time, so it was not its own write.")
    lines.append("Check the files before relying on them. No agent was paused for this, because the culprit is unknown.")
    return "\n".join(lines)


def git_internal_alert(*, paths: Sequence[str], evidence: Sequence[Mapping[str, Any]],
                       views: Mapping[str, AgentView], suspects: Sequence[str]) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:6])
    lines = [f"An agent wrote git internals: {shown}. Hooks run code on the next commit, so this is never part "
             "of a task's claim."]
    lines.extend(f"- {line}" for line in _who(evidence, views))
    names = [views[s].display_name for s in suspects if s in views]
    if len(names) == 1:
        lines.append(f"Paused **{names[0]}**. Check the file before resuming it.")
    elif names:
        lines.append("I could not tell which agent wrote it, so I paused all of them: "
                     + ", ".join(f"**{n}**" for n in names) + ". Check the file, then resume the ones you trust.")
    return "\n".join(lines)


__all__ = [
    "AgentView",
    "Recommendation",
    "git_internal_alert",
    "overlap_alert",
    "recommend",
    "unattributed_alert",
]
