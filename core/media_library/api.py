"""The media library's doors, for its own page (``/media``). Owner-only: this computer's own session, the app's
own page (same origin), JSON bodies; every answer is ``{"ok": true, ...}`` or ``{"ok": false, "error": word}``.

POST /api/media            {"action": ...}  overview, list, remove, upload_begin, upload_finish, upload_cancel,
                                            drives, plan, backup, check, restore, job, cancel, settings
POST /api/media/upload     one slice of a file being added (raw bytes; headers X-Media-Upload, X-Media-Offset)
GET  /api/media/item       ?id=..&thumb=1 one item, opened for the page only (Range supported, never cached)
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.media_library import backup as mb
from core.media_library import sealed
from core.media_library.store import KINDS, SECTIONS, LibraryError, library

SLICE_BYTES = 8 * 1024 * 1024
_RANGE_CAP = 8 * 1024 * 1024
_ID_RE = re.compile(r"[0-9a-f]{32}")


# --- settings -----------------------------------------------------------------------------------------------------

def _settings_path() -> Path:
    from core.runtime_paths import data_path

    return data_path("media_library_settings.json")


def settings() -> dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"mirror_removals": bool(data.get("mirror_removals", False)), "last_drive": str(data.get("last_drive") or "")}


def _save_settings(values: dict[str, Any]) -> dict[str, Any]:
    merged = {**settings(), **values}
    sealed.write_file(_settings_path(), json.dumps(merged).encode("utf-8"))
    return merged


# --- one job at a time (backup, check, restore) ---------------------------------------------------------------------

class _Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"kind": "", "running": False}
        self.stop = threading.Event()

    def start(self, kind: str, work) -> bool:
        with self.lock:
            if self.state.get("running"):
                return False
            self.stop = threading.Event()
            self.state = {"kind": kind, "running": True, "done": 0, "total": 0, "name": "", "started_at": time.time(), "result": None, "error": ""}
        threading.Thread(target=self._run, args=(work,), name=f"media-{kind}", daemon=True).start()
        return True

    def progress(self, values: dict[str, Any]) -> None:
        with self.lock:
            self.state.update({k: values[k] for k in ("done", "total", "name") if k in values})

    def _run(self, work) -> None:
        result: Any = None
        error = ""
        try:
            result = work(self.stop, self.progress)
        except mb.BackupStop as stop:
            error = stop.code
        except LibraryError as refused:
            error = refused.code
        except OSError:
            error = "drive_gone"
        except Exception:
            error = "failed"
        with self.lock:
            self.state.update({"running": False, "result": result, "error": error, "finished_at": time.time()})

    def view(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.state)


JOB = _Job()


# --- guards -------------------------------------------------------------------------------------------------------

def _lowered(headers: Any) -> dict[str, str]:
    return {str(k).lower(): str(v or "") for k, v in (headers or {}).items()}


def _refusal(headers: Any, client_host: str, *, raw: bool = False) -> tuple[int, dict[str, Any]] | None:
    from core.web.api.service import _credential_page_refusal

    lowered = _lowered(headers)
    if raw:
        if lowered.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            return 415, {"ok": False, "error": "content_type"}
        lowered["content-type"] = "application/json"
    why = _credential_page_refusal(lowered, client_host)
    if why:
        return (415 if why == "content_type" else 403), {"ok": False, "error": "owner_required"}
    return None


def _public(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta.get(k) for k in ("id", "section", "kind", "name", "media_type", "size", "taken_at", "added_at", "damaged")}


def _vault(body: dict[str, Any], *, create: bool = False) -> mb.Vault:
    folder = mb.drive_folder(str(body.get("drive_id") or ""))
    password = str(body.get("password") or "")
    if create and mb.Vault.describe(folder) is None:
        if len(password) < mb.MIN_PASSWORD:
            raise mb.BackupStop("password_short")
        if password != str(body.get("confirm_password") or ""):
            raise mb.BackupStop("password_mismatch")
    vault = mb.open_vault(folder, password=password, create=create)
    _save_settings({"last_drive": str(body.get("drive_id") or "")})
    return vault


# --- POST /api/media ------------------------------------------------------------------------------------------------

def handle(body: Any, *, headers: Any, client_host: str) -> tuple[int, dict[str, Any]]:
    refused = _refusal(headers, client_host)
    if refused:
        return refused
    if not isinstance(body, dict) or len(json.dumps(body)) > 16384 or not isinstance(body.get("action"), str):
        return 400, {"ok": False, "error": "invalid_input"}
    action = body["action"]
    lib = library()
    try:
        if action == "overview":
            try:
                summary = lib.summary()
            except LibraryError as refused_key:
                if refused_key.code != "empty":
                    raise
                summary = {"counts": {s: {k: 0 for k in KINDS} for s in SECTIONS}, "bytes": 0, "items": 0}
            return 200, {"ok": True, "summary": summary, "settings": settings(), "job": JOB.view()}
        if action == "list":
            section = str(body.get("section") or "")
            kind = str(body.get("kind") or "")
            if section and section not in SECTIONS or kind and kind not in KINDS:
                return 400, {"ok": False, "error": "invalid_input"}
            sort = "size" if body.get("sort") == "size" else "date"
            try:
                items = lib.items(section=section, kind=kind, sort=sort, descending=body.get("order") != "asc")
            except LibraryError as refused_key:
                if refused_key.code != "empty":
                    raise
                items = []
            return 200, {"ok": True, "items": [_public(m) for m in items]}
        if action == "remove":
            ids = body.get("ids")
            if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, str) and _ID_RE.fullmatch(i) for i in ids):
                return 400, {"ok": False, "error": "invalid_input"}
            if body.get("confirm") != len(ids):
                return 409, {"ok": False, "error": "confirm_needed", "count": len(ids)}
            for item_id in ids:
                lib.remove(item_id)
            return 200, {"ok": True, "removed": len(ids)}
        if action == "upload_begin":
            upload = lib.upload_begin(name=str(body.get("name") or ""), media_type=str(body.get("media_type") or ""),
                                      size=int(body.get("size") or 0), taken_at=float(body.get("taken_at") or 0) / 1000.0)
            return 200, {"ok": True, "upload_id": upload, "slice_bytes": SLICE_BYTES}
        if action == "upload_finish":
            return 200, {"ok": True, "item": _public(lib.upload_finish(str(body.get("upload_id") or "")))}
        if action == "upload_cancel":
            lib.upload_cancel(str(body.get("upload_id") or ""))
            return 200, {"ok": True}
        if action == "drives":
            return 200, {"ok": True, "drives": mb.drives(), "settings": settings()}
        if action == "settings":
            values = {}
            if "mirror_removals" in body:
                values["mirror_removals"] = bool(body["mirror_removals"])
            return 200, {"ok": True, "settings": _save_settings(values)}
        if action == "plan":
            vault = _vault(body)
            return 200, {"ok": True, "plan": mb.plan(lib, vault), "settings": settings()}
        if action in ("backup", "check", "restore"):
            vault = _vault(body, create=action == "backup" and bool(body.get("create")))
            mirror = settings()["mirror_removals"]
            confirm = body.get("confirm_removals")
            confirm = int(confirm) if isinstance(confirm, int) else None
            if action == "backup":
                work = lambda stop, progress: mb.backup(lib, vault, mirror_removals=mirror, confirm_removals=confirm, progress=progress, stop=stop)
            elif action == "check":
                work = lambda stop, progress: mb.check(vault, progress=progress)
            else:
                work = lambda stop, progress: mb.restore(lib, vault, progress=progress, stop=stop)
            if not JOB.start(action, work):
                return 409, {"ok": False, "error": "busy"}
            return 200, {"ok": True, "job": JOB.view()}
        if action == "job":
            return 200, {"ok": True, "job": JOB.view()}
        if action == "cancel":
            JOB.stop.set()
            return 200, {"ok": True}
    except mb.BackupStop as stop:
        return 409, {"ok": False, "error": stop.code}
    except LibraryError as refused_lib:
        return (404 if refused_lib.code == "not_found" else 409), {"ok": False, "error": refused_lib.code}
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "invalid_input"}
    return 400, {"ok": False, "error": "unknown_action"}


# --- POST /api/media/upload -------------------------------------------------------------------------------------------

def upload(raw_body: bytes, *, headers: Any, client_host: str) -> tuple[int, dict[str, Any]]:
    refused = _refusal(headers, client_host, raw=True)
    if refused:
        return refused
    lowered = _lowered(headers)
    try:
        offset = int(lowered.get("x-media-offset", ""))
    except ValueError:
        return 400, {"ok": False, "error": "invalid_input"}
    if not raw_body or len(raw_body) > SLICE_BYTES:
        return 413, {"ok": False, "error": "too_large"}
    try:
        return 200, {"ok": True, "next_offset": library().upload_piece(lowered.get("x-media-upload", ""), offset, raw_body)}
    except LibraryError as refused_lib:
        return 409, {"ok": False, "error": refused_lib.code}


# --- GET /api/media/item ---------------------------------------------------------------------------------------------

_NO_STORE = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}


def item(query: dict[str, list[str]], *, headers: Any, client_host: str):
    """One item, opened for this computer's own page: the whole file, a byte range (for video and sound), or its
    small preview. Answered as (status, content type, body or stream, headers)."""
    from core.request_trust import is_loopback_host

    lowered = _lowered(headers)
    site = lowered.get("sec-fetch-site", "").strip().lower()
    if not is_loopback_host(client_host) or (site and site not in ("same-origin", "none")):
        return 403, "application/json", b'{"ok":false,"error":"owner_required"}', {}
    item_id = str((query.get("id") or [""])[0])
    if not _ID_RE.fullmatch(item_id):
        return 404, "application/json", b'{"ok":false,"error":"not_found"}', {}
    lib = library()
    try:
        meta = lib.get(item_id)
        if (query.get("thumb") or [""])[0] == "1":
            thumb = lib.thumb(item_id)
            if thumb is None:
                return 404, "application/json", b'{"ok":false,"error":"no_preview"}', {}
            return 200, "image/jpeg", thumb, dict(_NO_STORE)
        opened = lib.open(item_id)
    except LibraryError as refused_lib:
        return 404, "application/json", json.dumps({"ok": False, "error": refused_lib.code}).encode(), {}
    except (sealed.SealedError, OSError):
        return 409, "application/json", b'{"ok":false,"error":"damaged"}', {}
    size = opened.plain_size
    media_type = str(meta.get("media_type") or "application/octet-stream")
    extra = {**_NO_STORE, "Accept-Ranges": "bytes"}
    wanted = re.fullmatch(r"bytes=(\d*)-(\d*)", lowered.get("range", "").strip())
    if wanted and (wanted.group(1) or wanted.group(2)):
        if wanted.group(1):
            start = int(wanted.group(1))
            end = int(wanted.group(2)) + 1 if wanted.group(2) else size
        else:
            start, end = max(0, size - int(wanted.group(2))), size
        end = min(end, size, start + _RANGE_CAP)
        if start >= size or start >= end:
            return 416, "application/json", b'{"ok":false,"error":"range"}', {"Content-Range": f"bytes */{size}"}
        extra.update({"Content-Range": f"bytes {start}-{end - 1}/{size}", "Content-Length": str(end - start)})
        return 206, media_type, _guarded(opened.range(start, end)), extra
    extra["Content-Length"] = str(size)
    return 200, media_type, _guarded(opened.chunks()), extra


def _guarded(chunks):
    """Stops the stream at the first piece that does not open: a changed file is never sent on as if whole."""
    try:
        yield from chunks
    except sealed.SealedError:
        return
