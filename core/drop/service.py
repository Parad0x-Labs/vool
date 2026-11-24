"""Bounded local relay and Drop carrier owner. UNREVIEWED: not for real messages.

Off unless ``VOOL_DROP=1``. Drop content is never admitted into general memory: a
Drop's note, file and task live only in this home's ``drop/`` folder and its
encrypted session store, and expiry or delete removes them together with any
carrier that names the Drop. One packet (note plus one selected file up to
10 MiB) and one selected-file digest task per Drop.

The relay is any folder both people can reach (a shared or synced folder). It
only ever holds ciphertext addressed by public fingerprints, so it does not have to
be private: a folder a sync service made (mode 755) is as good as a 700 one. What
Drop keeps on this computer (note, file name, file, task) is sealed with a key
derived from the vault key, which lives in the system keychain where there is one.
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

from core.drop import DropError, people, require_enabled
from core.drop.client import DropClient
from core.drop.session import b64, unb64

MAX_FILE = 10 * 1024 * 1024
CHUNK = 40 * 1024
TTL = 7 * 86400 * 1000
MAX_CHUNKS = MAX_FILE // CHUNK
# Frames of one Drop that arrived too far ahead of the ones before them. A whole file is
# at most MAX_CHUNKS + a few frames, so this holds any order a synced folder really makes.
AHEAD_LIMIT = MAX_CHUNKS + 64
# Records one person may keep waiting in the shared encrypted inbox, over all their Drops.
DEFER_PER_PEER = 64
# Live Drops one person may have open here at once.
DROPS_PER_PEER = 32
# One-use invites kept published for each contact, and when one is renewed.
CONTACT_SLOTS = 3
SLOT_RENEW = 4 * 86400 * 1000
_SEALED = "enc1:"
_SEALED_COLUMNS = ("note", "manifest", "task", "result")
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


def _relay_directory(path: Path, *, create=False):
    """A folder inside the shared relay. The relay is an untrusted carrier that a sync
    service may have created, so its mode is not a boundary and is not demanded. What is
    demanded: a real directory (never a link someone left there) that belongs to this user."""
    if create:
        with contextlib.suppress(FileExistsError):
            path.mkdir(mode=0o700)
    try:
        mode = path.lstat()
    except OSError as exc:
        raise DropError("MISSING_RELAY") from exc
    if not stat.S_ISDIR(mode.st_mode):
        raise DropError("RELAY_NOT_FOLDER")
    if mode.st_uid != os.getuid():
        raise DropError("RELAY_NOT_YOURS")


def check_relay(path) -> Path:
    """The shared folder the owner chose, resolved once. It must already exist: a typed
    path that is not there is a typing mistake, never a folder to create."""
    try:
        root = Path(path).expanduser()
        if not root.is_absolute():
            raise DropError("RELAY_REQUIRED")
        root = root.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise DropError("MISSING_RELAY") from exc
    _relay_directory(root)
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        raise DropError("RELAY_READ_ONLY")
    return root


def _slot_name(owner, peer, slot) -> str:
    """Where ``owner`` keeps invite number ``slot`` for ``peer`` in the shared folder. Both
    sides can work it out from the two fingerprints; knowing it grants nothing."""
    return hashlib.sha256(f"vool-drop-slot-v1|{owner}|{peer}|{slot}".encode()).hexdigest()[:32]


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
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DropError("INPUT_UNAVAILABLE") from exc
    if len(data) > limit:
        raise DropError("INPUT_LIMIT")
    return data


def _json(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        # Deep nesting is malformed input from the folder or a peer, never a local fault.
        raise DropError("INVALID_FORMAT") from exc
    if not isinstance(value, dict):
        raise DropError("INVALID_FORMAT")
    return value


class LocalRelay:
    def __init__(self, root, *, create=False):
        require_enabled()
        self.root = Path(root).absolute()
        if create:
            # The Settings controller checks the folder exists (check_relay) before it gets
            # here, so only the command-line laboratory ever makes the folder itself.
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _relay_directory(self.root)
        if create:
            _relay_directory(self.root / "invites", create=True)

    def put(self, peer, request, wire):
        # Fingerprints are public routing metadata, never an authorization grant.
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", peer):
            raise DropError("INVALID_PEER")
        _id(request)
        if len(wire) > 110000:
            raise DropError("INPUT_LIMIT")
        _relay_directory(self.root)
        mailbox = self.root / peer
        _relay_directory(mailbox, create=True)
        digest = hashlib.sha256(wire).hexdigest()
        path = mailbox / (f"{time.time_ns():020d}-{request}-{digest}.wire")
        _write(path, wire)
        return path

    def messages(self, peer):
        _relay_directory(self.root)
        mailbox = self.root / peer
        if not os.path.lexists(mailbox):
            return []
        _relay_directory(mailbox)
        # Oldest first and bounded: a flooded mailbox delays later frames but
        # never stops this home from reading the ones it can process. Only regular
        # files count: a directory or link someone left can never be consumed, so it
        # must not take a place in the window.
        frames = []
        for path in mailbox.glob("*.wire"):
            with contextlib.suppress(OSError):
                if stat.S_ISREG(path.lstat().st_mode):
                    frames.append(path)
        return sorted(frames)[:1024]


# Authenticated payloads or frames that are not acceptable here. Each one is
# consumed (recorded and removed from the relay or recovery queue) so a single
# bad, forged, replayed or late frame can never block the deliveries after it.
# Any other failure (busy store, disk error) leaves the frame for the next poll.
_REJECTED = frozenset({
    "INVALID_FORMAT", "NON_CANONICAL", "UNSUPPORTED_PROFILE", "INVALID_KEY", "AUTH_FAILED",
    "IDENTITY_MISMATCH", "SELF_SESSION", "UNKNOWN_PREKEY", "PREKEY_UNAVAILABLE", "EXPIRED", "REPLAY",
    "SKIP_LIMIT", "INVALID_COUNTER", "WRONG_SESSION", "UNKNOWN_SESSION", "SESSION_REVOKED", "PAYLOAD_LIMIT",
    "SESSION_EXHAUSTED", "CHAIN_EXHAUSTED", "INPUT_LIMIT", "INVALID_ID", "UNKNOWN_DROP", "DROP_REVOKED",
    "UNKNOWN_INVITE", "INVALID_MANIFEST", "CHUNK_INDEX", "CHUNK_SIZE", "CHUNK_CONFLICT",
    "INVALID_TASK", "TASK_CONFLICT", "INVALID_RESULT", "UNSUPPORTED_PAYLOAD",
    "ADMISSION_CONFLICT", "FILE_INTEGRITY", "INVALID_BASE64", "PEER_QUOTA", "INVALID_RECEIPT",
})


# This home's own queues are full, or a pending forget barrier withholds every
# Drop until another Drop's cleanup finishes. The frame is left in the folder for a
# later poll; it never stops the frames behind it and is never consumed unread.
_HELD = frozenset({"STORE_LIMIT", "ADMISSION_QUOTA", "DROP_WITHHELD"})


# Authentic frames that arrived ahead of the frame they depend on (a synced
# folder can deliver files out of order), or while a pending forget barrier
# withholds this Drop. They stay in the encrypted inbox and are retried on the
# next poll; a forgotten or expired Drop discards them.
_DEFERRED = frozenset({"MISSING_MANIFEST", "TASK_BEFORE_PACKET", "DROP_WITHHELD"})


def _discard(path: Path):
    # Whatever another relay user put in this mailbox (even a directory or an
    # unremovable entry) is skipped, never allowed to stop the poll.
    with contextlib.suppress(OSError):
        path.unlink()


def _names_drop(path: Path, sid) -> bool:
    """Whether a relay file is a frame of this Drop. Anything unreadable is not."""
    try:
        wire = _json(_read(path, 110000))
    except (DropError, OSError):
        return False
    intro = wire.get("intro")
    return wire.get("sid") == sid or (isinstance(intro, dict) and intro.get("sid") == sid)


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


_KEY_CACHE: dict[str, bytes] = {}


def _keychain_name(home: Path) -> str:
    # One key per VOOL profile: two profiles on one computer never share or overwrite it.
    return "vool_drop_vault_key_" + hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]


def _protected_vault_key(home: Path, path: Path) -> bytes:
    """The vault key of the running profile, kept in VOOL's credential store (the system
    keychain where there is one) instead of in a file beside the data it protects.

    A key file left by an earlier version is moved there and removed only after the stored
    copy was read back. A store that has data but whose key cannot be read is never given a
    new key: that would only turn "cannot read right now" into "lost for good"."""
    from core import credential_store

    name = _keychain_name(home)
    cached = _KEY_CACHE.get(name)
    if cached is not None and not path.exists():
        return cached
    try:
        with credential_store.strict_reads():
            stored = credential_store.get_credential(name)
    except Exception as exc:
        raise DropError("VAULT_KEY_UNAVAILABLE") from exc
    if stored:
        key = unb64(stored, 32)
        if path.exists():
            if _vault_key(path) != key:
                raise DropError("VAULT_KEY_CONFLICT")
            _scrub(path)
    else:
        if path.exists():
            key = _vault_key(path)
        elif (path.parent / "sessions" / "drop.sqlite").exists():
            raise DropError("VAULT_KEY_UNAVAILABLE")
        else:
            key = secrets.token_bytes(32)
        try:
            credential_store.store_credential(name, b64(key), label="VOOL Drop")
            with credential_store.strict_reads():
                back = credential_store.get_credential(name)
        except Exception as exc:
            raise DropError("VAULT_KEY_UNAVAILABLE") from exc
        if back != b64(key):
            raise DropError("VAULT_KEY_UNAVAILABLE")
        if path.exists():
            _scrub(path)
    _KEY_CACHE[name] = key
    return key


def _existing_vault_key(home: Path, crypto: Path) -> bytes | None:
    """The key of a session store that already exists, found where DropHome keeps it:
    the credential store for the running profile, a file for any other home. Never makes
    a key or a store. None when there is no store to open."""
    from core.runtime_paths import active_vool_home

    if not (crypto / "sessions" / "drop.sqlite").exists():
        return None
    path = crypto / "vault.key"
    if home.resolve() == active_vool_home().resolve():
        return _protected_vault_key(home, path)
    return _vault_key(path) if path.exists() else None


def _scrub(path: Path):
    with contextlib.suppress(OSError):
        with open(path, "r+b") as out:
            out.write(b"\0" * 32)
            out.flush()
            os.fsync(out.fileno())
    path.unlink(missing_ok=True)


class _Sealer:
    """AES-256-GCM for what Drop keeps on this computer. The key is derived from the vault
    key, so it is never the key that seals the session store itself."""

    def __init__(self, vault_key: bytes):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        self._aes = AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                                info=b"vool-drop-content-v1").derive(bytes(vault_key)))

    def seal(self, aad: str, data: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aes.encrypt(nonce, data, aad.encode())

    def open(self, aad: str, blob: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag

        try:
            return self._aes.decrypt(blob[:12], blob[12:], aad.encode())
        except (InvalidTag, ValueError) as exc:
            raise DropError("LOCAL_COPY_DAMAGED") from exc

    def text(self, aad: str, value: str) -> str:
        return _SEALED + b64(self.seal(aad, value.encode())) if value else ""

    def plain(self, aad: str, value: str) -> str:
        if not value.startswith(_SEALED):
            return value  # written before sealing existed
        import base64

        raw = value[len(_SEALED):]
        try:
            blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except ValueError as exc:
            raise DropError("LOCAL_COPY_DAMAGED") from exc
        return self.open(aad, blob).decode()


def discard_local_invite_tokens(home, tokens):
    """Drop pairing tokens from one home's local invite store, never the relay.

    Local hygiene for a feature that issued Drop invites and is being turned
    off: the relay folder keeps its bytes, Drop is never enabled, and a home
    without a Drop store is left untouched instead of being created. The
    store is changed under Drop's actor lock, so a live Drop actor and this
    helper never touch the same database and session store at once.
    """
    root = Path(home).absolute()/"drop"
    db = root/"drops.db"
    unique = sorted({str(token) for token in tokens if token})
    if not unique or not db.exists():
        return
    _private_directory(root)
    # Same order as DropHome.scope: the process lock first, then the actor file lock.
    with _HOME_LOCK:
        lock = (root/"actor.lock").open("a+")
        try:
            os.chmod(root/"actor.lock", 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            marks = ",".join("?" for _ in unique)
            con = sqlite3.connect(db, timeout=15)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(f"SELECT token, prekey FROM invites WHERE token IN ({marks})", unique).fetchall()
                if not rows:
                    return
                crypto = root/"crypto"
                key = _existing_vault_key(root.parent, crypto)
                if key is not None:
                    client = DropClient(crypto/"sessions", key)
                    try:
                        for row in rows:
                            client.discard_invite(row["prekey"])
                    finally:
                        client.close()
                with con:
                    for row in rows:
                        con.execute("DELETE FROM invites WHERE token=?", (row["token"],))
            finally:
                con.close()
        finally:
            lock.close()


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
        # The running profile keeps its key in the credential store. A home named by a test
        # or the command-line laboratory has no credential store of its own and keeps a file.
        # One opener at a time: the background check and a Settings request may both be the
        # first to open a fresh profile, and only one of them may make its key and identity.
        with _HOME_LOCK:
            key = (_protected_vault_key(self.home, crypto / "vault.key") if self._own_home
                   else _vault_key(crypto / "vault.key"))
            self._sealer = _Sealer(key)
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
            CREATE TABLE IF NOT EXISTS memory_erased (sid TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS contacts (
                peer TEXT PRIMARY KEY, name TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
                added INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS contact_slots (
                peer TEXT NOT NULL, slot INTEGER NOT NULL, token TEXT NOT NULL, bundle BLOB NOT NULL,
                PRIMARY KEY(peer,slot));
            CREATE TABLE IF NOT EXISTS used_bundles (id TEXT PRIMARY KEY, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS profile (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        have = {row[1] for row in self.db.execute("PRAGMA table_info(drops)")}
        for column, kind in (("sent", "INTEGER NOT NULL DEFAULT 0"), ("created", "INTEGER NOT NULL DEFAULT 0"),
                             ("delivered", "INTEGER NOT NULL DEFAULT 0"),
                             ("receipt_due", "INTEGER NOT NULL DEFAULT 0"), ("saved", "TEXT NOT NULL DEFAULT ''")):
            if column not in have:
                # Another opener of this profile may have added it a moment ago.
                with contextlib.suppress(sqlite3.OperationalError):
                    self.db.execute(f"ALTER TABLE drops ADD COLUMN {column} {kind}")
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
        row = dict(row)
        for column in _SEALED_COLUMNS:
            row[column] = self._sealer.plain(f"{sid}|{column}", row[column])
        return row

    def _update(self, sid, **values):
        """Write columns of one Drop; the ones that hold content are sealed first."""
        for column in values:
            if column in _SEALED_COLUMNS:
                values[column] = self._sealer.text(f"{sid}|{column}", values[column])
        self.db.execute("UPDATE drops SET " + ",".join(f"{c}=?" for c in values) + " WHERE sid=?",
                        (*values.values(), sid))

    def _live(self, sid):
        from core.memory.revocation import authorize_payload

        row = self._row(sid)
        if row["status"] in {"deleted", "expired", "incomplete"}:
            raise DropError("DROP_REVOKED")
        if self.clock() >= row["expires"]:
            self._expire_one(sid)
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
            token, raw = self._issue_invite(peer)
            _relay_directory(self.relay.root / "invites", create=True)
            _write(self.relay.root / "invites" / (token + ".invite"), raw)
            return "vool-drop://invite/" + token

    def _issue_invite(self, peer):
        token = secrets.token_hex(16)
        raw = self.client.issue_invite(now=self.clock(), ttl=TTL)
        body = _json(raw)["body"]
        self.db.execute("INSERT INTO invites VALUES(?,?,?,?)", (token, body["id"], peer, body["expires"]))
        self.db.commit()
        return token, raw

    # ---- people -------------------------------------------------------------------

    def _contact_name(self, row) -> str:
        return self._sealer.plain(f"contact|{row['peer']}", row["name"])

    def _person(self, peer) -> dict:
        row = self.db.execute("SELECT * FROM contacts WHERE peer=?", (peer,)).fetchone()
        return {"fingerprint": peer, "name": self._contact_name(row) if row else "", "tag": people.tag(peer),
                "verified": bool(row and row["verified"]), "known": row is not None}

    def me(self) -> dict:
        with self.scope():
            row = self.db.execute("SELECT value FROM profile WHERE name='name'").fetchone()
            name = self._sealer.plain("profile|name", row["value"]) if row else ""
            return {"fingerprint": self.fingerprint, "name": name, "tag": people.tag(self.fingerprint),
                    "safety_number": people.safety_number(self.fingerprint),
                    "card": people.make_card(self.fingerprint, name)}

    def set_name(self, name) -> dict:
        """The name this VOOL suggests on its card. The other person may call you anything."""
        name = people.clean_name(name)
        with self.scope():
            self.db.execute("INSERT OR REPLACE INTO profile VALUES('name',?)",
                            (self._sealer.text("profile|name", name),))
            self.db.commit()
        return self.me()

    def card_preview(self, card) -> dict:
        """What a card says, before anything is saved."""
        peer, suggested = people.read_card(card)
        if peer == self.fingerprint:
            raise DropError("SELF_CARD")
        with self.scope():
            known = self._person(peer)
        return {"fingerprint": peer, "suggested": suggested, "tag": people.tag(peer),
                "safety_number": people.safety_number(peer), "known_as": known["name"]}

    def _name_free(self, name, peer):
        """One name, one key. The same or a look-alike name on another key is refused, and so
        is another key that was made to show the same word tag."""
        for row in self.db.execute("SELECT * FROM contacts WHERE peer<>?", (peer,)).fetchall():
            relation = people.name_relation(name, self._contact_name(row))
            if relation == "same":
                raise DropError("NAME_TAKEN")
            if relation == "lookalike":
                raise DropError("NAME_LOOKALIKE")
            if people.tag(row["peer"]) == people.tag(peer):
                raise DropError("TAG_COLLISION")

    def contact_add(self, card, name="", *, verified=False) -> dict:
        peer, suggested = people.read_card(card)
        if peer == self.fingerprint:
            raise DropError("SELF_CARD")
        name = people.clean_name(name or suggested)
        if type(verified) is not bool:
            raise DropError("INVALID_INPUT")
        with self.scope():
            if self.db.execute("SELECT 1 FROM contacts WHERE peer=?", (peer,)).fetchone():
                raise DropError("CONTACT_EXISTS")
            if self.db.execute("SELECT count(*) FROM contacts").fetchone()[0] >= 256:
                raise DropError("CONTACT_LIMIT")
            self._name_free(name, peer)
            self.db.execute("INSERT INTO contacts VALUES(?,?,?,?)",
                            (peer, self._sealer.text(f"contact|{peer}", name), int(verified), self.clock()))
            self.db.commit()
            self._publish_contact_invites()
            return self._person(peer)

    def _contact(self, peer):
        people.check_fingerprint(peer)
        row = self.db.execute("SELECT * FROM contacts WHERE peer=?", (peer,)).fetchone()
        if row is None:
            raise DropError("UNKNOWN_CONTACT")
        return row

    def contact_rename(self, peer, name) -> dict:
        name = people.clean_name(name)
        with self.scope():
            self._contact(peer)
            self._name_free(name, peer)
            self.db.execute("UPDATE contacts SET name=? WHERE peer=?",
                            (self._sealer.text(f"contact|{peer}", name), peer))
            self.db.commit()
            return self._person(peer)

    def contact_verify(self, peer, verified) -> dict:
        """The owner says they compared the safety number with this person directly."""
        if type(verified) is not bool:
            raise DropError("INVALID_INPUT")
        with self.scope():
            self._contact(peer)
            self.db.execute("UPDATE contacts SET verified=? WHERE peer=?", (int(verified), peer))
            self.db.commit()
            return self._person(peer)

    def contact_remove(self, peer):
        """Remove (block) a person: their Drops here are deleted, every invite made for them
        stops working, and nothing from that key is accepted again until they are re-added."""
        with self.scope():
            self._contact(peer)
            self._discard_invites("peer=?", (peer,))
            for slot in range(CONTACT_SLOTS):
                (self.relay.root / "invites" / (_slot_name(self.fingerprint, peer, slot) + ".invite")).unlink(
                    missing_ok=True)
            self.db.execute("DELETE FROM contact_slots WHERE peer=?", (peer,))
            self.db.execute("DELETE FROM admissions WHERE peer=?", (peer,))
            self.db.execute("DELETE FROM contacts WHERE peer=?", (peer,))
            self.db.commit()
            unfinished = False
            for row in self.db.execute(
                    "SELECT sid FROM drops WHERE peer=? AND status NOT IN ('deleted','expired')", (peer,)).fetchall():
                try:
                    self._forget(row["sid"], "deleted")
                except DropError as exc:
                    if exc.code != "DROP_FORGET_INCOMPLETE":
                        raise
                    unfinished = True
            if unfinished:
                raise DropError("DROP_FORGET_INCOMPLETE")

    def contacts(self) -> list[dict]:
        with self.scope():
            out = []
            for row in self.db.execute("SELECT peer FROM contacts ORDER BY added,peer").fetchall():
                person = self._person(row["peer"])
                person["safety_number"] = people.safety_number(row["peer"])
                person["ready"] = any(
                    (self.relay.root / "invites" / (_slot_name(row["peer"], self.fingerprint, slot) + ".invite")).exists()
                    for slot in range(CONTACT_SLOTS))
                out.append(person)
            return sorted(out, key=lambda person: person["name"].casefold())

    def _open_drops(self, peer) -> int:
        return self.db.execute(
            "SELECT count(*) FROM drops WHERE peer=? AND status NOT IN ('deleted','expired')", (peer,)).fetchone()[0]

    def _publish_contact_invites(self):
        """Keep a few one-use invites in the shared folder for each contact, so they can
        start a Drop without asking first. Each is signed by this VOOL and only this
        contact's key can use it. A used or ageing one is replaced; a file that went
        missing is put back as it was."""
        now = self.clock()
        self._expire_invites()
        folder = self.relay.root / "invites"
        _relay_directory(folder, create=True)
        for contact in self.db.execute("SELECT peer FROM contacts").fetchall():
            peer = contact["peer"]
            if self._open_drops(peer) >= DROPS_PER_PEER:
                continue
            for slot in range(CONTACT_SLOTS):
                path = folder / (_slot_name(self.fingerprint, peer, slot) + ".invite")
                current = self.db.execute(
                    "SELECT s.bundle, i.expires FROM contact_slots s JOIN invites i ON i.token=s.token "
                    "WHERE s.peer=? AND s.slot=?", (peer, slot)).fetchone()
                if current and current["expires"] - now > SLOT_RENEW:
                    if not path.exists():
                        _write(path, current["bundle"])
                    continue
                try:
                    token, raw = self._issue_invite(peer)
                except DropError as exc:
                    if exc.code != "STORE_LIMIT":
                        raise
                    return
                _write(path, raw)
                self.db.execute("INSERT OR REPLACE INTO contact_slots VALUES(?,?,?,?)", (peer, slot, token, raw))
                self.db.commit()

    def send_to(self, peer, note, name, data):
        """Send a note and one file to a contact: opens a fresh Drop with one of the invites
        they keep published for this VOOL, then sends on it."""
        if not isinstance(note, str) or len(note.encode()) > 4096:
            raise DropError("NOTE_LIMIT")
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_FILE:
            raise DropError("INPUT_LIMIT")
        if not isinstance(name, str) or not name or len(name) > 128 or any(c in name for c in "/\\\0"):
            raise DropError("INVALID_FILENAME")
        with self.scope():
            self._contact(peer)
            now = self.clock()
            self.db.execute("DELETE FROM used_bundles WHERE expires<=?", (now,))
            self.db.commit()
            sid, seen = None, False
            for slot in range(CONTACT_SLOTS):
                path = self.relay.root / "invites" / (_slot_name(peer, self.fingerprint, slot) + ".invite")
                try:
                    raw = _read(path, 110000)
                    body = _json(raw)["body"]
                    bundle, expires = _id(body["id"]), body["expires"]
                    if type(expires) is not int:
                        raise DropError("INVALID_FORMAT")
                except (DropError, KeyError, TypeError):
                    continue
                seen = True
                if self.db.execute("SELECT 1 FROM used_bundles WHERE id=?", (bundle,)).fetchone():
                    continue
                try:
                    reply = self.client.start(invite=raw, peer_fingerprint=peer,
                                              plaintext=b'{"type":"hello"}', now=now)
                except DropError as exc:
                    if exc.code in _REJECTED:
                        continue  # expired, or not signed by this contact's key
                    raise
                sid = reply["session"]
                self.db.execute("INSERT OR IGNORE INTO used_bundles VALUES(?,?)", (bundle, expires))
                self.db.execute(
                    "INSERT INTO drops(sid,peer,expires,status,created) VALUES(?,?,?,'connected',?)",
                    (sid, peer, expires, now))
                self.db.commit()
                self._ensure_scope(sid)
                self._flush()
                break
            if sid is None:
                # Their invites are all used: they have not opened VOOL since. Or none is
                # there: they have not added this VOOL yet, or the folder has not synced.
                raise DropError("CONTACT_BUSY" if seen else "CONTACT_NOT_READY")
        return sid, self.send_bytes(sid, note, name, data)

    def inbox(self) -> list[dict]:
        """Every live Drop with what a person needs to recognise it: who, what file, when."""
        with self.scope():
            self._expire()
            out = []
            for item in self.db.execute(
                    "SELECT sid,status FROM drops WHERE status NOT IN ('deleted','expired') ORDER BY rowid DESC"
            ).fetchall():
                if item["status"] == "incomplete":
                    out.append({"sid": item["sid"], "status": "incomplete"})
                    continue
                row = self._row(item["sid"])
                manifest = json.loads(row["manifest"]) if row["manifest"] else {}
                out.append({"sid": row["sid"], "status": row["status"], "person": self._person(row["peer"]),
                            "created": row["created"], "expires": row["expires"], "sent": bool(row["sent"]),
                            "name": manifest.get("name", ""), "size": manifest.get("size", 0),
                            "file": self._progress(row["sid"], row), "task_state": row["task_state"]})
            return out

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
            self.db.execute("INSERT INTO drops(sid,peer,expires,status,created) VALUES(?,?,?,'connected',?)",
                            (sid, peer, expires, self.clock()))
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
                self.db.execute(
                    "INSERT INTO drops(sid,peer,expires,status,created) VALUES(?,?,?,'connected',?)",
                    (row["session"], intro["recipient"], intro["expires"], self.clock()))
                self.db.commit()
                self._ensure_scope(row["session"])
            try:
                state = self._live(row["session"])
            except DropError as exc:
                # A Drop being deleted, expired or withheld keeps its queued send until its
                # cleanup removes it or the barrier lifts; it never stops this home's poll.
                if exc.code in {"DROP_REVOKED", "EXPIRED", "DROP_WITHHELD"}:
                    continue
                raise
            self.relay.put(state["peer"], row["request"], wire)
            # Relay publication is durable. It is not recipient/execution proof.
            self.client.acknowledge_send(row["request"])

    def _send(self, sid, payload):
        self._live(sid)
        self.client.send(session=sid, plaintext=json.dumps(payload).encode(), now=self.clock())
        self._flush()

    def send_packet(self, sid, note, file):
        path = Path(file)
        return self.send_bytes(sid, note, path.name, _read(path, MAX_FILE))

    def send_bytes(self, sid, note, name, data):
        """Send a note and one file's bytes on a connected Drop. Nothing is written to disk
        on this side except the sealed note and file name."""
        with self.scope():
            row = self._live(sid)
            if row["manifest"]:
                raise DropError("PACKET_CONFLICT")
            if not isinstance(note, str) or len(note.encode()) > 4096:
                raise DropError("NOTE_LIMIT")
            if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_FILE:
                raise DropError("INPUT_LIMIT")
            data = bytes(data)
            if not isinstance(name, str) or not name or len(name) > 128 or any(c in name for c in "/\\\0"):
                raise DropError("INVALID_FILENAME")
            manifest = {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                        "chunks": max(1, (len(data) + CHUNK - 1) // CHUNK)}
            self._update(sid, note=note, manifest=json.dumps(manifest), sent=1)
            self.db.commit()
            self._send(sid, {"type": "packet", "note": note, "manifest": manifest})
            for index in range(manifest["chunks"]):
                self._send(sid, {"type": "chunk", "index": index,
                                 "data": b64(data[index * CHUNK:(index + 1) * CHUNK])})
            return manifest

    def poll(self):
        """Read this home's mailbox. Returns one outcome per frame processed:
        ``received``, ``duplicate``, ``rejected`` (consumed without effect),
        ``deferred`` (kept in the encrypted inbox) or ``held`` (left in the folder
        because this home's queue is full or its Drops are withheld for now)."""
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
                    if exc.code in _HELD:
                        results.append("held")
                        continue
                    if exc.code not in _REJECTED:
                        raise
                    if exc.code == "SKIP_LIMIT" and self._park(raw):
                        # Too far ahead of the frames before it (a synced folder delivers in
                        # any order). It waits on this computer until those arrive.
                        results.append("waiting")
                    else:
                        results.append("rejected")
                    _discard(path)
                    continue
                results.append(self._apply(record))
                path.unlink()
            results += self._retry_parked()
            if "deferred" in results:
                results += self._recover()
            self._send_receipts()
            self._publish_contact_invites()
            return results

    def _park(self, raw) -> bool:
        """Keep a frame that is ahead of its Drop's window. It is ciphertext and stays so.
        Bounded per Drop, so forged frames can cost one Drop its room and nothing else."""
        packet = _json(raw)
        sid = packet.get("sid")
        try:
            self._live(sid)
        except DropError:
            return False
        folder = self.root / sid
        _private_directory(folder, create=True)
        index = packet.get("n") if type(packet.get("n")) is int else 0
        name = f"{index:05d}-{hashlib.sha256(raw).hexdigest()[:32]}.ahead"
        if not (folder / name).exists() and len(list(folder.glob("*.ahead"))) >= AHEAD_LIMIT:
            return False
        _write(folder / name, raw)
        return True

    def _retry_parked(self):
        outcomes = []
        for row in self.db.execute(
                "SELECT sid FROM drops WHERE status NOT IN ('deleted','expired','incomplete')").fetchall():
            folder = self.root / row["sid"]
            if not folder.is_dir():
                continue
            progressed = True
            while progressed:
                progressed = False
                for path in sorted(folder.glob("*.ahead")):
                    try:
                        raw = _read(path, 110000)
                        record = self._open_wire(raw, b64(hashlib.sha256(raw).digest()))
                    except DropError as exc:
                        if exc.code == "SKIP_LIMIT" or exc.code in _HELD:
                            continue
                        if exc.code not in _REJECTED and exc.code not in {"INPUT_UNAVAILABLE"}:
                            raise
                        _discard(path)
                        outcomes.append("rejected")
                        continue
                    outcomes.append(self._apply(record))
                    _discard(path)
                    progressed = True
        return outcomes

    def _send_receipts(self):
        """Tell the sender their file arrived whole. A peer on an older version ignores it."""
        for row in self.db.execute("SELECT sid FROM drops WHERE receipt_due=1 AND status='ready'").fetchall():
            sid = row["sid"]
            try:
                manifest = json.loads(self._live(sid)["manifest"])
                self._send(sid, {"type": "receipt", "sha256": manifest["sha256"]})
            except DropError as exc:
                if exc.code not in {"DROP_REVOKED", "EXPIRED", "DROP_WITHHELD", "SESSION_EXHAUSTED",
                                    "CHAIN_EXHAUSTED", "STORE_LIMIT"}:
                    raise
                if exc.code in {"DROP_WITHHELD", "STORE_LIMIT"}:
                    continue
            self.db.execute("UPDATE drops SET receipt_due=0 WHERE sid=?", (sid,))
            self.db.commit()

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
        if self._open_drops(invite["peer"]) >= DROPS_PER_PEER:
            raise DropError("PEER_QUOTA")
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
        self.db.execute(
            "INSERT OR IGNORE INTO drops(sid,peer,expires,status,created) VALUES(?,?,?,'connected',?)",
            (draft["sid"], draft["peer"], draft["expires"], self.clock()))
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
            if exc.code in _DEFERRED and (exc.code == "DROP_WITHHELD" or self._may_wait(sid)):
                return "deferred"
            if exc.code in _DEFERRED:
                exc = DropError("PEER_QUOTA")
            if exc.code not in _REJECTED:
                raise
            # Authentic but unacceptable here: consumed without effect.
            outcome = "rejected"
        self.db.execute("INSERT INTO processed(delivery,sid) VALUES(?,?)", (delivery, sid))
        self.db.commit()
        self.client.acknowledge_receive(delivery)
        return outcome

    def _may_wait(self, sid) -> bool:
        """Whether one more record of this Drop may wait in the encrypted inbox. The inbox is
        shared by every Drop, so each person gets a bounded share of it."""
        peer = self._row(sid)["peer"]
        mine = {row["sid"] for row in self.db.execute("SELECT sid FROM drops WHERE peer=?", (peer,))}
        waiting = sum(1 for record in self.client.pending_receives() if record["session"] in mine)
        return waiting <= DEFER_PER_PEER

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
            if row["sent"]:
                raise DropError("INVALID_MANIFEST")
            _private_directory(self.root / sid, create=True)
            self._update(sid, note=payload["note"], manifest=json.dumps(manifest), status="receiving")
            # Pieces that came before this header were kept; any that do not fit it go.
            for part in (self.root / sid).glob("*.part"):
                index = int(part.name[:4]) if part.name[:4].isdigit() else -1
                expected = min(CHUNK, max(0, manifest["size"] - index * CHUNK))
                if not 0 <= index < manifest["chunks"] or self._part_size(part) != expected:
                    part.unlink()
            self._assemble(sid, manifest)
            return
        if kind == "chunk" and set(payload) == {"type", "index", "data"}:
            index = payload["index"]
            data = unb64(payload["data"], 0, CHUNK)
            if row["sent"]:
                raise DropError("CHUNK_INDEX")
            if row["manifest"]:
                manifest = json.loads(row["manifest"])
                if type(index) is not int or not 0 <= index < manifest["chunks"]:
                    raise DropError("CHUNK_INDEX")
                if len(data) != min(CHUNK, max(0, manifest["size"] - index * CHUNK)):
                    raise DropError("CHUNK_SIZE")
            else:
                # A piece ahead of its header (a synced folder delivers in any order) is kept
                # as a sealed part straight away. It is this person's own Drop folder, bounded
                # by the largest file a Drop can carry, and it never waits in the shared inbox.
                manifest = None
                if type(index) is not int or not 0 <= index < MAX_CHUNKS:
                    raise DropError("CHUNK_INDEX")
                _private_directory(self.root / sid, create=True)
            part = self.root / sid / f"{index:04d}.part"
            kept = None
            if part.exists():
                with contextlib.suppress(DropError):
                    kept = self._sealer.open(f"{sid}|part|{index}", _read(part, CHUNK + 64))
            if kept is not None:
                if kept != data:
                    raise DropError("CHUNK_CONFLICT")
            elif not (manifest and (self.root / sid / "selected.bin").exists()):
                _write(part, self._sealer.seal(f"{sid}|part|{index}", data))
            if manifest:
                self._assemble(sid, manifest)
            return
        if kind == "receipt" and set(payload) == {"type", "sha256"}:
            if (not row["sent"] or not row["manifest"]
                    or payload["sha256"] != json.loads(row["manifest"])["sha256"]):
                raise DropError("INVALID_RECEIPT")
            self.db.execute("UPDATE drops SET delivered=1 WHERE sid=?", (sid,))
            return
        self._apply_task(sid, payload)

    @staticmethod
    def _part_size(part: Path) -> int:
        try:
            return part.lstat().st_size - 28  # nonce and tag
        except OSError:
            return -1

    def _assemble(self, sid, manifest):
        parts = [self.root / sid / f"{i:04d}.part" for i in range(manifest["chunks"])]
        target = self.root / sid / "selected.bin"
        if target.exists():
            try:
                self._file(sid, manifest)
            except DropError:
                target.unlink()
                self.db.execute("UPDATE drops SET status='damaged' WHERE sid=?", (sid,))
                return
            for part in parts:
                part.unlink(missing_ok=True)
            self.db.execute("UPDATE drops SET status='ready' WHERE sid=?", (sid,))
            return
        if not all(p.exists() for p in parts):
            return
        try:
            data = b"".join(self._sealer.open(f"{sid}|part|{i}", _read(p, CHUNK + 64)) for i, p in enumerate(parts))
        except DropError:
            data = b""  # a part on this disk no longer opens: the same end as a wrong file
        if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            # The pieces are all here and do not make the promised file. Keeping them would
            # leave the Drop "arriving" for good, so they go and the Drop says it failed.
            for part in parts:
                part.unlink()
            self.db.execute("UPDATE drops SET status='damaged' WHERE sid=?", (sid,))
            return
        _write(target, self._sealer.seal(f"{sid}|file", data))
        for part in parts:
            part.unlink()
        self.db.execute("UPDATE drops SET status='ready',receipt_due=1 WHERE sid=?", (sid,))

    def _file(self, sid, manifest) -> bytes:
        """The received file, opened from its sealed copy and checked against its header."""
        stored = _read(self.root / sid / "selected.bin", MAX_FILE + 64)
        try:
            data = self._sealer.open(f"{sid}|file", stored)
        except DropError:
            # A copy an earlier version wrote unsealed is still this file if it matches the
            # header exactly; it is sealed now. Anything else is damage.
            if len(stored) != manifest["size"] or hashlib.sha256(stored).hexdigest() != manifest["sha256"]:
                raise
            data = stored
            _write(self.root / sid / "selected.bin", self._sealer.seal(f"{sid}|file", data))
        if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise DropError("FILE_INTEGRITY")
        return data

    def file_bytes(self, sid) -> tuple[str, bytes]:
        """(name, bytes) of a file this home received whole. Local controller only."""
        with self.scope():
            row = self._live(sid)
            if row["status"] != "ready" or row["sent"] or not row["manifest"]:
                raise DropError("FILE_NOT_READY")
            manifest = json.loads(row["manifest"])
            return manifest["name"], self._file(sid, manifest)

    def save_file(self, sid, folder) -> Path:
        """Write the received file into ``folder`` under its own name, never over a file
        that is already there. The name came from the other person, so only its last part
        is used and it never starts with a dot."""
        name, data = self.file_bytes(sid)
        folder = Path(folder)
        if not folder.is_dir():
            raise DropError("SAVE_FOLDER_MISSING")
        safe = "".join(c for c in name if c.isprintable() and c not in '/\\:*?"<>|').strip().lstrip(".") or "file"
        stem, dot, suffix = safe.rpartition(".")
        if not dot or not stem:
            stem, suffix = safe, ""
        for attempt in range(1000):
            candidate = folder / (f"{stem}{'' if not attempt else f' ({attempt})'}{'.' + suffix if suffix else ''}")
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                continue
            except OSError as exc:
                raise DropError("SAVE_FAILED") from exc
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
            except OSError as exc:
                candidate.unlink(missing_ok=True)
                raise DropError("SAVE_FAILED") from exc
            with self.scope():
                self._update(sid, saved=candidate.name)
                self.db.commit()
            return candidate
        raise DropError("SAVE_FAILED")

    def _progress(self, sid, row) -> dict:
        """What a person needs to know about the file: whose it is and how far it got."""
        manifest = json.loads(row["manifest"]) if row["manifest"] else None
        if manifest is None:
            return {"state": "empty"}
        if row["sent"]:
            return {"state": "delivered" if row["delivered"] else "sent"}
        if row["status"] == "ready":
            return {"state": "ready", "saved": row["saved"]}
        if row["status"] == "damaged":
            return {"state": "damaged"}
        folder = self.root / sid
        have = sum(1 for i in range(manifest["chunks"]) if (folder / f"{i:04d}.part").exists())
        return {"state": "arriving", "have": have, "total": manifest["chunks"]}

    def show(self, sid):
        with self.scope():
            row = self._live(sid)
            out = {k: row[k] for k in ("sid", "status", "note", "manifest", "task", "task_state", "result",
                                       "executions", "expires", "created")}
            out["file"] = self._progress(sid, row)
            out["person"] = self._person(row["peer"])
            return out

    def _apply_task(self, sid, payload):
        if payload.get("type") == "lend":
            from core.lend.service import receive

            receive(self, sid, payload)
            return
        row = self._row(sid)
        if payload.get("type") == "task":
            if not row["manifest"]:
                raise DropError("TASK_BEFORE_PACKET")
            if (set(payload) != {"type", "task_id", "operation", "file_sha256", "permissions", "description"}
                    or payload["operation"] != "sha256-selected-file"
                    or not isinstance(payload["description"], str) or len(payload["description"].encode()) > 4096
                    or not isinstance(payload["permissions"], list)
                    or any(not isinstance(p, str) for p in payload["permissions"])
                    or not row["manifest"] or row["status"] not in {"receiving", "ready", "damaged"}
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
            self._update(sid, task=encoded, task_id=payload["task_id"],
                         task_state="pending" if permitted else "refused")
        elif payload.get("type") == "result":
            if (set(payload) != {"type", "task_id", "digest"} or row["task_state"] != "submitted"
                    or payload["task_id"] != row["task_id"]
                    or not row["manifest"] or payload["digest"] != json.loads(row["manifest"])["sha256"]):
                raise DropError("INVALID_RESULT")
            self._update(sid, result=payload["digest"], task_state="complete")
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
            self._update(sid, task=json.dumps(payload, sort_keys=True), task_id=task_id, task_state="submitted")
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

    def decline(self, sid, task_id):
        """A local refusal is durable; replaying the task cannot restore approval."""
        with self.scope():
            row = self._live(sid)
            if row["task_id"] != task_id or row["task_state"] not in {"pending", "approved"}:
                raise DropError("TASK_NOT_APPROVABLE")
            self.db.execute("UPDATE drops SET task_state='declined' WHERE sid=?", (sid,))
            self.db.commit()

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
            digest = hashlib.sha256(self._file(sid, json.loads(row["manifest"]))).hexdigest()
            self._update(sid, result=digest, task_state="complete")
            self.db.commit()
            self._send(sid, {"type": "result", "task_id": row["task_id"], "digest": digest})
            return digest

    def _expire(self):
        self._expire_invites()
        for row in self.db.execute("SELECT sid FROM drops WHERE expires<=? AND status NOT IN ('deleted','expired')",
                                   (self.clock(),)).fetchall():
            self._expire_one(row["sid"])

    def _expire_one(self, sid):
        # A cleanup that cannot finish keeps its barrier and its 'incomplete' state, which
        # Settings shows with Delete to try again. It never stops this home from opening,
        # polling or listing its other Drops.
        try:
            self._forget(sid, "expired")
        except DropError as exc:
            if exc.code != "DROP_FORGET_INCOMPLETE":
                raise

    def _forget(self, sid, state):
        row = self._row(sid)
        if row["status"] in {"deleted", "expired"}:
            return
        # Memory first, then the Drop's own state. A retry after the memory part
        # finished goes straight to the Drop's own cleanup.
        if self.db.execute("SELECT 1 FROM memory_erased WHERE sid=?", (sid,)).fetchone() is None:
            if self._memory_part_finished(sid):
                # An earlier attempt stopped between completing the barrier and writing
                # its row. Running the memory part again could never finish (the Drop's
                # chat is already deleted) and would withhold the home for good.
                self.db.execute("INSERT OR IGNORE INTO memory_erased(sid) VALUES(?)", (sid,))
                self.db.commit()
            else:
                self._erase_memory(sid)
        try:
            # The Drop's own state: session keys and inbox, received files, queued
            # frames. If this cannot finish, the Drop stays 'incomplete' (never served,
            # Delete offered again) and the rest of the home's memory keeps serving.
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
            # Purge current queued ciphertext on both sides of this local relay. Relay copies
            # are outside what a forget can promise (other people can read and write that
            # folder), so this is best effort: a file that is not this Drop's own frame (junk,
            # another user's file, an unreadable entry) or an unavailable mailbox is left
            # alone and never fails the cleanup.
            for peer in (self.fingerprint, row["peer"]):
                try:
                    queued = self.relay.messages(peer)
                except (DropError, OSError):
                    continue
                for path in queued:
                    if _names_drop(path, sid):
                        with contextlib.suppress(OSError):
                            path.unlink()
            self.db.execute("UPDATE drops SET status=? WHERE sid=?", (state, sid))
            self.db.commit()
            # Both databases run in WAL mode: the erased note, task, inbox copy and session
            # state stay readable in their -wal files until a checkpoint. Empty them now.
            with contextlib.suppress(sqlite3.Error, DropError):
                self.client.checkpoint()
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        except Exception as exc:
            # Not a successful deletion: the Drop stays 'incomplete' and is never served.
            raise DropError("DROP_FORGET_INCOMPLETE") from exc

    def _memory_part_finished(self, sid):
        """True only when the memory part of this Drop's forget provably finished: its
        barrier completed, nothing is pending anywhere in the home, the owner erasure for
        this Drop is complete and its chat is deleted. Any doubt answers False, so the
        memory part runs again and a failure keeps the home withheld."""
        from core.context_namespace import chat_namespace_deleted_for_identity
        from core.memory import revocation
        from core.vool_memory import VoolMemory, _normalize_forget_token

        try:
            # Raises while any forget in this home is still pending.
            if _normalize_forget_token(sid) not in revocation.snapshot(None, self.home):
                return False
            if not chat_namespace_deleted_for_identity(sid):
                return False
            memory = VoolMemory(runtime_home=self.home)
            try:
                return not memory.erasure_incomplete(sid)
            finally:
                memory.close()
        except Exception:
            return False

    def _erase_memory(self, sid):
        from core.context_retrieval import forget_session_memory
        from core.memory import entries, revocation
        from core.vool_memory import VoolMemory

        # Publish before *any* destructive action. Even an incomplete file/DB
        # sweep must make old memory/receipt copies stop serving immediately.
        # Existing semantic rows use hashed scope ids while other stores use
        # raw ids, and some readers check the reader's chat rather than the
        # row's origin, so only a home-wide fence covers every representation.
        # The fence is kept short instead: it ends as soon as every memory store
        # is erased, before the Drop's own cleanup. The completed token keeps
        # withholding anything that still names this Drop.
        operation = revocation.begin("", sid, self.home)
        memory = VoolMemory(runtime_home=self.home)
        owner_operation = memory.begin_erasure(sid, sid)
        try:
            self.db.execute(
                "UPDATE drops SET status='incomplete',note='',manifest='',task='',result='',task_id='',task_state='' WHERE sid=?",
                (sid,))
            self.db.commit()
            # A memory store that cannot finish its own forget keeps the home-wide
            # fence pending until Delete succeeds: nothing can tell which stored
            # copies still carry this Drop's words.
            status = {}
            forget_session_memory(sid, sid, source_context={"runtime_home": str(self.home), "chat_id": sid},
                                  store_status=status)
            if any(name.endswith("_failures") and value for name, value in status.items()):
                raise DropError("EXISTING_FORGET_INCOMPLETE")
            entries.forget_sessions_memory([sid])
            entries.delete_conversation_session(sid)
            memory.node_delete_for_session(sid)
            memory.occurrence_delete_for_chat(sid)
            memory.learned_delete_for_session(sid)
            memory.block_delete(summary_block(sid))
            memory.record_revocation(sid, chat_id=sid)
            memory.complete_erasure(owner_operation)
            revocation.complete(operation, self.home)
            self.db.execute("INSERT OR IGNORE INTO memory_erased(sid) VALUES(?)", (sid,))
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
