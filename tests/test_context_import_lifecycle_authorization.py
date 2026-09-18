from __future__ import annotations

import pytest

from core import tiered_context_loader
from core.context_namespace import (
    ensure_chat_namespace,
    grant_context_import,
    list_context_imports,
    revoke_context_import,
    set_chat_namespace_state,
)
from core.context_scope import ContextAccessPolicy, annotate_and_filter
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


def _imported_item(source_chat_id: str) -> ContextItem:
    return ContextItem(
        item_id="imported-chat-marker",
        layer="relevant",
        source_type="runtime_memory",
        title="Imported chat marker",
        content="IMPORTED-CHAT-LIFECYCLE-741",
        metadata={
            "scope": "chat",
            "source": "runtime_memory",
            "status": "active",
            "origin_chat_id": source_chat_id,
            "origin_project_id": "",
            "provenance": {
                "kind": "verified_test_record",
                "source_id": "memory:lifecycle-741",
            },
        },
    )


def test_chat_import_tracks_source_lifecycle_before_ranking(
    context_home,
) -> None:
    source = "chat:import-source"
    target = "chat:import-target"
    ensure_chat_namespace(source)
    ensure_chat_namespace(target)
    grant_context_import(
        target,
        scope="chat",
        source_id=f"chat:{source}",
    )
    policy = _policy(target)
    candidate = _imported_item(source)

    allowed, denied = annotate_and_filter([candidate], policy)
    assert [item.item_id for item in allowed] == [candidate.item_id]
    assert not denied

    set_chat_namespace_state(source, "archived")

    allowed, denied = annotate_and_filter([candidate], policy)
    assert not allowed
    assert denied[0][1] == "imported_chat_namespace_archived_denied"
    assert source not in policy.imported_chat_ids
    assert any(
        grant.source_id == f"chat:{source}"
        for grant in list_context_imports(target)
    )

    set_chat_namespace_state(source, "active")

    allowed, denied = annotate_and_filter([candidate], policy)
    assert [item.item_id for item in allowed] == [candidate.item_id]
    assert not denied
    assert source in policy.imported_chat_ids

    set_chat_namespace_state(source, "deleted")

    allowed, denied = annotate_and_filter([candidate], policy)
    assert not allowed
    assert denied[0][1] == "imported_chat_namespace_deleted_denied"
    assert source not in policy.imported_chat_ids


def test_restore_does_not_reactivate_a_revoked_chat_import(
    context_home,
) -> None:
    source = "chat:revoked-source"
    target = "chat:revoked-target"
    ensure_chat_namespace(source)
    ensure_chat_namespace(target)
    grant_context_import(
        target,
        scope="chat",
        source_id=f"chat:{source}",
    )
    set_chat_namespace_state(source, "archived")
    assert revoke_context_import(
        target,
        scope="chat",
        source_id=f"chat:{source}",
    )

    set_chat_namespace_state(source, "active")
    policy = _policy(target)
    allowed, denied = annotate_and_filter(
        [_imported_item(source)],
        policy,
    )

    assert not allowed
    assert denied[0][1] == "cross_chat_denied"
    assert source not in policy.imported_chat_ids


def test_archived_only_import_stops_before_candidate_search(
    context_home,
    monkeypatch,
) -> None:
    source = "chat:pre-ranking-source"
    target = "chat:pre-ranking-target"
    ensure_chat_namespace(source)
    ensure_chat_namespace(target)
    grant_context_import(
        target,
        scope="chat",
        source_id=f"chat:{source}",
    )
    policy = _policy(target)
    set_chat_namespace_state(source, "archived")

    def unexpected_search(*_args, **_kwargs):
        raise AssertionError("archived import reached candidate search")

    monkeypatch.setattr(
        tiered_context_loader,
        "search_session_summaries",
        unexpected_search,
    )

    assert not tiered_context_loader._session_summary_items(
        "find the imported marker",
        ["imported", "marker"],
        session_id=target,
        policy=policy,
    )
