"""Auxiliary calls fire only on turns that need them.

The entity-ambiguity fold (rule riding the answering call instead of the serial
probe) was reverted and its dead code removed: the serial probe owns every
probe-eligible turn again, and no entity rule rides the answering prompt. The
recall phrase-writer still fires only on recall-shaped questions. Both gates
are the runtime's own structural readers -- the probe's own eligibility gate
and the plain-task recall detector -- never word lists.
"""
from __future__ import annotations

from core.agent_runtime.turn_planner_hook import search_expansions_for_turn


def test_no_entity_rule_rides_the_answering_prompt() -> None:
    """The fold is gone: a probe-eligible question's directives carry no entity rule
    (the serial probe in core.agent_runtime.agent owns the law; its failure path is
    pinned by the mixed-demand closure tests)."""
    from tests.test_v050_tiny_turn_token_overhead import _assemble

    request = _assemble(
        "what is the largest lake on the island of Borneo",
        output_mode="plain_text", task_kind="normalization_assist",
    )
    contents = [str(getattr(message, "content", "")) for message in request.messages]
    directives = [text for text in contents if text.startswith("Context for this turn:")]
    assert directives and "Entity rule for this question" not in directives[0]


def test_search_expansion_fires_only_on_recall_shaped_questions() -> None:
    class _MustNotRun:
        def __call__(self, *_args, **_kwargs):
            raise AssertionError("a non-recall turn bought a phrase-writing call")

    # A plain world-knowledge question: no phrase call.
    assert search_expansions_for_turn(_MustNotRun(), {}, "which moon is largest in the solar system?") == ()
    # A recall-shaped question about the speaker's own facts: the gate must not decline.
    from core.plain_task_routing import single_clause_recall_question

    assert single_clause_recall_question("what did I say my dog is called?") is True
