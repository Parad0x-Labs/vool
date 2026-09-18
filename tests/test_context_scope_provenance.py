from __future__ import annotations

import hashlib

import pytest

from core.context_scope import (
    ContextAccessPolicy,
    CurrentTurnCorrection,
    annotate_and_filter,
    current_turn_corrections,
    reconcile_context_candidates,
)
from core.prompt_assembly_report import ContextItem
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def context_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield
    finally:
        configure_runtime_home(None)


def _policy(chat_id: str) -> ContextAccessPolicy:
    return ContextAccessPolicy.for_request(
        session_id=chat_id,
        source_context={"surface": "local"},
    )


def test_server_assembled_current_context_gets_traceable_provenance(
    context_home,
) -> None:
    policy = _policy("chat:server-context")
    item = ContextItem(
        item_id="current-task",
        layer="bootstrap",
        source_type="task_constraints",
        title="Current task",
        content="Answer the current question.",
    )

    allowed, denied = annotate_and_filter([item], policy)

    assert not denied
    assert len(allowed) == 1
    assert allowed[0].metadata["origin_chat_id"] == policy.chat_id
    assert allowed[0].provenance["kind"] == "server_assembled_context"
    assert allowed[0].provenance["content_hash"] == hashlib.sha256(
        item.content.encode()
    ).hexdigest()


def test_legacy_user_heuristic_gets_final_content_hash_before_ranking(
    context_home,
) -> None:
    policy = _policy("chat:legacy-profile")
    item = ContextItem(
        item_id="user-heuristic-response_style-concise_direct",
        layer="relevant",
        source_type="user_heuristic",
        title="User heuristic response style",
        content="Keep answers concise and direct.",
        metadata={
            "scope": "user_profile",
            "source": "user_heuristic",
            "source_id": "profile:confirmed",
            "status": "active",
            "provenance": {
                "kind": "direct_user_observation",
                "origin_chat_id": "chat:older-profile",
                "origin_project_id": "",
            },
        },
    )

    allowed, denied = annotate_and_filter([item], policy)

    assert not denied
    assert len(allowed) == 1
    assert allowed[0].provenance["kind"] == "direct_user_observation"
    assert allowed[0].provenance["source_id"] == "profile:confirmed"
    assert allowed[0].provenance["content_hash"] == hashlib.sha256(
        item.content.encode()
    ).hexdigest()


@pytest.mark.parametrize(
    ("source_type", "scope"),
    (
        ("runtime_memory", "chat"),
        ("session_summary", "chat"),
    ),
)
def test_legacy_trusted_persisted_context_gets_final_content_hash_before_ranking(
    context_home,
    source_type: str,
    scope: str,
) -> None:
    policy = _policy("chat:legacy-persisted")
    item = ContextItem(
        item_id=f"legacy-{source_type}",
        layer="relevant",
        source_type=source_type,
        title="Legacy persisted context",
        content="LEGACY-PERSISTED-CONTEXT-713",
        metadata={
            "scope": scope,
            "source": source_type,
            "source_id": f"{source_type}:713",
            "status": "active",
            "origin_chat_id": policy.chat_id,
            "provenance": {"kind": "legacy_persisted_record"},
        },
    )

    allowed, denied = annotate_and_filter([item], policy)

    assert not denied
    assert len(allowed) == 1
    assert allowed[0].provenance["content_hash"] == hashlib.sha256(
        item.content.encode()
    ).hexdigest()


def test_unproven_durable_memory_is_quarantined_before_ranking(
    context_home,
) -> None:
    policy = _policy("chat:durable-context")
    item = ContextItem(
        item_id="legacy-memory",
        layer="relevant",
        source_type="runtime_memory",
        title="Legacy memory",
        content="LEGACY-UNPROVEN-741",
        metadata={
            "scope": "chat",
            "source": "runtime_memory",
            "status": "active",
            "origin_chat_id": policy.chat_id,
            "origin_project_id": "",
        },
    )

    allowed, denied = annotate_and_filter([item], policy)

    assert not allowed
    assert len(denied) == 1
    assert denied[0][1] == "context_provenance_missing"


def test_proven_durable_memory_remains_eligible(context_home) -> None:
    policy = _policy("chat:proven-context")
    item = ContextItem(
        item_id="proven-memory",
        layer="relevant",
        source_type="runtime_memory",
        title="Proven memory",
        content="PROVEN-742",
        metadata={
            "scope": "chat",
            "source": "runtime_memory",
            "status": "active",
            "origin_chat_id": policy.chat_id,
            "origin_project_id": "",
            "provenance": {
                "kind": "verified_test_record",
                "source_id": "memory:742",
            },
        },
    )

    allowed, denied = annotate_and_filter([item], policy)

    assert not denied
    assert [candidate.item_id for candidate in allowed] == [
        "proven-memory"
    ]


def test_explicit_marker_correction_excludes_stale_candidates_before_ranking(
    context_home,
) -> None:
    policy = _policy("chat:marker-correction")
    stale = ContextItem(
        item_id="stale-marker",
        layer="relevant",
        source_type="runtime_memory",
        title="Old marker",
        content="The marker was CEDAR-7391.",
        metadata={
            "scope": "chat",
            "source": "runtime_memory",
            "source_id": "memory:stale-marker",
            "status": "active",
            "origin_chat_id": policy.chat_id,
            "provenance": {
                "kind": "verified_test_record",
                "source_id": "memory:stale-marker",
            },
        },
    )
    current = ContextItem(
        item_id="current-note",
        layer="relevant",
        source_type="dialogue_turn",
        title="Current note",
        content="A general conversation note.",
        metadata={
            "scope": "chat",
            "source": "dialogue_turn",
            "source_id": "turn:current-note",
            "status": "active",
            "origin_chat_id": policy.chat_id,
        },
    )
    allowed, _denied = annotate_and_filter([stale, current], policy)
    corrections = current_turn_corrections(
        "Correction: the only valid marker for this chat is MEADOW-8053. "
        "Use it and forget any other marker."
    )

    reconciled, excluded = reconcile_context_candidates(allowed, corrections)

    assert [item.item_id for item in reconciled] == ["current-note"]
    assert [(item.item_id, reason) for item, reason in excluded] == [
        ("stale-marker", "superseded_by_current_turn:marker")
    ]


def test_marker_correction_does_not_filter_the_current_value(context_home) -> None:
    policy = _policy("chat:current-marker")
    current = ContextItem(
        item_id="current-marker",
        layer="relevant",
        source_type="runtime_memory",
        title="Current marker",
        content="The only valid marker is MEADOW-8053.",
        metadata={
            "scope": "chat",
            "source": "runtime_memory",
            "source_id": "memory:current-marker",
            "status": "active",
            "origin_chat_id": policy.chat_id,
            "provenance": {
                "kind": "verified_test_record",
                "source_id": "memory:current-marker",
            },
        },
    )
    allowed, denied = annotate_and_filter([current], policy)
    assert not denied

    reconciled, excluded = reconcile_context_candidates(
        allowed,
        current_turn_corrections(
            "Correction: the marker is now MEADOW-8053."
        ),
    )

    assert [item.item_id for item in reconciled] == ["current-marker"]
    assert not excluded


def test_explicit_fact_correction_excludes_an_older_matching_fact_before_ranking(
    context_home,
) -> None:
    policy = _policy("chat:fact-correction")
    stale = ContextItem(
        item_id="stale-favorite-color",
        layer="relevant",
        source_type="runtime_memory",
        title="Persistent memory preference",
        content="My favorite color is blue.",
        metadata={
            "fact_key": "preference:favorite color",
            "fact_value": "blue",
            "scope": "chat",
            "source": "runtime_memory",
            "source_id": "memory:favorite-color",
            "status": "active",
            "origin_chat_id": policy.chat_id,
            "provenance": {
                "kind": "verified_test_record",
                "source_id": "memory:favorite-color",
            },
        },
    )
    allowed, denied = annotate_and_filter([stale], policy)
    assert not denied

    corrections = current_turn_corrections(
        "Correction: my favorite color is green."
    )
    reconciled, excluded = reconcile_context_candidates(allowed, corrections)

    assert reconciled == []
    assert [(item.item_id, reason) for item, reason in excluded] == [
        (
            "stale-favorite-color",
            "superseded_by_current_turn:favorite color",
        )
    ]


def test_ordinary_statement_is_not_treated_as_a_fact_correction() -> None:
    assert current_turn_corrections("My favorite color is green.") == ()


def test_changed_mind_preference_is_a_typed_correction() -> None:
    assert current_turn_corrections(
        "I changed my mind: I prefer a short notebook after all."
    ) == (CurrentTurnCorrection("preference", "a short notebook after all"),)


def test_stale_preference_dialogue_is_excluded_before_ranking() -> None:
    stale = ContextItem(
        item_id="stale-preference",
        layer="relevant",
        source_type="dialogue_turn",
        title="Recent dialogue turn",
        content="For this chat, I prefer paper sketches to digital notes.",
        metadata={"scope": "chat", "source": "dialogue_turn", "status": "active"},
    )

    reconciled, excluded = reconcile_context_candidates(
        [stale],
        (CurrentTurnCorrection("preference", "a compact notebook after all"),),
    )

    assert reconciled == []
    assert [(item.item_id, reason) for item, reason in excluded] == [
        ("stale-preference", "superseded_by_current_turn:preference"),
    ]


def test_current_chat_receipt_with_wrong_project_is_denied(context_home) -> None:
    chat_id = "chat:receipt-project"
    ContextAccessPolicy.for_request(
        session_id=chat_id,
        source_context={
            "surface": "local",
            "_trusted_project_id": "project-a",
        },
    )
    policy = ContextAccessPolicy.for_request(
        session_id=chat_id,
        source_context={"surface": "local"},
    )
    item = ContextItem(
        item_id="wrong-project-receipt",
        layer="relevant",
        source_type="tool_observation",
        title="Receipt from another project",
        content='{"receipt_id":"receipt-b"}',
        metadata={
            "scope": "action_receipt",
            "source": "tool_observation",
            "status": "active",
            "receipt_id": "receipt-b",
            "origin_chat_id": chat_id,
            "origin_project_id": "project-b",
            "provenance": {
                "kind": "tool_action_receipt",
                "source_id": "receipt-b",
            },
        },
    )

    allowed, denied = annotate_and_filter([item], policy)

    assert not allowed
    assert denied[0][1] == "action_receipt_project_mismatch"
