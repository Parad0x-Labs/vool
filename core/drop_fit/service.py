"""Bounded local relay and Drop carrier owner. UNREVIEWED: not for real messages.

This opt-in experiment has no production route or automatic memory admission.
It supports one packet per session and one selected-file digest task per packet.
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

from core.drop_fit import DropFitError, require_enabled
from core.drop_fit.crypto import SessionBridge, b64, unb64

MAX_FILE = 10 * 1024 * 1024
CHUNK = 40 * 1024
TTL = 7 * 86400 * 1000
_HOME_LOCK = threading.RLock()
_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DropFitError("INVALID_ID")
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
        raise DropFitError("MISSING_RELAY") from exc
    if not stat.S_ISDIR(mode.st_mode) or mode.st_mode & 0o077 or mode.st_uid != os.getuid():
        raise DropFitError("DIRECTORY_PERMISSIONS")


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
        raise DropFitError("INPUT_UNAVAILABLE") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise DropFitError("INPUT_LIMIT")
    data = path.read_bytes()
    if len(data) > limit:
        raise DropFitError("INPUT_LIMIT")
    return data


def _json(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise DropFitError("INVALID_FORMAT") from exc
    if not isinstance(value, dict):
        raise DropFitError("INVALID_FORMAT")
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
            raise DropFitError("INVALID_PEER")
        _id(request)
        if len(wire) > 110000:
            raise DropFitError("INPUT_LIMIT")
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
        files = sorted(mailbox.glob("*.wire"))
        if len(files) > 1024:
            raise DropFitError("RELAY_QUOTA")
        return files


class DropHome:
    def __init__(self, home, relay, node, *, peer=None, clock=None, allowed=("read_files",)):
        require_enabled()
        self.home = Path(home).absolute()
        _private_directory(self.home, create=True)
        self.root = self.home / "drop_fit"
        _private_directory(self.root, create=True)
        self.relay = LocalRelay(relay)
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.allowed = frozenset(allowed)
        self.peer = peer
        self.bridge = SessionBridge(self.root / "crypto", node)
        self.fingerprint = self.bridge.fingerprint
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
                token TEXT PRIMARY KEY, prekey TEXT NOT NULL, expires INTEGER NOT NULL);
        """)
        self.db.commit()
        os.chmod(self.root / "drops.db", 0o600)
        self.lock = (self.root / "actor.lock").open("a+")
        os.chmod(self.root / "actor.lock", 0o600)
        with self.scope():
            from core.memory.entries import _ensure_memory_files
            from core.vool_memory import VoolMemory

            VoolMemory(runtime_home=self.home).close()
            _ensure_memory_files()
            for row in self.db.execute("SELECT sid FROM drops WHERE status NOT IN ('deleted','expired','incomplete')"):
                self._ensure_scope(row["sid"])

    def _ensure_scope(self, sid):
        from core.context_namespace import ensure_chat_namespace

        namespace = ensure_chat_namespace(sid, grant_current_receipts=False)
        if namespace.lifecycle_state != "active":
            raise DropFitError("DROP_REVOKED")

    def close(self):
        if self.lock.closed:
            return
        self.bridge.close()
        self.db.close()
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextlib.contextmanager
    def scope(self):
        # A trusted local caller selects the home. Remote fields cannot select it.
        from core import runtime_paths
        from storage.db import active_default_db_path, configure_default_db_path

        with _HOME_LOCK:
            fcntl.flock(self.lock, fcntl.LOCK_EX)
            previous_home = runtime_paths._VOOL_HOME_OVERRIDE
            previous_db = active_default_db_path()
            runtime_paths.configure_runtime_home(self.home)
            configure_default_db_path(self.home / "data" / "drop_runtime.db")
            try:
                yield
            finally:
                configure_default_db_path(previous_db)
                runtime_paths.configure_runtime_home(previous_home)
                fcntl.flock(self.lock, fcntl.LOCK_UN)

    def _row(self, sid):
        _id(sid)
        row = self.db.execute("SELECT * FROM drops WHERE sid=?", (sid,)).fetchone()
        if row is None:
            raise DropFitError("UNKNOWN_DROP")
        return dict(row)

    def _live(self, sid):
        from core.memory.revocation import authorize_payload

        row = self._row(sid)
        if row["status"] in {"deleted", "expired", "incomplete"}:
            raise DropFitError("DROP_REVOKED")
        if self.clock() >= row["expires"]:
            self._forget(sid, "expired")
            raise DropFitError("EXPIRED")
        if not authorize_payload({"drop": sid}, sid, self.home):
            raise DropFitError("DROP_WITHHELD")
        return row

    def invite(self):
        with self.scope():
            self._expire_invites()
            token = secrets.token_hex(16)
            wire = self.bridge.call("invite", now=self.clock(), ttl=TTL)["wire"]
            raw = unb64(wire)
            body = _json(raw)["body"]
            self.db.execute("INSERT INTO invites VALUES(?,?,?)", (token, body["id"], body["expires"]))
            self.db.commit()
            _write(self.relay.root / "invites" / (token + ".invite"), raw)
            return "vool-drop-fit://invite/" + token

    def join(self, link, peer):
        with self.scope():
            match = re.fullmatch(r"vool-drop-fit://invite/([0-9a-f]{32})", link)
            if not match:
                raise DropFitError("INVALID_INVITE_LINK")
            raw = _read(self.relay.root / "invites" / (match[1] + ".invite"), 110000)
            reply = self.bridge.call("start", invite=b64(raw), peer=peer,
                                     plaintext=b64(b'{"type":"hello"}'), now=self.clock())
            sid = reply["session"]
            expires = _json(raw)["body"]["expires"]
            self.db.execute("INSERT INTO drops(sid,peer,expires,status) VALUES(?,?,?,'connected')",
                            (sid, peer, expires))
            self.db.commit()
            self._ensure_scope(sid)
            self._flush()
            return sid

    def _flush(self):
        for row in self.bridge.call("pendingSend"):
            wire = unb64(row["wire"])
            # This comes from the library's authenticated local outbox, never
            # from the relay. Recover a cut after native start but before SQL.
            if not self.db.execute("SELECT 1 FROM drops WHERE sid=?", (row["session"],)).fetchone():
                intro = _json(wire).get("intro")
                if not intro or intro["sid"] != row["session"]:
                    raise DropFitError("OUTBOX_METADATA_MISSING")
                self.db.execute("INSERT INTO drops(sid,peer,expires,status) VALUES(?,?,?,'connected')",
                                (row["session"], intro["recipient"], intro["expires"]))
                self.db.commit()
                self._ensure_scope(row["session"])
            state = self._live(row["session"])
            if state["status"] in {"deleted", "expired", "incomplete"}:
                raise DropFitError("DROP_REVOKED")
            self.relay.put(state["peer"], row["request"], wire)
            # Relay publication is durable. It is not recipient/execution proof.
            self.bridge.call("ackSend", request=row["request"])

    def _send(self, sid, payload):
        self._live(sid)
        self.bridge.call("send", session=sid, plaintext=b64(json.dumps(payload).encode()), now=self.clock())
        self._flush()

    def send_packet(self, sid, note, file):
        with self.scope():
            row = self._live(sid)
            if row["manifest"] or not isinstance(note, str) or len(note.encode()) > 4096:
                raise DropFitError("PACKET_CONFLICT")
            path = Path(file)
            data = _read(path, MAX_FILE)
            name = path.name
            if not name or len(name) > 128 or any(c in name for c in "/\\\0"):
                raise DropFitError("INVALID_FILENAME")
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
        with self.scope():
            self._expire()
            self._flush()
            recovered = self.bridge.call("pendingReceive")
            for record in recovered:
                self._finish_admission(record)
                self._apply(record)
            results = []
            for path in self.relay.messages(self.fingerprint):
                raw = _read(path, 110000)
                digest = b64(hashlib.sha256(raw).digest())
                if self.db.execute("SELECT 1 FROM processed WHERE delivery=?", (digest,)).fetchone():
                    path.unlink()
                    results.append("duplicate")
                    continue
                packet = _json(raw)
                if "intro" in packet:
                    if not self.peer:
                        raise DropFitError("PEER_PIN_REQUIRED")
                    self._prepare_admission(raw, self.peer)
                    try:
                        record = self.bridge.call("accept", wire=b64(raw), peer=self.peer, now=self.clock())
                    except DropFitError as exc:
                        # A lost response may follow a committed native inbox;
                        # retain its draft instead of guessing that nothing ran.
                        if str(exc) not in {"CRYPTO_UNAVAILABLE", "CRYPTO_TIMEOUT", "CRYPTO_REFUSED"}:
                            self.db.execute("DELETE FROM admissions WHERE delivery=?", (digest,))
                            self.db.commit()
                        raise
                    self._finish_admission(record)
                else:
                    self._live(packet.get("sid"))
                    record = self.bridge.call("receive", wire=b64(raw), now=self.clock())
                self._apply(record)
                path.unlink()
                results.append("received")
            return results

    def _prepare_admission(self, raw, peer):
        intro = _json(raw).get("intro")
        if not isinstance(intro, dict) or type(intro.get("expires")) is not int:
            raise DropFitError("INVALID_FORMAT")
        sid, prekey = _id(intro.get("sid")), _id(intro.get("prekey"))
        if self.db.execute("SELECT count(*) FROM admissions").fetchone()[0] >= 64:
            raise DropFitError("ADMISSION_QUOTA")
        # Untrusted metadata is only a recovery draft. No serving row is
        # created until its exact delivery is in the verified native inbox.
        self.db.execute("INSERT OR IGNORE INTO admissions VALUES(?,?,?,?,?)",
                        (b64(hashlib.sha256(raw).digest()), sid, peer, intro["expires"], prekey))
        self.db.commit()

    def _finish_admission(self, record):
        draft = self.db.execute("SELECT * FROM admissions WHERE delivery=?", (record["delivery"],)).fetchone()
        if draft is None:
            return
        if draft["sid"] != record["session"]:
            raise DropFitError("ADMISSION_CONFLICT")
        self.db.execute("INSERT OR IGNORE INTO drops(sid,peer,expires,status) VALUES(?,?,?,'connected')",
                        (draft["sid"], draft["peer"], draft["expires"]))
        self.db.commit()
        self._ensure_scope(draft["sid"])
        self._discard_invites("prekey=?", (draft["prekey"],))
        self.db.execute("DELETE FROM admissions WHERE delivery=?", (record["delivery"],))
        self.db.commit()

    def _discard_invites(self, condition, params):
        for invite in self.db.execute("SELECT * FROM invites WHERE " + condition, params).fetchall():
            self.bridge.call("discardInvite", prekey=invite["prekey"])
            (self.relay.root / "invites" / (invite["token"] + ".invite")).unlink(missing_ok=True)
            self.db.execute("DELETE FROM invites WHERE token=?", (invite["token"],))
            self.db.commit()

    def _expire_invites(self):
        self._discard_invites("expires<=?", (self.clock(),))

    def _apply(self, record):
        sid = record["session"]
        delivery = record["delivery"]
        self._live(sid)
        if self.db.execute("SELECT 1 FROM processed WHERE delivery=?", (delivery,)).fetchone():
            self.bridge.call("ackReceive", delivery=delivery)
            return
        payload = _json(unb64(record["plaintext"]))
        kind = payload.get("type")
        row = self._row(sid)
        if kind == "hello" and set(payload) == {"type"}:
            pass
        elif kind == "packet" and set(payload) == {"type", "note", "manifest"}:
            manifest = payload["manifest"]
            if (row["manifest"] or not isinstance(payload["note"], str) or len(payload["note"].encode()) > 4096
                    or not isinstance(manifest, dict) or set(manifest) != {"name", "size", "sha256", "chunks"}
                    or type(manifest["size"]) is not int or not 0 <= manifest["size"] <= MAX_FILE
                    or type(manifest["chunks"]) is not int
                    or manifest["chunks"] != max(1, (manifest["size"] + CHUNK - 1) // CHUNK)
                    or not isinstance(manifest["name"], str) or not 0 < len(manifest["name"]) <= 128
                    or any(c in manifest["name"] for c in "/\\\0")
                    or not isinstance(manifest["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])):
                raise DropFitError("INVALID_MANIFEST")
            _private_directory(self.root / sid, create=True)
            self.db.execute("UPDATE drops SET note=?,manifest=?,status='receiving' WHERE sid=?",
                            (payload["note"], json.dumps(manifest), sid))
        elif kind == "chunk" and set(payload) == {"type", "index", "data"}:
            if not row["manifest"]:
                raise DropFitError("MISSING_MANIFEST")
            manifest = json.loads(row["manifest"])
            index = payload["index"]
            if type(index) is not int or not 0 <= index < manifest["chunks"]:
                raise DropFitError("CHUNK_INDEX")
            data = unb64(payload["data"])
            expected = min(CHUNK, max(0, manifest["size"] - index * CHUNK))
            if len(data) != expected:
                raise DropFitError("CHUNK_SIZE")
            part = self.root / sid / f"{index:04d}.part"
            if part.exists() and _read(part, CHUNK) != data:
                raise DropFitError("CHUNK_CONFLICT")
            _write(part, data)
            self._assemble(sid, manifest)
        else:
            self._apply_task(sid, payload)
        self.db.execute("INSERT INTO processed(delivery,sid) VALUES(?,?)", (delivery, sid))
        self.db.commit()
        self.bridge.call("ackReceive", delivery=delivery)

    def _assemble(self, sid, manifest):
        parts = [self.root / sid / f"{i:04d}.part" for i in range(manifest["chunks"])]
        target = self.root / sid / "selected.bin"
        if target.exists():
            data = _read(target, MAX_FILE)
            if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                raise DropFitError("FILE_INTEGRITY")
            for part in parts:
                part.unlink(missing_ok=True)
            self.db.execute("UPDATE drops SET status='ready' WHERE sid=?", (sid,))
            return
        if not all(p.exists() for p in parts):
            return
        data = b"".join(_read(p, CHUNK) for p in parts)
        if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise DropFitError("FILE_INTEGRITY")
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
            if (set(payload) != {"type", "task_id", "operation", "file_sha256", "permissions", "description"}
                    or payload["operation"] != "sha256-selected-file"
                    or not isinstance(payload["description"], str) or len(payload["description"].encode()) > 4096
                    or not isinstance(payload["permissions"], list)
                    or any(not isinstance(p, str) for p in payload["permissions"])
                    or not row["manifest"]
                    or payload["file_sha256"] != json.loads(row["manifest"])["sha256"]):
                raise DropFitError("INVALID_TASK")
            _id(payload["task_id"])
            encoded = json.dumps(payload, sort_keys=True)
            if row["task_id"]:
                if row["task_id"] != payload["task_id"] or row["task"] != encoded:
                    raise DropFitError("TASK_CONFLICT")
                return  # Same logical task, even when re-encrypted, preserves its state.
            permitted = payload["permissions"] == ["read_files"] and "read_files" in self.allowed
            self.db.execute("UPDATE drops SET task=?,task_id=?,task_state=? WHERE sid=?",
                            (encoded, payload["task_id"], "pending" if permitted else "refused", sid))
        elif payload.get("type") == "result":
            if (set(payload) != {"type", "task_id", "digest"} or payload["task_id"] != row["task_id"]
                    or not row["manifest"] or payload["digest"] != json.loads(row["manifest"])["sha256"]):
                raise DropFitError("INVALID_RESULT")
            self.db.execute("UPDATE drops SET result=?,task_state='complete' WHERE sid=?",
                            (payload["digest"], sid))
        else:
            raise DropFitError("UNSUPPORTED_PAYLOAD")

    def send_task(self, sid, description, *, permissions=("read_files",)):
        with self.scope():
            row = self._live(sid)
            if not row["manifest"] or row["task_id"] or not isinstance(description, str):
                raise DropFitError("TASK_CONFLICT")
            task_id = b64(secrets.token_bytes(16))
            payload = {"type": "task", "task_id": task_id, "operation": "sha256-selected-file",
                       "file_sha256": json.loads(row["manifest"])["sha256"],
                       "permissions": list(permissions), "description": description}
            if len(description.encode()) > 4096:
                raise DropFitError("TASK_LIMIT")
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
                raise DropFitError("TASK_NOT_APPROVABLE")
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
            raise DropFitError("RECIPIENT_PERMISSION_REFUSED")

    def execute_approved(self, sid):
        with self.scope():
            row = self._live(sid)
            if row["task_state"] != "approved" or row["executions"]:
                raise DropFitError("APPROVAL_REQUIRED_OR_ALREADY_CLAIMED")
            self._permission(sid, row)  # Approval never widens a later local denial.
            self.db.execute("BEGIN IMMEDIATE")
            claimed = self.db.execute(
                "UPDATE drops SET task_state='running',executions=executions+1 "
                "WHERE sid=? AND task_state='approved' AND executions=0", (sid,))
            self.db.commit()
            if claimed.rowcount != 1:
                raise DropFitError("ALREADY_CLAIMED")
            # This is the entire bounded executor. No prompt, shell or model receives the bytes.
            data = _read(self.root / sid / "selected.bin", MAX_FILE)
            digest = hashlib.sha256(data).hexdigest()
            if digest != json.loads(row["manifest"])["sha256"]:
                raise DropFitError("FILE_INTEGRITY")
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
            self.bridge.call("forget", session=sid)
            folder = self.root / sid
            if folder.exists():
                _private_directory(folder)
                for path in folder.iterdir():
                    # A surprising directory/link is a failed cleanup, never a
                    # license to traverse or remove somebody else's files.
                    if not stat.S_ISREG(path.lstat().st_mode) or path.lstat().st_nlink != 1:
                        raise DropFitError("UNEXPECTED_CARRIER")
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
                raise DropFitError("EXISTING_FORGET_INCOMPLETE")
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
            raise DropFitError("DROP_FORGET_INCOMPLETE") from exc
        finally:
            memory.close()

    def delete(self, sid):
        with self.scope():
            self._forget(sid, "deleted")

    def sweep(self):
        with self.scope():
            self._expire()
            return {r["sid"]: r["status"] for r in self.db.execute("SELECT sid,status FROM drops")}
