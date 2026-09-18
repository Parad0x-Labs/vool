"""Append-only, hash-chained, MAC'd journal with a durable HEAD.

One line per entry (JSON). Each entry carries ``seq`` (contiguous from 0), ``prev`` (the previous
entry's ``entry_hash``, empty at genesis), ``ts``, ``entry_hash`` = SHA-256 over
``prev + canonical(entry-without-hash-and-mac)``, and ``mac`` = HMAC-SHA-256 with the store's own
key over the canonical entry INCLUDING seq/prev/ts/entry_hash. ``HEAD`` records the count and the
last hash, written atomically after every append.

Commit ordering (what a crash can and cannot leave):

    append(line) -> fsync(journal) -> write HEAD.tmp -> fsync -> rename over HEAD -> fsync(dir)

A crash after the line is durable but before HEAD moves leaves HEAD one behind the tail; the next
append or ``verify`` repairs HEAD from the tail because the tail is the authority for what was
appended. A crash mid-line leaves a malformed last line; ``verify`` reports it, ``append``
refuses to extend a broken chain.

Tamper evidence, exactly what it is and is not:

- An edit to any recorded line fails its MAC (the line's own content is what the MAC signs), and
  breaks the hash link of every later line.
- Deleting entries from the tail leaves HEAD ahead of the file: ``tail_truncated``.
- Deleting the tail AND HEAD is reported as ``head_missing`` -- detectable as "someone removed
  the head", NOT as "these entries once existed". A copy of HEAD kept elsewhere is the only way to
  prove that, and this module does not provide one.
- A process running as the SAME USER can read ``journal.key`` and re-sign anything, including a
  wholly rewritten history. The key does not defend against the account that owns it; it defends
  the journal against edits made without opening the store as its owner (other users, tools that
  only know the file format, a model writing through a workspace tool). That is the whole claim.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # POSIX advisory locking; this lane ships on macOS/Linux.
    import fcntl
except ImportError:  # pragma: no cover - Windows lane is not this module's owner
    fcntl = None  # type: ignore[assignment]

GENESIS_PREV = ""
_UNSIGNED_KEYS = frozenset({"entry_hash", "mac"})
_HASH_EXCLUDED_KEYS = frozenset({"entry_hash", "mac"})
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
#: Per-store re-entrancy state for the process lock: flock is per open file description, so a
#: nested ``exclusive()`` on a fresh fd in the SAME thread would deadlock against itself. The
#: thread RLock serializes threads; this depth counter lets one thread nest.
_FLOCK_STATE: dict[str, dict[str, int]] = {}


class JournalError(RuntimeError):
    """Base class for typed journal failures."""


class JournalIntegrityError(JournalError):
    """The chain on disk does not verify; appending to it would launder the break."""


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    reason: str
    entries: int
    head_count: int | None
    first_bad_seq: int | None
    last_hash: str
    detail: str = ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(entry: dict[str, Any], *, exclude: frozenset[str]) -> bytes:
    body = {key: value for key, value in entry.items() if key not in exclude}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _entry_hash(prev: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256(prev.encode("utf-8") + canonical_bytes(entry, exclude=_HASH_EXCLUDED_KEYS)).hexdigest()


def _mac(key: bytes, entry: dict[str, Any]) -> str:
    return hmac.new(key, canonical_bytes(entry, exclude=frozenset({"mac"})), hashlib.sha256).hexdigest()


def _fsync_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_directory(path.parent)


class Journal:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.journal_path = self.root / "journal.jsonl"
        self.head_path = self.root / "HEAD"
        self.key_path = self.root / "journal.key"
        self.lock_path = self.root / "journal.lock"

    # -- locking -----------------------------------------------------------------------------
    def _thread_lock(self) -> threading.RLock:
        key = str(self.root.resolve()) if self.root.exists() else str(self.root)
        with _LOCKS_GUARD:
            lock = _LOCKS.get(key)
            if lock is None:
                lock = _LOCKS[key] = threading.RLock()
            return lock

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        """Thread + process exclusive section over the whole store (append, rollback, prune).
        Re-entrant within one thread: the outermost frame holds the flock, inner frames ride it."""
        self.root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
        key = str(self.root.resolve())
        with self._thread_lock():
            state = _FLOCK_STATE.setdefault(key, {"depth": 0, "fd": -1})
            if state["depth"] == 0:
                fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX)
                    except BaseException:
                        os.close(fd)
                        raise
                state["fd"] = fd
            state["depth"] += 1
            try:
                yield
            finally:
                state["depth"] -= 1
                if state["depth"] == 0:
                    fd = state.pop("fd", -1)
                    state["fd"] = -1
                    if fd >= 0:
                        if fcntl is not None:
                            with contextlib.suppress(OSError):
                                fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)

    # -- key ----------------------------------------------------------------------------------
    def key(self) -> bytes:
        try:
            data = self.key_path.read_bytes()
            if len(data) >= 32:
                return data
        except FileNotFoundError:
            pass
        with self.exclusive():
            try:
                data = self.key_path.read_bytes()
                if len(data) >= 32:
                    return data
            except FileNotFoundError:
                pass
            data = secrets.token_bytes(32)
            _atomic_write(self.key_path, data)
            return data

    # -- reading ------------------------------------------------------------------------------
    def _raw_lines(self) -> list[bytes]:
        try:
            raw = self.journal_path.read_bytes()
        except FileNotFoundError:
            return []
        if not raw:
            return []
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        return lines

    def entries(self) -> list[dict[str, Any]]:
        """Every well-formed entry, in order. Stops at the first malformed line (a crash mid-write
        leaves one) -- ``verify`` names it; a reader never gets a half entry."""
        out: list[dict[str, Any]] = []
        for line in self._raw_lines():
            try:
                item = json.loads(line.decode("utf-8"))
            except Exception:
                break
            if not isinstance(item, dict):
                break
            out.append(item)
        return out

    def head(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _tail(self) -> tuple[int, str]:
        """(count, last_hash) from the file itself -- the authority for what was appended."""
        lines = self._raw_lines()
        if not lines:
            return 0, GENESIS_PREV
        try:
            last = json.loads(lines[-1].decode("utf-8"))
        except Exception as exc:
            raise JournalIntegrityError("the last journal line is malformed; run verify") from exc
        seq = int(last.get("seq", -1))
        if seq != len(lines) - 1:
            raise JournalIntegrityError(f"tail seq {seq} disagrees with {len(lines)} lines; run verify")
        return len(lines), str(last.get("entry_hash") or "")

    # -- writing ------------------------------------------------------------------------------
    def _write_head(self, count: int, last_hash: str) -> None:
        _atomic_write(
            self.head_path,
            json.dumps({"count": count, "last_hash": last_hash, "updated_at": _utcnow()}, sort_keys=True).encode("utf-8"),
        )

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Durably append one entry and return it with seq/prev/ts/entry_hash/mac filled in."""
        if not isinstance(entry, dict):
            raise JournalError("a journal entry is a dict")
        key = self.key()
        with self.exclusive():
            count, last_hash = self._tail()
            head = self.head()
            if head is not None:
                head_count = int(head.get("count") or 0)
                if head_count > count:
                    # Entries HEAD swore to are gone: extending the chain would make the deletion
                    # look like it never happened. Refuse; verify() names it.
                    raise JournalIntegrityError(
                        f"HEAD records {head_count} entries but the journal holds {count}: tail truncated"
                    )
            full = {k: v for k, v in entry.items() if k not in _UNSIGNED_KEYS and k not in {"seq", "prev", "ts"}}
            full["seq"] = count
            full["prev"] = last_hash
            full["ts"] = _utcnow()
            full["entry_hash"] = _entry_hash(last_hash, full)
            full["mac"] = _mac(key, full)
            line = json.dumps(full, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            fd = os.open(str(self.journal_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            _fsync_directory(self.root)
            self._write_head(count + 1, full["entry_hash"])
            return full

    # -- verification -------------------------------------------------------------------------
    def verify(self) -> ChainReport:
        key = self.key()
        lines = self._raw_lines()
        prev = GENESIS_PREV
        last_hash = GENESIS_PREV
        head = self.head()
        head_count = int(head.get("count") or 0) if head else None
        for index, line in enumerate(lines):
            try:
                item = json.loads(line.decode("utf-8"))
            except Exception:
                return ChainReport(False, "malformed_line", index, head_count, index, last_hash)
            if not isinstance(item, dict):
                return ChainReport(False, "malformed_line", index, head_count, index, last_hash)
            stored_mac = str(item.get("mac") or "")
            if not hmac.compare_digest(stored_mac, _mac(key, item)):
                return ChainReport(False, "mac_mismatch", index, head_count, index, last_hash)
            if int(item.get("seq", -1)) != index:
                return ChainReport(False, "sequence_gap", index, head_count, index, last_hash)
            if str(item.get("prev") or "") != prev:
                return ChainReport(False, "hash_chain_broken", index, head_count, index, last_hash)
            expected_hash = _entry_hash(prev, item)
            if str(item.get("entry_hash") or "") != expected_hash:
                return ChainReport(False, "hash_mismatch", index, head_count, index, last_hash)
            prev = last_hash = expected_hash
        count = len(lines)
        if count == 0 and head is None:
            return ChainReport(True, "empty", 0, None, None, GENESIS_PREV)
        if head is None:
            return ChainReport(False, "head_missing", count, None, None, last_hash)
        if head_count is not None and head_count > count:
            return ChainReport(False, "tail_truncated", count, head_count, count, last_hash)
        if head_count is not None and head_count < count:
            # A crash between the durable append and the HEAD move; the tail is the truth.
            return ChainReport(True, "head_behind_tail", count, head_count, None, last_hash)
        if str(head.get("last_hash") or "") != last_hash:
            return ChainReport(False, "head_mismatch", count, head_count, None, last_hash)
        return ChainReport(True, "verified", count, head_count, None, last_hash)


__all__ = ["GENESIS_PREV", "ChainReport", "Journal", "JournalError", "JournalIntegrityError", "canonical_bytes"]
