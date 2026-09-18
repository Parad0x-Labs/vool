from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from core.discovery_index import (
    delivery_endpoints_for_peer,
    delivery_targets_for_peer,
    note_verified_peer_endpoint_delivery_result,
    record_signed_peer_endpoint_observation,
    record_verified_peer_endpoint_proof,
    register_peer_endpoint,
    register_peer_endpoint_candidate,
    verified_endpoints_for_peer,
)
from network.protocol import Protocol, encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _clear_endpoint_tables() -> None:
    conn = get_connection()
    try:
        for table in ("peer_endpoints", "peer_endpoint_observations", "peer_endpoint_candidates"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _signed_envelope(*, msg_type: str = "PING") -> dict[str, object]:
    raw = encode_message(
        msg_id="msg-" + msg_type.lower(),
        msg_type=msg_type,
        sender_peer_id=local_peer_id(),
        nonce="nonce-" + msg_type.lower(),
        payload={},
    )
    return Protocol.decode_and_validate(raw)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _set_verified_delivery_success(peer_id: str, host: str, port: int, *, timestamp: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE peer_endpoints
            SET last_delivery_success_at = ?,
                last_delivery_attempt_at = ?,
                last_delivery_failure_at = '',
                consecutive_delivery_failures = 0,
                updated_at = ?
            WHERE peer_id = ? AND host = ? AND port = ?
            """,
            (timestamp, timestamp, timestamp, peer_id, host, int(port)),
        )
        conn.commit()
    finally:
        conn.close()


def test_run_migrations_rebuilds_legacy_peer_endpoints_into_multi_endpoint_rows(tmp_path) -> None:
    db_path = tmp_path / "mesh-endpoints.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE peer_endpoints (
                peer_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'direct',
                last_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL DEFAULT '',
                verification_kind TEXT NOT NULL DEFAULT '',
                proof_count INTEGER NOT NULL DEFAULT 0,
                proof_message_id TEXT NOT NULL DEFAULT '',
                proof_message_type TEXT NOT NULL DEFAULT '',
                proof_hash TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE peer_endpoint_observations (
                peer_id TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'observed',
                verification_kind TEXT NOT NULL DEFAULT 'protocol_signature',
                proof_message_id TEXT NOT NULL DEFAULT '',
                proof_message_type TEXT NOT NULL DEFAULT '',
                proof_hash TEXT NOT NULL DEFAULT '',
                proof_signature TEXT NOT NULL DEFAULT '',
                proof_timestamp TEXT NOT NULL DEFAULT '',
                first_verified_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                proof_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, host, port, source)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO peer_endpoints (
                peer_id, host, port, source, last_seen_at, last_verified_at,
                verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-1",
                "198.51.100.10",
                49110,
                "bootstrap",
                "2026-03-26T08:00:00+00:00",
                "",
                "",
                0,
                "",
                "",
                "",
                "2026-03-26T08:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO peer_endpoint_observations (
                peer_id, host, port, source, verification_kind,
                proof_message_id, proof_message_type, proof_hash, proof_signature, proof_timestamp,
                first_verified_at, last_verified_at, proof_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-1",
                "198.51.100.11",
                49111,
                "observed",
                "protocol_signature",
                "msg-1",
                "PING",
                "hash-1",
                "sig-1",
                "2026-03-26T09:00:00+00:00",
                "2026-03-26T09:00:00+00:00",
                "2026-03-26T09:00:00+00:00",
                1,
                "2026-03-26T09:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(db_path=db_path)
    migrated = get_connection(db_path)
    try:
        pk_columns = [
            row["name"]
            for row in migrated.execute("PRAGMA table_info(peer_endpoints)").fetchall()
            if int(row["pk"] or 0)
        ]
        rows = migrated.execute(
            "SELECT peer_id, host, port, source, proof_timestamp FROM peer_endpoints WHERE peer_id = ? ORDER BY host ASC",
            ("peer-1",),
        ).fetchall()
    finally:
        migrated.close()

    assert pk_columns == ["peer_id", "host", "port"]
    assert [(row["host"], row["port"], row["source"], row["proof_timestamp"]) for row in rows] == [
        ("198.51.100.10", 49110, "bootstrap", "2026-03-26T08:00:00+00:00"),
        ("198.51.100.11", 49111, "observed", "2026-03-26T09:00:00+00:00"),
    ]


def test_run_migrations_handles_very_legacy_peer_endpoints_without_verification_columns(tmp_path) -> None:
    db_path = tmp_path / "mesh-endpoints-very-legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE peer_endpoints (
                peer_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'direct',
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO peer_endpoints (
                peer_id, host, port, source, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-legacy",
                "203.0.113.10",
                49112,
                "bootstrap",
                "2026-03-26T08:00:00+00:00",
                "2026-03-26T08:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(db_path=db_path)
    migrated = get_connection(db_path)
    try:
        pk_columns = [
            row["name"]
            for row in migrated.execute("PRAGMA table_info(peer_endpoints)").fetchall()
            if int(row["pk"] or 0)
        ]
        row = migrated.execute(
            """
            SELECT peer_id, host, port, source, last_verified_at, proof_timestamp,
                   last_delivery_attempt_at, consecutive_delivery_failures
            FROM peer_endpoints
            WHERE peer_id = ?
            LIMIT 1
            """,
            ("peer-legacy",),
        ).fetchone()
    finally:
        migrated.close()

    assert pk_columns == ["peer_id", "host", "port"]
    assert row["host"] == "203.0.113.10"
    assert row["port"] == 49112
    assert row["source"] == "bootstrap"
    assert row["last_verified_at"] == ""
    assert row["proof_timestamp"] == "2026-03-26T08:00:00+00:00"
    assert row["last_delivery_attempt_at"] == ""
    assert row["consecutive_delivery_failures"] == 0


def test_run_migrations_backfills_missing_peer_endpoint_proof_timestamp_column(tmp_path) -> None:
    db_path = tmp_path / "mesh-endpoints-backfill.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE peer_endpoints (
                peer_id TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'observed',
                last_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL DEFAULT '',
                verification_kind TEXT NOT NULL DEFAULT '',
                proof_count INTEGER NOT NULL DEFAULT 0,
                proof_message_id TEXT NOT NULL DEFAULT '',
                proof_message_type TEXT NOT NULL DEFAULT '',
                proof_hash TEXT NOT NULL DEFAULT '',
                last_delivery_attempt_at TEXT NOT NULL DEFAULT '',
                last_delivery_success_at TEXT NOT NULL DEFAULT '',
                last_delivery_failure_at TEXT NOT NULL DEFAULT '',
                consecutive_delivery_failures INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, host, port)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE peer_endpoint_observations (
                peer_id TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'observed',
                verification_kind TEXT NOT NULL DEFAULT 'protocol_signature',
                proof_message_id TEXT NOT NULL DEFAULT '',
                proof_message_type TEXT NOT NULL DEFAULT '',
                proof_hash TEXT NOT NULL DEFAULT '',
                proof_signature TEXT NOT NULL DEFAULT '',
                proof_timestamp TEXT NOT NULL DEFAULT '',
                first_verified_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                proof_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, host, port, source)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO peer_endpoints (
                peer_id, host, port, source, last_seen_at, last_verified_at,
                verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash,
                last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
                consecutive_delivery_failures, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-2",
                "198.51.100.12",
                49112,
                "observed",
                "2026-03-26T10:00:00+00:00",
                "2026-03-26T10:00:00+00:00",
                "protocol_signature",
                1,
                "msg-2",
                "PING",
                "hash-2",
                "",
                "",
                "",
                0,
                "2026-03-26T10:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO peer_endpoint_observations (
                peer_id, host, port, source, verification_kind,
                proof_message_id, proof_message_type, proof_hash, proof_signature, proof_timestamp,
                first_verified_at, last_verified_at, proof_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-2",
                "198.51.100.12",
                49112,
                "observed",
                "protocol_signature",
                "msg-2",
                "PING",
                "hash-2",
                "sig-2",
                "2026-03-26T10:05:00+00:00",
                "2026-03-26T10:05:00+00:00",
                "2026-03-26T10:05:00+00:00",
                1,
                "2026-03-26T10:05:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(db_path=db_path)
    migrated = get_connection(db_path)
    try:
        row = migrated.execute(
            """
            SELECT proof_timestamp
            FROM peer_endpoints
            WHERE peer_id = ? AND host = ? AND port = ?
            LIMIT 1
            """,
            ("peer-2", "198.51.100.12", 49112),
        ).fetchone()
    finally:
        migrated.close()

    assert row is not None
    assert row["proof_timestamp"] == "2026-03-26T10:05:00+00:00"


def test_verified_endpoints_keep_multiple_rows_per_peer_and_sort_by_strength() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-multi-verified"
    register_peer_endpoint(peer_id, "198.51.100.20", 49120, source="bootstrap")
    register_peer_endpoint(peer_id, "198.51.100.21", 49121, source="self")

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)

    assert [(item.host, item.port, item.source) for item in endpoints] == [
        ("198.51.100.21", 49121, "self"),
        ("198.51.100.20", 49120, "bootstrap"),
    ]


def test_signed_observation_upgrades_existing_endpoint_without_duplicate_rows() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = local_peer_id()
    register_peer_endpoint(peer_id, "198.51.100.30", 49130, source="bootstrap")

    record_signed_peer_endpoint_observation(
        peer_id,
        "198.51.100.30",
        49130,
        envelope=_signed_envelope(msg_type="HEARTBEAT"),
    )

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)
    assert len(endpoints) == 1
    assert (endpoints[0].host, endpoints[0].port, endpoints[0].source) == ("198.51.100.30", 49130, "observed")
    assert endpoints[0].verification_kind == "protocol_signature"
    assert endpoints[0].proof_count == 1
    assert endpoints[0].proof_timestamp


def test_delivery_endpoints_prefer_verified_rows_and_append_candidates() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-delivery"
    register_peer_endpoint(peer_id, "198.51.100.40", 49140, source="bootstrap")
    register_peer_endpoint(peer_id, "198.51.100.41", 49141, source="observed")
    register_peer_endpoint_candidate(peer_id, "198.51.100.42", 49142, source="dht")

    endpoints = delivery_endpoints_for_peer(peer_id, verified_limit=4, include_candidates=True, candidate_limit=2)

    assert endpoints == [
        ("198.51.100.41", 49141),
        ("198.51.100.40", 49140),
        ("198.51.100.42", 49142),
    ]


def test_delivery_targets_promote_recent_delivery_success_over_registry_only_order() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-delivery-liveness"
    register_peer_endpoint(peer_id, "198.51.100.50", 49150, source="observed")
    register_peer_endpoint(peer_id, "198.51.100.51", 49151, source="bootstrap")
    note_verified_peer_endpoint_delivery_result(peer_id, "198.51.100.51", 49151, delivered=True)

    selected = verified_endpoints_for_peer(peer_id, limit=2)
    targets = delivery_targets_for_peer(peer_id, verified_limit=2, include_candidates=False)

    assert [(item.host, item.port) for item in selected] == [
        ("198.51.100.51", 49151),
        ("198.51.100.50", 49150),
    ]
    assert [(item.host, item.port) for item in targets] == [
        ("198.51.100.51", 49151),
        ("198.51.100.50", 49150),
    ]


def test_fresh_observed_transport_proof_outranks_stale_signed_api_declaration() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-fresh-observed"
    now = datetime.now(timezone.utc)
    stale = _iso(now - timedelta(hours=30))
    fresh = _iso(now - timedelta(minutes=30))

    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.60",
        49160,
        source="api",
        verification_kind="signed_api_write",
        proof_message_id="api-stale",
        proof_timestamp=stale,
    )
    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.61",
        49161,
        source="observed",
        verification_kind="protocol_signature",
        proof_message_id="observed-fresh",
        proof_timestamp=fresh,
    )

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)

    assert [(item.host, item.port) for item in endpoints[:2]] == [
        ("198.51.100.61", 49161),
        ("198.51.100.60", 49160),
    ]
    assert endpoints[0].source == "observed"
    assert endpoints[0].verification_kind == "protocol_signature"


def test_stale_delivery_success_does_not_outrank_fresh_observed_transport_proof() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-stale-success"
    now = datetime.now(timezone.utc)
    stale_success = _iso(now - timedelta(hours=8))
    fresh = _iso(now - timedelta(minutes=20))

    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.70",
        49170,
        source="observed",
        verification_kind="protocol_signature",
        proof_message_id="observed-old",
        proof_timestamp=_iso(now - timedelta(hours=9)),
    )
    _set_verified_delivery_success(peer_id, "198.51.100.70", 49170, timestamp=stale_success)
    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.71",
        49171,
        source="observed",
        verification_kind="protocol_signature",
        proof_message_id="observed-fresh",
        proof_timestamp=fresh,
    )

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)
    targets = delivery_targets_for_peer(peer_id, verified_limit=2, include_candidates=False)

    assert [(item.host, item.port) for item in endpoints[:2]] == [
        ("198.51.100.71", 49171),
        ("198.51.100.70", 49170),
    ]
    assert [(item.host, item.port) for item in targets] == [
        ("198.51.100.71", 49171),
        ("198.51.100.70", 49170),
    ]


def test_stale_observed_proof_does_not_override_fresher_api_proof_on_same_endpoint() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-proof-precedence"
    now = datetime.now(timezone.utc)
    fresh = _iso(now - timedelta(minutes=10))
    stale = _iso(now - timedelta(hours=3))

    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.80",
        49180,
        source="api",
        verification_kind="signed_api_write",
        proof_message_id="api-fresh",
        proof_timestamp=fresh,
    )
    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.80",
        49180,
        source="observed",
        verification_kind="protocol_signature",
        proof_message_id="observed-stale",
        proof_timestamp=stale,
    )

    endpoints = verified_endpoints_for_peer(peer_id, limit=2)

    assert len(endpoints) == 1
    assert endpoints[0].source == "api"
    assert endpoints[0].verification_kind == "signed_api_write"
    assert endpoints[0].proof_message_id == "api-fresh"
    assert endpoints[0].proof_timestamp == fresh


def test_fresh_observed_transport_proof_replaces_stale_api_label_on_same_endpoint() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-proof-upgrade"
    now = datetime.now(timezone.utc)
    stale = _iso(now - timedelta(hours=6))
    fresh = _iso(now - timedelta(minutes=5))

    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.81",
        49181,
        source="api",
        verification_kind="signed_api_write",
        proof_message_id="api-stale",
        proof_timestamp=stale,
    )
    record_verified_peer_endpoint_proof(
        peer_id,
        "198.51.100.81",
        49181,
        source="observed",
        verification_kind="protocol_signature",
        proof_message_id="observed-fresh",
        proof_timestamp=fresh,
    )

    endpoints = verified_endpoints_for_peer(peer_id, limit=2)

    assert len(endpoints) == 1
    assert endpoints[0].source == "observed"
    assert endpoints[0].verification_kind == "protocol_signature"
    assert endpoints[0].proof_message_id == "observed-fresh"
    assert endpoints[0].proof_timestamp == fresh
