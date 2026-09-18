from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.active_context_capsule import (
    _capsule_record_hash,
    create_shadow_capsule,
    ensure_active_capsule_schema,
    list_capsule_versions,
    load_active_capsule,
    load_latest_capsule,
)
from core.agent_runtime.orchestrator import build_tool_action_record
from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    store_tool_receipt,
)
from core.runtime_paths import configure_runtime_home
from storage.db import active_default_db_path, get_connection
from storage.migrations import run_migrations


@pytest.fixture
def capsule_home(tmp_path):
    configure_runtime_home(tmp_path)
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)
        configure_runtime_continuity_db_path(active_default_db_path())


def _store_valid_action_receipt(
    *,
    chat_id: str,
    project_id: str = "",
    summary: str = "Verified tool action completed.",
) -> tuple[str, dict[str, object]]:
    checkpoint_id = f"checkpoint:{chat_id}"
    record = build_tool_action_record(
        {
            "chat_id": chat_id,
            "_trusted_project_id": project_id,
            "checkpoint_id": checkpoint_id,
            "turn_id": f"turn:{chat_id}",
        },
        event_type="tool_executed",
        message=summary,
        details={
            "tool_name": "workspace.search_text",
            "status": "executed",
            "mode": "tool_executed",
            "ok": True,
            "summary": summary,
            "arguments": {"query": "capsule provenance"},
        },
    )
    assert record is not None
    receipt_id = str(record["receipt_id"])
    store_tool_receipt(
        receipt_key=receipt_id,
        session_id=chat_id,
        checkpoint_id=checkpoint_id,
        tool_name=str(record["tool_name"]),
        idempotency_key=str(record["action_id"]),
        arguments=dict(record["parameters"]),
        execution={"action_record": record},
    )
    return receipt_id, record


def test_capsule_creation_requires_existing_matching_namespace(
    capsule_home,
) -> None:
    chat_id = "chat:must-exist-for-capsule"
    with pytest.raises(ValueError, match="does not exist"):
        create_shadow_capsule(
            chat_id=chat_id,
            user_text="This must not create a namespace.",
        )

    ensure_chat_namespace(chat_id, project_id="project-a")
    with pytest.raises(ValueError, match="project does not match"):
        create_shadow_capsule(
            chat_id=chat_id,
            project_id="project-b",
            user_text="This must not cross projects.",
        )
    assert list_capsule_versions(chat_id) == ()


def test_shadow_capsules_are_versioned_source_backed_and_not_active(
    capsule_home,
) -> None:
    chat_id = "chat:capsule"
    ensure_chat_namespace(chat_id)
    first = create_shadow_capsule(
        chat_id=chat_id,
        user_text="We decided to use SQLite. Never copy secrets.",
    )
    second = create_shadow_capsule(
        chat_id=chat_id,
        user_text="Now implement the retrieval boundary.",
    )

    assert first.version_number == 1
    assert second.version_number == 2
    assert second.previous_version_id == first.version_id
    assert second.transcript_head_hash != first.transcript_head_hash
    assert len(first.record_hash) == 64
    assert first.record_hash != first.payload_hash
    assert load_latest_capsule(chat_id) == second
    assert load_active_capsule(chat_id) is None
    assert [item.version_number for item in list_capsule_versions(chat_id)] == [
        1,
        2,
    ]
    assert first.constraints
    assert all(
        claim.source_type == "transcript_user"
        for claim in (*first.decisions, *first.constraints)
    )


def test_capsule_rows_are_immutable_and_corruption_is_rejected(
    capsule_home,
) -> None:
    ensure_chat_namespace("chat:immutable")
    capsule = create_shadow_capsule(
        chat_id="chat:immutable",
        user_text="Keep this source-backed.",
    )
    conn = get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE context_capsule_versions
                SET capsule_json = '{}'
                WHERE version_id = ?
                """,
                (capsule.version_id,),
            )
    finally:
        conn.close()


def test_legacy_capsules_are_backfilled_before_strict_hash_enforcement(
    capsule_home,
) -> None:
    chat_id = "chat:legacy-capsule"
    ensure_chat_namespace(chat_id)
    encoded = json.dumps(
        {
            "objective": None,
            "decisions": [],
            "constraints": [],
            "system_state": [],
            "unresolved_work": [],
            "verified_actions": [],
            "receipt_references": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_hash = hashlib.sha256(encoded.encode()).hexdigest()
    version_id = "capsule:legacy-version"
    conn = get_connection()
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS context_capsule_heads;
            DROP TABLE IF EXISTS context_capsule_versions;

            CREATE TABLE context_capsule_versions (
                version_id TEXT PRIMARY KEY,
                version_number INTEGER NOT NULL DEFAULT 1,
                chat_id TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                previous_version_id TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL,
                capsule_json TEXT NOT NULL,
                transcript_head_hash TEXT NOT NULL DEFAULT '',
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, version_number)
            );

            CREATE TABLE context_capsule_heads (
                chat_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TRIGGER context_capsule_versions_no_update
            BEFORE UPDATE ON context_capsule_versions
            BEGIN
                SELECT RAISE(ABORT, 'context capsule versions are immutable');
            END;
            """
        )
        conn.execute(
            """
            INSERT INTO context_capsule_versions (
                version_id, version_number, chat_id, project_id,
                previous_version_id, mode, capsule_json,
                transcript_head_hash, payload_hash, created_at
            ) VALUES (?, 1, ?, '', '', 'shadow', ?, ?, ?, ?)
            """,
            (
                version_id,
                chat_id,
                encoded,
                "legacy-transcript-head",
                payload_hash,
                "2026-07-27T12:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO context_capsule_heads (
                chat_id, version_id, updated_at
            ) VALUES (?, ?, ?)
            """,
            (chat_id, version_id, "2026-07-27T12:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    ensure_active_capsule_schema()
    migrated = load_latest_capsule(chat_id)

    assert migrated is not None
    assert len(migrated.record_hash) == 64
    conn = get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE context_capsule_versions
                SET project_id = 'tampered'
                WHERE version_id = ?
                """,
                (version_id,),
            )
    finally:
        conn.close()


def test_capsules_cannot_update_archived_or_deleted_chats(
    capsule_home,
) -> None:
    chat_id = "chat:inactive-capsule"
    ensure_chat_namespace(chat_id)
    set_chat_namespace_state(chat_id, "archived")
    with pytest.raises(ValueError, match="active chat"):
        create_shadow_capsule(
            chat_id=chat_id,
            user_text="must not be stored",
        )

    set_chat_namespace_state(chat_id, "active")
    set_chat_namespace_state(chat_id, "deleted")
    with pytest.raises(ValueError, match="active chat"):
        create_shadow_capsule(
            chat_id=chat_id,
            user_text="must not resurrect",
        )


def test_two_chats_never_share_capsule_state(capsule_home) -> None:
    ensure_chat_namespace("chat:left")
    ensure_chat_namespace("chat:right")
    left = create_shadow_capsule(
        chat_id="chat:left",
        user_text="Left marker LEFT-CAPSULE-731.",
    )
    right = create_shadow_capsule(
        chat_id="chat:right",
        user_text="Right marker RIGHT-CAPSULE-842.",
    )

    assert left.chat_id != right.chat_id
    assert left.version_id != right.version_id
    assert "RIGHT-CAPSULE-842" not in str(left)
    assert "LEFT-CAPSULE-731" not in str(right)


def test_concurrent_capsule_writes_form_one_linear_chain(capsule_home) -> None:
    chat_id = "chat:concurrent-capsule"
    ensure_chat_namespace(chat_id)

    def _write(turn: int):
        return create_shadow_capsule(
            chat_id=chat_id,
            user_text=f"Turn {turn}; never expose marker {turn}.",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        written = list(pool.map(_write, range(1, 7)))

    versions = list_capsule_versions(chat_id)
    by_id = {capsule.version_id: capsule for capsule in versions}

    assert len(written) == len(versions) == 6
    assert [capsule.version_number for capsule in versions] == list(range(1, 7))
    assert versions[0].previous_version_id == ""
    assert all(
        capsule.previous_version_id == versions[index - 1].version_id
        for index, capsule in enumerate(versions[1:], start=1)
    )
    assert all(
        capsule.previous_version_id in by_id
        for capsule in versions[1:]
    )
    assert load_latest_capsule(chat_id) == versions[-1]


def test_verified_actions_are_derived_only_from_matching_durable_receipts(
    capsule_home,
) -> None:
    chat_id = "chat:verified-action"
    project_id = "project:verified-action"
    ensure_chat_namespace(chat_id, project_id=project_id)
    receipt_id, _ = _store_valid_action_receipt(
        chat_id=chat_id,
        project_id=project_id,
        summary="Found the verified source file.",
    )

    capsule = create_shadow_capsule(
        chat_id=chat_id,
        project_id=project_id,
        user_text="Continue with the verified result.",
        verified_actions=[
            {
                "verified": True,
                "receipt_id": receipt_id,
                "safe_summary": "Caller-forged summary must not win.",
            }
        ],
        receipt_references=[receipt_id],
    )

    assert capsule.receipt_references == (receipt_id,)
    assert len(capsule.verified_actions) == 1
    assert capsule.verified_actions[0].source_id == receipt_id
    assert capsule.verified_actions[0].text == "Found the verified source file."
    assert "Caller-forged" not in str(capsule)


def test_missing_or_unbound_receipts_never_enter_a_capsule(
    capsule_home,
) -> None:
    target_chat = "chat:receipt-target"
    target_project = "project:receipt-target"
    ensure_chat_namespace(target_chat, project_id=target_project)
    wrong_chat_receipt, _ = _store_valid_action_receipt(
        chat_id="chat:receipt-other",
        project_id=target_project,
    )
    wrong_project_receipt, _ = _store_valid_action_receipt(
        chat_id=target_chat,
        project_id="project:receipt-other",
    )

    capsule = create_shadow_capsule(
        chat_id=target_chat,
        project_id=target_project,
        user_text="Keep only receipts bound to this namespace.",
        verified_actions=[
            {
                "verified": True,
                "safe_summary": "No receipt ID must not synthesize one.",
            },
            {
                "verified": True,
                "receipt_id": "receipt:missing",
                "safe_summary": "Missing durable receipt.",
            },
            {
                "verified": True,
                "receipt_id": wrong_chat_receipt,
            },
            {
                "verified": True,
                "receipt_id": wrong_project_receipt,
            },
        ],
        receipt_references=[
            "receipt:missing",
            wrong_chat_receipt,
            wrong_project_receipt,
        ],
    )

    assert capsule.verified_actions == ()
    assert capsule.receipt_references == ()
    assert "action:" not in str(capsule)


def test_tampered_durable_action_record_is_quarantined(capsule_home) -> None:
    chat_id = "chat:tampered-action"
    project_id = "project:tampered-action"
    ensure_chat_namespace(chat_id, project_id=project_id)
    receipt_id, record = _store_valid_action_receipt(
        chat_id=chat_id,
        project_id=project_id,
    )
    tampered = dict(record)
    tampered["result"] = {
        **dict(record["result"]),
        "summary": "Tampered after hashing.",
    }
    store_tool_receipt(
        receipt_key=receipt_id,
        session_id=chat_id,
        checkpoint_id=f"checkpoint:{chat_id}",
        tool_name=str(record["tool_name"]),
        idempotency_key=str(record["action_id"]),
        arguments=dict(record["parameters"]),
        execution={"action_record": tampered},
    )

    capsule = create_shadow_capsule(
        chat_id=chat_id,
        project_id=project_id,
        user_text="Do not trust the tampered record.",
        verified_actions=[
            {
                "verified": True,
                "receipt_id": receipt_id,
            }
        ],
        receipt_references=[receipt_id],
    )

    assert capsule.verified_actions == ()
    assert capsule.receipt_references == ()


def test_capsule_record_hash_binds_all_immutable_record_fields(
    capsule_home,
) -> None:
    base = {
        "version_id": "capsule:hash-binding",
        "version_number": 2,
        "chat_id": "chat:hash-binding",
        "project_id": "project:hash-binding",
        "previous_version_id": "capsule:previous",
        "mode": "shadow",
        "capsule_json": '{"objective":null}',
        "transcript_head_hash": "transcript-head",
        "payload_hash": "payload-hash",
        "created_at": "2026-07-27T12:00:00+00:00",
    }
    baseline = _capsule_record_hash(**base)
    mutations = {
        "version_id": "capsule:other",
        "version_number": 3,
        "chat_id": "chat:other",
        "project_id": "project:other",
        "previous_version_id": "capsule:other-previous",
        "mode": "active",
        "capsule_json": '{"objective":{"text":"changed"}}',
        "transcript_head_hash": "other-transcript-head",
        "payload_hash": "other-payload-hash",
        "created_at": "2026-07-27T12:00:01+00:00",
    }

    for field, value in mutations.items():
        candidate = dict(base)
        candidate[field] = value
        assert _capsule_record_hash(**candidate) != baseline


def test_rehashed_payload_tampering_is_rejected_by_record_hash(
    capsule_home,
) -> None:
    chat_id = "chat:payload-tamper"
    ensure_chat_namespace(chat_id)
    create_shadow_capsule(
        chat_id=chat_id,
        user_text="Original source-backed objective.",
    )
    encoded = json.dumps(
        {
            "objective": None,
            "decisions": [],
            "constraints": [],
            "system_state": [],
            "unresolved_work": [],
            "verified_actions": [],
            "receipt_references": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    conn = get_connection()
    try:
        conn.execute("DROP TRIGGER context_capsule_versions_no_update")
        conn.execute(
            """
            UPDATE context_capsule_versions
            SET capsule_json = ?, payload_hash = ?
            WHERE chat_id = ?
            """,
            (
                encoded,
                hashlib.sha256(encoded.encode()).hexdigest(),
                chat_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert load_latest_capsule(chat_id) is None
    assert list_capsule_versions(chat_id) == ()


def test_cross_chat_or_nonsequential_predecessor_invalidates_chain_and_writes(
    capsule_home,
) -> None:
    chat_id = "chat:chain-target"
    ensure_chat_namespace(chat_id)
    ensure_chat_namespace("chat:chain-other")
    first = create_shadow_capsule(
        chat_id=chat_id,
        user_text="First target turn.",
    )
    other = create_shadow_capsule(
        chat_id="chat:chain-other",
        user_text="Unrelated chat turn.",
    )
    forged_version_id = "capsule:forged-cross-chat"
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT capsule_json, payload_hash, created_at
            FROM context_capsule_versions
            WHERE version_id = ?
            """,
            (first.version_id,),
        ).fetchone()
        assert row is not None
        forged_record_hash = _capsule_record_hash(
            version_id=forged_version_id,
            version_number=2,
            chat_id=chat_id,
            project_id=first.project_id,
            previous_version_id=other.version_id,
            mode="shadow",
            capsule_json=str(row["capsule_json"]),
            transcript_head_hash=first.transcript_head_hash,
            payload_hash=str(row["payload_hash"]),
            created_at=str(row["created_at"]),
        )
        conn.execute(
            """
            INSERT INTO context_capsule_versions (
                version_id, version_number, chat_id, project_id,
                previous_version_id, mode, capsule_json,
                transcript_head_hash, payload_hash, record_hash, created_at
            ) VALUES (?, 2, ?, ?, ?, 'shadow', ?, ?, ?, ?, ?)
            """,
            (
                forged_version_id,
                chat_id,
                first.project_id,
                other.version_id,
                row["capsule_json"],
                first.transcript_head_hash,
                row["payload_hash"],
                forged_record_hash,
                row["created_at"],
            ),
        )
        conn.execute(
            """
            UPDATE context_capsule_heads
            SET version_id = ?
            WHERE chat_id = ?
            """,
            (forged_version_id, chat_id),
        )
        conn.commit()
    finally:
        conn.close()

    assert load_latest_capsule(chat_id) is None
    assert list_capsule_versions(chat_id) == ()
    with pytest.raises(ValueError, match="chain is corrupt"):
        create_shadow_capsule(
            chat_id=chat_id,
            user_text="A corrupt chain must reject the next write.",
        )
