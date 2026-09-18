from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.db import active_default_db_path, get_connection

EXTERNAL_IDENTITY_KEY = "_external_ingress_identity"
_CONNECTORS = frozenset({"discord", "telegram"})
_HEX_24_RE = re.compile(r"^[0-9a-f]{24}$")
_HOST_AUTHORITY = object()
_SCHEMA_LOCK = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_LOCK = threading.Lock()


def _require_exact_string(name: str, value: Any) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact built-in str")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty without surrounding whitespace")
    return value


def _require_exact_nonempty_string(name: str, value: Any) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact built-in str")
    if not value:
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_connector(value: Any) -> str:
    connector = _require_exact_string("connector", value)
    if connector not in _CONNECTORS:
        raise ValueError("connector must be discord or telegram")
    return connector


def _require_ref(name: str, value: Any, prefix: str) -> str:
    ref = _require_exact_string(name, value)
    suffix = ref.removeprefix(prefix)
    if not ref.startswith(prefix) or not _HEX_24_RE.fullmatch(suffix):
        raise ValueError(f"{name} must be an opaque {prefix}<24 lowercase hex> reference")
    return ref


@dataclass(frozen=True)
class ExternalIngressIdentity:
    connector: str
    account_ref: str
    conversation_ref: str
    principal_ref: str
    persona_ref: str
    event_ref: str

    def __post_init__(self) -> None:
        connector = _require_connector(self.connector)
        _require_ref("account_ref", self.account_ref, f"{connector}-account-")
        _require_ref("conversation_ref", self.conversation_ref, f"{connector}-conversation-")
        _require_ref("principal_ref", self.principal_ref, f"{connector}-principal-")
        _require_ref("persona_ref", self.persona_ref, "persona-")
        _require_ref("event_ref", self.event_ref, f"{connector}-msg-")

    def public_metadata(self) -> dict[str, str]:
        return asdict(self)


def issue_external_ingress_identity(
    *,
    connector: Any,
    account_ref: Any,
    conversation_ref: Any,
    principal_ref: Any,
    persona_ref: Any,
    event_ref: Any,
) -> ExternalIngressIdentity:
    identity = ExternalIngressIdentity(
        connector=connector,
        account_ref=account_ref,
        conversation_ref=conversation_ref,
        principal_ref=principal_ref,
        persona_ref=persona_ref,
        event_ref=event_ref,
    )
    object.__setattr__(identity, "_host_authority", _HOST_AUTHORITY)
    return identity


def trusted_external_identity(value: Any) -> ExternalIngressIdentity | None:
    if type(value) is not ExternalIngressIdentity:
        return None
    return value if getattr(value, "_host_authority", None) is _HOST_AUTHORITY else None


def external_identity_from_context(source_context: Any) -> ExternalIngressIdentity | None:
    if type(source_context) is not dict:
        return None
    return trusted_external_identity(source_context.get(EXTERNAL_IDENTITY_KEY))


def external_session_id(identity: ExternalIngressIdentity) -> str:
    trusted = trusted_external_identity(identity)
    if trusted is None:
        raise PermissionError("external identity is not host-issued")
    canonical = "\0".join(
        (
            trusted.connector,
            trusted.account_ref,
            trusted.conversation_ref,
            trusted.principal_ref,
            trusted.persona_ref,
        )
    )
    return "external:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def external_persona_ref(persona_id: Any) -> str:
    clean_persona_id = _require_exact_nonempty_string("persona_id", persona_id)
    return "persona-" + hashlib.sha256(clean_persona_id.encode("utf-8")).hexdigest()[:24]


def build_external_source_context(identity: ExternalIngressIdentity) -> dict[str, Any]:
    trusted = trusted_external_identity(identity)
    if trusted is None:
        raise PermissionError("external identity is not host-issued")
    session_id = external_session_id(trusted)
    return {
        EXTERNAL_IDENTITY_KEY: trusted,
        "_owner_local": False,
        "surface": "external",
        "platform": trusted.connector,
        "connector": trusted.connector,
        "account_ref": trusted.account_ref,
        "conversation_ref": trusted.conversation_ref,
        "principal_ref": trusted.principal_ref,
        "persona_ref": trusted.persona_ref,
        "event_ref": trusted.event_ref,
        "runtime_session_id": session_id,
        "session_id": session_id,
        "operating_mode": "manual",
    }


def durable_external_source_context(source_context: Any) -> dict[str, Any] | None:
    identity = external_identity_from_context(source_context)
    if identity is None:
        return None
    return {
        "surface": "external",
        "connector": identity.connector,
        "account_ref": identity.account_ref,
        "conversation_ref": identity.conversation_ref,
        "principal_ref": identity.principal_ref,
        "persona_ref": identity.persona_ref,
        "event_ref": identity.event_ref,
    }


def external_approval_origin(source_context: Any) -> dict[str, str]:
    identity = external_identity_from_context(source_context)
    if identity is None:
        return {}
    return {
        "origin_kind": "external",
        "origin_connector": identity.connector,
        "origin_account_ref": identity.account_ref,
        "origin_conversation_ref": identity.conversation_ref,
        "origin_principal_ref": identity.principal_ref,
    }


def _validated_event_ref(value: Any) -> str:
    event_ref = _require_exact_string("event_ref", value)
    for connector in _CONNECTORS:
        prefix = f"{connector}-msg-"
        if event_ref.startswith(prefix) and _HEX_24_RE.fullmatch(event_ref[len(prefix) :]):
            return event_ref
    raise ValueError("event_ref must be an opaque connector message reference")


class ExternalIngressEventConflictError(RuntimeError):
    """An event reference was previously associated with another session."""


class ExternalSessionLease(AbstractContextManager["ExternalSessionLease"]):
    def __init__(
        self,
        store: ExternalIngressStateStore,
        identity: ExternalIngressIdentity,
        *,
        wait_timeout_seconds: float,
    ) -> None:
        self._store = store
        self._identity = identity
        self._wait_timeout_seconds = max(0.0, min(float(wait_timeout_seconds), 30.0))
        self._file: Any = None
        self._process_lock: threading.Lock | None = None
        self._token = secrets.token_urlsafe(24)

    @property
    def token(self) -> str:
        return self._token

    def __enter__(self) -> ExternalSessionLease:
        path = self._store.session_lock_path(self._identity)
        with _PROCESS_LOCKS_LOCK:
            process_lock = _PROCESS_LOCKS.setdefault(str(path), threading.Lock())
        deadline = time.monotonic() + self._wait_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if process_lock.acquire(timeout=max(0.0, min(remaining, self._store.poll_interval_seconds))):
                self._process_lock = process_lock
                break
            if remaining <= 0:
                raise TimeoutError("external session is busy")
        try:
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
            while True:
                if self._try_lock(lock_file):
                    self._file = lock_file
                    self._store._register_lease(self)
                    return self
                if time.monotonic() >= deadline:
                    lock_file.close()
                    raise TimeoutError("external session is busy")
                time.sleep(self._store.poll_interval_seconds)
        except Exception:
            self._release_process_lock()
            raise

    @staticmethod
    def _try_lock(lock_file: Any) -> bool:
        try:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self) -> bool:
        lock_file = self._file
        if lock_file is None:
            return False
        self._file = None
        try:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
            self._store._unregister_lease(self)
            self._release_process_lock()
        return True

    def _release_process_lock(self) -> None:
        if self._process_lock is not None:
            self._process_lock.release()
            self._process_lock = None

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


class ExternalIngressStateStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        terminal_retention: int = 1000,
        poll_interval_seconds: float = 0.02,
        session_wait_timeout_seconds: float = 1.0,
        lock_dir: str | Path | None = None,
    ) -> None:
        self.db_path = str(db_path) if db_path is not None else active_default_db_path()
        self.terminal_retention = max(1, min(int(terminal_retention), 100_000))
        self.poll_interval_seconds = max(0.005, min(float(poll_interval_seconds), 1.0))
        self.session_wait_timeout_seconds = max(0.0, min(float(session_wait_timeout_seconds), 30.0))
        self.lock_dir = Path(lock_dir) if lock_dir is not None else Path(self.db_path).parent / "external_ingress_locks"
        self._owned_leases: dict[str, ExternalSessionLease] = {}
        self._owned_leases_lock = threading.Lock()
        self._ensure_schema()

    def _connection(self):
        return get_connection(self.db_path)

    def _ensure_schema(self) -> None:
        with _SCHEMA_LOCK:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS external_ingress_events (
                        event_ref TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        connector TEXT NOT NULL,
                        status TEXT NOT NULL,
                        claim_token TEXT,
                        claimed_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        terminal_at REAL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_external_ingress_events_terminal
                    ON external_ingress_events(status, terminal_at)
                    """
                )
                columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(external_ingress_events)")}
                if "claim_token" not in columns:
                    conn.execute("ALTER TABLE external_ingress_events ADD COLUMN claim_token TEXT")
                conn.commit()
            finally:
                conn.close()

    def claim_event(self, identity: ExternalIngressIdentity, *, now: float | None = None) -> str | None:
        trusted = trusted_external_identity(identity)
        if trusted is None:
            raise PermissionError("external identity is not host-issued")
        if not self._owns_session_lock(trusted):
            raise RuntimeError("external event claims require the owning session lock")
        timestamp = time.time() if now is None else float(now)
        session_id = external_session_id(trusted)
        claim_token = secrets.token_urlsafe(24)
        conn = self._connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT session_id, status FROM external_ingress_events WHERE event_ref = ?",
                (trusted.event_ref,),
            ).fetchone()
            if row is not None and str(row["session_id"]) != session_id:
                conn.rollback()
                raise ExternalIngressEventConflictError("external event is bound to another session")
            if row is not None and str(row["status"]) == "completed":
                conn.commit()
                return None
            if row is None:
                conn.execute(
                    """
                    INSERT INTO external_ingress_events (
                        event_ref, session_id, connector, status, claim_token, claimed_at, updated_at, terminal_at
                    ) VALUES (?, ?, ?, 'active', ?, ?, ?, NULL)
                    """,
                    (trusted.event_ref, session_id, trusted.connector, claim_token, timestamp, timestamp),
                )
            else:
                conn.execute(
                    """
                    UPDATE external_ingress_events
                    SET status = 'active', claim_token = ?, claimed_at = ?, updated_at = ?, terminal_at = NULL
                    WHERE event_ref = ?
                    """,
                    (claim_token, timestamp, timestamp, trusted.event_ref),
                )
            conn.commit()
            return claim_token
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_event(
        self,
        identity: ExternalIngressIdentity,
        claim_token: Any,
        *,
        status: str = "completed",
        now: float | None = None,
    ) -> bool:
        trusted = trusted_external_identity(identity)
        if trusted is None:
            raise PermissionError("external identity is not host-issued")
        if not self._owns_session_lock(trusted):
            return False
        if type(claim_token) is not str or not claim_token:
            return False
        if type(status) is not str or status not in {"completed", "failed"}:
            raise ValueError("terminal status must be completed or failed")
        timestamp = time.time() if now is None else float(now)
        conn = self._connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE external_ingress_events
                SET status = ?, updated_at = ?, terminal_at = CASE WHEN ? = 'completed' THEN ? ELSE NULL END
                WHERE event_ref = ? AND session_id = ? AND status = 'active' AND claim_token = ?
                """,
                (
                    status,
                    timestamp,
                    status,
                    timestamp,
                    trusted.event_ref,
                    external_session_id(trusted),
                    claim_token,
                ),
            )
            conn.execute(
                """
                DELETE FROM external_ingress_events
                WHERE event_ref IN (
                    SELECT event_ref FROM external_ingress_events
                    WHERE status = 'completed'
                    ORDER BY terminal_at DESC, event_ref DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.terminal_retention,),
            )
            conn.commit()
            return bool(cursor.rowcount)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail_event(self, identity: ExternalIngressIdentity, claim_token: Any, *, now: float | None = None) -> bool:
        return self.complete_event(identity, claim_token, status="failed", now=now)

    def event_status(self, event_ref: Any) -> str | None:
        clean_ref = _validated_event_ref(event_ref)
        conn = self._connection()
        try:
            row = conn.execute(
                "SELECT status FROM external_ingress_events WHERE event_ref = ? LIMIT 1",
                (clean_ref,),
            ).fetchone()
            return str(row["status"]) if row else None
        finally:
            conn.close()

    def session_lock_path(self, identity: ExternalIngressIdentity) -> Path:
        trusted = trusted_external_identity(identity)
        if trusted is None:
            raise PermissionError("external identity is not host-issued")
        digest = hashlib.sha256(external_session_id(trusted).encode("utf-8")).hexdigest()
        return self.lock_dir / digest

    def _register_lease(self, lease: ExternalSessionLease) -> None:
        with self._owned_leases_lock:
            self._owned_leases[lease.token] = lease

    def _unregister_lease(self, lease: ExternalSessionLease) -> None:
        with self._owned_leases_lock:
            if self._owned_leases.get(lease.token) is lease:
                self._owned_leases.pop(lease.token, None)

    def _owns_session_lock(self, identity: ExternalIngressIdentity) -> bool:
        session_id = external_session_id(identity)
        with self._owned_leases_lock:
            return any(
                lease._file is not None and external_session_id(lease._identity) == session_id
                for lease in self._owned_leases.values()
            )

    def claim_session(self, identity: ExternalIngressIdentity, *, wait_timeout_seconds: float | None = None) -> str | None:
        lease = self.session_lease(identity, wait_timeout_seconds=wait_timeout_seconds)
        try:
            lease.__enter__()
        except TimeoutError:
            return None
        return lease.token

    def release_session(self, identity: ExternalIngressIdentity, token: Any) -> bool:
        if type(token) is not str:
            return False
        with self._owned_leases_lock:
            lease = self._owned_leases.get(token)
        if lease is None or external_session_id(identity) != external_session_id(lease._identity):
            return False
        return lease.release()

    def session_lease(
        self,
        identity: ExternalIngressIdentity,
        *,
        wait_timeout_seconds: float | None = None,
    ) -> ExternalSessionLease:
        if trusted_external_identity(identity) is None:
            raise PermissionError("external identity is not host-issued")
        return ExternalSessionLease(
            self,
            identity,
            wait_timeout_seconds=self.session_wait_timeout_seconds if wait_timeout_seconds is None else wait_timeout_seconds,
        )


__all__ = [
    "EXTERNAL_IDENTITY_KEY",
    "ExternalIngressEventConflictError",
    "ExternalIngressIdentity",
    "ExternalIngressStateStore",
    "ExternalSessionLease",
    "build_external_source_context",
    "durable_external_source_context",
    "external_approval_origin",
    "external_identity_from_context",
    "external_persona_ref",
    "external_session_id",
    "issue_external_ingress_identity",
    "trusted_external_identity",
]
