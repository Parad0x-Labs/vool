from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.discord_recent_observations import discord_message_ref, parse_discord_message_id
from network.signer import derive_local_secret
from relay.bridge_workers.discord_external_ingress import discord_conversation_ref

GATEWAY_VERSION = 10
GATEWAY_ENCODING = "json"
MAX_PENDING_STAGES = 512
MAX_AUTHORIZED_CHANNELS = 512
MAX_COMMAND_TEXT_BYTES = 8_192
MAX_STAGE_CIPHERTEXT_BYTES = 16_384
MAX_TERMINAL_STAGES = 1_024
MAX_SESSION_CIPHERTEXT_BYTES = 2_048

_SESSION_DOMAIN = "vool:discord-gateway-session:v1"
_SESSION_KEY_LABEL = "vool:discord-gateway-session:key:v1"
_STAGE_DOMAIN = "vool:discord-gateway-stage:v1"
_STAGE_KEY_LABEL = "vool:discord-gateway-stage:key:v1"
_WRAPPER_KEYS = frozenset({"schema_version", "nonce_b64", "ciphertext_b64"})
_GAP_STATUSES = frozenset(
    {"none", "non_resumable_session", "staging_failure", "invalid_state", "authority_changed"}
)
_STAGE_STATUSES = frozenset({"pending", "completed", "revoked", "invalid"})
_GATEWAY_OWNER_DOMAIN = b"vool:discord-gateway-owner:v1\x00"
_GATEWAY_OWNER_MARKER = object()
_GATEWAY_OWNERS_LOCK = threading.RLock()
_GATEWAY_OWNERS: dict[str, _DiscordGatewayOwnershipGuard] = {}


class DiscordGatewayStateError(RuntimeError):
    pass


class DiscordGatewayOwnerBusyError(DiscordGatewayStateError):
    pass


class _DiscordGatewayOwnershipGuard:
    """One M5B owner per state DB; process death releases the OS lock, M5D owns failover."""

    __slots__ = ("_active", "_file", "_key", "_marker", "_pid")

    def __init__(self, key: str, lock_file: Any, marker: object) -> None:
        if marker is not _GATEWAY_OWNER_MARKER:
            raise TypeError("Gateway ownership authority is invalid")
        self._key = key
        self._file = lock_file
        self._pid = os.getpid()
        self._marker = marker
        self._active = True

    def _authorizes(self, key: str) -> bool:
        if (
            self._marker is not _GATEWAY_OWNER_MARKER
            or not self._active
            or self._pid != os.getpid()
            or self._key != key
            or self._file is None
        ):
            return False
        with _GATEWAY_OWNERS_LOCK:
            return _GATEWAY_OWNERS.get(key) is self

    def release(self) -> bool:
        if self._pid != os.getpid() or not self._active:
            return False
        self._active = False
        lock_file = self._file
        self._file = None
        try:
            if lock_file is not None:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            if lock_file is not None:
                lock_file.close()
            with _GATEWAY_OWNERS_LOCK:
                if _GATEWAY_OWNERS.get(self._key) is self:
                    del _GATEWAY_OWNERS[self._key]
        return True

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("Gateway ownership authority cannot be serialized")


class DiscordGatewayCorruptStageError(DiscordGatewayStateError):
    def __init__(self, internal_row_id: int, internal_row_token: str) -> None:
        super().__init__("Gateway pending stage is invalid")
        self.internal_row_id = internal_row_id
        self.internal_row_token = internal_row_token


@dataclass(frozen=True)
class DiscordGatewayState:
    enablement_generation: int
    gateway_epoch: int
    account_ref: str | None
    durable_resume_seq: int | None
    session_valid: bool
    gap_status: str
    identify_not_before: float


@dataclass(frozen=True)
class DiscordGatewayResume:
    account_ref: str
    durable_resume_seq: int
    enablement_generation: int
    gateway_epoch: int
    session_id: str
    resume_gateway_url: str


@dataclass(frozen=True)
class DiscordGatewayStage:
    account_ref: str
    conversation_ref: str
    event_ref: str
    gateway_epoch: int
    gateway_sequence: int
    enablement_generation: int
    reply_source_ref: str
    raw_channel_id: str
    raw_message_id: str
    raw_author_id: str
    command_text: str
    internal_row_id: int = field(repr=False, compare=False)
    internal_row_token: str = field(repr=False, compare=False)


_AUTHORIZED_STAGE_MARKER = object()


@dataclass(frozen=True, slots=True)
class DiscordGatewayAuthorizedStage:
    account_ref: str
    conversation_ref: str
    event_ref: str
    gateway_epoch: int
    gateway_sequence: int
    enablement_generation: int
    reply_source_ref: str
    raw_channel_id: str
    raw_message_id: str
    raw_author_id: str
    command_text: str
    internal_row_id: int
    internal_row_token: str
    authority_gateway_epoch: int
    channel_authority_revision: int
    invocation_digest: str
    _marker: object = field(repr=False, compare=False)

    def is_authorized(self) -> bool:
        return (
            self._marker is _AUTHORIZED_STAGE_MARKER
            and self.invocation_digest == _authorized_stage_digest(self)
        )

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("Gateway execution authority cannot be serialized")


@dataclass(frozen=True)
class DiscordGatewayExecutionAuthorization:
    status: str
    permit: DiscordGatewayAuthorizedStage | None = None


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _safe_ref(value: Any, *, prefix: str, name: str) -> str:
    if type(value) is not str or len(value) != len(prefix) + 24 or not value.startswith(prefix):
        raise ValueError(f"{name} is invalid")
    if any(character not in "0123456789abcdef" for character in value[len(prefix) :]):
        raise ValueError(f"{name} is invalid")
    return value


def _safe_reply_source_ref(value: Any) -> str:
    return _safe_ref(value, prefix="discord-read-", name="reply source ref")


def _safe_sequence(value: Any, *, name: str = "gateway sequence") -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise ValueError(f"{name} is invalid")
    return value


def _safe_generation(value: Any) -> int:
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise ValueError("enablement generation is invalid")
    return value


def _safe_internal_row_id(value: Any) -> int:
    if type(value) is not int or value < 1 or value > 2**63 - 1:
        raise DiscordGatewayStateError("Gateway stage storage identity is invalid")
    return value


def _length_delimited(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _sqlite_value_bytes(value: Any) -> bytes:
    if value is None:
        return b"N"
    if type(value) is int:
        return b"I" + _length_delimited(str(value).encode("ascii"))
    if type(value) is float:
        return b"R" + struct.pack(">d", value)
    if type(value) is str:
        return b"T" + _length_delimited(value.encode(errors="surrogatepass"))
    if type(value) is bytes:
        return b"B" + _length_delimited(value)
    type_name = f"{type(value).__module__}.{type(value).__qualname__}".encode()
    return b"X" + _length_delimited(type_name)


def _digest_values(domain: bytes, values: tuple[tuple[str, Any], ...]) -> str:
    digest = hashlib.sha256(domain)
    for name, value in values:
        digest.update(_length_delimited(name.encode("ascii")))
        digest.update(_length_delimited(_sqlite_value_bytes(value)))
    return digest.hexdigest()


def _stage_row_token(row: sqlite3.Row) -> str:
    return _digest_values(
        b"vool:discord-gateway-stage-row:v2\x00",
        tuple(
            (name, row[name])
            for name in (
                "internal_row_id",
                "account_ref",
                "conversation_ref",
                "event_ref",
                "gateway_epoch",
                "gateway_sequence",
                "enablement_generation",
                "reply_source_ref",
                "sealed_payload",
                "status",
            )
        ),
    )


def _authorized_stage_digest(stage: DiscordGatewayAuthorizedStage) -> str:
    return _digest_values(
        b"vool:discord-gateway-authorized-stage:v1\x00",
        tuple(
            (name, getattr(stage, name))
            for name in (
                "internal_row_id",
                "internal_row_token",
                "account_ref",
                "conversation_ref",
                "event_ref",
                "gateway_epoch",
                "gateway_sequence",
                "enablement_generation",
                "reply_source_ref",
                "raw_channel_id",
                "raw_message_id",
                "raw_author_id",
                "command_text",
                "authority_gateway_epoch",
                "channel_authority_revision",
            )
        ),
    )


def _safe_gateway_url(value: Any) -> str:
    if type(value) is not str or not 1 <= len(value) <= 512:
        raise ValueError("Gateway URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != "gateway.discord.gg"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Gateway URL is invalid")
    return value


def _safe_session_id(value: Any) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256 or any(ord(ch) < 33 for ch in value):
        raise ValueError("Gateway session ID is invalid")
    return value


def _aad(
    domain: str,
    *,
    generation: int,
    epoch: int,
    account_ref: str,
    conversation_ref: str | None = None,
    event_ref: str | None = None,
    sequence: int | None = None,
    reply_source_ref: str | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "account_ref": _safe_ref(account_ref, prefix="discord-account-", name="account ref"),
        "domain": domain,
        "enablement_generation": _safe_generation(generation),
        "encoding": GATEWAY_ENCODING,
        "gateway_epoch": _safe_sequence(epoch, name="gateway epoch"),
        "gateway_version": GATEWAY_VERSION,
        "schema_version": 1,
    }
    if conversation_ref is not None:
        payload["conversation_ref"] = _safe_ref(
            conversation_ref, prefix="discord-conversation-", name="conversation ref"
        )
    if event_ref is not None:
        payload["event_ref"] = _safe_ref(event_ref, prefix="discord-msg-", name="event ref")
    if sequence is not None:
        payload["gateway_sequence"] = _safe_sequence(sequence)
    if reply_source_ref is not None:
        payload["reply_source_ref"] = _safe_reply_source_ref(reply_source_ref)
    return _canonical_json(payload)


def _seal(payload: dict[str, Any], *, key_label: str, aad: bytes, max_bytes: int) -> str:
    plaintext = _canonical_json(payload)
    if len(plaintext) > max_bytes:
        raise ValueError("Gateway encrypted payload is too large")
    nonce = os.urandom(12)
    ciphertext = AESGCM(derive_local_secret(key_label, length=32)).encrypt(nonce, plaintext, aad)
    return json.dumps(
        {
            "schema_version": 1,
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _open(value: Any, *, key_label: str, aad: bytes, max_bytes: int) -> dict[str, Any]:
    if type(value) is not str or len(value) > max_bytes * 2 + 512:
        raise DiscordGatewayStateError("Gateway encrypted state is invalid")
    try:
        wrapper = json.loads(value)
        if type(wrapper) is not dict or set(wrapper) != _WRAPPER_KEYS:
            raise ValueError
        if type(wrapper["schema_version"]) is not int or wrapper["schema_version"] != 1:
            raise ValueError
        nonce_b64 = wrapper["nonce_b64"]
        ciphertext_b64 = wrapper["ciphertext_b64"]
        if type(nonce_b64) is not str or type(ciphertext_b64) is not str:
            raise ValueError
        nonce = base64.b64decode(nonce_b64, validate=True)
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
        if len(nonce) != 12 or not 16 <= len(ciphertext) <= max_bytes + 16:
            raise ValueError
        plaintext = AESGCM(derive_local_secret(key_label, length=32)).decrypt(
            nonce, ciphertext, aad
        )
        if len(plaintext) > max_bytes:
            raise ValueError
        payload = json.loads(plaintext.decode("utf-8"))
        if type(payload) is not dict:
            raise ValueError
        return payload
    except (ValueError, binascii.Error, InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscordGatewayStateError("Gateway encrypted state is invalid") from exc


def _stage_from_row(row: sqlite3.Row, *, row_token: str) -> DiscordGatewayStage:
    row_id = _safe_internal_row_id(row["internal_row_id"])
    if row["status"] not in _STAGE_STATUSES:
        raise DiscordGatewayStateError("Gateway stage status is invalid")
    account = _safe_ref(row["account_ref"], prefix="discord-account-", name="account ref")
    conversation = _safe_ref(
        row["conversation_ref"], prefix="discord-conversation-", name="conversation ref"
    )
    event = _safe_ref(row["event_ref"], prefix="discord-msg-", name="event ref")
    generation = _safe_generation(row["enablement_generation"])
    epoch = _safe_sequence(row["gateway_epoch"], name="gateway epoch")
    sequence = _safe_sequence(row["gateway_sequence"])
    reply_source = _safe_reply_source_ref(row["reply_source_ref"])
    payload = _open(
        row["sealed_payload"],
        key_label=_STAGE_KEY_LABEL,
        aad=_aad(
            _STAGE_DOMAIN,
            generation=generation,
            epoch=epoch,
            account_ref=account,
            conversation_ref=conversation,
            event_ref=event,
            sequence=sequence,
            reply_source_ref=reply_source,
        ),
        max_bytes=MAX_STAGE_CIPHERTEXT_BYTES,
    )
    if set(payload) != {"command_text", "raw_author_id", "raw_channel_id", "raw_message_id"}:
        raise DiscordGatewayStateError("Gateway stage payload is invalid")
    raw_channel_id = payload["raw_channel_id"]
    raw_message_id = payload["raw_message_id"]
    raw_author_id = payload["raw_author_id"]
    command_text = payload["command_text"]
    if (
        type(raw_channel_id) is not str
        or parse_discord_message_id(raw_channel_id) is None
        or type(raw_message_id) is not str
        or parse_discord_message_id(raw_message_id) is None
        or type(raw_author_id) is not str
        or parse_discord_message_id(raw_author_id) is None
        or type(command_text) is not str
        or not command_text.strip()
        or len(command_text.encode("utf-8")) > MAX_COMMAND_TEXT_BYTES
    ):
        raise DiscordGatewayStateError("Gateway stage payload is invalid")
    if discord_conversation_ref(raw_channel_id) != conversation:
        raise DiscordGatewayStateError("Gateway stage conversation binding is invalid")
    if discord_message_ref(raw_channel_id, raw_message_id) != event:
        raise DiscordGatewayStateError("Gateway stage event binding is invalid")
    return DiscordGatewayStage(
        account,
        conversation,
        event,
        epoch,
        sequence,
        generation,
        reply_source,
        raw_channel_id,
        raw_message_id,
        raw_author_id,
        command_text,
        row_id,
        row_token,
    )


class DiscordGatewayStateStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            home = Path(os.environ.get("VOOL_HOME") or (Path.home() / ".vool_runtime"))
            db_path = home / "state" / "discord_gateway.sqlite3"
        self.db_path = Path(db_path)
        self._canonical_db_path = self.db_path.expanduser().resolve(strict=False)
        self._canonical_db_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path = os.path.normcase(str(self._canonical_db_path))
        self._ownership_key = hashlib.sha256(
            _GATEWAY_OWNER_DOMAIN + canonical_path.encode(errors="surrogatepass")
        ).hexdigest()
        # M5B supports normalized and symlink path aliases. Hard-link identity is not supported.
        self._ownership_lock_path = self._canonical_db_path.parent / (
            f".discord-gateway-owner-{self._ownership_key}.lock"
        )
        self._ensure_schema()

    def acquire_gateway_ownership(self) -> _DiscordGatewayOwnershipGuard:
        with _GATEWAY_OWNERS_LOCK:
            existing = _GATEWAY_OWNERS.get(self._ownership_key)
            if existing is not None:
                if existing._pid != os.getpid() or existing._authorizes(
                    self._ownership_key
                ):
                    raise DiscordGatewayOwnerBusyError("Discord Gateway owner is busy")
            path = self._ownership_lock_path
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError:
                lock_file = path.open("r+b")
            else:
                lock_file = os.fdopen(descriptor, "r+b")
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_file.close()
                raise DiscordGatewayOwnerBusyError("Discord Gateway owner is busy") from None
            guard = _DiscordGatewayOwnershipGuard(
                self._ownership_key, lock_file, _GATEWAY_OWNER_MARKER
            )
            _GATEWAY_OWNERS[self._ownership_key] = guard
            return guard

    def _require_gateway_owner(self, owner: Any) -> _DiscordGatewayOwnershipGuard:
        if type(owner) is not _DiscordGatewayOwnershipGuard or not owner._authorizes(
            self._ownership_key
        ):
            raise DiscordGatewayOwnerBusyError("Discord Gateway ownership is unavailable")
        return owner

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._canonical_db_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _ensure_schema(self) -> None:
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_gateway_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    schema_version INTEGER NOT NULL,
                    gateway_version INTEGER NOT NULL,
                    encoding TEXT NOT NULL,
                    enablement_generation INTEGER NOT NULL,
                    gateway_epoch INTEGER NOT NULL,
                    account_ref TEXT,
                    durable_resume_seq INTEGER,
                    sealed_session TEXT,
                    session_valid INTEGER NOT NULL,
                    gap_status TEXT NOT NULL,
                    identify_not_before REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO discord_gateway_state (
                    singleton, schema_version, gateway_version, encoding,
                    enablement_generation, gateway_epoch, account_ref,
                    durable_resume_seq, sealed_session, session_valid,
                    gap_status, identify_not_before, updated_at
                ) VALUES (1, 1, ?, ?, 0, 0, NULL, NULL, NULL, 0, 'none', 0, ?)
                """,
                (GATEWAY_VERSION, GATEWAY_ENCODING, time.time()),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_gateway_stages (
                    event_ref TEXT PRIMARY KEY,
                    account_ref TEXT NOT NULL,
                    conversation_ref TEXT NOT NULL,
                    gateway_epoch INTEGER NOT NULL,
                    gateway_sequence INTEGER NOT NULL,
                    enablement_generation INTEGER NOT NULL,
                    reply_source_ref TEXT NOT NULL,
                    sealed_payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_gateway_stage_order
                ON discord_gateway_stages(status, gateway_epoch, gateway_sequence)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_gateway_channel_authority (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    schema_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO discord_gateway_channel_authority (
                    singleton, schema_version, revision, updated_at
                ) VALUES (1, 1, 0, ?)
                """,
                (time.time(),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_gateway_authorized_channels (
                    conversation_ref TEXT PRIMARY KEY,
                    reply_source_ref TEXT NOT NULL
                )
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> DiscordGatewayState:
        if (
            row["schema_version"] != 1
            or row["gateway_version"] != GATEWAY_VERSION
            or row["encoding"] != GATEWAY_ENCODING
        ):
            raise DiscordGatewayStateError("Gateway state protocol is invalid")
        generation = _safe_generation(row["enablement_generation"])
        epoch = _safe_sequence(row["gateway_epoch"], name="gateway epoch")
        account_ref = row["account_ref"]
        if account_ref is not None:
            account_ref = _safe_ref(account_ref, prefix="discord-account-", name="account ref")
        sequence = row["durable_resume_seq"]
        if sequence is not None:
            sequence = _safe_sequence(sequence)
        gap_status = row["gap_status"]
        if gap_status not in _GAP_STATUSES:
            raise DiscordGatewayStateError("Gateway state is invalid")
        not_before = float(row["identify_not_before"])
        if not math.isfinite(not_before) or not_before < 0:
            raise DiscordGatewayStateError("Gateway state is invalid")
        return DiscordGatewayState(
            generation,
            epoch,
            account_ref,
            sequence,
            row["session_valid"] == 1,
            gap_status,
            not_before,
        )

    def read_state(self) -> DiscordGatewayState:
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT * FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
        finally:
            connection.close()
        if row is None or row["schema_version"] != 1:
            raise DiscordGatewayStateError("Gateway state is missing")
        if row["gateway_version"] != GATEWAY_VERSION or row["encoding"] != GATEWAY_ENCODING:
            raise DiscordGatewayStateError("Gateway protocol state is incompatible")
        return self._state_from_row(row)

    def reconcile_authority(self, generation: Any, *, enabled: bool) -> DiscordGatewayState:
        selected_generation = _safe_generation(generation)
        if type(enabled) is not bool:
            raise ValueError("enabled state is invalid")
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise DiscordGatewayStateError("Gateway state is missing")
            changed = row["enablement_generation"] != selected_generation
            if changed or not enabled:
                connection.execute(
                    """
                    UPDATE discord_gateway_state
                    SET enablement_generation = ?, account_ref = NULL,
                        durable_resume_seq = NULL, sealed_session = NULL,
                        session_valid = 0, gap_status = 'authority_changed', updated_at = ?
                    WHERE singleton = 1
                    """,
                    (selected_generation, time.time()),
                )
                connection.execute(
                    """
                    UPDATE discord_gateway_stages
                    SET status = 'revoked', updated_at = ?
                    WHERE status = 'pending' AND enablement_generation != ?
                    """,
                    (time.time(), selected_generation),
                )
                if not enabled:
                    connection.execute(
                        """
                        UPDATE discord_gateway_stages
                        SET status = 'revoked', updated_at = ?
                        WHERE status = 'pending'
                        """,
                        (time.time(),),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.read_state()

    def record_ready(
        self,
        *,
        generation: Any,
        sequence: Any,
        account_ref: Any,
        session_id: Any,
        resume_gateway_url: Any,
    ) -> DiscordGatewayState:
        selected_generation = _safe_generation(generation)
        selected_sequence = _safe_sequence(sequence)
        account = _safe_ref(account_ref, prefix="discord-account-", name="account ref")
        selected_session = _safe_session_id(session_id)
        selected_url = _safe_gateway_url(resume_gateway_url)
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT enablement_generation, gateway_epoch FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
            if row is None or row["enablement_generation"] != selected_generation:
                raise DiscordGatewayStateError("Gateway authority changed before READY")
            epoch = _safe_sequence(row["gateway_epoch"], name="gateway epoch") + 1
            sealed = _seal(
                {"resume_gateway_url": selected_url, "session_id": selected_session},
                key_label=_SESSION_KEY_LABEL,
                aad=_aad(
                    _SESSION_DOMAIN,
                    generation=selected_generation,
                    epoch=epoch,
                    account_ref=account,
                ),
                max_bytes=MAX_SESSION_CIPHERTEXT_BYTES,
            )
            connection.execute(
                """
                UPDATE discord_gateway_state
                SET gateway_epoch = ?, account_ref = ?, durable_resume_seq = ?,
                    sealed_session = ?, session_valid = 1, gap_status = 'none', updated_at = ?
                WHERE singleton = 1
                """,
                (epoch, account, selected_sequence, sealed, time.time()),
            )
            connection.execute(
                """
                UPDATE discord_gateway_stages
                SET status = 'revoked', updated_at = ?
                WHERE status = 'pending' AND account_ref != ?
                """,
                (time.time(), account),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.read_state()

    def resume_state(self, generation: Any) -> DiscordGatewayResume | None:
        selected_generation = _safe_generation(generation)
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT * FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
        finally:
            connection.close()
        if (
            row is None
            or row["session_valid"] != 1
            or row["enablement_generation"] != selected_generation
            or row["account_ref"] is None
            or row["durable_resume_seq"] is None
            or row["sealed_session"] is None
        ):
            return None
        state = self._state_from_row(row)
        account = state.account_ref
        if account is None or state.durable_resume_seq is None:
            return None
        payload = _open(
            row["sealed_session"],
            key_label=_SESSION_KEY_LABEL,
            aad=_aad(
                _SESSION_DOMAIN,
                generation=selected_generation,
                epoch=state.gateway_epoch,
                account_ref=account,
            ),
            max_bytes=MAX_SESSION_CIPHERTEXT_BYTES,
        )
        if set(payload) != {"resume_gateway_url", "session_id"}:
            raise DiscordGatewayStateError("Gateway Resume state is invalid")
        return DiscordGatewayResume(
            account,
            state.durable_resume_seq,
            selected_generation,
            state.gateway_epoch,
            _safe_session_id(payload["session_id"]),
            _safe_gateway_url(payload["resume_gateway_url"]),
        )

    def advance_ignored(self, *, generation: Any, sequence: Any) -> DiscordGatewayState:
        selected_generation = _safe_generation(generation)
        selected_sequence = _safe_sequence(sequence)
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT enablement_generation, durable_resume_seq FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
            if row is None or row["enablement_generation"] != selected_generation:
                raise DiscordGatewayStateError("Gateway authority changed")
            current = row["durable_resume_seq"]
            if current is None or selected_sequence > current:
                connection.execute(
                    "UPDATE discord_gateway_state SET durable_resume_seq = ?, updated_at = ? WHERE singleton = 1",
                    (selected_sequence, time.time()),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.read_state()

    def stage_command(
        self,
        *,
        generation: Any,
        sequence: Any,
        account_ref: Any,
        conversation_ref: Any,
        event_ref: Any,
        reply_source_ref: Any,
        raw_channel_id: Any,
        raw_message_id: Any,
        raw_author_id: Any,
        command_text: Any,
    ) -> bool:
        selected_generation = _safe_generation(generation)
        selected_sequence = _safe_sequence(sequence)
        account = _safe_ref(account_ref, prefix="discord-account-", name="account ref")
        conversation = _safe_ref(
            conversation_ref, prefix="discord-conversation-", name="conversation ref"
        )
        event = _safe_ref(event_ref, prefix="discord-msg-", name="event ref")
        reply_source = _safe_reply_source_ref(reply_source_ref)
        if type(raw_channel_id) is not str or parse_discord_message_id(raw_channel_id) is None:
            raise ValueError("raw channel ID is invalid")
        if type(raw_message_id) is not str or parse_discord_message_id(raw_message_id) is None:
            raise ValueError("raw message ID is invalid")
        if type(raw_author_id) is not str or parse_discord_message_id(raw_author_id) is None:
            raise ValueError("raw author ID is invalid")
        if discord_message_ref(raw_channel_id, raw_message_id) != event:
            raise ValueError("event ref does not match staged message")
        if type(command_text) is not str or not command_text.strip():
            raise ValueError("command text is invalid")
        if len(command_text.encode("utf-8")) > MAX_COMMAND_TEXT_BYTES:
            raise ValueError("command text is too large")
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state_row = connection.execute(
                "SELECT * FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
            if state_row is None or state_row["enablement_generation"] != selected_generation:
                raise DiscordGatewayStateError("Gateway authority changed")
            state = self._state_from_row(state_row)
            if not state.session_valid or state.account_ref != account:
                raise DiscordGatewayStateError("Gateway session is not authorized")
            existing = connection.execute(
                "SELECT * FROM discord_gateway_stages WHERE event_ref = ?", (event,)
            ).fetchone()
            inserted = existing is None
            if existing is not None:
                expected = (
                    account,
                    conversation,
                    state.gateway_epoch,
                    selected_sequence,
                    selected_generation,
                    reply_source,
                )
                actual = (
                    existing["account_ref"],
                    existing["conversation_ref"],
                    existing["gateway_epoch"],
                    existing["gateway_sequence"],
                    existing["enablement_generation"],
                    existing["reply_source_ref"],
                )
                if actual != expected:
                    raise DiscordGatewayStateError("Gateway stage identity conflict")
            else:
                pending_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM discord_gateway_stages WHERE status = 'pending'"
                ).fetchone()["count"]
                if pending_count >= MAX_PENDING_STAGES:
                    raise DiscordGatewayStateError("Gateway command stage is full")
                sealed = _seal(
                    {
                        "command_text": command_text,
                        "raw_author_id": raw_author_id,
                        "raw_channel_id": raw_channel_id,
                        "raw_message_id": raw_message_id,
                    },
                    key_label=_STAGE_KEY_LABEL,
                    aad=_aad(
                        _STAGE_DOMAIN,
                        generation=selected_generation,
                        epoch=state.gateway_epoch,
                        account_ref=account,
                        conversation_ref=conversation,
                        event_ref=event,
                        sequence=selected_sequence,
                        reply_source_ref=reply_source,
                    ),
                    max_bytes=MAX_STAGE_CIPHERTEXT_BYTES,
                )
                now = time.time()
                connection.execute(
                    """
                    INSERT INTO discord_gateway_stages (
                        event_ref, account_ref, conversation_ref, gateway_epoch,
                        gateway_sequence, enablement_generation, reply_source_ref,
                        sealed_payload, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        event,
                        account,
                        conversation,
                        state.gateway_epoch,
                        selected_sequence,
                        selected_generation,
                        reply_source,
                        sealed,
                        now,
                        now,
                    ),
                )
            current = state.durable_resume_seq
            if current is None or selected_sequence > current:
                connection.execute(
                    "UPDATE discord_gateway_state SET durable_resume_seq = ?, updated_at = ? WHERE singleton = 1",
                    (selected_sequence, time.time()),
                )
            connection.commit()
            return inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def authorize_stage_execution(
        self,
        stage: DiscordGatewayStage,
        *,
        enabled: bool,
        generation: Any,
        owner: _DiscordGatewayOwnershipGuard,
    ) -> DiscordGatewayExecutionAuthorization:
        self._require_gateway_owner(owner)
        if type(stage) is not DiscordGatewayStage or type(enabled) is not bool:
            raise ValueError("Gateway stage execution request is invalid")
        selected_generation = _safe_generation(generation)
        row_id = _safe_internal_row_id(stage.internal_row_id)
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            revision, channel_authority = self._read_channel_authority(connection)
            row = connection.execute(
                "SELECT rowid AS internal_row_id, * FROM discord_gateway_stages WHERE rowid = ?",
                (row_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                connection.commit()
                return DiscordGatewayExecutionAuthorization("unavailable")
            if _stage_row_token(row) != stage.internal_row_token:
                connection.commit()
                return DiscordGatewayExecutionAuthorization("unavailable")
            try:
                stored_stage = _stage_from_row(row, row_token=stage.internal_row_token)
            except (DiscordGatewayStateError, TypeError, ValueError):
                connection.execute(
                    """
                    UPDATE discord_gateway_stages SET status = 'invalid', updated_at = ?
                    WHERE rowid = ? AND status = 'pending'
                    """,
                    (time.time(), row_id),
                )
                connection.commit()
                return DiscordGatewayExecutionAuthorization("invalid")
            state_row = connection.execute(
                "SELECT * FROM discord_gateway_state WHERE singleton = 1"
            ).fetchone()
            if state_row is None:
                raise DiscordGatewayStateError("Gateway state is missing")
            state = self._state_from_row(state_row)

            should_revoke = (
                not enabled
                or selected_generation != stored_stage.enablement_generation
                or state.enablement_generation != selected_generation
                or channel_authority.get(stored_stage.conversation_ref)
                != stored_stage.reply_source_ref
            )
            if not should_revoke and (not state.session_valid or state.account_ref is None):
                connection.commit()
                return DiscordGatewayExecutionAuthorization("deferred")
            should_revoke = should_revoke or state.account_ref != stored_stage.account_ref
            should_revoke = should_revoke or stored_stage.gateway_epoch > state.gateway_epoch
            if should_revoke:
                cursor = connection.execute(
                    """
                    UPDATE discord_gateway_stages SET status = 'revoked', updated_at = ?
                    WHERE rowid = ? AND status = 'pending'
                    """,
                    (time.time(), row_id),
                )
                connection.commit()
                return DiscordGatewayExecutionAuthorization(
                    "revoked" if cursor.rowcount else "unavailable"
                )
            if stored_stage != stage:
                connection.commit()
                return DiscordGatewayExecutionAuthorization("unavailable")

            authorized = DiscordGatewayAuthorizedStage(
                stored_stage.account_ref,
                stored_stage.conversation_ref,
                stored_stage.event_ref,
                stored_stage.gateway_epoch,
                stored_stage.gateway_sequence,
                stored_stage.enablement_generation,
                stored_stage.reply_source_ref,
                stored_stage.raw_channel_id,
                stored_stage.raw_message_id,
                stored_stage.raw_author_id,
                stored_stage.command_text,
                row_id,
                stored_stage.internal_row_token,
                state.gateway_epoch,
                revision,
                "",
                _AUTHORIZED_STAGE_MARKER,
            )
            object.__setattr__(authorized, "invocation_digest", _authorized_stage_digest(authorized))
            connection.commit()
            return DiscordGatewayExecutionAuthorization("authorized", authorized)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def pending_stage(self) -> DiscordGatewayStage | None:
        connection = self._connection()
        try:
            row = connection.execute(
                """
                SELECT rowid AS internal_row_id, * FROM discord_gateway_stages
                WHERE status = 'pending'
                ORDER BY gateway_epoch ASC, gateway_sequence ASC, event_ref ASC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        row_token = _stage_row_token(row)
        try:
            return _stage_from_row(row, row_token=row_token)
        except (DiscordGatewayStateError, TypeError, ValueError):
            raise DiscordGatewayCorruptStageError(
                _safe_internal_row_id(row["internal_row_id"]), row_token
            ) from None

    def quarantine_corrupt_pending_stage(
        self, selected: DiscordGatewayCorruptStageError
    ) -> bool:
        if type(selected) is not DiscordGatewayCorruptStageError:
            raise ValueError("Gateway corrupt-stage selection is invalid")
        row_id = _safe_internal_row_id(selected.internal_row_id)
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT rowid AS internal_row_id, * FROM discord_gateway_stages WHERE rowid = ?",
                (row_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "pending"
                or _stage_row_token(row) != selected.internal_row_token
            ):
                connection.commit()
                return False
            cursor = connection.execute(
                """
                UPDATE discord_gateway_stages SET status = 'invalid', updated_at = ?
                WHERE rowid = ? AND status = 'pending'
                """,
                (time.time(), row_id),
            )
            connection.commit()
            return bool(cursor.rowcount)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_channel_authority(value: Any) -> dict[str, str]:
        if type(value) is not dict or len(value) > MAX_AUTHORIZED_CHANNELS:
            raise ValueError("Gateway channel authority is invalid")
        selected: dict[str, str] = {}
        for conversation_ref, reply_source_ref in value.items():
            conversation = _safe_ref(
                conversation_ref,
                prefix="discord-conversation-",
                name="conversation ref",
            )
            reply_source = _safe_reply_source_ref(reply_source_ref)
            selected[conversation] = reply_source
        return selected

    def _read_channel_authority(
        self, connection: sqlite3.Connection
    ) -> tuple[int, dict[str, str]]:
        state = connection.execute(
            "SELECT schema_version, revision FROM discord_gateway_channel_authority WHERE singleton = 1"
        ).fetchone()
        if state is None or state["schema_version"] != 1:
            raise DiscordGatewayStateError("Gateway channel authority is invalid")
        revision = _safe_generation(state["revision"])
        current = self._validate_channel_authority(
            dict(
                connection.execute(
                    "SELECT conversation_ref, reply_source_ref FROM discord_gateway_authorized_channels"
                )
            )
        )
        return revision, current

    def _synchronize_channel_authority(
        self,
        connection: sqlite3.Connection,
        selected: dict[str, str],
    ) -> tuple[int, dict[str, str]]:
        revision, current = self._read_channel_authority(connection)
        if current != selected:
            if revision == 2**63 - 1:
                raise DiscordGatewayStateError("Gateway channel authority revision is exhausted")
            connection.execute("DELETE FROM discord_gateway_authorized_channels")
            connection.executemany(
                """
                INSERT INTO discord_gateway_authorized_channels (
                    conversation_ref, reply_source_ref
                ) VALUES (?, ?)
                """,
                sorted(selected.items()),
            )
            revision += 1
            connection.execute(
                """
                UPDATE discord_gateway_channel_authority
                SET revision = ?, updated_at = ? WHERE singleton = 1
                """,
                (revision, time.time()),
            )
        return revision, selected

    def synchronize_channel_authority(
        self,
        authority: Any,
        *,
        owner: _DiscordGatewayOwnershipGuard,
    ) -> int:
        self._require_gateway_owner(owner)
        selected = self._validate_channel_authority(authority)
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            revision, _selected = self._synchronize_channel_authority(connection, selected)
            connection.commit()
            return revision
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def terminalize_stage(self, event_ref: Any, *, status: str) -> bool:
        event = _safe_ref(event_ref, prefix="discord-msg-", name="event ref")
        if status not in {"completed", "revoked"}:
            raise ValueError("terminal stage status is invalid")
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE discord_gateway_stages SET status = ?, updated_at = ?
                WHERE event_ref = ? AND status = 'pending'
                """,
                (status, time.time(), event),
            )
            terminal_count = connection.execute(
                "SELECT COUNT(*) AS count FROM discord_gateway_stages WHERE status != 'pending'"
            ).fetchone()["count"]
            if terminal_count > MAX_TERMINAL_STAGES:
                connection.execute(
                    """
                    DELETE FROM discord_gateway_stages WHERE event_ref IN (
                        SELECT event_ref FROM discord_gateway_stages
                        WHERE status != 'pending'
                        ORDER BY updated_at ASC LIMIT ?
                    )
                    """,
                    (terminal_count - MAX_TERMINAL_STAGES,),
                )
            connection.commit()
            return bool(cursor.rowcount)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def invalidate_session(self, *, gap_status: str) -> DiscordGatewayState:
        if gap_status not in _GAP_STATUSES or gap_status == "none":
            raise ValueError("Gateway gap status is invalid")
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE discord_gateway_state
                SET sealed_session = NULL, session_valid = 0, gap_status = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (gap_status, time.time()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.read_state()

    def identify_ready(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        if not math.isfinite(current):
            return False
        return current >= self.read_state().identify_not_before

    def set_identify_not_before(self, not_before: Any) -> DiscordGatewayState:
        value = float(not_before)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Identify not-before is invalid")
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE discord_gateway_state SET identify_not_before = ?, updated_at = ? WHERE singleton = 1",
                (value, time.time()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.read_state()


__all__ = [
    "GATEWAY_ENCODING",
    "GATEWAY_VERSION",
    "MAX_AUTHORIZED_CHANNELS",
    "MAX_COMMAND_TEXT_BYTES",
    "MAX_PENDING_STAGES",
    "DiscordGatewayAuthorizedStage",
    "DiscordGatewayCorruptStageError",
    "DiscordGatewayExecutionAuthorization",
    "DiscordGatewayOwnerBusyError",
    "DiscordGatewayResume",
    "DiscordGatewayStage",
    "DiscordGatewayState",
    "DiscordGatewayStateError",
    "DiscordGatewayStateStore",
]
