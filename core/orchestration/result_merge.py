from __future__ import annotations

from typing import Any

from .task_envelope import TaskEnvelopeV1


def _failure_merge_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    role = str(item.get("role") or "").strip()
    status = str(item.get("status") or "").strip()
    task_id = str(item.get("task_id") or "").strip()
    return (
        0 if role == "verifier" else 1,
        0 if status not in {"dependency_failed", "capacity_blocked"} else 1,
        task_id,
    )


def merge_task_results(parent: TaskEnvelopeV1, results: list[dict[str, Any]]) -> dict[str, Any]:
    clean_results = [dict(item) for item in results if isinstance(item, dict)]
    strategy = str(parent.merge_strategy or "first_success").strip()
    if not clean_results:
        return {"strategy": strategy, "ok": False, "results": []}
    if strategy == "highest_score":
        failed_results = [item for item in clean_results if not bool(item.get("ok", False))]
        if failed_results:
            winner = min(failed_results, key=_failure_merge_priority)
            return {
                "strategy": strategy,
                "ok": False,
                "winner": winner,
                "results": clean_results,
                "failed_results": failed_results,
            }
        winner = max(clean_results, key=lambda item: (float(item.get("score") or 0.0), str(item.get("task_id") or "")))
        return {"strategy": strategy, "ok": bool(winner.get("ok", True)), "winner": winner, "results": clean_results}
    if strategy == "concat_sections":
        ordered = sorted(clean_results, key=lambda item: str(item.get("task_id") or ""))
        combined = "\n\n".join(str(item.get("text") or "").strip() for item in ordered if str(item.get("text") or "").strip())
        return {"strategy": strategy, "ok": all(bool(item.get("ok", True)) for item in ordered), "text": combined, "results": ordered}
    ordered = list(clean_results)
    if strategy == "last_success":
        verifier_failures = [
            item for item in ordered if str(item.get("role") or "").strip() == "verifier" and not bool(item.get("ok", False))
        ]
        if verifier_failures:
            winner = min(verifier_failures, key=_failure_merge_priority)
            return {
                "strategy": strategy,
                "ok": False,
                "winner": winner,
                "results": ordered,
                "failed_results": verifier_failures,
            }
        successful = [item for item in ordered if bool(item.get("ok", False))]
        winner = successful[-1] if successful else ordered[-1]
        return {"strategy": strategy, "ok": bool(winner.get("ok", False)), "winner": winner, "results": ordered}
    winner = next((item for item in ordered if bool(item.get("ok", False))), ordered[0])
    return {"strategy": strategy, "ok": bool(winner.get("ok", False)), "winner": winner, "results": ordered}
