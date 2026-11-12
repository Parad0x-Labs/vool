"""What an in-app update must keep: the person's chats, pins, projects, settings, facts and keys.

``ops/update_journey.py`` used to prove "data kept" with one marker file. This module puts real user
state into the disposable profile through the OLD version's own doors (the same calls Settings and the
sidebar make), records what the person sees and an inventory of the profile, and after the update (or
after a broken update restored itself) checks the version that is running against both:

* **What the person sees** must be unchanged: every chat with every message, the pinned chat, the
  project and its folder, the preferences and the facts the person gave. Read through the running
  version's own doors, so a store the new version can no longer read fails here even when the bytes
  survived.
* **Nothing the person had is lost**: every profile file is still there; key and secret files are
  byte-identical (a regenerated key silently orphans a wallet, a pairing or an identity); every SQLite
  row is still there by primary key (a migration may add tables, columns and rows, never drop them).
  Files and rows that changed in place are listed as evidence, not judged: the person-view check is
  what decides whether a change lost something.

The seed needs no model: chats come in through Settings' chat import (an export file, read by the
app's own importer), so the journey stays offline. Standard library only (the journey's runner).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

#: The chats the seed brings in: (export id, title, [(role, text), ...]). Plain, distinct words, so
#: a lost or mixed-up message cannot match by accident.
SEED_CHATS = (
    ("keep-garden-1", "Garden plan for spring", (
        ("user", "Which tomatoes grow best on a north-facing balcony?"),
        ("assistant", "Cherry tomatoes such as Sungold cope best with partial shade."),
        ("user", "And how often should I water them in July?"),
        ("assistant", "Every morning in July, and twice on days above 28 degrees."),
    )),
    ("keep-trip-2", "Trip to Vilnius", (
        ("user", "Remind me later: the hotel is the Narutis, booking number 4471."),
        ("assistant", "Noted: Hotel Narutis, booking number 4471."),
    )),
)
PINNED_TITLE = "Garden plan for spring"
PROJECT_NAME = "Balcony garden"
PREFS = {"humor_percent": 55}
FACTS = {"preferred_name": "Rasa", "timezone": "Europe/Vilnius", "language": "Lithuanian"}

#: Profile parts that are the app's own working state, rewritten on every start: never user data.
VOLATILE_DIRS = ("data/update_v2", "data/logs", "data/preflight", "tmp", "data/chat_import/incoming")
VOLATILE_SUFFIXES = (".pid", ".lock", "-wal", "-shm", "-journal", ".log")
#: Files whose bytes ARE the identity: any change is a loss, whatever the person view says.
SECRET_SUFFIXES = (".secret", ".passphrase", ".hex", ".pem", ".key")
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


class PreservationFailure(RuntimeError):
    pass


# -- the app's own doors ------------------------------------------------------------------------------

def _request(origin: str, path: str, *, body: object = None, raw: bytes | None = None,
             content_type: str = "application/json", timeout: float = 60) -> dict:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(origin + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"Content-Type": content_type} if data is not None else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise PreservationFailure(f"{path} answered {exc.code}: {detail}") from exc


def _export_zip() -> bytes:
    """A chat export in the mapping-tree shape the importer reads (conversations.json in a zip)."""
    conversations = []
    for number, (chat_id, title, turns) in enumerate(SEED_CHATS):
        start = 1_760_000_000 + number * 86_400
        mapping: dict[str, dict] = {"root": {"id": "root", "message": None, "parent": None, "children": []}}
        parent = "root"
        for index, (role, text) in enumerate(turns):
            node = f"{chat_id}-{index}"
            mapping[node] = {"id": node, "parent": parent, "children": [], "message": {
                "id": node, "author": {"role": role}, "create_time": start + index * 30, "metadata": {},
                "content": {"content_type": "text", "parts": [text]}}}
            mapping[parent]["children"].append(node)
            parent = node
        conversations.append({"id": chat_id, "title": title, "create_time": start,
                              "update_time": start + len(turns) * 30, "mapping": mapping, "current_node": parent})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(conversations))
    return buffer.getvalue()


def seed(origin: str, home: Path) -> dict:
    """Put the person's state into the running (old) version through its own doors."""
    upload = _request(origin, "/api/chat/import/upload", raw=_export_zip(), content_type="application/zip")
    preview = _request(origin, "/api/chat/import/preview", body={"upload_id": upload["upload_id"]})
    refs = [chat["ref"] for chat in preview.get("chats") or []]
    if len(refs) != len(SEED_CHATS):
        raise PreservationFailure(f"the importer offered {len(refs)} of {len(SEED_CHATS)} chats: {preview}")
    _request(origin, "/api/chat/import/start", body={"preview_id": preview["preview_id"], "chats": refs})
    deadline = time.monotonic() + 120
    status: dict = {}
    while time.monotonic() < deadline:
        status = _request(origin, "/api/chat/import/status")
        if status.get("state") not in ("running", "queued"):
            break
        time.sleep(1)
    outcome = status.get("outcome") or {}
    if status.get("state") != "done" or outcome.get("brought_in") != len(SEED_CHATS):
        raise PreservationFailure(f"chat import did not finish: {status}")

    sessions = {row.get("title"): row.get("session_id") for row in _request(origin, "/api/chat/sessions")["sessions"]}
    pinned = _request(origin, "/api/chat/pin-chat", body={"session_id": sessions[PINNED_TITLE], "pinned": True})
    if pinned.get("ok") is not True:
        raise PreservationFailure(f"pinning a chat was refused: {pinned}")

    folder = home / "Documents" / PROJECT_NAME
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "beds.txt").write_text("three raised beds\n")
    project = _request(origin, "/api/projects", body={"name": PROJECT_NAME, "root": str(folder)})
    if not (project.get("project") or {}).get("id"):
        raise PreservationFailure(f"creating a project was refused: {project}")

    if _request(origin, "/api/settings/prefs", body=PREFS).get("ok") is not True:
        raise PreservationFailure("saving preferences was refused")
    named = _request(origin, "/api/onboarding/pact/name",
                     body={"keep_default": True, "preferred_address": FACTS["preferred_name"]})
    if named.get("ok") is not True:
        raise PreservationFailure(f"saving the preferred name was refused: {named}")
    facts = _request(origin, "/api/onboarding/pact/facts", body={"items": [
        {"category": key, "value": value} for key, value in FACTS.items() if key != "preferred_name"]})
    if facts.get("ok") is not True:
        raise PreservationFailure(f"saving facts was refused: {facts}")

    view = person_view(origin)
    expected = expected_view(str(folder))
    missing = view_losses(expected, view)
    if missing:
        raise PreservationFailure(f"the old version does not show what was just saved: {missing}")
    return view


def expected_view(project_root: str) -> dict:
    return {
        "chats": {title: [[role, text] for role, text in turns] for _, title, turns in SEED_CHATS},
        "pinned": [PINNED_TITLE],
        "projects": [[PROJECT_NAME, project_root]],
        "prefs": dict(PREFS),
        "facts": dict(FACTS),
    }


def person_view(origin: str) -> dict:
    """What the person sees, read through the running version's own doors, in a comparable shape."""
    rows = _request(origin, "/api/chat/sessions").get("sessions") or []
    chats: dict[str, list] = {}
    pinned = []
    for row in rows:
        title = str(row.get("title") or "")
        session = urllib.parse.quote(str(row.get("session_id") or ""), safe="")
        history = _request(origin, f"/api/chat/history?session={session}").get("messages") or []
        chats[title] = [[str(m.get("role") or ""), str(m.get("content") or "")] for m in history]
        if row.get("pinned"):
            pinned.append(title)
    projects = [[str(p.get("name") or ""), str(p.get("root") or "")]
                for p in _request(origin, "/api/projects").get("projects") or []]
    prefs = _request(origin, "/api/settings/prefs")
    facts = {str(item.get("category")): str(item.get("value"))
             for item in _request(origin, "/api/profile").get("items") or [] if item.get("status") == "active"}
    return {"chats": chats, "pinned": sorted(pinned), "projects": sorted(projects),
            "prefs": {key: prefs.get(key) for key in PREFS}, "facts": facts}


def view_losses(before: dict, after: dict) -> list[str]:
    """Everything in ``before`` that ``after`` no longer shows (the new version may show more)."""
    losses = []
    for title, messages in before["chats"].items():
        if title not in after["chats"]:
            losses.append(f"chat gone: {title!r}")
        elif after["chats"][title][:len(messages)] != messages:
            losses.append(f"chat {title!r} messages differ: {after['chats'][title]!r}")
    losses += [f"no longer pinned: {title!r}" for title in before["pinned"] if title not in after["pinned"]]
    losses += [f"project gone: {project!r}" for project in before["projects"] if project not in after["projects"]]
    losses += [f"preference {key} was {value!r}, now {after['prefs'].get(key)!r}"
               for key, value in before["prefs"].items() if after["prefs"].get(key) != value]
    losses += [f"fact {key} was {value!r}, now {after['facts'].get(key)!r}"
               for key, value in before["facts"].items() if after["facts"].get(key) != value]
    return losses


# -- the profile on disk ------------------------------------------------------------------------------

def _volatile(relative: str) -> bool:
    return relative.startswith(tuple(d + "/" for d in VOLATILE_DIRS)) or relative.endswith(VOLATILE_SUFFIXES)


def _sqlite_rows(path: Path) -> dict:
    """Per table: its key columns and one digest per row keyed by primary key (rowid when none)."""
    tables: dict[str, dict] = {}
    with sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True, timeout=10) as connection:
        names = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for name in names:
            columns = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            keys = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
            without_rowid = connection.execute(
                "SELECT sql LIKE '%WITHOUT ROWID%' FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            if not keys and without_rowid:
                keys = [c[1] for c in columns]
            key_sql = ", ".join(f'"{k}"' for k in keys) if keys else "rowid"
            rows = {}
            for row in connection.execute(f'SELECT {key_sql}, * FROM "{name}"'):
                width = len(keys) or 1
                rows[json.dumps(row[:width], default=repr)] = hashlib.sha256(
                    json.dumps(row[width:], default=repr).encode()).hexdigest()
            tables[name] = {"key": keys or ["rowid"], "rows": rows}
    return tables


def inventory(runtime_home: Path) -> dict:
    """Every user file in the profile: bytes digest, and per-row digests for SQLite databases."""
    files: dict[str, dict] = {}
    for directory, dirs, names in os.walk(runtime_home):
        dirs.sort()
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(runtime_home).as_posix()
            if _volatile(relative) or path.is_symlink() or not path.is_file():
                continue
            entry = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
            if name.endswith(SQLITE_SUFFIXES):
                entry["tables"] = _sqlite_rows(path)
            files[relative] = entry
    return files


def inventory_losses(before: dict, after: dict) -> tuple[list[str], list[str]]:
    """(losses, changed): losses fail the journey; changed in-place files and rows are evidence."""
    losses, changed = [], []
    for relative, old in before.items():
        new = after.get(relative)
        if new is None:
            losses.append(f"file gone: {relative}")
            continue
        if old["sha256"] == new["sha256"]:
            continue
        if relative.endswith(SECRET_SUFFIXES) or "/keys/" in f"/{relative}":
            losses.append(f"key or secret file changed: {relative}")
            continue
        if "tables" not in old:
            changed.append(f"file changed: {relative}")
            continue
        for table, data in old["tables"].items():
            current = (new.get("tables") or {}).get(table)
            if current is None:
                if data["rows"]:
                    losses.append(f"table gone: {relative}:{table} ({len(data['rows'])} rows)")
                continue
            if current["key"] != data["key"]:
                changed.append(f"table key changed: {relative}:{table} {data['key']} -> {current['key']}")
                continue
            gone = [key for key in data["rows"] if key not in current["rows"]]
            if gone:
                losses.append(f"rows gone: {relative}:{table}: {len(gone)} of {len(data['rows'])}, e.g. {gone[:3]}")
            edited = [key for key in data["rows"] if key in current["rows"] and current["rows"][key] != data["rows"][key]]
            if edited:
                changed.append(f"rows changed in place: {relative}:{table}: {len(edited)}")
    return losses, changed
