from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolIntentExecution:
    handled: bool
    ok: bool
    status: str
    response_text: str = ""
    user_safe_response_text: str = ""
    mode: str = "tool_failed"
    tool_name: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    learned_plan: Any = None


@dataclass
class WorkflowPlannerDecision:
    handled: bool
    reason: str
    next_payload: dict[str, Any] | None = None
    stop_after: bool = False
    # The rest of the CONCRETE plan behind `next_payload`, when the planner already knows it.
    #
    # The deterministic planner emits one payload per round, so the permission controller saw one
    # write at a time and could only ever offer a one-action prompt -- even for a scaffold whose
    # whole file list was decided before the first call. Measured on this branch before the fix: a
    # plan of `workspace.ensure_directory` plus two `workspace.write_file` calls raised three
    # separate prompts, each offering allow-once/deny and no batch, because `last_tool_decision` is
    # None on a planner round and there was nothing else for the loop to hand over.
    #
    # Carrying it here is a HAND-OFF, not an authorization: the controller re-derives every
    # member's action set and fingerprint from scratch and drops anything it will not cover.
    planned_batch: list[dict[str, Any]] = field(default_factory=list)


def _tool_observation(
    *,
    intent: str,
    tool_surface: str,
    ok: bool,
    status: str,
    **payload: Any,
) -> dict[str, Any]:
    observation = {
        "schema": "tool_observation_v1",
        "intent": str(intent or "").strip(),
        "tool_surface": str(tool_surface or "").strip(),
        "ok": bool(ok),
        "status": str(status or "").strip(),
    }
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        observation[str(key)] = value
    return observation
