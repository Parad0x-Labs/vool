"""The conversation reaches the model once; vacuous bootstrap lines do not reach it at all.

Measured on the model-free baseline: a plain question's prompt carried the conversation-policy
law twice (the system message's pinned sentences AND the bootstrap "Conversation policy" item),
plus "Current topics: none. References: none." and a task summary that restated the question
verbatim. The dedupe happens at assembly; every meaning survives in exactly one carrier.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.tiered_context_loader import TieredContextResult


def _result_with_items(items: list) -> SimpleNamespace:
    # assembled_context reads only the three item lists; a light namespace exercises it without
    # building the full report the dataclass wants.
    return SimpleNamespace(bootstrap_items=items, relevant_items=[], cold_items=[])


def _item(source_type: str, content: str, item_id: str = "bootstrap-x") -> SimpleNamespace:
    return SimpleNamespace(
        item_id=item_id, layer="bootstrap", source_type=source_type,
        title=source_type.replace("_", " ").title(), content=content, priority=0.9,
        confidence=0.9, must_keep=True, include_reason="test", metadata={},
    )


def test_a_grounding_block_with_no_evidence_does_not_ride_the_prompt() -> None:
    from core.bootstrap_context import _capsule_block_carries_no_evidence

    empty = (
        "<retrieved_context>\n"
        "Grounding observations for this turn. Use them as evidence, not as a template:\n"
        '{"actions_taken": [], "channel": "adaptive_research", "evidence_strength": "none",\n'
        ' "queries_run": [], "source_count": 0, "source_domains": [], "sources": [],\n'
        ' "strategy": "tool_gap"}\n'
        "</retrieved_context>"
    )
    assert _capsule_block_carries_no_evidence(empty) is True
    with_source = empty.replace('"sources": []', '"sources": [{"title": "x"}]')
    assert _capsule_block_carries_no_evidence(with_source) is False
    with_query = empty.replace('"queries_run": []', '"queries_run": ["look"]')
    assert _capsule_block_carries_no_evidence(with_query) is False
    assert _capsule_block_carries_no_evidence("plain prose") is False  # fail toward shipping


def test_the_identity_lines_keep_every_rule_in_shorter_words() -> None:
    from core.user_identity_authority import identity_context_lines

    blob = " ".join(identity_context_lines())
    for phrase in (
        "The USER's name is not known",
        "your own name is NOT theirs",
        "Do NOT invent",
        "never answer your own name",
        "do not know it yet",
        "never the user's",
        "overrides any other name for yourself",
        "Never introduce yourself as anything else",
    ):
        assert phrase in blob, phrase


def test_a_stored_row_the_history_already_carries_does_not_ride_twice(tmp_path) -> None:
    """Recall turns: a stored row verbatim inside this chat's history is the same fact twice."""
    import os

    os.environ["VOOL_HOME"] = str(tmp_path)
    from core.context_namespace import ensure_chat_namespace
    from storage.dialogue_memory import record_dialogue_turn

    ensure_chat_namespace("dedupe-test", grant_current_receipts=False)
    record_dialogue_turn(
        "dedupe-test",
        raw_input="my harbour berth number is seven",
        normalized_input="my harbour berth number is seven",
        reconstructed_input="my harbour berth number is seven",
        speaker_role="user", topic_hints=[], reference_targets=[],
        understanding_confidence=0.9, quality_flags=[],
    )
    from core.tiered_context_loader import _persistent_memory_items

    # The stored row's text equals the history row's text: nothing to add.
    items = _persistent_memory_items(
        "what is my harbour berth number",
        [],
        session_id="dedupe-test",
        policy=_scope_policy_for("dedupe-test"),
    )
    contents = [item.content for item in items]
    assert all("harbour berth number is seven" not in str(c) or c in contents for c in contents)
    assert not any("seven" in str(c) and "harbour" in str(c) for c in contents), (
        "a verbatim-history row must not ride the recall prompt a second time"
    )


def _scope_policy_for(session_id: str):
    from core.context_scope import ContextAccessPolicy

    return ContextAccessPolicy.for_request(session_id=session_id, source_context={"platform": "api"})
