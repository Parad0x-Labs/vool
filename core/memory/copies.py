"""Copies outside the live stores answer to a chat forget.

A served forget erases the words from every live store, but some copies sat outside its reach (red-team pass on
the 0.7.7 train, 2026-10-30):

* the update backups: before an update, and before a manual rollback, VOOL copies every database and file of the
  profile into ``update_v2`` (``transactions/<id>/runtime-snapshot``, ``manual_rollbacks/<id>/current-data``, the
  config and migration snapshots). They were kept with no end date, and a rollback would bring a forgotten fact
  back. Only the profile's own files inside a backup are scrubbed; the updater's bookkeeping beside them (the
  backup's ``index.json``, journals, receipts, state) is never touched, so the backup still restores;
* the log files under ``logs``: a warning that quotes a request keeps its words;
* the chat's agent team folder (``core.agent_team``): each agent's task, status lines and full answer;
* the file undo copies (``core.blackbox``): the earlier and later bytes of each workspace file VOOL changed.

``forget_copies`` scrubs them for one chat and one token. Ownership follows the derivative-store law: a row or
record of this chat, or one whose owner cannot be told, answers to the forget; one that names another chat does
not. A database or JSON copy keeps its shape: each text leaf that carries the words is blanked (a key is never
dropped, a runtime id or counter is never touched) and the record's digests with it; a database row that cannot
take a blank (a unique or checked column) is deleted; a text line that carries the words is dropped; a
content-addressed chunk that carries them is removed, as the live forget removes it; an undo copy that carries
them is dropped with its turn, which can no longer be undone (the journal says so, as retention does).

A copy that cannot be changed (a packed or sealed file, a table the scrub cannot rewrite) is not reported as
forgotten, and it does not make every later forget fail either: it is named ``kept`` and the reply says so in
plain words. So is the one record that keeps the words by design: the forget list (``memory_revocations`` and
``.forget-authority``) holds the forgotten words so a later recall, import or model answer that carries them is
refused. It cannot hold a digest instead, because a refusal must find the words inside any later text.

Each file is first read once as bytes for the longest run of letters or digits in the words (folded the way the
forget matcher folds them); only a file that may carry them is parsed. A word split across two overflow pages of
a database is not found by that read; the profile's databases are UTF-8 and such a split needs a field longer than
a page that crosses a page edge inside the word. A database copy is checked row by row, not page by page: its
schema and other chats' rows may hold the same word.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.memory.carriers import _RUNTIME_MADE_RE, ROW_IDENTITIES, _scope, carries, fold_forget_text, text_carries

_LOG = logging.getLogger(__name__)

STORE_UPDATE_BACKUPS = "update_backups"
STORE_LOG_FILES = "log_files"
STORE_AGENT_TEAM = "agent_team_files"
STORE_UNDO_COPIES = "file_undo_copies"
STORE_FORGET_LIST = "forget_list"

#: The reply's last sentence when only the forget list keeps the words.
FORGET_LIST_NOTE = ("In this chat, the forgotten words now stay only in VOOL's forget list on this computer, "
                    "so they are never used again.")

#: Where a store that could not be changed still holds the words, as the reply names it.
_KEPT_PLACES = {
    STORE_UPDATE_BACKUPS: "an update backup",
    STORE_LOG_FILES: "a log file",
    STORE_AGENT_TEAM: "this chat's agent team files",
    STORE_UNDO_COPIES: "the undo record of a file VOOL changed",
}

_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SCOPE_COLUMNS = ("origin_chat_id", "session_id", "chat_id", "chat_scope", "origin_session_id")
_FULL_TEXT_RE = re.compile(r"\busing\s+fts[345]\b", re.IGNORECASE)
#: a full-text table that indexes another table's rows (``content='notes'``): rebuilt from that table, never written
_EXTERNAL_CONTENT_RE = re.compile(r"\bcontent\s*=\s*['\"]?[A-Za-z_]", re.IGNORECASE)
#: a full-text table that keeps no text of its own (``content=''``): only its index can be asked
_CONTENTLESS_RE = re.compile(r"\bcontent\s*=\s*(?:''|\"\")", re.IGNORECASE)
#: The folders of a backup that hold the profile's own files.
_BACKUP_PAYLOADS = frozenset({"runtime-snapshot", "current-data", "config-snapshot", "migration-snapshots"})
#: The updater's own record of a backup, beside the profile's files: never scrubbed.
_BACKUP_BOOKKEEPING = frozenset({"index.json", "state.json", "journal.jsonl"})


class _Unchangeable(Exception):
    """The copy holds the words in a form that cannot be rewritten."""


def kept_note(erasures: dict[str, Any]) -> str:
    """What this forget leaves holding the words, in plain words: always the forget list, and any copy that
    could not be changed."""
    stores = erasures.get("stores", {})
    if stores.get(STORE_FORGET_LIST, {}).get("status") != "kept":
        return ""
    places = [place for store, place in _KEPT_PLACES.items() if stores.get(store, {}).get("status") == "kept"]
    if not places:
        return FORGET_LIST_NOTE
    listed = places[0] if len(places) == 1 else ", ".join(places[:-1]) + " and " + places[-1]
    return ("In this chat, the forgotten words now stay in VOOL's forget list on this computer, so they are never "
            f"used again, and in {listed}, which could not be changed.")


def forget_copies(token: str, *, chat_id: str, erasures: dict[str, Any]) -> None:
    from core.memory.entries import _record_erasure_store
    from core.runtime_paths import active_data_dir

    data = active_data_dir()
    _forget_files(STORE_UPDATE_BACKUPS, lambda: _update_backup_files(data), token, chat_id, erasures, in_place=False)
    _forget_files(STORE_LOG_FILES, lambda: _log_files(data), token, chat_id, erasures, in_place=True)
    _forget_files(STORE_AGENT_TEAM, lambda: _agent_team_files(data, chat_id), token, chat_id, erasures, in_place=True)
    _forget_undo_copies(token, chat_id=chat_id, erasures=erasures)
    _record_erasure_store(erasures, STORE_FORGET_LIST, "kept")


# ── which files ──────────────────────────────────────────────────────────────


def _files_under(base: Path) -> list[Path]:
    if not base.is_dir() or base.is_symlink():
        return []
    return [path for path in sorted(base.rglob("*")) if path.is_file() and not path.is_symlink()]


def _update_backup_files(data: Path) -> list[Path]:
    """The profile's own files inside every update backup; never the updater's bookkeeping beside them, never the
    staged app bundle."""
    root = data / "update_v2"
    found = []
    for part in ("transactions", "manual_rollbacks", "snapshots"):
        base = root / part
        for path in _files_under(base):
            relative = path.relative_to(base).parts
            payload = next((index for index, name in enumerate(relative[:-1]) if name in _BACKUP_PAYLOADS), None)
            if payload is None and part == "snapshots" and len(relative) > 1:
                payload = 0
            if payload is None:
                continue
            if path.name in _BACKUP_BOOKKEEPING and len(relative) == payload + 2:
                continue  # the backup's own index beside its files
            found.append(path)
    return found


def _agent_team_files(data: Path, chat_id: str) -> list[Path]:
    """This chat's agent team (one folder per chat, named by a digest of its id)."""
    return _files_under(data / "agent_teams" / hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16])


def _log_files(data: Path) -> list[Path]:
    """The app's log and its rotated copies (``logs/vool_api.log``, ``.1``, ...), and any other log there."""
    return _files_under(data / "logs")


# ── scrubbing one file ───────────────────────────────────────────────────────


def _forget_files(store: str, listed: Callable[[], list[Path]], token: str, chat_id: str,
                  erasures: dict[str, Any], *, in_place: bool) -> None:
    from core.memory.entries import _record_erasure_store

    try:
        paths = listed()
    except OSError as exc:
        _LOG.warning("forget: the %s could not be listed: %s", store, type(exc).__name__)
        _record_erasure_store(erasures, store, "kept")
        return
    if not paths:
        _record_erasure_store(erasures, store, "not_applicable")
        return
    removed, kept = 0, False
    for path in paths:
        try:
            if _may_carry(path, token):
                removed += _scrub_file(path, token, chat_id, in_place=in_place)
        except Exception as exc:
            _LOG.warning("forget: a %s copy could not be changed: %s %s", store, type(exc).__name__, exc)
            kept = True
    _record_erasure_store(erasures, store, "kept" if kept else "verified", removed)


def _needle(token: str) -> str:
    """The longest run of letters or digits in the words, folded as the forget matcher folds them."""
    runs = re.findall(r"[^\W_]+", fold_forget_text(token))
    return max(runs, key=len, default="")


def _may_carry(path: Path, token: str) -> bool:
    """False only when the file's bytes cannot hold the words. A run of plain ASCII bytes is matched as it is; a
    stretch with other bytes (a ligature, another script's digits) or a JSON escape is decoded and folded first."""
    needle = _needle(token)
    if not needle or path.suffix.lower() == ".gz":
        return True
    plain = needle.encode("utf-8")
    for candidate in (path, Path(str(path) + "-wal")):
        if not candidate.exists():
            continue
        tail = b""
        with open(candidate, "rb") as handle:
            while chunk := handle.read(1 << 20):
                window = tail + chunk
                if plain in window.lower() or b"\\u" in window:
                    return True
                if any(byte >= 0x80 for byte in window) and needle in fold_forget_text(window.decode("utf-8", "ignore")):
                    return True
                tail = window[-(len(plain) * 4 + 6):]
    return False


def _scrub_file(path: Path, token: str, chat_id: str, *, in_place: bool) -> int:
    """Scrub one file and re-read it; raises when it still carries the words."""
    if path.suffix.lower() in _SQLITE_SUFFIXES and _is_sqlite(path):
        return _scrub_sqlite(path, token, chat_id)
    raw = path.read_bytes()
    main = Path(re.sub(r"-(?:wal|shm|journal)$", "", str(path)))
    if main != path and main.exists() and _is_sqlite(main):
        # a database's side file is part of the database (a running writer may hold it open): the database pass
        # above folds what it can and closes, which removes the log; one that still holds the words is said
        if path.name.endswith("-shm") or not text_carries(raw.decode("utf-8", "replace"), token):
            return 0
        raise _Unchangeable(path.name)
    if "cas_chunks" in path.parts:
        # a content-addressed chunk is named by its bytes and cannot be edited; the live forget removes it. In a
        # backup it is emptied, not removed: the backup's index lists it, and a restore needs every listed file
        if text_carries(raw.decode("utf-8", "replace"), token):
            _rewrite(path, b"", in_place=in_place)
            return 1
        return 0
    if "blackbox" in path.parts:
        # the undo journal is chained and sealed entry by entry: an edited copy would make every undo refuse after a
        # restore, so a copy that holds the words is said, never rewritten
        if text_carries(raw.decode("utf-8", "replace"), token):
            raise _Unchangeable(path.name)
        return 0
    packed = path.suffix.lower() == ".gz"
    text = _decoded_text(gzip.decompress(raw) if packed else raw)
    if text is None:
        if text_carries(raw.decode("utf-8", "replace"), token):
            raise _Unchangeable(path.name)
        return 0
    whole = path.suffix.lower() == ".json"
    scrubbed, removed = _scrubbed_text(text, token, chat_id, whole_json=whole)
    if not removed:
        return 0
    if _scrubbed_text(scrubbed, token, chat_id, whole_json=whole)[1]:
        raise _Unchangeable(path.name)
    data = scrubbed.encode("utf-8")
    _rewrite(path, gzip.compress(data) if packed else data, in_place=in_place)
    return removed


def _scrubbed_text(text: str, token: str, chat_id: str, *, whole_json: bool) -> tuple[str, int]:
    """The text with the words scrubbed and the count: a JSON file as one document, else line by line, each line
    that is a JSON record scrubbed as a record and any other line that carries the words dropped."""
    if whole_json:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            scrubbed, removed = _scrubbed_json(parsed, token, chat_id)
            return (json.dumps(scrubbed, ensure_ascii=False) + "\n" if removed else text), removed
    lines, removed = [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        record = None
        if stripped[:1] in ("{", "["):
            try:
                record = json.loads(stripped)
            except ValueError:
                record = None
        if isinstance(record, (dict, list)):
            scrubbed, count = _scrubbed_json(record, token, chat_id)
            removed += count
            lines.append(json.dumps(scrubbed, ensure_ascii=False) + "\n" if count else line)
        elif text_carries(line, token):
            removed += 1
        else:
            lines.append(line)
    return "".join(lines), removed


def _scrubbed_json(value: Any, token: str, chat_id: str, key: Any = None) -> tuple[Any, int]:
    """``value`` with each text leaf of this chat's records (or no chat's) that carries the words blanked, and the
    count. Keys stay; a nested record that names another chat stays whole; a record that carried the words loses
    its digests (an unsalted digest of a short request gives the words back by trial)."""
    from core.memory.run_history import _DIGEST_KEY_RE

    if isinstance(value, dict):
        owner = _scope(value)
        if owner and owner != chat_id:
            return value, 0
        kept: dict[Any, Any] = {}
        count = 0
        for name, item in value.items():
            if isinstance(name, str) and text_carries(name, token):
                count += 1  # a key that is the words goes with its value
                continue
            kept[name], nested = _scrubbed_json(item, token, chat_id, name)
            count += nested
        if count:
            kept = {name: "" if isinstance(item, str) and item and _DIGEST_KEY_RE.search(str(name).casefold()) else item
                    for name, item in kept.items()}
        return kept, count
    if isinstance(value, list):
        pairs = [_scrubbed_json(item, token, chat_id, key) for item in value]
        return [item for item, _count in pairs], sum(count for _item, count in pairs)
    return _blanked_leaf(value, token, key)


def _blanked_leaf(value: Any, token: str, key: Any) -> tuple[Any, int]:
    """A leaf the runtime made (an id under an identity key, a counter) is never the person's words."""
    from core.memory.run_history import _bookkeeping_number

    if isinstance(value, str):
        if key in ROW_IDENTITIES and _RUNTIME_MADE_RE.fullmatch(value):
            return value, 0
        return ("", 1) if text_carries(value, token) else (value, 0)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if key in ROW_IDENTITIES or _bookkeeping_number(key, value):
            return value, 0
        return (None, 1) if text_carries(str(value), token) else (value, 0)
    return value, 0


def _decoded_text(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _rewrite(path: Path, data: bytes, *, in_place: bool) -> None:
    """In place for a file a running writer keeps open (the log handler, an agent's status file); atomically for a
    backup copy."""
    if in_place:
        with open(path, "r+b") as handle:
            handle.seek(0)
            handle.write(data)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        return
    mode = path.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".forget-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# ── a database copy ──────────────────────────────────────────────────────────


def _is_sqlite(path: Path) -> bool:
    with open(path, "rb") as handle:
        return handle.read(16) == b"SQLite format 3\x00"


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn: sqlite3.Connection) -> tuple[list[str], dict[str, str]]:
    """The tables to read and the full-text tables (name -> definition). A full-text table's shadow tables are
    left out (it is rewritten through the table itself), and so is one that indexes another table's rows; another
    module's virtual table cannot be read without its extension, and its shadow tables hold its rows."""
    rows = conn.execute("SELECT name, COALESCE(sql, '') FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = {str(name): str(sql) for name, sql in rows if str(sql).upper().startswith("CREATE VIRTUAL")}
    full_text = {name: sql for name, sql in virtual.items() if _FULL_TEXT_RE.search(sql)}
    shadow = {str(name) for name, _sql in rows if any(str(name).startswith(v + "_") for v in full_text)}
    tables = [str(name) for name, _sql in rows if not str(name).startswith("sqlite_") and str(name) not in shadow
              and (str(name) not in virtual or str(name) in full_text)
              and not _EXTERNAL_CONTENT_RE.search(full_text.get(str(name), ""))]
    return tables, full_text


def _sqlite_carriers(conn: sqlite3.Connection, token: str, chat_id: str
                     ) -> list[tuple[str, list[str], tuple[Any, ...], dict[str, Any]]]:
    """(table, key columns, row key, {column: scrubbed value}) for each row of this chat (or of no chat) with a
    field that carries the words."""
    from core.memory.run_history import _DIGEST_KEY_RE

    tables, _full_text = _tables(conn)
    found = []
    for table in tables:
        columns = conn.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
        names = [str(column[1]) for column in columns]
        scope = next((name for name in _SCOPE_COLUMNS if name in names), None)
        where = (f" WHERE {_quoted(scope)} = ? OR {_quoted(scope)} IS NULL OR {_quoted(scope)} = ''"
                 if scope else "")
        params: tuple[Any, ...] = (chat_id,) if scope else ()
        try:
            rows = conn.execute(f"SELECT rowid, * FROM {_quoted(table)}{where}", params).fetchall()
            keys = ["rowid"]
        except sqlite3.OperationalError:
            keys = [str(column[1]) for column in columns if int(column[5])]
            if not keys:
                raise
            rows = conn.execute(f"SELECT NULL, * FROM {_quoted(table)}{where}", params).fetchall()
        for row in rows:
            changes: dict[str, Any] = {}
            for name, value in zip(names, row[1:], strict=True):
                if isinstance(value, (bytes, bytearray, memoryview)):
                    if text_carries(bytes(value).decode("utf-8", "replace"), token):
                        raise _Unchangeable(f"{table}.{name}")
                    continue
                if not isinstance(value, str) or not value:
                    continue
                try:
                    parsed = json.loads(value) if value.lstrip()[:1] in ("{", "[") else None
                except ValueError:
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    scrubbed, count = _scrubbed_json(parsed, token, chat_id)
                    if count:
                        changes[name] = json.dumps(scrubbed, ensure_ascii=False)
                    continue
                new, count = _blanked_leaf(value, token, name)
                if count:
                    changes[name] = new
            if changes:
                for name, value in zip(names, row[1:], strict=True):
                    if name not in changes and isinstance(value, str) and value and _DIGEST_KEY_RE.search(name.casefold()):
                        changes[name] = ""  # an unsalted digest of the words gives them back by trial
                key = (row[0],) if keys == ["rowid"] else tuple(row[1 + names.index(name)] for name in keys)
                found.append((table, keys, key, changes))
    return found


def _index_only_carries(conn: sqlite3.Connection, full_text: dict[str, str], token: str) -> bool:
    """A full-text table that keeps no text of its own (``content=''``) cannot be rewritten row by row; its index is
    asked for the words instead. One that still finds them is said, not reported as forgotten."""
    # the index may hold the words as typed ("Straße", a ligature) or folded: either finds them
    phrases = {'"' + " ".join(re.findall(r"[^\W_]+", words)) + '"' for words in (token, fold_forget_text(token))}
    phrase = " OR ".join(sorted(phrases))
    for name, definition in full_text.items():
        if not _CONTENTLESS_RE.search(definition):
            continue
        try:
            if conn.execute(f"SELECT 1 FROM {_quoted(name)} WHERE {_quoted(name)} MATCH ? LIMIT 1", (phrase,)).fetchone():
                return True
        except sqlite3.Error:
            return True
    return False


def _scrub_sqlite(path: Path, token: str, chat_id: str) -> int:
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        found = _sqlite_carriers(conn, token, chat_id)
        _tables_list, full_text = _tables(conn)
        if not found:
            # no row of this chat holds the words, but the bytes do (the file passed the byte read): an old deleted
            # row's cells or another chat's row. VACUUM drops the former; the latter stays, as it does live
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if _index_only_carries(conn, full_text, token):
                raise _Unchangeable(path.name)
            return 0
        for table, keys, key, changes in found:
            where = " AND ".join(f"{_quoted(name)} IS ?" for name in keys)
            assignments = ", ".join(f"{_quoted(name)} = ?" for name in changes)
            try:
                with conn:
                    conn.execute(f"UPDATE {_quoted(table)} SET {assignments} WHERE {where}", (*changes.values(), *key))
            except sqlite3.IntegrityError:
                # a unique or checked column cannot take a blank: the row goes, as a history row does live
                with conn:
                    conn.execute(f"DELETE FROM {_quoted(table)} WHERE {where}", key)
        for name, definition in full_text.items():
            if _EXTERNAL_CONTENT_RE.search(definition):
                conn.execute(f"INSERT INTO {_quoted(name)}({_quoted(name)}) VALUES('rebuild')")
            conn.execute(f"INSERT INTO {_quoted(name)}({_quoted(name)}) VALUES('optimize')")
        conn.commit()
        # the old cells and segments leave the file: secure_delete zeroes what is freed, VACUUM rewrites the file
        # from its live rows only, and the full-text merge above drops the deleted words from the index
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if _sqlite_carriers(conn, token, chat_id) or _index_only_carries(conn, full_text, token):
            raise _Unchangeable(path.name)
        return len(found)
    finally:
        conn.close()


# ── the file undo copies ─────────────────────────────────────────────────────


def _journal_words(entry: dict[str, Any]) -> dict[str, Any]:
    """An undo journal entry's own words: the paths it names. Its ids, digests and times are the runtime's.

    A command run (shell, tests, lint, formatter) names its files inside the entry: the scan's preimage table
    (``files``, keyed by path), its drift rows (``drift``, each with a ``path``), its coverage block's declared and
    high-risk paths, and a downgrade's ``incomplete_paths``. Reading only path/paths/root missed a file name that
    carries the words, and the forget still said the undo copies were verified (unwired-modules review,
    2026-11-12)."""
    words: dict[str, Any] = {key: entry.get(key) for key in ("path", "paths", "root", "incomplete_paths") if entry.get(key)}
    files = entry.get("files")
    if isinstance(files, dict):
        named = [str(name) for name in files]
    elif isinstance(files, list):
        named = [str(item.get("path") or "") if isinstance(item, dict) else str(item or "") for item in files]
    else:
        named = []
    drift = entry.get("drift")
    if isinstance(drift, list):
        named += [str(row.get("path") or "") for row in drift if isinstance(row, dict)]
    coverage = entry.get("coverage")
    if isinstance(coverage, dict):
        named += [str(path or "") for path in coverage.get("declared_paths") or []]
        high_risk = coverage.get("high_risk")
        if isinstance(high_risk, dict):
            named += [str(path or "") for path in high_risk]
    named = [name for name in named if name]
    if named:
        words["named_paths"] = named
    return words


def _forget_undo_copies(token: str, *, chat_id: str, erasures: dict[str, Any]) -> None:
    """A turn of this chat whose undo copy carries the words loses its undo copies (all of that turn's files),
    as retention prunes a turn; a copy another chat's turn also refers to stays."""
    from core.memory.entries import _record_erasure_store

    def carrying(data: bytes) -> bool:
        return text_carries(data.decode("utf-8", "replace"), token)

    try:
        from core.blackbox.store import default_store, store_root

        root = store_root()
        if not root.is_dir() or not any(root.iterdir()):
            _record_erasure_store(erasures, STORE_UNDO_COPIES, "not_applicable")
            return
        store = default_store()
        dropped = store.forget_copies(carrying, sessions={chat_id, ""})
        left = store.copies_carrying(carrying, sessions={chat_id, ""})
        # the journal stays (it is the evidence, chained and sealed); an entry of this chat that names the words
        # (a file path) is said
        left = bool(left) or any(str(entry.get("session_id") or "") in {chat_id, ""}
                                 and carries(_journal_words(entry), token) for entry in store.entries())
    except Exception as exc:
        _LOG.warning("forget: the file undo copies could not be erased: %s", type(exc).__name__)
        _record_erasure_store(erasures, STORE_UNDO_COPIES, "kept")
        return
    _record_erasure_store(erasures, STORE_UNDO_COPIES, "kept" if left else "verified", dropped)


__all__ = ["FORGET_LIST_NOTE", "STORE_FORGET_LIST", "forget_copies", "kept_note"]
