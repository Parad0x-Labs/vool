"""SQLite persistence for the wallet, on the runtime-continuity database, created lazily.

Same pattern as the fault plane: ``CREATE TABLE IF NOT EXISTS`` on every access, no store
version bump (purely additive). The only column that ever holds key material is
``wallet_profiles.sealed_blob``, and it holds ciphertext.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

LOCK = threading.RLock()

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS wallet_profiles (
        wallet_id TEXT PRIMARY KEY, mode TEXT NOT NULL, network TEXT NOT NULL, public_key TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, sealed_blob TEXT NOT NULL DEFAULT '',
        pin_hash TEXT NOT NULL DEFAULT '', pin_salt TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS wallet_proposals (
        proposal_id TEXT PRIMARY KEY, wallet_id TEXT NOT NULL, network TEXT NOT NULL, asset TEXT NOT NULL,
        amount_minor INTEGER NOT NULL, destination TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL,
        idempotency_key TEXT NOT NULL DEFAULT '', content_digest TEXT NOT NULL, state TEXT NOT NULL,
        simulation_json TEXT NOT NULL DEFAULT '{}', tx_signature TEXT NOT NULL DEFAULT '', fault_code TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_proposals_idem ON wallet_proposals(wallet_id, idempotency_key) WHERE idempotency_key <> ''",
    "CREATE INDEX IF NOT EXISTS idx_wallet_proposals_state ON wallet_proposals(state, created_at)",
    """CREATE TABLE IF NOT EXISTS wallet_proposal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, state TEXT NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wallet_spend_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, wallet_id TEXT NOT NULL, asset TEXT NOT NULL, amount_minor INTEGER NOT NULL,
        destination TEXT NOT NULL, proposal_id TEXT NOT NULL UNIQUE, spent_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'settled')""",
    "CREATE INDEX IF NOT EXISTS idx_wallet_spend_ledger_window ON wallet_spend_ledger(wallet_id, asset, spent_at)",
    """CREATE TABLE IF NOT EXISTS wallet_signing_requests (
        request_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, wallet_id TEXT NOT NULL, public_key TEXT NOT NULL, network TEXT NOT NULL,
        message_b64 TEXT NOT NULL, unsigned_tx_b64 TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
        consumed_at REAL NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS wallet_controls (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wallet_x402_bindings (
        binding_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL UNIQUE, url TEXT NOT NULL, method TEXT NOT NULL, pay_to TEXT NOT NULL,
        amount_minor INTEGER NOT NULL, asset TEXT NOT NULL, network TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT '',
        tx_signature TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, resource_status INTEGER NOT NULL DEFAULT 0,
        resource_digest TEXT NOT NULL DEFAULT '', resource_bytes INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wallet_limits (
        wallet_id TEXT NOT NULL, asset TEXT NOT NULL, per_tx_minor INTEGER NOT NULL, daily_minor INTEGER NOT NULL,
        per_destination_daily_minor INTEGER NOT NULL, PRIMARY KEY (wallet_id, asset))""",
    """CREATE TABLE IF NOT EXISTS wallet_card_tokens (
        token_id TEXT PRIMARY KEY, provider TEXT NOT NULL, token_ref TEXT NOT NULL, last4 TEXT NOT NULL,
        brand TEXT NOT NULL DEFAULT '', exp_month INTEGER NOT NULL, exp_year INTEGER NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wallet_facilitators (
        facilitator_id TEXT NOT NULL, origin TEXT NOT NULL, network TEXT NOT NULL, scheme TEXT NOT NULL,
        x402_version INTEGER NOT NULL DEFAULT 2, verified INTEGER NOT NULL DEFAULT 0, verified_at TEXT NOT NULL DEFAULT '',
        evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, PRIMARY KEY (facilitator_id, network, scheme))""",
    """CREATE TABLE IF NOT EXISTS wallet_receipts (
        receipt_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, wallet_id TEXT NOT NULL, network TEXT NOT NULL,
        asset TEXT NOT NULL, amount_minor INTEGER NOT NULL, destination TEXT NOT NULL, origin TEXT NOT NULL,
        state TEXT NOT NULL, tx_signature TEXT NOT NULL DEFAULT '', fault_code TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE IF NOT EXISTS wallet_unlock_attempts (
        scope TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS wallet_quotes (
        quote_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, wallet_id TEXT NOT NULL, network TEXT NOT NULL, environment TEXT NOT NULL,
        digest TEXT NOT NULL, fields_json TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL)""",
    # one pilot transfer per proposal: the claim, the signed bytes, the dispatch and what the chain showed
    """CREATE TABLE IF NOT EXISTS wallet_transfers (
        proposal_id TEXT PRIMARY KEY, wallet_id TEXT NOT NULL, network TEXT NOT NULL, environment TEXT NOT NULL, family TEXT NOT NULL,
        from_address TEXT NOT NULL, to_address TEXT NOT NULL, asset TEXT NOT NULL, amount_minor INTEGER NOT NULL, fee_max_minor INTEGER NOT NULL,
        quote_id TEXT NOT NULL UNIQUE, quote_digest TEXT NOT NULL, challenge_digest TEXT NOT NULL,
        nonce INTEGER, blockhash TEXT, blockhash_slot INTEGER, last_valid_block_height INTEGER,
        tx_id TEXT, raw_b64 TEXT,
        owner_token TEXT NOT NULL, lease_until REAL NOT NULL, dispatch_deadline REAL NOT NULL,
        epoch_freeze INTEGER NOT NULL, epoch_enabled INTEGER NOT NULL, epoch_environment INTEGER NOT NULL, enabled_generation INTEGER NOT NULL,
        dispatch_claimed_at REAL, attempts_sent INTEGER NOT NULL DEFAULT 0, cancel_requested_at REAL,
        state TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]', charged_fee_minor INTEGER, balance_after_minor INTEGER,
        block_ref INTEGER, block_hash TEXT, finality_seen TEXT, evidence_kind TEXT, offered_exit_json TEXT NOT NULL DEFAULT '{}',
        crossing_block INTEGER, crossing_block_hash TEXT, stopped_waiting_at REAL, observe_until REAL, a6_outcome TEXT,
        effect_instance_id TEXT, a6_resolved_at TEXT, last_receipt_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS wallet_usepod_operations (
        operation_key TEXT PRIMARY KEY, provider TEXT NOT NULL, correlation_id TEXT NOT NULL, authority TEXT NOT NULL,
        network TEXT NOT NULL, asset TEXT NOT NULL, pay_to TEXT NOT NULL, amount_minor INTEGER NOT NULL, expires_at REAL NOT NULL,
        resource TEXT NOT NULL DEFAULT '', requirement_digest TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT '', wallet_id TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL, mint_token TEXT NOT NULL DEFAULT '', mint_lease_until REAL NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_usepod_operations_proposal ON wallet_usepod_operations(proposal_id) WHERE proposal_id <> ''",
    "CREATE INDEX IF NOT EXISTS idx_wallet_usepod_operations_correlation ON wallet_usepod_operations(correlation_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transfers_tx ON wallet_transfers(network, tx_id) "
    "WHERE tx_id IS NOT NULL AND state NOT IN ('released', 'discarded', 'cancelled')",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_transfers_nonce ON wallet_transfers(network, from_address, nonce) "
    "WHERE family = 'evm' AND state IN ('claimed', 'signed', 'signed_revoked', 'dispatching', 'unknown', 'pending', 'confirmed', 'failed_on_chain')",
    "CREATE INDEX IF NOT EXISTS idx_wallet_transfers_state ON wallet_transfers(state, updated_at)",
)

TABLES: tuple[str, ...] = (
    "wallet_profiles", "wallet_proposals", "wallet_proposal_events", "wallet_spend_ledger", "wallet_limits",
    "wallet_card_tokens", "wallet_receipts", "wallet_signing_requests", "wallet_x402_bindings", "wallet_controls",
    "wallet_facilitators", "wallet_unlock_attempts", "wallet_quotes", "wallet_transfers", "wallet_usepod_operations",
)

#: Columns added after a table first shipped. Additive, idempotent, checked on every connection.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("wallet_spend_ledger", "state", "TEXT NOT NULL DEFAULT 'settled'"),
    ("wallet_signing_requests", "family", "TEXT NOT NULL DEFAULT 'svm'"),
    ("wallet_signing_requests", "typed_data_b64", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_spend_ledger", "fee_minor", "INTEGER NOT NULL DEFAULT 0"),
    ("wallet_profiles", "device_sealed_blob", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_profiles", "seal_kind", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_profiles", "approval_method", "TEXT NOT NULL DEFAULT 'pin'"),
    ("wallet_spend_ledger", "chain", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "version", "INTEGER NOT NULL DEFAULT 1"),
    ("wallet_x402_bindings", "offer_json", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "resource_origin", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_transfers", "fee_state", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_transfers", "fee_known_minor", "INTEGER"),
    ("wallet_transfers", "fee_missing_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("wallet_usepod_operations", "digest_version", "INTEGER NOT NULL DEFAULT 1"),
    ("wallet_transfers", "fee_fork", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "resource_method", "TEXT NOT NULL DEFAULT 'GET'"),
    ("wallet_x402_bindings", "facilitator_id", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "eip712_name", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "eip712_version", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "asset_transfer_method", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "asset_address", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "nonce", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "deadline", "INTEGER NOT NULL DEFAULT 0"),
    ("wallet_x402_bindings", "expires_at", "INTEGER NOT NULL DEFAULT 0"),
    ("wallet_x402_bindings", "max_facilitator_fee_minor", "INTEGER NOT NULL DEFAULT 0"),
    ("wallet_x402_bindings", "max_network_fee_minor", "INTEGER NOT NULL DEFAULT 0"),
    ("wallet_x402_bindings", "sponsored_gas", "INTEGER NOT NULL DEFAULT 0"),
    ("wallet_x402_bindings", "fee_asset", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_x402_bindings", "max_timeout_seconds", "INTEGER NOT NULL DEFAULT 60"),
    ("wallet_profiles", "seal_policy", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_profiles", "setup_state", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_profiles", "creation_key", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_profiles", "revealed_at", "TEXT NOT NULL DEFAULT ''"),
    ("wallet_profiles", "setup_updated_at", "TEXT NOT NULL DEFAULT ''"),
    # the credential generation: bumped by every recovery or credential change; a signing session opened under an
    # older generation is revoked before the transmit site, and a stale recovery handle cannot commit
    ("wallet_profiles", "credential_generation", "INTEGER NOT NULL DEFAULT 1"),
    ("wallet_spend_ledger", "settled_at", "REAL NOT NULL DEFAULT 0"),
    # PA Contacts: the recipient snapshot a proposal was resolved to ('' = none named)
    ("wallet_proposals", "recipient_json", "TEXT NOT NULL DEFAULT ''"),
)

#: Statements that depend on added columns: they run after ``_ensure_columns`` on every connection, so an upgraded
#: database gains them in the same pass that gains the columns.
POST_MIGRATION: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_profiles_creation_key ON wallet_profiles(network, creation_key) WHERE creation_key <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_quotes_open ON wallet_quotes(proposal_id) WHERE state = 'open'",
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def loads(raw: Any, fallback: Any) -> Any:
    try:
        loaded = json.loads(str(raw or ""))
    except Exception:
        return fallback
    return loaded if isinstance(loaded, type(fallback)) else fallback


def _raw_conn() -> sqlite3.Connection:
    from core.runtime_continuity import _conn as runtime_conn

    return runtime_conn()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """A migrated connection with the wallet schema present. Commits on clean exit."""
    with LOCK:
        conn = _raw_conn()
        try:
            for statement in SCHEMA:
                conn.execute(statement)
            _ensure_columns(conn)
            for statement in POST_MIGRATION:
                conn.execute(statement)
            yield conn
            conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        finally:
            conn.close()
