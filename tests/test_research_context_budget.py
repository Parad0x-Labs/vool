"""Research notes reach the model by budget, not by a fixed count of four.

`adaptive_research_observations` kept `notes[:4]`: a five-facet comparison that retrieved seven
short sources handed the model four of them, so at least one facet could never be answered from
evidence whatever the retrieval did.
"""
from __future__ import annotations

from core.agent_runtime.chat_surface import adaptive_research_observations
from core.curiosity_roamer import AdaptiveResearchResult


def _notes(n: int, size: int = 200) -> list[dict]:
    return [{"result_title": f"Source {i}", "result_url": f"https://fixture.example/{i}", "origin_domain": "fixture.example",
             "summary": ("x" * size)} for i in range(n)]


def test_seven_short_notes_all_reach_the_model() -> None:
    result = AdaptiveResearchResult(enabled=True, reason="research_task", notes=_notes(7))
    observations = adaptive_research_observations(task_class="research", research_result=result)
    assert observations["source_count"] == 7, observations["source_count"]


def test_the_character_budget_bounds_the_context() -> None:
    result = AdaptiveResearchResult(enabled=True, reason="research_task", notes=_notes(20, size=1500))
    observations = adaptive_research_observations(task_class="research", research_result=result)
    total = sum(len(s["summary"]) for s in observations["sources"])
    assert observations["source_count"] < 20
    assert total <= 8000, total
