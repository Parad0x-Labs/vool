"""Payload-free provenance shared by the existing owners of imported resources."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any


def digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def keyed_hasher() -> Callable[[Any], str]:
    """Stable local identities without retaining a public plaintext-confirmation hash."""
    from storage.db import active_default_db_path, get_connection

    database = Path(active_default_db_path())
    path = database.with_name(database.name + ".chat-import-key")
    marker = path.with_name(path.name + ".version")
    if not path.exists() and marker.exists():
        raise RuntimeError("import identity authority missing")
    if not path.exists() or not marker.exists():
        connection = get_connection()
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not path.exists() and any(connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
                                         for table in ("chat_import_origins", "chat_import_resources") if table in tables):
                raise RuntimeError("import identity authority missing")
            # The database is the source-identity owner. Its write lock also
            # serializes first initialization across processes and profile views.
            connection.execute("BEGIN IMMEDIATE")
            if not path.exists():
                temporary = path.with_name(path.name + "." + secrets.token_hex(8))
                try:
                    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(secrets.token_bytes(32))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            key = path.read_bytes()
            if len(key) != 32:
                raise RuntimeError("import identity authority unreadable")
            if not marker.exists():
                descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write((hashlib.sha256(key).hexdigest() + "\n").encode("ascii"))
                    handle.flush()
                    os.fsync(handle.fileno())
            connection.commit()
        finally:
            connection.close()
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("import identity authority unreadable")
    fingerprint = hashlib.sha256(key).hexdigest()
    if marker.read_text(encoding="ascii").strip() != fingerprint:
        raise RuntimeError("import identity authority changed")
    return lambda payload: hmac.new(key, json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                  separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def keyed_digest(payload: Any) -> str:
    return keyed_hasher()(payload)


def origin(batch_id: str, session_id: str, source: str, payload: Any) -> dict:
    if not batch_id or not session_id or not source:
        raise ValueError("import ownership is required")
    return {"version": 1, "batch_id": batch_id, "session_id": session_id,
            "source": source, "content_hash": keyed_digest(payload)}


def belongs(value: Any, batch_id: str, session_id: str) -> bool:
    return (isinstance(value, dict) and value.get("version") == 1
            and value.get("batch_id") == batch_id and value.get("session_id") == session_id)


def unchanged(payload: Any, provenance: dict) -> bool:
    return bool(provenance.get("content_hash")) and keyed_digest(payload) == provenance["content_hash"]


def visible(payload: Any, provenance: Any) -> bool:
    if provenance is None:
        return True
    if not isinstance(provenance, dict) or provenance.get("version") != 1 or not provenance.get("session_id"):
        return False
    from core.memory.revocation import authorize_payload

    try:
        return authorize_payload(payload, str(provenance["session_id"]))
    except RuntimeError:
        return False
