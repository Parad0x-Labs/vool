"""Encrypted durable session store. UNREVIEWED: not for real messages.

Every record value is AES-256-GCM sealed with the caller's 32-byte vault key; the
AAD binds the random store id, record kind, id and owning session. The key is
never written into the database. Every client operation runs inside one
``BEGIN IMMEDIATE`` transaction so concurrent processes serialize.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
from pathlib import Path

from core.drop.session import (
    LIMITS,
    PROFILE,
    DropError,
    canonical,
    check_id,
    demand,
    domain,
    encode,
    fresh_id,
    open_box,
    seal,
)

RECORD_LIMIT = 262144
KIND_LIMITS = {"identity": 1, "prekey": LIMITS["prekeys"], "session": LIMITS["sessions"],
               "outbox": LIMITS["queued"], "inbox": LIMITS["queued"], "revoked": 1024}


def secure_directory(directory) -> Path:
    demand(os.name == "posix", "UNSUPPORTED_STORE_PLATFORM")
    directory = Path(directory).absolute()
    with contextlib.suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    info = directory.lstat()
    demand(stat.S_ISDIR(info.st_mode), "UNSAFE_STORE_PATH")
    demand(info.st_mode & 0o077 == 0 and info.st_uid == os.getuid(), "UNSAFE_STORE_PERMISSIONS")
    return directory


def secure_file(path: Path, create=False):
    if create:
        with contextlib.suppress(FileExistsError):
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
    info = path.lstat()
    demand(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_STORE_PATH")
    demand(info.st_mode & 0o077 == 0 and info.st_uid == os.getuid(), "UNSAFE_STORE_PERMISSIONS")


class EncryptedStore:
    def __init__(self, directory, key: bytes):
        demand(isinstance(key, (bytes, bytearray)) and len(key) == 32, "INVALID_VAULT_KEY")
        self._key = bytearray(key)
        self._db = None
        self._transaction = False
        self._closed = False
        try:
            path = secure_directory(directory) / "drop.sqlite"
            secure_file(path, create=True)
            self._db = sqlite3.connect(path, timeout=5, isolation_level=None)
            self._db.executescript("""
                PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA trusted_schema=OFF;
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL,
                  owner TEXT NOT NULL, blob TEXT NOT NULL, PRIMARY KEY(kind,id));
            """)
            columns = [row[1] for row in self._db.execute("PRAGMA table_info(records)")]
            demand(columns == ["kind", "id", "owner", "blob"], "UNSUPPORTED_STORE_SCHEMA")
            self._db.execute("CREATE INDEX IF NOT EXISTS record_owner ON records(owner,kind)")
            self.transaction(self._initialize)
            for suffix in ("-wal", "-shm"):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists():
                    os.chmod(sidecar, 0o600)
                    secure_file(sidecar)
        except BaseException:
            self.close()
            raise

    def _initialize(self):
        existing = self._db.execute("SELECT value FROM metadata WHERE name='store'").fetchone()
        if existing:
            self._store_id = check_id(existing[0])
            row = self._db.execute("SELECT owner,blob FROM records WHERE kind='check' AND id='key'").fetchone()
            demand(row is not None, "CORRUPT_STORE")
            check = self._decrypt("check", "key", row[0], row[1])
            demand(check == {"store": self._store_id, "profile": PROFILE}, "CORRUPT_STORE")
        else:
            demand(self._db.execute("SELECT count(*) FROM records").fetchone()[0] == 0, "CORRUPT_STORE")
            self._store_id = fresh_id()
            self._db.execute("INSERT INTO metadata VALUES ('store',?)", (self._store_id,))
            self._db.execute("INSERT INTO records VALUES ('check','key','',?)",
                             (self._encrypt("check", "key", "", {"store": self._store_id, "profile": PROFILE}),))

    def _aad(self, kind, record_id, owner) -> bytes:
        return domain("vault", encode({"store": self._store_id, "kind": kind, "id": record_id, "owner": owner}))

    def _encrypt(self, kind, record_id, owner, value) -> str:
        return canonical(seal(bytes(self._key), encode(value), self._aad(kind, record_id, owner),
                              limit=RECORD_LIMIT))

    def _decrypt(self, kind, record_id, owner, blob):
        demand(isinstance(blob, str) and len(blob) <= RECORD_LIMIT * 2, "CORRUPT_STORE")
        try:
            plain = open_box(bytes(self._key), json.loads(blob), self._aad(kind, record_id, owner), RECORD_LIMIT)
            return json.loads(plain.decode("utf-8"))
        except (DropError, ValueError, UnicodeError) as exc:
            raise DropError("CORRUPT_STORE_OR_WRONG_KEY") from exc

    def transaction(self, operation):
        demand(not self._closed and not self._transaction, "STORE_UNAVAILABLE")
        self._db.execute("BEGIN IMMEDIATE")
        self._transaction = True
        try:
            result = operation()
            self._db.execute("COMMIT")
            return result
        except BaseException:
            try:
                self._db.execute("ROLLBACK")
            except sqlite3.Error:
                self.close()
            raise
        finally:
            self._transaction = False

    def _require(self):
        demand(self._transaction, "TRANSACTION_REQUIRED")

    def get(self, kind, record_id):
        self._require()
        row = self._db.execute("SELECT owner,blob FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return self._decrypt(kind, record_id, row[0], row[1]) if row else None

    def put(self, kind, record_id, value):
        demand(self._transaction and kind in KIND_LIMITS, "TRANSACTION_REQUIRED")
        exists = self._db.execute("SELECT 1 FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        if not exists:
            demand(self.count(kind) < KIND_LIMITS[kind], "STORE_LIMIT")
        owner = (check_id(value["sid"]) if kind == "session"
                 else check_id(value["session"]) if kind in ("inbox", "outbox") else "")
        self._db.execute(
            "INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET owner=excluded.owner,blob=excluded.blob",
            (kind, record_id, owner, self._encrypt(kind, record_id, owner, value)))

    def count(self, kind) -> int:
        self._require()
        return self._db.execute("SELECT count(*) FROM records WHERE kind=?", (kind,)).fetchone()[0]

    def list(self, kind):
        self._require()
        rows = self._db.execute("SELECT id,owner,blob FROM records WHERE kind=? ORDER BY id", (kind,)).fetchall()
        return [(row[0], self._decrypt(kind, row[0], row[1], row[2])) for row in rows]

    def remove(self, kind, record_id):
        self._require()
        self._db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, record_id))

    def remove_session_queues(self, session):
        self._require()
        check_id(session)
        self._db.execute("DELETE FROM records WHERE owner=? AND kind IN ('inbox','outbox')", (session,))

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._db is not None:
                self._db.close()
        finally:
            for index in range(len(self._key)):
                self._key[index] = 0
