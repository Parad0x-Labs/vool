from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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
    find_disputed_memory_facts,
    resolve_memory_access_policy,
)
from core.memory.files import (
    load_jsonl,
    memory_entries_path,
    rewrite_jsonl,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def memory_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def _policy(chat_id: str) -> ContextAccessPolicy:
    return resolve_memory_access_policy(chat_id=chat_id)


def _add_dispute(
    chat_id: str,
    *,
    fact_key: str,
    first: str,
    second: str,
    scope: str = "chat",
    project_id: str = "",
    expires_at: str = "",
) -> None:
    policy = _policy(chat_id)
    assert add_memory_fact(
        first,
        session_id=chat_id,
        project_id=project_id,
        scope=scope,
        fact_key=fact_key,
        expires_at=expires_at,
        access_policy=policy,
    )
    assert add_memory_fact(
        second,
        session_id=chat_id,
        project_id=project_id,
        scope=scope,
        fact_key=fact_key,
        expires_at=expires_at,
        access_policy=policy,
    )


def _prepare_project_chat(chat_id: str, project_id: str) -> None:
    ensure_chat_namespace(chat_id, project_id=project_id)
    grant_context_import(
        chat_id,
        scope="project",
        source_id=f"project:{project_id}",
        source_project_id=project_id,
    )


def test_current_chat_dispute_returns_only_sanitized_descriptor(
    memory_home,
) -> None:
    chat_id = "chat:current-dispute"
    ensure_chat_namespace(chat_id)
    _add_dispute(
        chat_id,
        fact_key="location:office",
        first="The office is in Seattle.",
        second="The office is in Vancouver.",
    )

    result = find_disputed_memory_facts(
        "Where is the office?",
        access_policy=_policy(chat_id),
    )

    assert len(result) == 1
    descriptor = result[0]
    assert descriptor["fact_key"] == "location:office"
    assert descriptor["scope"] == "chat"
    assert descriptor["origin_chat_id"] == chat_id
    assert descriptor["record_count"] == 2
    assert len(descriptor["record_ids"]) == 2
    assert len(descriptor["content_hashes"]) == 2
    assert len(descriptor["provenance_hash"]) == 64
    assert set(descriptor) == {
        "fact_key",
        "scope",
        "record_count",
        "record_ids",
        "content_hashes",
        "provenance_hash",
        "origin_chat_id",
    }
    serialized = json.dumps(result).lower()
    assert "seattle" not in serialized
    assert "vancouver" not in serialized
    assert "the office is" not in serialized


def test_foreign_chat_disputes_are_filtered_before_matching(
    memory_home,
) -> None:
    target = "chat:target"
    foreign = "chat:foreign"
    ensure_chat_namespace(target)
    ensure_chat_namespace(foreign)
    _add_dispute(
        target,
        fact_key="location:office",
        first="The office is in Portland.",
        second="The office is in Victoria.",
    )
    _add_dispute(
        foreign,
        fact_key="location:office",
        first="The office is in Seattle.",
        second="The office is in Vancouver.",
    )

    result = find_disputed_memory_facts(
        "office location",
        access_policy=_policy(target),
    )

    assert len(result) == 1
    assert result[0]["origin_chat_id"] == target
    assert result[0]["record_count"] == 2
    serialized = json.dumps(result).lower()
    assert "seattle" not in serialized
    assert "vancouver" not in serialized
    assert foreign not in serialized


def test_project_dispute_requires_matching_persisted_grant(
    memory_home,
) -> None:
    project_id = "project:atlas"
    source = "chat:project-source"
    target = "chat:project-target"
    _prepare_project_chat(source, project_id)
    ensure_chat_namespace(target, project_id=project_id)
    _add_dispute(
        source,
        fact_key="release:channel",
        first="The release channel is stable.",
        second="The release channel is preview.",
        scope="project",
        project_id=project_id,
    )

    assert find_disputed_memory_facts(
        "release channel",
        access_policy=_policy(target),
    ) == []

    grant_context_import(
        target,
        scope="project",
        source_id=f"project:{project_id}",
        source_project_id=project_id,
    )
    result = find_disputed_memory_facts(
        "release channel",
        access_policy=_policy(target),
    )

    assert len(result) == 1
    assert result[0]["scope"] == "project"
    assert result[0]["origin_project_id"] == project_id
    assert result[0]["origin_chat_ids"] == [source]


def test_caller_forged_grants_and_missing_namespaces_fail_closed(
    memory_home,
) -> None:
    project_id = "project:forged"
    source = "chat:forged-source"
    target = "chat:forged-target"
    _prepare_project_chat(source, project_id)
    ensure_chat_namespace(target, project_id=project_id)
    _add_dispute(
        source,
        fact_key="deployment:region",
        first="The deployment region is east.",
        second="The deployment region is west.",
        scope="project",
        project_id=project_id,
    )
    forged_grant = ContextImportGrant(
        grant_id="grant:forged",
        chat_id=target,
        scope="project",
        source_id=f"project:{project_id}",
        source_project_id=project_id,
    )
    forged = ContextAccessPolicy(
        chat_id=target,
        project_id=project_id,
        grants=(forged_grant,),
    )

    assert find_disputed_memory_facts(
        "deployment region",
        access_policy=forged,
    ) == []
    assert find_disputed_memory_facts(
        "deployment region",
        access_policy=ContextAccessPolicy(
            chat_id="chat:missing",
            project_id=project_id,
            grants=(forged_grant,),
        ),
    ) == []


def test_archived_and_deleted_origins_are_never_reported(
    memory_home,
) -> None:
    project_id = "project:lifecycle"
    source = "chat:lifecycle-source"
    target = "chat:lifecycle-target"
    _prepare_project_chat(source, project_id)
    _prepare_project_chat(target, project_id)
    _add_dispute(
        source,
        fact_key="service:endpoint",
        first="The service endpoint is alpha.",
        second="The service endpoint is beta.",
        scope="project",
        project_id=project_id,
    )
    policy = _policy(target)
    assert find_disputed_memory_facts(
        "service endpoint",
        access_policy=policy,
    )

    set_chat_namespace_state(source, "archived")
    assert find_disputed_memory_facts(
        "service endpoint",
        access_policy=policy,
    ) == []

    set_chat_namespace_state(source, "active")
    assert find_disputed_memory_facts(
        "service endpoint",
        access_policy=policy,
    )
    set_chat_namespace_state(source, "deleted")
    assert find_disputed_memory_facts(
        "service endpoint",
        access_policy=policy,
    ) == []


def test_expired_secret_legacy_and_non_conflicts_are_quarantined(
    memory_home,
) -> None:
    chat_id = "chat:quarantine"
    ensure_chat_namespace(chat_id)
    _add_dispute(
        chat_id,
        fact_key="temporary:code",
        first="The temporary code is 1122.",
        second="The temporary code is 3344.",
        expires_at=(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(),
    )
    assert add_memory_fact(
        "The probable color is violet.",
        session_id=chat_id,
        fact_key="preference:color",
        authority="model_inference",
        access_policy=_policy(chat_id),
    )

    rows = load_jsonl(memory_entries_path())
    valid = next(row for row in rows if row["fact_key"] == "temporary:code")
    secret = (
        "[redacted-openrouter-prefix]"
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    legacy_rows = [
        {
            **valid,
            "record_id": "memory-legacy-a",
            "fact_key": "legacy:unproven",
            "text": "The legacy answer is north.",
            "keywords": ["legacy", "answer"],
            "expires_at": "",
            "provenance": {},
        },
        {
            **valid,
            "record_id": "memory-legacy-b",
            "fact_key": "legacy:unproven",
            "text": "The legacy answer is south.",
            "keywords": ["legacy", "answer"],
            "expires_at": "",
            "provenance": {},
        },
        {
            **valid,
            "record_id": "memory-secret-a",
            "fact_key": "credential:provider",
            "text": f"api_key={secret}",
            "keywords": ["credential", "provider"],
            "expires_at": "",
        },
        {
            **valid,
            "record_id": "memory-secret-b",
            "fact_key": "credential:provider",
            "text": "The provider credential is different.",
            "keywords": ["credential", "provider"],
            "expires_at": "",
        },
    ]
    rewrite_jsonl(memory_entries_path(), [*rows, *legacy_rows])

    assert find_disputed_memory_facts(
        "temporary code legacy answer provider credential probable color",
        access_policy=_policy(chat_id),
    ) == []
