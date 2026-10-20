"""Encrypted household records. No memory, model admission, network or retained plaintext.

SQLite contains only random record ids and authenticated ciphertext references. Each record's
metadata and attached bytes are sealed together through the existing encrypted blob API. Updates
commit a new reference before collecting old ciphertext; interrupted writes are collected on the
next mutation. Downloads are returned in memory; the runtime keeps no export or packet cache.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from core.runtime_paths import active_data_dir
from storage.blackbox.cas import EncryptedBlobStore

FIELDS = ("name", "maker", "model", "serial", "bought_on", "bought_from", "price", "warranty_end", "notes", "specs", "order_items")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 24 * 1024 * 1024
MAX_FILES = 12


class ThingsError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _private(path: Path, mode: int) -> None:
    if path.is_symlink():
        raise ThingsError("unsafe_storage", "Your things could not be opened safely.", 503)
    os.chmod(path, mode)


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ThingsError("bad_id", "Choose a saved thing.")
    return value


def clean_fields(fields: dict) -> dict:
    if not isinstance(fields, dict) or set(fields) - set(FIELDS):
        raise ThingsError("bad_fields", "Check the details and try again.")
    clean = {}
    for field in FIELDS:
        value = fields.get(field, "")
        if not isinstance(value, str) or len(value) > (6000 if field in {"notes", "specs", "order_items"} else 250):
            raise ThingsError("bad_fields", "One of the details is too long or could not be read.")
        clean[field] = value.strip()
    if not clean["name"]:
        raise ThingsError("name_required", "Give this thing a name.")
    for field in ("bought_on", "warranty_end"):
        if clean[field]:
            try:
                date.fromisoformat(clean[field])
            except ValueError:
                raise ThingsError("bad_date", "Use a full date, or leave it empty if you do not know it.") from None
    return clean


def file_payload(name: str, data: bytes, kind: str) -> dict:
    if kind not in {"receipt", "manual", "photo", "note"}:
        raise ThingsError("bad_kind", "Choose a receipt, manual, photo or note.")
    if not isinstance(name, str) or not name.strip() or len(name) > 180 or any(c in name for c in "/\\\x00"):
        raise ThingsError("bad_filename", "Choose a file with a simple name.")
    if not data or len(data) > MAX_FILE_BYTES:
        raise ThingsError("file_size", "Choose a file smaller than 8 MB that is not empty.")
    return {"id": uuid.uuid4().hex, "name": name, "kind": kind, "data": base64.b64encode(data).decode("ascii"), "size": len(data)}


class ThingsStore:
    def __init__(self):
        self.root = active_data_dir() / "my_things"

    @contextmanager
    def _tx(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private(self.root, 0o700)
        path = self.root / "records.sqlite3"
        if path.is_symlink():
            raise ThingsError("unsafe_storage", "Your things could not be opened safely.", 503)
        conn = sqlite3.connect(path, timeout=10)
        _private(path, 0o600)
        try:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, ref TEXT NOT NULL, revision INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS preferences (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL)")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _cas(self, thing_id: str):
        from core.blackbox.coverage.cas_keys import resolve_keyring

        keys = resolve_keyring()
        if keys is None:
            raise ThingsError("locked", "Your private storage is unavailable. Nothing was saved.", 503)
        return EncryptedBlobStore(self.root / "objects" / _id(thing_id), keys)

    def _enabled(self, conn):
        row = conn.execute("SELECT enabled FROM preferences WHERE id=1").fetchone()
        return row is None or bool(row[0])

    def preferences(self) -> dict:
        if not (self.root / "records.sqlite3").exists():
            return {"enabled": True}
        with self._tx() as conn:
            return {"enabled": self._enabled(conn)}

    def set_enabled(self, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise ThingsError("bad_setting", "Choose on or off.")
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO preferences VALUES (1,?)", (int(enabled),))
        return {"enabled": enabled}

    def _require_enabled(self, conn):
        if not self._enabled(conn):
            raise ThingsError("disabled", "My things is turned off. Turn it on in Settings to continue.", 409)

    def _load(self, conn, thing_id: str) -> dict:
        row = conn.execute("SELECT ref,revision FROM records WHERE id=?", (_id(thing_id),)).fetchone()
        if row is None:
            raise ThingsError("missing", "This thing is no longer saved.", 404)
        record = json.loads(self._cas(thing_id).get(row[0]))
        return {**record, "id": thing_id, "revision": row[1]}

    def _public(self, record):
        return {**record, "files": [{k: v for k, v in file.items() if k != "data"} for file in record["files"]]}

    def list(self) -> list[dict]:
        if not (self.root / "records.sqlite3").exists():
            return []
        with self._tx() as conn:
            self._require_enabled(conn)
            return [self._public(self._load(conn, row[0])) for row in conn.execute("SELECT id FROM records ORDER BY id").fetchall()]

    def get(self, thing_id: str, *, with_files=False):
        with self._tx() as conn:
            self._require_enabled(conn)
            record = self._load(conn, thing_id)
            return record if with_files else self._public(record)

    def _write(self, conn, thing_id, record, revision):
        content = {k: v for k, v in record.items() if k not in {"id", "revision"}}
        raw = json.dumps(content, ensure_ascii=False).encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise ThingsError("record_size", "These files are too large together. Use smaller files.")
        ref, _ = self._cas(thing_id).put(raw, max_bytes=MAX_RECORD_BYTES)
        # The CAS creates private files; tighten intermediate directories as well.
        for path in (self.root / "objects").rglob("*"):
            _private(path, 0o700 if path.is_dir() else 0o600)
        _private(self.root / "objects", 0o700)
        conn.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (thing_id, ref, revision))

    def _collect(self):
        with self._tx() as conn:
            live = dict(conn.execute("SELECT id,ref FROM records"))
            objects = self.root / "objects"
            if not objects.exists():
                return
            for folder in objects.iterdir():
                _id(folder.name)
                _private(folder, 0o700)
                if folder.name not in live:
                    shutil.rmtree(folder)
                else:
                    cas = self._cas(folder.name)
                    for ref in list(cas.iter_ids()):
                        if ref != live[folder.name]:
                            cas.delete(ref)

    def save(self, fields: dict, *, thing_id: str = "", revision: int = 0, files: list[dict] | None = None) -> dict:
        clean = clean_fields(fields)
        with self._tx() as conn:
            self._require_enabled(conn)
            if thing_id:
                record = self._load(conn, thing_id)
                if not isinstance(revision, int) or revision != record["revision"]:
                    raise ThingsError("stale", "This thing changed. Open it again before saving.", 409)
                if files is not None:
                    raise ThingsError("bad_files", "Attach files separately when editing.")
                clean["files"] = record["files"]
                revision += 1
            else:
                thing_id, revision = uuid.uuid4().hex, 1
                clean["files"] = files or []
            if len(clean["files"]) > MAX_FILES:
                raise ThingsError("too_many_files", "Keep at most 12 files with one thing.")
            self._write(conn, thing_id, clean, revision)
        self._collect()
        return self.get(thing_id)

    def attach(self, thing_id, revision, file):
        with self._tx() as conn:
            self._require_enabled(conn)
            record = self._load(conn, thing_id)
            if record["revision"] != revision:
                raise ThingsError("stale", "This thing changed. Open it again before attaching a file.", 409)
            if len(record["files"]) >= MAX_FILES:
                raise ThingsError("too_many_files", "Keep at most 12 files with one thing.")
            record["files"].append(file)
            self._write(conn, thing_id, record, revision + 1)
        self._collect()
        return self.get(thing_id)

    def delete(self, thing_id, revision):
        with self._tx() as conn:
            record = self._load(conn, thing_id)
            if record["revision"] != revision:
                raise ThingsError("stale", "This thing changed. Open it again before deleting.", 409)
            conn.execute("DELETE FROM records WHERE id=?", (thing_id,))
        self._collect()
        return {"deleted": True}

    def forget(self, token: str) -> dict:
        """Owner registration seam. Encrypted records are scanned, including file bytes and names.

        Erase the entire thing if metadata or any file carries the target. Undecodable binary
        files are conservatively erased too: a byte scan cannot prove absence in compressed data.
        This deliberately removes more than textual matching, and reports the count.
        """
        if not isinstance(token, str) or len(token.strip()) < 2:
            raise ThingsError("bad_target", "Give the exact words to forget.")
        if not (self.root / "records.sqlite3").exists():
            return {"status": "verified", "removed": 0}
        needle = token.casefold()
        removed = 0
        with self._tx() as conn:
            for (thing_id,) in conn.execute("SELECT id FROM records").fetchall():
                record = self._load(conn, thing_id)
                encoded = json.dumps({k: v for k, v in record.items() if k != "files"}, ensure_ascii=False).casefold()
                erase = needle in encoded
                for file in record["files"]:
                    raw = base64.b64decode(file["data"], validate=True)
                    try:
                        words = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        erase = True
                    else:
                        erase = erase or needle in words.casefold() or needle in file["name"].casefold()
                if erase:
                    conn.execute("DELETE FROM records WHERE id=?", (thing_id,))
                    removed += 1
        self._collect()
        return {"status": "verified", "removed": removed}

    def export(self) -> bytes:
        with self._tx() as conn:
            self._require_enabled(conn)
            records = [self._load(conn, row[0]) for row in conn.execute("SELECT id FROM records").fetchall()]
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("things.json", json.dumps([self._public(r) for r in records], ensure_ascii=False, indent=2))
            for record in records:
                for file in record["files"]:
                    archive.writestr(record["id"] + "/" + file["id"] + "-" + file["name"], base64.b64decode(file["data"], validate=True))
        return out.getvalue()
