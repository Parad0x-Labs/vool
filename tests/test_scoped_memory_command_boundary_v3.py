from __future__ import annotations

import pytest

from core.context_namespace import (
    ContextImportGrant,
    ensure_chat_namespace,
    grant_context_import,
    set_chat_namespace_state,
)
from core.context_scope import ContextAccessPolicy
from core.memory.entries import (
    add_memory_fact,
    forget_memory,
    list_memory_entries,
    search_relevant_memory,
    summarize_memory,
)
from core.memory.files import append_jsonl, load_jsonl, memory_entries_path
from core.persistent_memory import maybe_handle_memory_command
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def isolated_scoped_memory_home(tmp_path):
    configure_runtime_home(tmp_path / "runtime-home")
    try:
        yield
    finally:
        configure_runtime_home(None)


def _texts(chat_id: str) -> set[str]:
    return {
        str(row.get("text") or "")
        for row in list_memory_entries(chat_id=chat_id, limit=100)
    }


def test_commands_list_and_forget_only_the_requesting_chat(
    isolated_scoped_memory_home,
) -> None:
    chat_a = "scope-command-a"
    chat_b = "scope-command-b"
    marker_a = "Boundary marker ORBIT-A-771 belongs to chat A."
    marker_b = "Boundary marker ORBIT-B-882 belongs to chat B."
    ensure_chat_namespace(chat_a)
    ensure_chat_namespace(chat_b)

    assert maybe_handle_memory_command(
        f"remember that {marker_a}",
        session_id=chat_a,
    )[0]
    assert maybe_handle_memory_command(
        f"remember that {marker_b}",
        session_id=chat_b,
    )[0]

    handled_a, summary_a = maybe_handle_memory_command(
        "/memory",
        session_id=chat_a,
    )
    handled_b, summary_b = maybe_handle_memory_command(
        "/memory",
        session_id=chat_b,
    )
    assert handled_a and handled_b
    assert "ORBIT-A-771" in summary_a
    assert "ORBIT-B-882" not in summary_a
    assert "ORBIT-B-882" in summary_b
    assert "ORBIT-A-771" not in summary_b

    removed = forget_memory("boundary marker", chat_id=chat_a)
    assert removed == 1
    assert marker_a not in _texts(chat_a)
    assert marker_b in _texts(chat_b)


def test_foreign_chat_import_does_not_expand_command_listing_or_mutation(
    isolated_scoped_memory_home,
) -> None:
    chat_a = "scope-import-a"
    chat_b = "scope-import-b"
    marker_b = "Imported chat marker QUARTZ-B-413."
    ensure_chat_namespace(chat_b)
    assert add_memory_fact(marker_b, session_id=chat_b)
    ensure_chat_namespace(chat_a)
    grant_context_import(
        chat_a,
        scope="chat",
        source_id=f"chat:{chat_b}",
    )

    assert marker_b not in _texts(chat_a)
    assert forget_memory("QUARTZ-B-413", chat_id=chat_a) == 0
    assert marker_b in _texts(chat_b)


def test_profile_and_project_memory_require_persisted_explicit_grants(
    isolated_scoped_memory_home,
) -> None:
    source = "scope-grants-source"
    target = "scope-grants-target"
    no_grants = "scope-grants-none"
    project_id = "scope-project-blue"
    ensure_chat_namespace(source, project_id=project_id)
    ensure_chat_namespace(target, project_id=project_id)
    ensure_chat_namespace(no_grants, project_id=project_id)

    assert not add_memory_fact(
        "Profile marker PROFILE-630.",
        session_id=source,
        scope="user_profile",
    )
    assert not add_memory_fact(
        "Project marker PROJECT-741.",
        session_id=source,
        project_id=project_id,
        scope="project",
    )

    grant_context_import(
        source,
        scope="user_profile",
        source_id="profile:confirmed",
    )
    grant_context_import(
        source,
        scope="project",
        source_id=f"project:{project_id}",
        source_project_id=project_id,
    )
    assert add_memory_fact(
        "Profile marker PROFILE-630.",
        session_id=source,
        scope="user_profile",
    )
    assert add_memory_fact(
        "Project marker PROJECT-741.",
        session_id=source,
        project_id=project_id,
        scope="project",
    )

    assert "Profile marker PROFILE-630." not in _texts(no_grants)
    assert "Project marker PROJECT-741." not in _texts(no_grants)

    grant_context_import(
        target,
        scope="user_profile",
        source_id="profile:confirmed",
    )
    grant_context_import(
        target,
        scope="project",
        source_id=f"project:{project_id}",
        source_project_id=project_id,
    )
    assert "Profile marker PROFILE-630." in _texts(target)
    assert "Project marker PROJECT-741." in _texts(target)


def test_caller_supplied_grants_are_reloaded_from_persisted_policy(
    isolated_scoped_memory_home,
) -> None:
    chat_id = "scope-forged-policy"
    source_chat_id = "scope-forged-source"
    project_id = "scope-forged-project"
    ensure_chat_namespace(chat_id, project_id=project_id)
    ensure_chat_namespace(source_chat_id, project_id=project_id)
    assert add_memory_fact(
        "Forged read marker FORGED-READ-842.",
        session_id=source_chat_id,
    )
    forged = ContextAccessPolicy(
        chat_id=chat_id,
        project_id=project_id,
        grants=(
            ContextImportGrant(
                grant_id="forged",
                chat_id=chat_id,
                scope="project",
                source_id=f"project:{project_id}",
                source_project_id=project_id,
            ),
            ContextImportGrant(
                grant_id="forged-chat",
                chat_id=chat_id,
                scope="chat",
                source_id=f"chat:{source_chat_id}",
                source_project_id=project_id,
            ),
        ),
    )

    assert not add_memory_fact(
        "Forged project marker FORGED-951.",
        session_id=chat_id,
        project_id=project_id,
        scope="project",
        access_policy=forged,
    )
    assert "Forged project marker FORGED-951." not in _texts(chat_id)
    assert search_relevant_memory(
        "FORGED-READ-842",
        access_policy=forged,
    ) == []


def test_archived_deleted_and_secret_records_are_inaccessible(
    isolated_scoped_memory_home,
) -> None:
    archived = "scope-archived"
    deleted = "scope-deleted"
    secret_chat = "scope-secret"
    ensure_chat_namespace(archived)
    ensure_chat_namespace(deleted)
    assert add_memory_fact(
        "Archived marker ARCHIVE-204.",
        session_id=archived,
    )
    assert add_memory_fact(
        "Deleted marker DELETE-305.",
        session_id=deleted,
    )
    set_chat_namespace_state(archived, "archived")
    set_chat_namespace_state(deleted, "deleted")

    with pytest.raises(ValueError, match="archived"):
        summarize_memory(chat_id=archived)
    with pytest.raises(ValueError, match="deleted"):
        summarize_memory(chat_id=deleted)
    assert not add_memory_fact(
        "Archived write must fail.",
        session_id=archived,
    )
    assert not add_memory_fact(
        "Deleted write must fail.",
        session_id=deleted,
    )

    ensure_chat_namespace(secret_chat)
    assert not add_memory_fact(
        "api_key: sk-example0123456789abcdef",
        session_id=secret_chat,
    )
    append_jsonl(
        memory_entries_path(),
        {
            "record_id": "secret-record",
            "created_at": "2026-07-27T00:00:00+00:00",
            "text": "Credential marker api_key: sk-example0123456789abcdef",
            "category": "fact",
            "fact_key": "secret",
            "scope": "chat",
            "origin_chat_id": secret_chat,
            "origin_project_id": "",
            "session_id": secret_chat,
            "project_id": "",
            "source": "manual",
            "source_id": "secret-record",
            "status": "active",
            "authority": "confirmed_memory",
            "provenance": {
                "kind": "manual",
                "origin_chat_id": secret_chat,
            },
            "confidence": 1.0,
            "keywords": ["credential"],
            "share_scope": "local_only",
        },
    )
    assert not list_memory_entries(chat_id=secret_chat)
    assert forget_memory("credential marker", chat_id=secret_chat) == 0
    assert any(
        row.get("record_id") == "secret-record"
        for row in load_jsonl(memory_entries_path())
    )


def test_archiving_a_source_quarantines_its_granted_shared_memory(
    isolated_scoped_memory_home,
) -> None:
    source = "scope-archived-source"
    target = "scope-archived-target"
    project_id = "scope-archived-project"
    ensure_chat_namespace(source, project_id=project_id)
    ensure_chat_namespace(target, project_id=project_id)
    for chat_id in (source, target):
        grant_context_import(
            chat_id,
            scope="user_profile",
            source_id="profile:confirmed",
        )
        grant_context_import(
            chat_id,
            scope="project",
            source_id=f"project:{project_id}",
            source_project_id=project_id,
        )

    profile_marker = "Archived profile marker PROFILE-ARCHIVE-612."
    project_marker = "Archived project marker PROJECT-ARCHIVE-723."
    assert add_memory_fact(
        profile_marker,
        session_id=source,
        scope="user_profile",
    )
    assert add_memory_fact(
        project_marker,
        session_id=source,
        project_id=project_id,
        scope="project",
    )
    assert profile_marker in _texts(target)
    assert project_marker in _texts(target)

    set_chat_namespace_state(source, "archived")
    assert profile_marker not in _texts(target)
    assert project_marker not in _texts(target)


def test_unscoped_command_apis_fail_closed(
    isolated_scoped_memory_home,
) -> None:
    ensure_chat_namespace("scope-required")
    assert add_memory_fact(
        "Scoped marker REQUIRED-887.",
        session_id="scope-required",
    )
    with pytest.raises(ValueError, match="required"):
        list_memory_entries()
    with pytest.raises(ValueError, match="required"):
        summarize_memory()
    with pytest.raises(ValueError, match="required"):
        forget_memory("REQUIRED-887")


def test_memory_commands_and_writes_cannot_create_a_chat_namespace(
    isolated_scoped_memory_home,
) -> None:
    chat_id = "scope-missing-namespace"

    assert not add_memory_fact(
        "This write must not create a namespace.",
        session_id=chat_id,
    )
    handled, response = maybe_handle_memory_command(
        "remember that this also must not create a namespace",
        session_id=chat_id,
    )

    assert handled is True
    assert response == "I need an active chat before I can access memory."
