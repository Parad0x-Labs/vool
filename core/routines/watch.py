"""Watching one folder while the person does a task once.

Nothing but that folder is looked at: VOOL compares what the files in it are before, during and after
the watch (names, sizes, dates, content hashes, and the text of small CSV files). No screen, keys, other
apps or other folders. A watch lasts at most 30 minutes and only one runs at a time. What it saw is kept
in VOOL's own data until the routine is saved or discarded, then deleted.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.routines.model import RoutineError

WATCH_SECONDS = 30 * 60
MAX_ENTRIES = 2000
MAX_DEPTH = 4
HASH_LIMIT = 64 * 1024 * 1024
CSV_TEXT_LIMIT = 2 * 1024 * 1024
#: A timer that missed its deadline (VOOL was closed) by more than this ends the watch with nothing kept.
_GRACE_SECONDS = 15

_LOCK = threading.RLock()
_TIMERS: dict[str, threading.Timer] = {}


def _state_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "routines" / "watch.json"


def _read() -> dict[str, Any] | None:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def forget() -> None:
    """Delete everything a watch kept (its snapshots and the changes it saw)."""
    with _LOCK:
        for timer in list(_TIMERS.values()):
            timer.cancel()
        _TIMERS.clear()
        with contextlib.suppress(FileNotFoundError):
            _state_path().unlink()


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def snapshot(folder: Path, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every visible file under ``folder`` (no links, no hidden names), at most MAX_DEPTH deep.

    A file whose size and modification time match ``previous`` keeps its earlier hash, so polling a
    folder during a watch does not re-read files that did not change.
    """
    files: dict[str, dict[str, Any]] = {}
    known = (previous or {}).get("files") or {}
    pending = [(folder, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth + 1 < MAX_DEPTH:
                    pending.append((Path(entry.path), depth + 1))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if len(files) >= MAX_ENTRIES:
                raise RoutineError("folder_too_big", "That folder has too many files to watch. Choose a smaller folder.")
            stat = entry.stat(follow_symlinks=False)
            rel = Path(entry.path).relative_to(folder).as_posix()
            record: dict[str, Any] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": ""}
            earlier = known.get(rel)
            if earlier and earlier.get("size") == stat.st_size and earlier.get("mtime_ns") == stat.st_mtime_ns:
                record["sha256"] = earlier.get("sha256", "")
                if "csv" in earlier:
                    record["csv"] = earlier["csv"]
            else:
                try:
                    if stat.st_size <= HASH_LIMIT:
                        record["sha256"] = _digest(Path(entry.path))
                    if rel.lower().endswith(".csv") and stat.st_size <= CSV_TEXT_LIMIT:
                        record["csv"] = Path(entry.path).read_bytes().decode("utf-8-sig", errors="replace")
                except OSError:
                    continue
            files[rel] = record
    return {"taken": time.time(), "files": files}


def _identity(record: dict[str, Any]) -> tuple:
    return (record.get("sha256"),) if record.get("sha256") else (record.get("size"), record.get("mtime_ns"))


def _rows(text: str) -> list[list[str]]:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """The changes between two snapshots, as plain notes. Moves are matched by content."""
    old, new = before.get("files") or {}, after.get("files") or {}
    gone = {path: record for path, record in old.items() if path not in new or _identity(new[path]) != _identity(record)}
    came = {path: record for path, record in new.items() if path not in old or _identity(old[path]) != _identity(record)}
    notes: list[dict[str, Any]] = []
    # A file that changed in place is not a move: handle those first.
    for path in sorted(set(gone) & set(came)):
        was, now = gone.pop(path), came.pop(path)
        before_text, after_text = was.get("csv"), now.get("csv")
        if path.lower().endswith(".csv") and before_text is not None and after_text is not None:
            if after_text.startswith(before_text.rstrip("\r\n")):
                old_rows = _rows(before_text)
                all_rows = _rows(after_text)
                if all_rows[: len(old_rows)] == old_rows and len(all_rows) > len(old_rows):
                    notes.append({"kind": "rows", "path": path, "rows": all_rows[len(old_rows):],
                                  "header": old_rows[0] if old_rows else []})
                    continue
        notes.append({"kind": "changed", "path": path})
    for source in sorted(gone):
        record = gone[source]
        matches = sorted((path for path, other in came.items() if _identity(other) == _identity(record)),
                         key=lambda path: (Path(path).name != Path(source).name, path))
        if not matches:
            continue
        target = matches[0]
        came.pop(target)
        gone[source] = None  # type: ignore[assignment]
        notes.append({"kind": "moved", "from": source, "to": target, "size": record.get("size"),
                      "mtime_ns": record.get("mtime_ns"), "sha256": record.get("sha256", "")})
    for source, record in sorted(gone.items()):
        if record is not None:
            notes.append({"kind": "removed", "path": source})
    for path, record in sorted(came.items()):
        if path.lower().endswith(".csv") and record.get("csv") is not None:
            rows = _rows(record["csv"])
            if rows:
                notes.append({"kind": "rows", "path": path, "rows": rows[1:] if len(rows) > 1 else rows,
                              "header": rows[0] if len(rows) > 1 else [], "created": True})
                continue
        notes.append({"kind": "added", "path": path})
    order = {"moved": 0, "rows": 1, "added": 2, "changed": 3, "removed": 4}
    return sorted(notes, key=lambda note: (order[note["kind"]], note.get("from") or note.get("path") or ""))


def _public(state: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    running = not state.get("ended")
    view = {"id": state["id"], "folder": state["folder"], "running": running,
            "seconds_left": max(0, int(state["deadline"] - now)) if running else 0,
            "end_reason": state.get("end_reason", "")}
    if running:
        view["changes"] = state.get("live_changes", 0)
    else:
        view["notes"] = state.get("notes") or []
    return view


def _arm(state: dict[str, Any]) -> None:
    watch_id = state["id"]
    delay = max(0.0, state["deadline"] - time.time())
    timer = threading.Timer(delay, lambda: _finish_if(watch_id, "time_limit"))
    timer.daemon = True
    _TIMERS[watch_id] = timer
    timer.start()


def start(folder_text: str) -> dict[str, Any]:
    from core.routines.folders import admit

    folder = admit(folder_text)
    with _LOCK:
        current = _read()
        if current and not current.get("ended"):
            refresh(current)
            current = _read()
            if current and not current.get("ended"):
                raise RoutineError("watch_running", "VOOL is already watching a folder. Stop that watch first.", 409)
        forget()
        now = time.time()
        state = {"id": "w-" + secrets.token_hex(5), "folder": str(folder), "started": now,
                 "deadline": now + WATCH_SECONDS, "before": snapshot(folder), "live_changes": 0}
        _write(state)
        _arm(state)
        return _public(state)


def refresh(state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The watch as it stands; a running watch past its deadline ends here if its timer could not."""
    with _LOCK:
        state = state or _read()
        if not state:
            return None
        if not state.get("ended"):
            if time.time() > state["deadline"] + _GRACE_SECONDS:
                # The deadline passed while nothing was watching (VOOL was closed or asleep): what the folder
                # looks like now may include changes made after the 30 minutes, so nothing is kept.
                forget()
                return {"id": state["id"], "folder": state["folder"], "running": False, "seconds_left": 0,
                        "end_reason": "expired", "notes": []}
            if state["id"] not in _TIMERS:
                _arm(state)
            try:
                now = snapshot(Path(state["folder"]), state["before"])
                state["live_changes"] = len([note for note in diff(state["before"], now)])
                _write(state)
            except RoutineError:
                pass
        return _public(state)


def _finish_if(watch_id: str, reason: str) -> None:
    with contextlib.suppress(RoutineError):
        stop(watch_id, reason=reason)


def stop(watch_id: str = "", *, reason: str = "stopped") -> dict[str, Any]:
    with _LOCK:
        state = _read()
        if not state or (watch_id and state.get("id") != watch_id):
            raise RoutineError("no_watch", "There is no watch to stop. Start one first.", 409)
        if state.get("ended"):
            return _public(state)
        timer = _TIMERS.pop(state["id"], None)
        if timer is not None:
            timer.cancel()
        folder = Path(state["folder"])
        try:
            after = snapshot(folder, state["before"])
        except RoutineError:
            forget()
            raise
        state.update(ended=time.time(), end_reason=reason, after=after, notes=diff(state["before"], after))
        _write(state)
        return _public(state)


def current() -> dict[str, Any] | None:
    """The full kept state of the finished watch (for making the proposal); None when there is none."""
    state = _read()
    if not state or not state.get("ended"):
        return None
    return state


__all__ = ["MAX_ENTRIES", "WATCH_SECONDS", "current", "diff", "forget", "refresh", "snapshot", "start", "stop"]
