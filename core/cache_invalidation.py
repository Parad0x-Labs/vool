from __future__ import annotations

from core.candidate_knowledge_lane import invalidate_candidate, recent_candidates


def invalidate_candidate_output(candidate_id: str, *, reason: str) -> None:
    invalidate_candidate(candidate_id, reason=reason)


def invalidate_failed_turn_decision(task_hash: str, *, reason: str) -> bool:
    """Drop the cached decision that produced a failed turn, so the next identical ask re-runs.

    A decision is recorded at model-call time, before its tool executes, and validation there only
    asks "is this a well-formed tool intent" — so a well-formed WRONG choice is stored `valid` at
    trust 1.0 and replays forever.

    Measured on the deployed build 2026-07-28: "give me a rundown of what lives in
    ~/Desktop/my-workshop-folder" had the model pick `workspace.list_files` (workspace-relative,
    no workspace bound) instead of `machine.list_directory`. The turn correctly refused rather than
    guessing — and then the refusal became permanent for that exact sentence, twice in a row, while
    the same phrasing on a different folder answered fine. The user's only escape was to reword.

    Retrying a failure is not always right, but replaying one never is: the model is nondeterministic
    and the world changes, so the second ask deserves a fresh attempt. Failures stay recorded in the
    audit trail; this only stops them being served as answers.
    """

    if not str(task_hash or "").strip():
        return False
    dropped = False
    try:
        for candidate in recent_candidates(limit=200):
            if candidate.get("invalidated_at") or candidate.get("task_hash") != task_hash:
                continue
            invalidate_candidate(candidate["candidate_id"], reason=reason)
            dropped = True
    except Exception:
        return dropped
    return dropped


def invalidate_stale_candidates() -> int:
    invalidated = 0
    for candidate in recent_candidates(limit=200):
        if candidate.get("invalidated_at"):
            continue
        if candidate.get("expires_at") and candidate["expires_at"] <= candidate["created_at"]:
            invalidate_candidate(candidate["candidate_id"], reason="invalid_expiry_window")
            invalidated += 1
    return invalidated
