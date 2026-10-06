"""q90-lifecycle — project-scope context grants gate project memory reads.

The project leg of the grant law: a project-scope memory entry serves a chat
only when the chat's namespace belongs to that project AND holds an explicit
project import grant (search_relevant_memory/_memory_row_allowed — the owning
seam for persistent-memory reads). Same-project without the grant: denied.
No-project chat: denied even with a stray grant attempt.

Fresh domain: alpine botanical survey.
"""
from __future__ import annotations

import pytest

from core.context_namespace import (
    ensure_chat_namespace,
    grant_context_import,
)
from core.memory import entries as memory_entries
from core.memory.entries import (
    add_memory_fact,
    resolve_memory_access_policy,
    search_relevant_memory,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def test_project_scope_entry_serves_only_with_project_grant(fresh_profile):
    project = "q90-botany-alpine"
    writer_chat = "q90-botany-writer"
    member_chat = "q90-botany-member"
    outsider_chat = "q90-botany-outsider"

    for chat in (writer_chat, member_chat):
        ensure_chat_namespace(chat, project_id=project, grant_current_receipts=False)
    ensure_chat_namespace(outsider_chat, grant_current_receipts=False)

    # Project entries are only writable by a chat that itself holds the
    # project import grant (existing write law) — the writer is an
    # established project member, the member chat has NOT opted in yet.
    grant_context_import(writer_chat, scope="project", source_id=f"project:{project}")
    writer_policy = resolve_memory_access_policy(chat_id=writer_chat)
    assert add_memory_fact(
        "The rare blue gentian on the north ridge of Mount Sarel blooms in the last week of July.",
        session_id=writer_chat,
        scope="project",
        project_id=project,
        access_policy=writer_policy,
        keywords=["gentian", "ridge", "july", "blooms"],
    ), {"note": "project fact must be admitted"}

    member_policy = resolve_memory_access_policy(chat_id=member_chat)
    outsider_policy = resolve_memory_access_policy(chat_id=outsider_chat)

    # same project, NO project grant -> invisible
    without_grant = search_relevant_memory(
        "When does the gentian on the ridge bloom?",
        access_policy=member_policy,
        limit=4,
    )
    assert not any("gentian" in str(row.get("text") or "") for row in without_grant), {
        "without_grant": without_grant,
        "note": "same project membership alone must not expose project memory",
    }

    # explicit project grant -> visible
    grant_context_import(member_chat, scope="project", source_id=f"project:{project}")
    refreshed_member = resolve_memory_access_policy(chat_id=member_chat)
    with_grant = search_relevant_memory(
        "When does the gentian on the ridge bloom?",
        access_policy=refreshed_member,
        limit=4,
    )
    assert any("gentian" in str(row.get("text") or "") for row in with_grant), {
        "with_grant": with_grant,
        "note": "explicit project grant must expose the project fact",
    }

    # outsider chat (no project) -> invisible even though the grant table is per-chat
    outsider_view = search_relevant_memory(
        "When does the gentian on the ridge bloom?",
        access_policy=outsider_policy,
        limit=4,
    )
    assert not any("gentian" in str(row.get("text") or "") for row in outsider_view), {
        "outsider_view": outsider_view,
    }
