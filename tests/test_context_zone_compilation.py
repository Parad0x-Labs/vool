"""NIA-011 first landing: served context is COMPILED through the three-zone law.

`TieredContextResult.assembled_context` now routes through
`core.context_layout.compile_context`: stable frozen prefix (bootstrap), then
the append-only digest (cold/closed work), then the volatile frontier
(relevant/open work) — the fixed order a provider exact-prefix cache keys on.
With no stable prefix the compiler law refuses and we fall back to the legacy
join rather than shipping a context that can never cache.
"""
from __future__ import annotations

from core.context_layout import LayoutLawViolation
from core.prompt_assembly_report import PromptAssemblyReport
from core.tiered_context_loader import TieredContextResult


def _result(bootstrap, relevant, cold):
    def _items(pairs):
        from core.tiered_context_loader import ContextItem

        return [
            ContextItem(
                item_id=f"test-{i}",
                layer="bootstrap",
                source_type="test",
                title=t,
                content=c,
                confidence=0.9,
                priority=1.0,
                metadata={},
            )
            for i, (t, c) in enumerate(pairs)
        ]

    from core.prompt_assembly_report import PromptAssemblyReport
    from core.tiered_context_loader import ColdContextDecision

    report = PromptAssemblyReport(
        task_id="test-task",
        trace_id="test-trace",
        total_context_budget=8000,
        bootstrap_budget=2000,
        relevant_budget=4000,
        cold_budget=2000,
    )
    return TieredContextResult(
        bootstrap_items=_items(bootstrap),
        relevant_items=_items(relevant),
        cold_items=_items(cold),
        local_candidates=[],
        swarm_metadata=[],
        report=report,
        retrieval_confidence_score=1.0,
        cold_decision=ColdContextDecision(allow=False, reason="test"),
    )


def test_zone_order_is_frozen_digest_frontier():
    result = _result(
        bootstrap=[("sys", "you are VOOL")],
        relevant=[("open", "the open bug the user just mentioned")],
        cold=[("closed", "the finished design from last week")],
    )
    text = result.assembled_context()
    assert text.index("you are VOOL") < text.index("the finished design")
    assert text.index("the finished design") < text.index("the open bug")


def test_compiled_output_is_deterministic():
    result = _result(
        bootstrap=[("sys", "stable prefix")],
        relevant=[("open", "volatile")],
        cold=[("closed", "archive")],
    )
    assert result.assembled_context() == result.assembled_context()


def test_no_frozen_prefix_falls_back_instead_of_refusing():
    # The compiler law refuses an empty frozen zone; the loader must fall back
    # to the legacy join, never raise into a turn.
    result = _result(bootstrap=[], relevant=[("open", "only open work")], cold=[])
    text = result.assembled_context()
    assert "only open work" in text


def test_zone_law_itself_refuses_empty_frozen():
    from core.context_layout import ContextBlock, Role, compile_context

    try:
        compile_context([ContextBlock(name="f", role=Role.FRONTIER, text="x")])
    except LayoutLawViolation:
        pass
    else:
        raise AssertionError("empty frozen zone must be refused by the law")
