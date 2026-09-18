from __future__ import annotations

import pytest

from core.context_namespace import (
    clone_chat_namespace,
    ensure_chat_namespace,
    grant_context_import,
    list_chat_namespaces,
    list_context_imports,
    load_chat_namespace,
    set_chat_namespace_project,
    set_chat_namespace_state,
)
from core.persistent_memory import (
    append_conversation_event,
    list_conversation_sessions,
    recent_conversation_events,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def context_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def test_new_namespace_is_visible_without_history(context_home) -> None:
    namespace = ensure_chat_namespace(
        "chat:new",
        project_id="project-a",
        grant_confirmed_profile=True,
    )

    assert namespace.lifecycle_state == "active"
    assert recent_conversation_events(namespace.chat_id) == []
    assert any(
        item["session_id"] == namespace.chat_id
        and item["turn_count"] == 0
        and item["title"] == "New chat"
        for item in list_conversation_sessions()
    )


def test_lifecycle_and_grant_apis_cannot_create_a_namespace(
    context_home,
) -> None:
    chat_id = "chat:must-exist"

    with pytest.raises(ValueError, match="does not exist"):
        set_chat_namespace_state(chat_id, "archived")
    with pytest.raises(ValueError, match="does not exist"):
        grant_context_import(
            chat_id,
            scope="chat",
            source_id="chat:foreign",
        )
    assert load_chat_namespace(chat_id) is None


def test_archive_restore_and_delete_are_enforced_at_write_boundary(
    context_home,
) -> None:
    chat_id = "chat:lifecycle"
    ensure_chat_namespace(chat_id)
    append_conversation_event(
        session_id=chat_id,
        user_input="first",
        assistant_output="stored",
    )

    archived = set_chat_namespace_state(chat_id, "archived")
    assert archived.lifecycle_state == "archived"
    with pytest.raises(ValueError, match="chat namespace is archived"):
        append_conversation_event(
            session_id=chat_id,
            user_input="must not persist",
            assistant_output="blocked",
        )
    with pytest.raises(ValueError, match="active chat"):
        grant_context_import(
            chat_id,
            scope="chat",
            source_id="chat:foreign",
        )

    restored = set_chat_namespace_state(chat_id, "active")
    assert restored.lifecycle_state == "active"
    append_conversation_event(
        session_id=chat_id,
        user_input="after restore",
        assistant_output="stored",
    )

    deleted = set_chat_namespace_state(chat_id, "deleted")
    assert deleted.lifecycle_state == "deleted"
    assert list_context_imports(chat_id) == ()
    with pytest.raises(ValueError, match="cannot be restored"):
        set_chat_namespace_state(chat_id, "active")
    with pytest.raises(ValueError, match="chat namespace is deleted"):
        append_conversation_event(
            session_id=chat_id,
            user_input="must not resurrect",
            assistant_output="blocked",
        )
    assert all(
        item.chat_id != chat_id
        for item in list_chat_namespaces()
    )
    assert load_chat_namespace(chat_id) is not None


def test_duplicate_and_branch_copy_only_a_stable_transcript_snapshot(
    context_home,
) -> None:
    source = "chat:source"
    ensure_chat_namespace(source, project_id="project-a")
    grant_context_import(
        source,
        scope="project",
        source_id="project:project-a",
        source_project_id="project-a",
    )
    for number in range(1, 4):
        append_conversation_event(
            session_id=source,
            user_input=f"turn {number}",
            assistant_output=f"answer {number}",
            source_context={"_trusted_project_id": "project-a"},
        )

    duplicate = clone_chat_namespace(source, "chat:duplicate")
    branch = clone_chat_namespace(
        source,
        "chat:branch",
        through_turn=2,
    )

    duplicate_rows = recent_conversation_events(
        duplicate.chat_id,
        limit=10,
    )
    branch_rows = recent_conversation_events(branch.chat_id, limit=10)
    assert [row["event_sequence"] for row in duplicate_rows] == [1, 2, 3]
    assert [row["event_sequence"] for row in branch_rows] == [1, 2]
    assert all(row.get("source_event_id") for row in duplicate_rows)
    assert all(row["session_id"] == duplicate.chat_id for row in duplicate_rows)
    assert branch.parent_chat_id == source
    assert branch.branch_turn == 2
    assert not any(
        grant.scope == "project"
        for grant in list_context_imports(duplicate.chat_id)
    )


def test_project_reassignment_revokes_prior_project_import(
    context_home,
) -> None:
    chat_id = "chat:move-project"
    ensure_chat_namespace(chat_id, project_id="project-a")
    grant_context_import(
        chat_id,
        scope="project",
        source_id="project:project-a",
        source_project_id="project-a",
    )

    moved = set_chat_namespace_project(chat_id, "project-b")

    assert moved.project_id == "project-b"
    assert not any(
        grant.scope == "project"
        for grant in list_context_imports(chat_id)
    )
