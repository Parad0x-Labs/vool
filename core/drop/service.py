"""Bounded local relay and Drop carrier owner. UNREVIEWED: not for real messages.

Off unless ``VOOL_DROP=1``. Drop content is never admitted into general memory: a
Drop's note, file and task live only in this home's ``drop/`` folder and its
encrypted session store, and expiry or delete removes them together with any
carrier that names the Drop. One packet (note plus one selected file up to
10 MiB) and one selected-file digest task per Drop.

The relay is any folder both people can reach (a shared or synced folder). It
only ever holds ciphertext addressed by public fingerprints.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from pathlib import Path

from core.drop import DropError, require_enabled
from core.drop.client import DropClient
from core.drop.session import b64, unb64

MAX_FILE = 10 * 1024 * 1024
CHUNK = 40 * 1024
TTL = 7 * 86400 * 1000
_HOME_LOCK = threading.RLock()
_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DropError("INVALID_ID")
    return value


def summary_block(sid):
    """Map an opaque session id onto the existing memory block grammar."""
    return "drop_" + hashlib.sha256(_id(sid).encode()).hexdigest()[:32]


def _private_directory(path: Path, *, create=False):
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        mode = path.lstat()
    except OSError as exc:
        raise DropError("MISSING_RELAY") from exc
    if not stat.S_ISDIR(mode.st_mode) or mode.st_mode & 0o077 or mode.st_uid != os.getuid():
        raise DropError("DIRECTORY_PERMISSIONS")


def _write(path, data):
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read(path, limit):
    try:
        info = path.lstat()
    except OSError as exc:
        raise DropError("INPUT_UNAVAILABLE") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise DropError("INPUT_LIMIT")
    data = path.read_bytes()
    if len(data) > limit:
        raise DropError("INPUT_LIMIT")
    return data


def _json(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise DropError("INVALID_FORMAT") from exc
    if not isinstance(value, dict):
        raise DropError("INVALID_FORMAT")
    return value


class LocalRelay:
    def __init__(self, root, *, create=False):
        require_enabled()
        self.root = Path(root).absolute()
        _private_directory(self.root, create=create)
        if create:
            _private_directory(self.root / "invites", create=True)

    def put(self, peer, request, wire):
        # Fingerprints are public routing metadata, never an authorization grant.
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", peer):
            raise DropError("INVALID_PEER")
        _id(request)
        if len(wire) > 110000:
            raise DropError("INPUT_LIMIT")
        _private_directory(self.root)
        mailbox = self.root / peer
        _private_directory(mailbox, create=True)
        digest = hashlib.sha256(wire).hexdigest()
        path = mailbox / (f"{time.time_ns():020d}-{request}-{digest}.wire")
        _write(path, wire)
        return path

    def messages(self, peer):
        _private_directory(self.root)
        mailbox = self.root / peer
        if not mailbox.exists():
            return []
        _private_directory(mailbox)
        # Oldest first and bounded: a flooded mailbox delays later frames but
        # never stops this home from reading the ones it can process.
        return sorted(mailbox.glob("*.wire"))[:1024]


# Authenticated payloads or frames that are not acceptable here. Each one is
# consumed (recorded and removed from the relay or recovery queue) so a single
# bad, forged, replayed or late frame can never block the deliveries after it.
# Any other failure (busy store, disk error) leaves the frame for the next poll.
_REJECTED = frozenset({
    "INVALID_FORMAT", "NON_CANONICAL", "UNSUPPORTED_PROFILE", "INVALID_KEY", "AUTH_FAILED",
    "IDENTITY_MISMATCH", "SELF_SESSION", "UNKNOWN_PREKEY", "PREKEY_UNAVAILABLE", "EXPIRED", "REPLAY",
    "SKIP_LIMIT", "INVALID_COUNTER", "WRONG_SESSION", "UNKNOWN_SESSION", "SESSION_REVOKED", "PAYLOAD_LIMIT",
    "SESSION_EXHAUSTED", "CHAIN_EXHAUSTED", "INPUT_LIMIT", "INVALID_ID", "UNKNOWN_DROP", "DROP_REVOKED",
    "DROP_WITHHELD", "UNKNOWN_INVITE", "INVALID_MANIFEST", "CHUNK_INDEX", "CHUNK_SIZE", "CHUNK_CONFLICT",
    "INVALID_TASK", "TASK_CONFLICT", "INVALID_RESULT", "UNSUPPORTED_PAYLOAD",
    "ADMISSION_CONFLICT", "FILE_INTEGRITY", "INVALID_BASE64",
})


# Authentic frames that arrived ahead of the frame they depend on (a synced
# folder can deliver files out of order). They stay in the encrypted inbox and
# are retried on the next poll; a forgotten or expired Drop discards them.
_DEFERRED = frozenset({"MISSING_MANIFEST", "TASK_BEFORE_PACKET"})


def _discard(path: Path):
    # Whatever another relay user put in this mailbox (even a directory or an
    # unremovable entry) is skipped, never allowed to stop the poll.
    with contextlib.suppress(OSError):
        path.unlink()


def _vault_key(path: Path) -> bytes:
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(secrets.token_bytes(32))
            out.flush()
            os.fsync(out.fileno())
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        raise DropError("VAULT_KEY_PERMISSIONS")
    if info.st_size != 32:
        raise DropError("VAULT_KEY_SIZE")
    return path.read_bytes()


class DropHome:
    """One person's Drops inside a VOOL home (the active home unless one is named)."""

    def __init__(self, home=None, relay=None, *, clock=None, allowed=("read_files",)):
        require_enabled()
        from core.runtime_paths import active_vool_home

        if relay is None:
            raise DropError("RELAY_REQUIRED")
        self.home = Path(home).absolute() if home is not None else active_vool_home()
        self._own_home = self.home.resolve() == active_vool_home().resolve()
        _private_directory(self.home, create=True)
        self.root = self.home / "drop"
        _private_directory(self.root, create=True)
        self.relay = LocalRelay(relay)
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.allowed = frozenset(allowed)
        crypto = self.root / "crypto"
        _private_directory(crypto, create=True)
        key = _vault_key(crypto / "vault.key")
        self.client = DropClient(crypto / "sessions", key)
        self.fingerprint = self.client.fingerprint
        self.db = sqlite3.connect(self.root / "drops.db", timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS drops (
                sid TEXT PRIMARY KEY, peer TEXT NOT NULL, expires INTEGER NOT NULL,
                status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                manifest TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '', task_state TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '', executions INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS processed (delivery TEXT PRIMARY KEY, sid TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admissions (
                delivery TEXT PRIMARY KEY, sid TEXT NOT NULL, peer TEXT NOT NULL,
                expires INTEGER NOT NULL, prekey TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invites (
                token TEXT PRIMARY KEY, prekey TEXT NOT NULL UNIQUE, peer TEXT NOT NULL,
                expires INTEGER NOT NULL);
        """)
        self.db.commit()
        os.chmod(self.root / "drops.db", 0o600)
        self.lock = (self.root / "actor.lock").open("a+")
        os.chmod(self.root / "actor.lock", 0o600)
        try:
            with self.scope():
                from core.memory.entries import _ensure_memory_files
                from core.vool_memory import VoolMemory

                VoolMemory(runtime_home=self.home).close()
                _ensure_memory_files()
                # Expiry does not wait for a poll: every open sweeps first.
                self._expire()
                for row in self.db.execute(
                        "SELECT sid FROM drops WHERE status NOT IN ('deleted','expired','incomplete')"):
                    self._ensure_scope(row["sid"])
        except BaseException:
            self.close()
            raise

    def _ensure_scope(self, sid):
        from core.context_namespace import ensure_chat_namespace

        namespace = ensure_chat_namespace(sid, grant_current_receipts=False)
        if namespace.lifecycle_state != "active":
            raise DropError("DROP_REVOKED")

    def close(self):
        if getattr(self, "lock", None) is not None and self.lock.closed:
            return
        try:
            self.client.close()
        finally:
            if getattr(self, "db", None) is not None:
                self.db.close()
            if getattr(self, "lock", None) is not None:
                self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextlib.contextmanager
    def scope(self):
        """Serialize Drop work; point the memory owners at this home.

        In the running app the home is the active one and nothing global changes.
        Only a second home in the same process (the two-home tests and demo)
        swaps the process-wide home, under a lock, for the duration of a call.
        """
        from core import runtime_paths
        from storage.db import active_default_db_path, configure_default_db_path

        with _HOME_LOCK:
            fcntl.flock(self.lock, fcntl.LOCK_EX)
            try:
                if self._own_home:
                    yield
                    return
                previous_home = runtime_paths._VOOL_HOME_OVERRIDE
                previous_db = active_default_db_path()
                runtime_paths.configure_runtime_home(self.home)
                configure_default_db_path(self.home / "data" / "drop_runtime.db")
                try:
                    yield
                finally:
                    configure_default_db_path(previous_db)
                    runtime_paths.configure_runtime_home(previous_home)
            finally:
                fcntl.flock(self.lock, fcntl.LOCK_UN)

    def _row(self, sid):
        _id(sid)
        row = self.db.execute("SELECT * FROM drops WHERE sid=?", (sid,)).fetchone()
        if row is None:
            raise DropError("UNKNOWN_DROP")
        return dict(row)

    def _live(self, sid):
        from core.memory.revocation import authorize_payload

        row = self._row(sid)
        if row["status"] in {"deleted", "expired", "incomplete"}:
            raise DropError("DROP_REVOKED")
        if self.clock() >= row["expires"]:
            self._forget(sid, "expired")
            raise DropError("EXPIRED")
        if not authorize_payload({"drop": sid}, sid, self.home):
            raise DropError("DROP_WITHHELD")
        return row

    def invite(self, peer):
        """A one-use invite link for the person whose fingerprint is ``peer``.

        The fingerprint must come from that person over a channel you trust
        (in person, a call); the link alone never authenticates anyone.
        """
        unb64(peer, 32)
        with self.scope():
            self._expire_invites()
            token = secrets.token_hex(16)
            raw = self.client.issue_invite(now=self.clock(), ttl=TTL)
            body = _json(raw)["body"]
            self.db.execute("INSERT INTO invites VALUES(?,?,?,?)", (token, body["id"], peer, body["expires"]))
            self.db.commit()
            _write(self.relay.root / "invites" / (token + ".invite"), raw)
            return "vool-drop://invite/" + token

    def join(self, link, peer):
        """Open a Drop from someone's invite; ``peer`` is their fingerprint."""
        with self.scope():
            match = re.fullmatch(r"vool-drop://invite/([0-9a-f]{32})", link)
            if not match:
                raise DropError("INVALID_INVITE_LINK")
            raw = _read(self.relay.root / "invites" / (match[1] + ".invite"), 110000)
            reply = self.client.start(invite=raw, peer_fingerprint=peer,
                                      plaintext=b'{"type":"hello"}', now=self.clock())
            sid = reply["session"]
            expires = _json(raw)["body"]["expires"]
            self.db.execute("INSERT INTO drops(sid,peer,expires,status) VALUES(?,?,?,'connected')",
                            (sid, peer, expires))
            self.db.commit()
            self._ensure_scope(sid)
            self._flush()
            return sid

    def _flush(self):
        for row in self.client.pending_sends():
            wire = row["wire"]
            # The library's authenticated local outbox, never the relay, names
            # the session. Recover a cut after the native start but before SQL.
            if not self.db.execute("SELECT 1 FROM drops WHERE sid=?", (row["session"],)).fetchone():
                intro = _json(wire).get("intro")
                if not intro or intro.get("sid") != row["session"]:
                    raise DropError("OUTBOX_METADATA_MISSING")
                self.db.execute("INSERT INTO drops(sid,peer,expires,status) VALUES(?,?,?,'connected')",
                                (row["session"], intro["recipient"], intro["expires"]))
                self.db.commit()
                self._ensure_scope(row["session"])
            state = self._live(row["session"])
            self.relay.put(state["peer"], row["request"], wire)
            # Relay publication is durable. It is not recipient/execution proof.
            self.client.acknowledge_send(row["request"])

    def _send(self, sid, payload):
        self._live(sid)
        self.client.send(session=sid, plaintext=json.dumps(payload).encode(), now=self.clock())
        self._flush()

    def send_packet(self, sid, note, file):
        with self.scope():
            row = self._live(sid)
            if row["manifest"] or not isinstance(note, str) or len(note.encode()) > 4096:
                raise DropError("PACKET_CONFLICT")
            path = Path(file)
            data = _read(path, MAX_FILE)
            name = path.name
            if not name or len(name) > 128 or any(c in name for c in "/\\\0"):
                raise DropError("INVALID_FILENAME")
            manifest = {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                        "chunks": max(1, (len(data) + CHUNK - 1) // CHUNK)}
            self.db.execute("UPDATE drops SET note=?,manifest=? WHERE sid=?", (note, json.dumps(manifest), sid))
            self.db.commit()
            self._send(sid, {"type": "packet", "note": note, "manifest": manifest})
            for index in range(manifest["chunks"]):
                self._send(sid, {"type": "chunk", "index": index,
                                 "data": b64(data[index * CHUNK:(index + 1) * CHUNK])})
            return manifest

    def poll(self):
        """Read this home's mailbox. Returns one outcome per frame processed:
        ``received``, ``duplicate`` or ``rejected`` (consumed without effect)."""
        with self.scope():
            self._expire()
            self._flush()
            results = self._recover()
            for path in self.relay.messages(self.fingerprint):
                try:
                    raw = _read(path, 110000)
                except DropError as exc:
                    if exc.code not in {"INPUT_LIMIT", "INPUT_UNAVAILABLE"}:
                        raise
                    _discard(path)
                    results.append("rejected")
                    continue
                digest = b64(hashlib.sha256(raw).digest())
                if self.db.execute("SELECT 1 FROM processed WHERE delivery=?", (digest,)).fetchone():
                    path.unlink()
                    results.append("duplicate")
                    continue
                try:
                    record = self._open_wire(raw, digest)
                except DropError as exc:
                    if exc.code not in _REJECTED:
                        raise
                    _discard(path)
                    results.append("rejected")
                    continue
                results.append(self._apply(record))
                path.unlink()
            if "deferred" in results:
                results += self._recover()
            return results

    def _recover(self):
        outcomes = []
        for record in self.client.pending_receives():
            self._finish_admission(record)
            outcomes.append(self._apply(record))
        return outcomes

    def _open_wire(self, raw, digest):
        packet = _json(raw)
        if "intro" not in packet:
            self._live(packet.get("sid"))
            return self.client.receive(wire=raw, now=self.clock())
        peer = self._prepare_admission(raw, digest)
        try:
            record = self.client.accept(wire=raw, peer_fingerprint=peer, now=self.clock())
        except DropError:
            # Nothing was committed: the draft goes and the invite stays usable.
            self.db.execute("DELETE FROM admissions WHERE delivery=?", (digest,))
            self.db.commit()
            raise
        self._finish_admission(record)
        return record

    def _prepare_admission(self, raw, digest):
        intro = _json(raw).get("intro")
        if not isinstance(intro, dict) or type(intro.get("expires")) is not int:
            raise DropError("INVALID_FORMAT")
        sid, prekey = _id(intro.get("sid")), _id(intro.get("prekey"))
        invite = self.db.execute("SELECT peer FROM invites WHERE prekey=?", (prekey,)).fetchone()
        if invite is None:
            raise DropError("UNKNOWN_INVITE")
        if self.db.execute("SELECT count(*) FROM admissions").fetchone()[0] >= 64:
            raise DropError("ADMISSION_QUOTA")
        # The expected sender comes from our own invite record, never from the
        # frame. The draft lets a crash after the native commit finish the Drop.
        self.db.execute("INSERT OR IGNORE INTO admissions VALUES(?,?,?,?,?)",
                        (digest, sid, invite["peer"], intro["expires"], prekey))
        self.db.commit()
        return invite["peer"]

    def _finish_admission(self, record):
        draft = self.db.execute("SELECT * FROM admissions WHERE delivery=?", (record["delivery"],)).fetchone()
        if draft is None:
            return
        if draft["sid"] != record["session"]:
            raise DropError("ADMISSION_CONFLICT")
        self.db.execute("INSERT OR IGNORE INTO drops(sid,peer,expires,status) VALUES(?,?,?,'connected')",
                        (draft["sid"], draft["peer"], draft["expires"]))
        self.db.commit()
        self._ensure_scope(draft["sid"])
        self._discard_invites("prekey=?", (draft["prekey"],))
        self.db.execute("DELETE FROM admissions WHERE delivery=?", (record["delivery"],))
        self.db.commit()

    def _discard_invites(self, condition, params):
        for invite in self.db.execute("SELECT * FROM invites WHERE " + condition, params).fetchall():
            self.client.discard_invite(invite["prekey"])
            (self.relay.root / "invites" / (invite["token"] + ".invite")).unlink(missing_ok=True)
            self.db.execute("DELETE FROM invites WHERE token=?", (invite["token"],))
            self.db.commit()

    def _expire_invites(self):
        now = self.clock()
        self._discard_invites("expires<=?", (now,))
        self.db.execute("DELETE FROM admissions WHERE expires<=?", (now,))
        self.db.commit()

    def _apply(self, record):
        sid = record["session"]
        delivery = record["delivery"]
        if self.db.execute("SELECT 1 FROM processed WHERE delivery=?", (delivery,)).fetchone():
            self.client.acknowledge_receive(delivery)
            return "duplicate"
        try:
            self._live(sid)
            self._apply_payload(sid, _json(record["plaintext"]))
            outcome = "received"
        except DropError as exc:
            self.db.rollback()
            if exc.code in _DEFERRED:
                return "deferred"
            if exc.code not in _REJECTED:
                raise
            # Authentic but unacceptable here: consumed without effect.
            outcome = "rejected"
        self.db.execute("INSERT INTO processed(delivery,sid) VALUES(?,?)", (delivery, sid))
        self.db.commit()
        self.client.acknowledge_receive(delivery)
        return outcome

    def _apply_payload(self, sid, payload):
        kind = payload.get("type")
        row = self._row(sid)
        if kind == "hello" and set(payload) == {"type"}:
            return
        if kind == "packet" and set(payload) == {"type", "note", "manifest"}:
            manifest = payload["manifest"]
            if (row["manifest"] or not isinstance(payload["note"], str) or len(payload["note"].encode()) > 4096
                    or not isinstance(manifest, dict) or set(manifest) != {"name", "size", "sha256", "chunks"}
                    or type(manifest["size"]) is not int or not 0 <= manifest["size"] <= MAX_FILE
                    or type(manifest["chunks"]) is not int
                    or manifest["chunks"] != max(1, (manifest["size"] + CHUNK - 1) // CHUNK)
                    or not isinstance(manifest["name"], str) or not 0 < len(manifest["name"]) <= 128
                    or any(c in manifest["name"] for c in "/\\\0")
                    or not isinstance(manifest["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])):
                raise DropError("INVALID_MANIFEST")
            _private_directory(self.root / sid, create=True)
            self.db.execute("UPDATE drops SET note=?,manifest=?,status='receiving' WHERE sid=?",
                            (payload["note"], json.dumps(manifest), sid))
            return
        if kind == "chunk" and set(payload) == {"type", "index", "data"}:
            if not row["manifest"]:
                raise DropError("MISSING_MANIFEST")
            manifest = json.loads(row["manifest"])
            index = payload["index"]
            if type(index) is not int or not 0 <= index < manifest["chunks"]:
                raise DropError("CHUNK_INDEX")
            data = unb64(payload["data"], 0, CHUNK)
            expected = min(CHUNK, max(0, manifest["size"] - index * CHUNK))
            if len(data) != expected:
                raise DropError("CHUNK_SIZE")
            part = self.root / sid / f"{index:04d}.part"
            if part.exists() and _read(part, CHUNK) != data:
                raise DropError("CHUNK_CONFLICT")
            _write(part, data)
            self._assemble(sid, manifest)
            return
        self._apply_task(sid, payload)

    def _assemble(self, sid, manifest):
        parts = [self.root / sid / f"{i:04d}.part" for i in range(manifest["chunks"])]
        target = self.root / sid / "selected.bin"
        if target.exists():
            data = _read(target, MAX_FILE)
            if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                raise DropError("FILE_INTEGRITY")
            for part in parts:
                part.unlink(missing_ok=True)
            self.db.execute("UPDATE drops SET status='ready' WHERE sid=?", (sid,))
            return
        if not all(p.exists() for p in parts):
            return
        data = b"".join(_read(p, CHUNK) for p in parts)
        if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise DropError("FILE_INTEGRITY")
        _write(target, data)
        for part in parts:
            part.unlink()
        self.db.execute("UPDATE drops SET status='ready' WHERE sid=?", (sid,))

    def show(self, sid):
        with self.scope():
            row = self._live(sid)
            return {k: row[k] for k in ("sid", "status", "note", "manifest", "task", "task_state", "result", "executions", "expires")}

    def _apply_task(self, sid, payload):
        row = self._row(sid)
        if payload.get("type") == "task":
            if not row["manifest"]:
                raise DropError("TASK_BEFORE_PACKET")
            if (set(payload) != {"type", "task_id", "operation", "file_sha256", "permissions", "description"}
                    or payload["operation"] != "sha256-selected-file"
                    or not isinstance(payload["description"], str) or len(payload["description"].encode()) > 4096
                    or not isinstance(payload["permissions"], list)
                    or any(not isinstance(p, str) for p in payload["permissions"])
                    or not row["manifest"] or row["status"] not in {"receiving", "ready"}
                    or payload["file_sha256"] != json.loads(row["manifest"])["sha256"]):
                # Only the side that received the packet can be asked to work on it.
                raise DropError("INVALID_TASK")
            _id(payload["task_id"])
            encoded = json.dumps(payload, sort_keys=True)
            if row["task_id"]:
                if row["task_id"] != payload["task_id"] or row["task"] != encoded:
                    raise DropError("TASK_CONFLICT")
                return  # Same logical task, even when re-encrypted, preserves its state.
            permitted = payload["permissions"] == ["read_files"] and "read_files" in self.allowed
            self.db.execute("UPDATE drops SET task=?,task_id=?,task_state=? WHERE sid=?",
                            (encoded, payload["task_id"], "pending" if permitted else "refused", sid))
        elif payload.get("type") == "result":
            if (set(payload) != {"type", "task_id", "digest"} or row["task_state"] != "submitted"
                    or payload["task_id"] != row["task_id"]
                    or not row["manifest"] or payload["digest"] != json.loads(row["manifest"])["sha256"]):
                raise DropError("INVALID_RESULT")
            self.db.execute("UPDATE drops SET result=?,task_state='complete' WHERE sid=?",
                            (payload["digest"], sid))
        else:
            raise DropError("UNSUPPORTED_PAYLOAD")

    def send_task(self, sid, description, *, permissions=("read_files",)):
        with self.scope():
            row = self._live(sid)
            if not row["manifest"] or row["task_id"] or not isinstance(description, str):
                raise DropError("TASK_CONFLICT")
            task_id = b64(secrets.token_bytes(16))
            payload = {"type": "task", "task_id": task_id, "operation": "sha256-selected-file",
                       "file_sha256": json.loads(row["manifest"])["sha256"],
                       "permissions": list(permissions), "description": description}
            if len(description.encode()) > 4096:
                raise DropError("TASK_LIMIT")
            self.db.execute("UPDATE drops SET task=?,task_id=?,task_state='submitted' WHERE sid=?",
                            (json.dumps(payload, sort_keys=True), task_id, sid))
            self.db.commit()
            self._send(sid, payload)
            return task_id

    def approve(self, sid, task_id):
        """Local controller call only. No relay payload can invoke this method."""
        with self.scope():
            row = self._live(sid)
            if (row["task_id"] != task_id or row["task_state"] != "pending"
                    or row["status"] != "ready"):
                raise DropError("TASK_NOT_APPROVABLE")
            self._permission(sid, row)
            self.db.execute("UPDATE drops SET task_state='approved' WHERE sid=?", (sid,))
            self.db.commit()

    def _permission(self, sid, row):
        from core.mode_permission_policy import PermissionEffect, decide_tool_call

        path = self.root / sid / "selected.bin"
        decision = decide_tool_call(
            intent="workspace.read_file", arguments={"path": "selected.bin"}, task_id=row["task_id"],
            source_context={"runtime_session_id": sid, "operating_mode": "manual",
                            "workspace_root": str(path.parent),
                            "project_permissions": {"read_files": "read_files" in self.allowed}},
        )
        if decision.effect is not PermissionEffect.ALLOW:
            raise DropError("RECIPIENT_PERMISSION_REFUSED")

    def execute_approved(self, sid):
        with self.scope():
            row = self._live(sid)
            if row["task_state"] != "approved" or row["executions"]:
                raise DropError("APPROVAL_REQUIRED_OR_ALREADY_CLAIMED")
            self._permission(sid, row)  # Approval never widens a later local denial.
            self.db.execute("BEGIN IMMEDIATE")
            claimed = self.db.execute(
                "UPDATE drops SET task_state='running',executions=executions+1 "
                "WHERE sid=? AND task_state='approved' AND executions=0", (sid,))
            self.db.commit()
            if claimed.rowcount != 1:
                raise DropError("ALREADY_CLAIMED")
            # This is the entire bounded executor. No prompt, shell or model receives the bytes.
            data = _read(self.root / sid / "selected.bin", MAX_FILE)
            digest = hashlib.sha256(data).hexdigest()
            if digest != json.loads(row["manifest"])["sha256"]:
                raise DropError("FILE_INTEGRITY")
            self.db.execute("UPDATE drops SET result=?,task_state='complete' WHERE sid=?", (digest, sid))
            self.db.commit()
            self._send(sid, {"type": "result", "task_id": row["task_id"], "digest": digest})
            return digest

    def _expire(self):
        self._expire_invites()
        for row in self.db.execute("SELECT sid FROM drops WHERE expires<=? AND status NOT IN ('deleted','expired')",
                                   (self.clock(),)).fetchall():
            self._forget(row["sid"], "expired")

    def _forget(self, sid, state):
        from core.context_retrieval import forget_session_memory
        from core.memory import entries, revocation
        from core.vool_memory import VoolMemory

        row = self._row(sid)
        if row["status"] in {"deleted", "expired"}:
            return
        # Publish before *any* destructive action. Even an incomplete file/DB
        # sweep must make old memory/receipt copies stop serving immediately.
        # Existing semantic rows use hashed scope ids while other stores use
        # raw ids. A home-wide fence covers both representations immediately.
        # In this isolated lab, incomplete cleanup withholds the whole home;
        # the completed token revokes only carriers containing this Drop id.
        operation = revocation.begin("", sid, self.home)
        memory = VoolMemory(runtime_home=self.home)
        owner_operation = memory.begin_erasure(sid, sid)
        try:
            self.db.execute(
                "UPDATE drops SET status='incomplete',note='',manifest='',task='',result='',task_id='',task_state='' WHERE sid=?",
                (sid,))
            self.db.commit()
            self.client.forget_session(sid)
            folder = self.root / sid
            if folder.exists():
                _private_directory(folder)
                for path in folder.iterdir():
                    # A surprising directory/link is a failed cleanup, never a
                    # license to traverse or remove somebody else's files.
                    if not stat.S_ISREG(path.lstat().st_mode) or path.lstat().st_nlink != 1:
                        raise DropError("UNEXPECTED_CARRIER")
                    path.unlink()
                folder.rmdir()
            # Purge current queued ciphertext on both sides of this local relay.
            for peer in (self.fingerprint, row["peer"]):
                for path in self.relay.messages(peer):
                    wire = _json(_read(path, 110000))
                    if wire.get("sid") == sid or wire.get("intro", {}).get("sid") == sid:
                        path.unlink()
            status = {}
            forget_session_memory(sid, sid, source_context={"runtime_home": str(self.home), "chat_id": sid},
                                  store_status=status)
            if any(name.endswith("_failures") and value for name, value in status.items()):
                raise DropError("EXISTING_FORGET_INCOMPLETE")
            entries.forget_sessions_memory([sid])
            entries.delete_conversation_session(sid)
            memory.node_delete_for_session(sid)
            memory.occurrence_delete_for_chat(sid)
            memory.block_delete(summary_block(sid))
            memory.record_revocation(sid, chat_id=sid)
            memory.complete_erasure(owner_operation)
            revocation.complete(operation, self.home)
            self.db.execute("UPDATE drops SET status=? WHERE sid=?", (state, sid))
            self.db.commit()
        except Exception as exc:
            # Keep both independently persisted barriers pending. Do not call
            # cleanup failure a successful deletion or re-enable a reader.
            raise DropError("DROP_FORGET_INCOMPLETE") from exc
        finally:
            memory.close()

    def delete(self, sid):
        with self.scope():
            self._forget(sid, "deleted")

    def list(self):
        """Every Drop this home knows, without its content."""
        with self.scope():
            self._expire()
            return [dict(row) for row in self.db.execute(
                "SELECT sid,peer,status,expires,task_state FROM drops ORDER BY rowid")]

    def sweep(self):
        with self.scope():
            self._expire()
            return {r["sid"]: r["status"] for r in self.db.execute("SELECT sid,status FROM drops")}
