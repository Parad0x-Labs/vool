"""Guard: a workspace search must search for the SYMBOL, not the whole sentence.

Every search pattern anchors its query group with `$`, so a lazy `.+?` ran to end of line:
"search this workspace for plan_tool_workflow, then read the file that defines it and explain what it
does" searched for that entire 12-word string and reported "No text matches" for a symbol that is
present -- a false negative the runtime returned as ok=True. The determiner was also pinned to
"this", so "search THE repo for X" matched no pattern at all.
"""
from __future__ import annotations

import pytest

from core.execution.planner import _planned_workspace_search_payload


def _query(text: str):
    payload = _planned_workspace_search_payload(text)
    return payload[1]["arguments"]["query"] if payload else None


@pytest.mark.parametrize(
    ("phrasing", "expected"),
    [
        # The audit's exact repro.
        ("Search this workspace for plan_tool_workflow, then read the file that defines it and explain what it does.",
         "plan_tool_workflow"),
        ("search the repo for WorkflowPlannerDecision and then read the file that defines it",
         "WorkflowPlannerDecision"),
        ("search the project for ContextItem. Then summarise it", "ContextItem"),
        ("search my codebase for handle_turn_frontdoor", "handle_turn_frontdoor"),
        ("search the repository for ToolIntentExecution and explain it", "ToolIntentExecution"),
        ("search this workspace for foo", "foo"),
    ],
)
def test_query_is_the_symbol_not_the_sentence(phrasing, expected):
    assert _query(phrasing) == expected


def test_a_genuine_multi_word_query_is_preserved():
    """Trimming must cut CHAINED CLAUSES, not legitimate multi-word search terms."""
    assert _query("search this workspace for my long phrase with spaces") == "my long phrase with spaces"


def test_reach_across_determiners():
    """A number, so a future narrowing is a visible regression."""
    phrasings = [
        "search this workspace for alpha", "search the repo for beta", "search my project for gamma",
        "search our codebase for delta", "search the repository for epsilon",
    ]
    hits = sum(1 for p in phrasings if _query(p))
    assert hits == len(phrasings), f"workspace-search reach fell to {hits}/{len(phrasings)}"
