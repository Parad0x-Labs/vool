from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from core.routing_authority_v2 import (
    AuthorityRecord,
    ContractValidationError,
    RoutingAuthorityV2ShadowStore,
    ShadowStoreIntegrityError,
    SubjectBindingV2,
)


def _subject(*, context_character: str = "a") -> SubjectBindingV2:
    return SubjectBindingV2(
        authenticated_subject_id="subject-1",
        authenticated_session_id="session-1",
        turn_id="turn-1",
        context_digest=context_character * 64,
        credential_generation=1,
    )


def test_shadow_store_persists_only_canonical_non_authorizing_records(tmp_path: Path) -> None:
    store = RoutingAuthorityV2ShadowStore(tmp_path / "routing-shadow.sqlite3")
    record = _subject()
    assert store.execution_authority is False
    with pytest.raises(AttributeError):
        store.execution_authority = True  # type: ignore[misc]
    assert store.persist_shadow_record(record) == record.digest()
    assert store.persist_shadow_record(record) == record.digest()
    assert store.read_shadow_record(record.digest()) == record
    assert store.list_shadow_record_digests() == (record.digest(),)
    assert store.list_shadow_record_digests(record_type="SubjectBindingV2") == (record.digest(),)
    assert store.list_shadow_record_digests(record_type="RoutingPlanV2") == ()


def test_shadow_store_database_constraint_rejects_execution_authority(tmp_path: Path) -> None:
    path = tmp_path / "routing-shadow.sqlite3"
    store = RoutingAuthorityV2ShadowStore(path)
    record = _subject()
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO routing_authority_v2_shadow_records (
                record_digest, record_type, schema_version, canonical_bytes, execution_authority
            ) VALUES (?, ?, ?, ?, 1)
            """,
            (record.digest(), record.RECORD_TYPE, record.SCHEMA_VERSION, record.canonical_bytes()),
        )
    assert store.read_shadow_record(record.digest()) is None


def test_shadow_store_detects_canonical_byte_tampering(tmp_path: Path) -> None:
    path = tmp_path / "routing-shadow.sqlite3"
    store = RoutingAuthorityV2ShadowStore(path)
    record = _subject()
    store.persist_shadow_record(record)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE routing_authority_v2_shadow_records SET canonical_bytes = ? WHERE record_digest = ?",
            (b'{"record_type":"SubjectBindingV2"}', record.digest()),
        )
    with pytest.raises(ShadowStoreIntegrityError, match="invalid canonical bytes"):
        store.read_shadow_record(record.digest())


@pytest.mark.parametrize(
    ("set_clause", "tampered_value", "tampered_digest"),
    (
        ("record_type = ?", "RoutingIntentV2", None),
        ("schema_version = ?", 1, None),
        ("execution_authority = ?", 1, None),
        ("authority_state = ?", "EXECUTABLE", None),
        ("authority_phase = ?", 1, None),
        ("canonical_bytes = ?", b'{"record_type":"SubjectBindingV2"}', None),
        ("canonical_bytes = ?", _subject(context_character="b").canonical_bytes(), None),
        ("record_digest = ?", "b" * 64, "b" * 64),
        ("record_type = ?", "SubjectBindingV2Allowed", None),
    ),
)
def test_mutant_idempotent_persist_and_read_reject_every_authority_row_mismatch(
    tmp_path: Path,
    set_clause: str,
    tampered_value: object,
    tampered_digest: str | None,
) -> None:
    path = tmp_path / "routing-shadow.sqlite3"
    store = RoutingAuthorityV2ShadowStore(path)
    record = _subject()
    store.persist_shadow_record(record)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE routing_authority_v2_shadow_records SET {set_clause} WHERE record_digest = ?",
            (tampered_value, record.digest()),
        )
    attacked_digest = tampered_digest or record.digest()
    with pytest.raises(ShadowStoreIntegrityError):
        store.read_shadow_record(attacked_digest)
    with pytest.raises(ShadowStoreIntegrityError):
        store.list_shadow_record_digests()
    with pytest.raises(ShadowStoreIntegrityError):
        store.list_shadow_record_digests(record_type="SubjectBindingV2")
    with pytest.raises(ShadowStoreIntegrityError):
        store.persist_shadow_record(record)


def test_shadow_store_upgrades_legacy_phase_zero_schema_before_full_row_validation(tmp_path: Path) -> None:
    path = tmp_path / "routing-shadow.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE routing_authority_v2_shadow_records (
                record_digest TEXT PRIMARY KEY NOT NULL,
                record_type TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                canonical_bytes BLOB NOT NULL,
                execution_authority INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    store = RoutingAuthorityV2ShadowStore(path)
    record = _subject()
    assert store.persist_shadow_record(record) == record.digest()
    assert store.persist_shadow_record(record) == record.digest()
    assert store.read_shadow_record(record.digest()) == record
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            """
            SELECT authority_phase, authority_state, execution_authority
            FROM routing_authority_v2_shadow_records
            WHERE record_digest = ?
            """,
            (record.digest(),),
        ).fetchone()
    assert stored == (0, "NON_EXECUTABLE_SHADOW", 0)


def test_shadow_store_concurrent_idempotent_writers_keep_one_canonical_record(tmp_path: Path) -> None:
    path = tmp_path / "routing-shadow.sqlite3"
    store = RoutingAuthorityV2ShadowStore(path)
    record = _subject()
    with ThreadPoolExecutor(max_workers=8) as pool:
        digests = tuple(pool.map(lambda _index: store.persist_shadow_record(record), range(32)))
    assert digests == (record.digest(),) * 32
    assert store.list_shadow_record_digests() == (record.digest(),)
    assert store.read_shadow_record(record.digest()) == record


def test_shadow_store_public_surface_has_no_execution_or_network_claim_api(tmp_path: Path) -> None:
    store = RoutingAuthorityV2ShadowStore(tmp_path / "routing-shadow.sqlite3")
    forbidden = {
        "execute",
        "issue_live_permit",
        "claim_for_network",
        "invoke_provider",
        "authorize_execution",
    }
    assert forbidden.isdisjoint(name for name in dir(store) if not name.startswith("_"))


def test_mutant_custom_executable_authority_subclass_store_bypass_is_killed(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class ReviewerExecutableRecord(AuthorityRecord):
        RECORD_TYPE: ClassVar[str] = "ReviewerExecutableRecord"
        SCHEMA_VERSION: ClassVar[int] = 2
        HASH_DOMAIN: ClassVar[str] = "REVIEWER_EXECUTABLE_RECORD"
        serializer_calls: ClassVar[int] = 0

        authority_state: str = "EXECUTABLE"
        execution_authority: bool = True

        def canonical_bytes(self) -> bytes:
            type(self).serializer_calls += 1
            return super().canonical_bytes()

    store = RoutingAuthorityV2ShadowStore(tmp_path / "routing-shadow.sqlite3")
    record = ReviewerExecutableRecord()
    with pytest.raises(ContractValidationError, match="caller-defined"):
        store.persist_shadow_record(record)
    assert ReviewerExecutableRecord.serializer_calls == 0
    assert store.list_shadow_record_digests() == ()


def test_shadow_store_schema_and_reader_reject_unknown_executable_discriminator(tmp_path: Path) -> None:
    path = tmp_path / "routing-shadow.sqlite3"
    store = RoutingAuthorityV2ShadowStore(path)
    record = _subject()
    store.persist_shadow_record(record)
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            UPDATE routing_authority_v2_shadow_records
            SET record_type = 'ReviewerExecutableRecord', schema_version = 2
            WHERE record_digest = ?
            """,
            (record.digest(),),
        )
    assert store.read_shadow_record(record.digest()) == record
    with pytest.raises(ContractValidationError, match="unknown"):
        store.list_shadow_record_digests(record_type="ReviewerExecutableRecord")
