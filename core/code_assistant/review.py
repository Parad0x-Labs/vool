"""Review and evidence summary: a read-only projection of what an engagement actually did.

Two layers:

- ``summarize_effects`` turns the Blackbox journal for one turn into typed evidence rows and
  re-hashes the CURRENT bytes on disk against the journaled after-hash — VERIFIED means the bytes
  the user can see right now are the bytes the authorized mutation produced, DRIFTED means someone
  touched them since, MISSING means they are gone. It reads; it never executes, never journals,
  and never mutates a byte.
- ``review_evidence_tool`` is the runtime-tool handler behind the ``code.review_evidence`` intent,
  registered at the runtime dispatch seam, so the MODEL can request the projection like any other
  read-only tool and the boundary's permission decision stands behind the call like any other.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

STATE_VERIFIED = "VERIFIED"
STATE_DRIFTED = "DRIFTED"
STATE_MISSING = "MISSING"
STATE_NO_JOURNAL = "UNJOURNALED"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _observation_sha(observation: Any) -> str:
    for attr in ("sha256", "content_sha256", "hash"):
        value = getattr(observation, attr, None)
        if value:
            return str(value)
    if isinstance(observation, dict):
        for key in ("sha256", "content_sha256", "hash"):
            if observation.get(key):
                return str(observation[key])
    return ""


def summarize_effects(turn_id: str, *, workspace_root: Path | str) -> dict[str, Any]:
    """Journal-backed evidence rows for one Blackbox turn, verified against current bytes."""
    from core.blackbox.store import default_store

    root = Path(workspace_root)
    store = default_store()
    effects = store.effects_for_turn(turn_id)
    rows: list[dict[str, Any]] = []
    for effect in effects:
        absolute = Path(effect.root) / effect.path if effect.root else root / effect.path
        after = effect.after
        after_sha = _observation_sha(after) if after is not None else ""
        if not absolute.is_file():
            state = STATE_MISSING if after_sha else STATE_NO_JOURNAL
            current_sha = ""
        else:
            current_sha = _sha256_file(absolute)
            if not after_sha:
                state = STATE_NO_JOURNAL
            elif current_sha == after_sha:
                state = STATE_VERIFIED
            else:
                state = STATE_DRIFTED
        rows.append(
            {
                "effect_id": str(getattr(effect, "effect_id", "") or ""),
                "path": str(effect.path),
                "operation": str(effect.intended.get("operation") or effect.intended.get("intent") or ""),
                "outcome": str(effect.outcome or ""),
                "before_sha256": _observation_sha(effect.before),
                "after_sha256": after_sha,
                "current_sha256": current_sha,
                "state": state,
            }
        )
    return {
        "turn_id": turn_id,
        "workspace_root": str(root),
        "effects": rows,
        "verified_count": sum(1 for row in rows if row["state"] == STATE_VERIFIED),
        "drifted_count": sum(1 for row in rows if row["state"] == STATE_DRIFTED),
        "missing_count": sum(1 for row in rows if row["state"] == STATE_MISSING),
    }


def latest_turn_for_root(*, workspace_root: Path | str) -> str:
    """The most recent journaled turn whose effects landed under this workspace root."""
    from core.blackbox.store import default_store

    root = Path(workspace_root).resolve()
    store = default_store()
    index = store.turn_index()
    best_id = ""
    best_seq = -1
    for turn_id, summary in index.items():
        summary_root = str(getattr(summary, "root", "") or "")
        if summary_root and Path(summary_root).resolve() != root:
            continue
        seq = int(getattr(summary, "first_seq", 0) or 0)
        if seq >= best_seq:
            best_seq = seq
            best_id = turn_id
    return best_id


def review_evidence_tool(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    source_context: dict[str, Any] | None = None,
) -> Any:
    """Handler for the ``code.review_evidence`` runtime intent. Read-only by construction: it
    consults the journal and the filesystem, runs nothing, and journals nothing."""
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    from core.blackbox.store import default_store

    turn_id = str((arguments or {}).get("turn_id") or "").strip()
    if not turn_id:
        turn_id = latest_turn_for_root(workspace_root=workspace_root)
    if not turn_id or turn_id not in default_store().turn_index():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text="No journaled workspace effects found for this workspace yet.",
            details={
                "executed": False,
                "observation": _tool_observation(
                    intent="code.review_evidence",
                    tool_surface="code_assistant",
                    ok=False,
                    status="not_found",
                ),
            },
        )
    summary = summarize_effects(turn_id, workspace_root=workspace_root)
    rows = summary["effects"]
    lines = [f"Evidence for Blackbox turn `{turn_id}`:"]
    for row in rows:
        lines.append(
            f"- {row['state']}: `{row['path']}` {row['operation']} → outcome {row['outcome']} "
            f"(after {row['after_sha256'][:12] or '—'}, now {row['current_sha256'][:12] or '—'})"
        )
    if not rows:
        lines.append("- (no journaled effects for this turn)")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="summarized",
        response_text="\n".join(lines),
        details={
            "turn_id": turn_id,
            "evidence": rows,
            "verified_count": summary["verified_count"],
            "drifted_count": summary["drifted_count"],
            "missing_count": summary["missing_count"],
            "observation": _tool_observation(
                intent="code.review_evidence",
                tool_surface="code_assistant",
                ok=True,
                status="summarized",
                turn_id=turn_id,
                verified_count=summary["verified_count"],
                drifted_count=summary["drifted_count"],
                missing_count=summary["missing_count"],
            ),
        },
    )


__all__ = [
    "STATE_DRIFTED",
    "STATE_MISSING",
    "STATE_NO_JOURNAL",
    "STATE_VERIFIED",
    "latest_turn_for_root",
    "review_evidence_tool",
    "summarize_effects",
]
