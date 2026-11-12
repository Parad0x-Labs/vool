"""Doing an approved plan, and the receipt that says exactly what happened.

A run starts only from the digest of a plan the person saw. It works the plan out again and refuses
when anything differs. Each file is checked once more right before it moves (same size, time and
content), moves without ever replacing a file (VOOL's own no-replace move), and only then gets its list
row. The first problem stops the run: what was done stays done and is listed, nothing else is touched.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.routines.model import RoutineError
from core.routines.plan import plan, row_text

_RUN_LOCK = threading.Lock()
KEEP_RECEIPTS = 20


def _runs_dir(routine_id: str) -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "routines" / "runs" / routine_id


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _make_folders(folder: Path, relatives: list[str]) -> None:
    for relative in relatives:
        current = folder
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                raise RoutineError("bad_target", "A folder on the way is a link, so nothing was moved there.")
            try:
                os.mkdir(current, 0o755)
            except FileExistsError:
                if not current.is_dir() or current.is_symlink():
                    raise RoutineError("bad_target", "A folder on the way is not a folder.") from None


def _append_row(path: Path, row: list[str], delimiter: str, expected: tuple[int, str]) -> tuple[int, str]:
    data = path.read_bytes()
    if (len(data), hashlib.sha256(data).hexdigest()) != expected:
        raise RoutineError("list_changed", f"{path.name} changed while the routine was running.")
    addition = row_text(row, delimiter)
    if data and not data.endswith((b"\n", b"\r")):
        addition = ("\r\n" if b"\r\n" in data else "\n") + addition
    if b"\r\n" in data:
        addition = addition.replace("\n", "\r\n").replace("\r\r\n", "\r\n")
    with path.open("ab") as handle:
        handle.write(addition.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    new = path.read_bytes()
    return len(new), hashlib.sha256(new).hexdigest()


def run(routine: dict[str, Any], digest: str) -> dict[str, Any]:
    from core.machine_file_ops import move_path

    if not _RUN_LOCK.acquire(blocking=False):
        raise RoutineError("run_busy", "A routine is already running. Wait for it to finish.", 409)
    try:
        current = plan(routine)
        if current["digest"] != str(digest or ""):
            raise RoutineError("plan_changed", "The folder changed since you looked. Look at the new list, then run it.", 409)
        if current["stop"]:
            raise RoutineError("plan_stopped", "This list cannot run as it is. Look at it again.", 409)
        if not current["operations"]:
            raise RoutineError("nothing_to_do", "There is nothing for this routine to do right now.", 409)
        folder = Path(current["folder"])
        log = current.get("log")
        log_path = folder / log["file"] if log else None
        expected = (log["size"], log["sha256"]) if log else None
        receipt = {"id": "run-" + secrets.token_hex(5), "routine_id": routine["id"], "name": routine["name"],
                   "started": time.time(), "done": [], "stopped": "", "stopped_at": ""}
        for operation in current["operations"]:
            source = folder / operation["from"]
            target = folder / operation["to"]
            try:
                stat = source.lstat()
                same = (stat.st_size, stat.st_mtime_ns) == (operation["size"], operation["mtime_ns"]) and \
                    _digest(source) == operation["sha256"]
            except OSError:
                same = False
            if not same:
                receipt.update(stopped="file_changed", stopped_at=operation["from"])
                break
            try:
                _make_folders(folder, operation["make"])
            except (RoutineError, OSError):
                receipt.update(stopped="bad_target", stopped_at=operation["from"])
                break
            moved = move_path(str(source), str(target), consent_fn=lambda _prompt: True, allowed_roots=[folder])
            if not moved.ok:
                receipt.update(stopped=moved.status, stopped_at=operation["from"])
                break
            entry = {"from": operation["from"], "to": operation["to"]}
            if log_path is not None and "row" in operation:
                try:
                    expected = _append_row(log_path, operation["row"], log["delimiter"], expected)
                    entry["row"] = operation["row"]
                except (RoutineError, OSError):
                    receipt["done"].append(entry)
                    receipt.update(stopped="list_changed", stopped_at=operation["from"])
                    break
            receipt["done"].append(entry)
        receipt["finished"] = time.time()
        receipt["seconds"] = round(receipt["finished"] - receipt["started"], 2)
        receipt["checks"] = _check(folder, current["operations"], receipt["done"])
        receipt["planned"] = len(current["operations"])
        _keep(receipt)
        return receipt
    finally:
        _RUN_LOCK.release()


def _check(folder: Path, operations: list[dict[str, Any]], done: list[dict[str, Any]]) -> dict[str, Any]:
    """After the run: every moved file is where the list said, with the same content, and gone from
    where it was."""
    planned = {operation["from"]: operation for operation in operations}
    good = 0
    for entry in done:
        operation = planned[entry["from"]]
        target = folder / entry["to"]
        try:
            ok = target.is_file() and not target.is_symlink() and _digest(target) == operation["sha256"] \
                and not (folder / entry["from"]).exists()
        except OSError:
            ok = False
        good += 1 if ok else 0
    return {"moved_as_planned": good, "moved": len(done)}


def _keep(receipt: dict[str, Any]) -> None:
    directory = _runs_dir(receipt["routine_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (receipt["id"] + ".json")
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    kept = sorted(directory.glob("run-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in kept[KEEP_RECEIPTS:]:
        with contextlib.suppress(OSError):
            old.unlink()


def receipts_for(routine_id: str) -> list[dict[str, Any]]:
    directory = _runs_dir(routine_id)
    out = []
    for path in sorted(directory.glob("run-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:KEEP_RECEIPTS]:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


__all__ = ["KEEP_RECEIPTS", "receipts_for", "run"]
