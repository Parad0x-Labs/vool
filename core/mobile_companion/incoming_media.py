"""Photos, videos and sound recordings the phone's private capture sends to this computer.

The VOOL phone app has its own camera and recorder. What it captures never goes to the phone's
gallery or to any cloud: it is kept encrypted inside the app until this computer has it, then the
phone deletes its copy. This module is the computer's side of that hand-over (``capture.begin`` /
``capture.chunk`` / ``capture.finish`` in core.web.api.mobile_companion_api). Files of any size
travel in pieces and are hashed as a stream, so a long video never has to fit in memory:

* begin   the phone names the file by its size and SHA-256. A file already saved is answered
          "done" at once, so a retry after a lost answer never makes a second copy. A half-sent
          file answers with how far it got, so the phone carries on from there.
* chunk   one piece, appended only at the exact offset the computer already holds.
* finish  the computer hashes what it received. Only when that matches the phone's hash is the
          file saved and the hash returned; the phone deletes its copy only on that answer.
          A mismatch throws the partial away and the phone keeps its file to try again.

Partial uploads live in VOOL's own data folder, readable only by this user. Saved files go to
``~/Pictures/VOOL Phone Camera/<date>/`` on a normal install (inside the VOOL home on an isolated
test profile), one folder per day, never into a photo library.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

CHUNK_BYTES = 768 * 1024
PARTIAL_TTL_SECONDS = 7 * 24 * 3600
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_ID_RE = re.compile(r"cap_[0-9a-f]{40}")
#: media type -> (file ending, largest size). HEIC and Ogg arrive only when the phone's optional
#: compression is on; by default photos are JPEG and sound is M4A, exactly as captured.
_TYPES = {
    "image/jpeg": (".jpg", 64 * 1024 * 1024),
    "image/heic": (".heic", 64 * 1024 * 1024),
    "video/mp4": (".mp4", 8 * 1024 * 1024 * 1024),
    "audio/mp4": (".m4a", 2 * 1024 * 1024 * 1024),
    "audio/ogg": (".ogg", 2 * 1024 * 1024 * 1024),
}
_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
MAX_BYTES = max(limit for _ending, limit in _TYPES.values())


def ability_for(media_type: str) -> str:
    """The phone right a capture needs: sound needs the microphone right, pictures and video the camera right."""
    return "microphone" if str(media_type).strip().lower().startswith("audio/") else "camera"


def _looks_right(media_type: str, head: bytes) -> bool:
    if media_type == "image/jpeg":
        return head[:3] == b"\xff\xd8\xff"
    if media_type == "audio/ogg":
        return head[:4] == b"OggS"
    if head[4:8] != b"ftyp":  # HEIC, MP4 video and M4A sound are all ISO media files
        return False
    is_heif = head[8:12] in _HEIF_BRANDS
    return is_heif if media_type == "image/heic" else not is_heif


class MediaRefused(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise MediaRefused("store_unavailable", "The phone capture store on this computer is not usable.", 500)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def _staging(device_id: str) -> Path:
    from core.runtime_paths import active_data_dir

    root = _private_dir((active_data_dir() / "mobile_companion" / "incoming_photos").resolve())
    return _private_dir(root / hashlib.sha256(str(device_id).encode("utf-8")).hexdigest()[:32])


def photos_root() -> Path:
    """Where saved phone photos live: ~/Pictures/VOOL Phone Camera, or inside an isolated VOOL home."""
    from core.runtime_paths import runtime_auxiliary_path

    return runtime_auxiliary_path("phone-camera", legacy=Path.home() / "Pictures" / "VOOL Phone Camera")


def _upload_id(device_id: str, sha256: str) -> str:
    return "cap_" + hashlib.sha256(f"{device_id}:{sha256}".encode()).hexdigest()[:40]


def _index_path(device_id: str) -> Path:
    return _staging(device_id) / "saved.json"


def _index(device_id: str) -> dict[str, Any]:
    try:
        data = json.loads(_index_path(device_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _sync_file(path: Path) -> None:
    """Asks the operating system to put this file on the disk now (not just in its memory)."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_dir(folder: Path) -> None:
    """Makes a rename or new file in this folder survive a power cut (where the system allows it)."""
    with contextlib.suppress(OSError, AttributeError):
        descriptor = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_json(path: Path, value: Any) -> None:
    temp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    _sync_dir(path.parent)


def _sweep(stage: Path, now: float) -> None:
    for meta in stage.glob("cap_*.json"):
        with contextlib.suppress(OSError, ValueError):
            if now - float(json.loads(meta.read_text(encoding="utf-8")).get("started_at", 0)) > PARTIAL_TTL_SECONDS:
                meta.with_suffix(".part").unlink(missing_ok=True)
                meta.unlink(missing_ok=True)


def _saved_answer(record: dict[str, Any]) -> dict[str, Any]:
    return {"done": True, "sha256": record["sha256"], "saved_as": record["name"], "size_bytes": record["size_bytes"]}


def begin(device_id: str, params: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    moment = time.time() if now is None else now
    sha256 = str(params.get("sha256") or "").strip().lower()
    media_type = str(params.get("media_type") or "").strip().lower()
    size = params.get("size_bytes")
    if not _SHA_RE.fullmatch(sha256):
        raise MediaRefused("bad_capture_hash", "This file could not be checked.")
    if type(size) is not int or size <= 0:
        raise MediaRefused("bad_capture_size", "This file is empty.")
    if media_type not in _TYPES:
        raise MediaRefused("capture_type_not_allowed", "Only photos, videos and recordings from VOOL can be sent this way.")
    if size > _TYPES[media_type][1]:
        raise MediaRefused("capture_too_large", "This file is too big to send to the computer.", 413)
    taken_at = params.get("taken_at")
    taken_at = float(taken_at) if type(taken_at) in (int, float) and 0 < taken_at < moment + 86400 else moment
    stage = _staging(device_id)
    _sweep(stage, moment)
    saved = _index(device_id).get(sha256)
    if isinstance(saved, dict) and Path(str(saved.get("path") or "")).is_file():
        return _saved_answer(saved)
    upload_id = _upload_id(device_id, sha256)
    meta_path = stage / f"{upload_id}.json"
    part = stage / f"{upload_id}.part"
    meta: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("sha256") != sha256 or meta.get("size_bytes") != size:
        part.unlink(missing_ok=True)
        meta = {"sha256": sha256, "size_bytes": size, "media_type": media_type, "taken_at": taken_at,
                "started_at": moment}
        _write_json(meta_path, meta)
    if not part.exists():
        descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    return {"done": False, "upload_id": upload_id, "next_offset": part.stat().st_size, "chunk_bytes": CHUNK_BYTES}


def _open_upload(device_id: str, upload_id: str) -> tuple[dict[str, Any], Path, Path]:
    if not _ID_RE.fullmatch(str(upload_id)):
        raise MediaRefused("unknown_upload", "Start sending this file again.", 404)
    stage = _staging(device_id)
    meta_path, part = stage / f"{upload_id}.json", stage / f"{upload_id}.part"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MediaRefused("unknown_upload", "Start sending this file again.", 404) from None
    if _upload_id(device_id, str(meta.get("sha256"))) != upload_id or not part.is_file():
        raise MediaRefused("unknown_upload", "Start sending this file again.", 404)
    return meta, meta_path, part


def upload_media_type(device_id: str, upload_id: str) -> str:
    """What kind of file an upload in progress is, or "" when there is no such upload."""
    try:
        return str(_open_upload(device_id, upload_id)[0].get("media_type") or "")
    except MediaRefused:
        return ""


def chunk(device_id: str, upload_id: str, offset: int, data: bytes) -> dict[str, Any]:
    meta, _meta_path, part = _open_upload(device_id, upload_id)
    have = part.stat().st_size
    if offset != have:
        raise MediaRefused("wrong_offset", "Carry on from where the computer is.", 409)
    if not data or len(data) > CHUNK_BYTES or have + len(data) > int(meta["size_bytes"]):
        raise MediaRefused("bad_chunk", "This piece of the file does not fit. Start again.")
    with open(part, "ab") as handle:
        handle.write(data)
    return {"next_offset": have + len(data)}


def _destination(meta: dict[str, Any]) -> Path:
    taken = datetime.fromtimestamp(float(meta["taken_at"]))
    folder = photos_root() / taken.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    stem = "VOOL_" + taken.strftime("%Y%m%d_%H%M%S") + "_" + str(meta["sha256"])[:8]
    return folder / (stem + _TYPES[meta["media_type"]][0])


def finish(device_id: str, upload_id: str) -> dict[str, Any]:
    meta, meta_path, part = _open_upload(device_id, upload_id)
    digest = hashlib.sha256()
    size = 0
    with open(part, "rb") as handle:
        head = handle.read(12)
        digest.update(head)
        size += len(head)
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
            size += len(piece)
    if size != int(meta["size_bytes"]):
        raise MediaRefused("capture_incomplete", "The computer does not have the whole file yet.", 409)
    # On the disk before anything is confirmed: the phone deletes its copy when it hears "done".
    _sync_file(part)
    if digest.hexdigest() != meta["sha256"]:
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise MediaRefused("capture_hash_mismatch", "It arrived damaged. It stays on your phone and will be sent again.", 422)
    if not _looks_right(str(meta["media_type"]), head):
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise MediaRefused("capture_type_not_allowed", "Only photos, videos and recordings from VOOL can be sent this way.")
    target = _destination(meta)
    count = 1
    while target.exists():
        count += 1
        target = target.with_name(f"{target.stem.rsplit('~', 1)[0]}~{count}{target.suffix}")
    try:
        os.replace(part, target)
    except OSError:  # the photos folder is on another disk than VOOL's data
        shutil.copyfile(part, target)
        _sync_file(target)
        part.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)
    _sync_dir(target.parent)
    # Read back from the disk: the hash the phone hears is the hash of the file as stored.
    stored = hashlib.sha256()
    with open(target, "rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            stored.update(piece)
    if stored.hexdigest() != meta["sha256"]:
        raise MediaRefused("capture_not_stored", "The computer could not store it. It stays on your phone and will be sent again.", 500)
    record = {"sha256": meta["sha256"], "path": str(target), "name": target.name, "size_bytes": size,
              "saved_at": time.time()}
    index = _index(device_id)
    index[meta["sha256"]] = record
    _write_json(_index_path(device_id), index)
    meta_path.unlink(missing_ok=True)
    return _saved_answer(record)


def record_removed(device_id: str, sha256: str) -> dict[str, Any]:
    """The phone removed its copy of a capture this computer stored. Only a capture this computer
    holds for this phone can be recorded; the answer names the stored file."""
    sha256 = str(sha256 or "").strip().lower()
    if not _SHA_RE.fullmatch(sha256):
        raise MediaRefused("bad_capture_hash", "This file could not be checked.")
    saved = _index(device_id).get(sha256)
    if not isinstance(saved, dict):
        raise MediaRefused("unknown_capture", "This computer has no such file from this phone.", 404)
    return {"recorded": True, "sha256": sha256, "saved_as": saved.get("name", "")}
