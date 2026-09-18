from __future__ import annotations

from dataclasses import dataclass

_EXPLICIT_ARCHIVE_MARKERS = (
    "old history",
    "older history",
    "previous",
    "earlier",
    "before",
    "archive",
    "audit",
    "receipt",
    "trace",
    "what did we",
    "remember when",
)


@dataclass(frozen=True)
class ColdContextDecision:
    allow: bool
    reason: str
    explicit_request: bool = False
    archive_dependent: bool = False


def evaluate_cold_context_gate(
    *,
    user_text: str,
    task_class: str,
    relevant_confidence_score: float,
    strategy: dict[str, object] | None = None,
) -> ColdContextDecision:
    lower = (user_text or "").lower()
    strategy = strategy or {}
    explicit_request = any(marker in lower for marker in _EXPLICIT_ARCHIVE_MARKERS)
    archive_dependent = bool(strategy.get("archive_dependent", False))

    if explicit_request:
        return ColdContextDecision(True, "explicit_archive_request", explicit_request=True, archive_dependent=archive_dependent)
    if archive_dependent:
        return ColdContextDecision(True, "archive_dependent_task", explicit_request=False, archive_dependent=True)
    if relevant_confidence_score < 0.42 and task_class in {"research", "system_design", "config"}:
        return ColdContextDecision(True, "relevance_confidence_low", explicit_request=False, archive_dependent=False)
    return ColdContextDecision(False, "cold_context_not_justified")
