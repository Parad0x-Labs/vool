"""Files the computer sends to a paired phone: a short-lived, device-scoped copy the phone pulls.

"Send day4.jpg to my phone" copies the file into VOOL's own data folder for that one phone. Nothing
leaves the computer until the paired phone fetches it over its sealed link (``files.list`` /
``files.fetch`` in core.web.api.mobile_companion_api). A copy lives 24 hours, is readable only by
the phone it was sent to, and goes when that phone is unpaired.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 50 * 1024 * 1024
CHUNK_BYTES = 768 * 1024  # a multiple of 3: the phone joins the base64 pieces as they come
TTL_SECONDS = 24 * 3600
MAX_FILES_PER_PHONE = 40

_ID_RE = re.compile(r"out_[0-9a-f]{32}")


class SendRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _root() -> Path:
    from core.runtime_paths import active_data_dir

    root = (active_data_dir() / "mobile_companion" / "outgoing_files").resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise SendRefused("store_unavailable", "The phone file store is not usable.")
    root.mkdir(mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(root, 0o700)
    return root


def _device_dir(device_id: str) -> Path:
    digest = hashlib.sha256(str(device_id).encode("utf-8")).hexdigest()[:32]
    path = _root() / digest
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _write_private(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


def _records(device_id: str) -> list[dict[str, Any]]:
    rows = []
    for manifest in _device_dir(device_id).glob("out_*.json"):
        with contextlib.suppress(OSError, ValueError):
            record = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("device_id") == device_id:
                rows.append(record)
    return rows


def _drop(device_id: str, file_id: str) -> None:
    folder = _device_dir(device_id)
    for suffix in (".json", ".bin"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(folder / (file_id + suffix))


def _expire(device_id: str, now: float) -> list[dict[str, Any]]:
    kept = []
    for record in _records(device_id):
        if float(record.get("expires_at") or 0) <= now:
            _drop(device_id, str(record.get("id")))
        else:
            kept.append(record)
    return kept


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("id", "name", "media_type", "size_bytes", "sha256", "sent_at", "expires_at")}


def send(device_id: str, source: Path, *, session_id: str = "") -> dict[str, Any]:
    """Copy one regular file for ``device_id`` to fetch; the caller already checked the path is allowed."""

    if not device_id:
        raise SendRefused("no_phone", "No paired phone to send it to.")
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise SendRefused("unreadable", f"I couldn't open {source.name}.") from None
    try:
        info = os.fstat(descriptor)
        import stat

        if not stat.S_ISREG(info.st_mode):
            raise SendRefused("not_a_file", f"{source.name} is not a file.")
        if info.st_size <= 0:
            raise SendRefused("empty", f"{source.name} is empty.")
        if info.st_size > MAX_BYTES:
            raise SendRefused("too_large", f"{source.name} is {info.st_size / 1048576:.0f} MB; a phone can take files up to 50 MB.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(MAX_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    now = time.time()
    kept = sorted(_expire(device_id, now), key=lambda row: float(row.get("sent_at") or 0))
    for old in kept[: max(0, len(kept) - MAX_FILES_PER_PHONE + 1)]:
        _drop(device_id, str(old.get("id")))
    file_id = "out_" + secrets.token_hex(16)
    folder = _device_dir(device_id)
    _write_private(folder / (file_id + ".bin"), data)
    record = {
        "id": file_id,
        "device_id": device_id,
        "session_id": str(session_id or ""),
        "name": source.name,
        "media_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sent_at": now,
        "expires_at": now + TTL_SECONDS,
    }
    _write_private(folder / (file_id + ".json"), json.dumps(record).encode("utf-8"))
    return _public(record)


def listing(device_id: str) -> list[dict[str, Any]]:
    """What the computer sent this phone and it can still fetch, newest first."""

    rows = _expire(device_id, time.time())
    return [_public(row) for row in sorted(rows, key=lambda row: -float(row.get("sent_at") or 0))]


def chunk(device_id: str, file_id: str, offset: int) -> tuple[dict[str, Any], bytes]:
    """One piece of a sent file, only for the phone it was sent to."""

    if not _ID_RE.fullmatch(str(file_id or "")):
        raise SendRefused("not_found", "That file is no longer available.")
    record = next((row for row in _expire(device_id, time.time()) if row.get("id") == file_id), None)
    if record is None:
        raise SendRefused("not_found", "That file is no longer available.")
    start = max(0, int(offset or 0))
    with open(_device_dir(device_id) / (file_id + ".bin"), "rb") as handle:
        handle.seek(start)
        data = handle.read(CHUNK_BYTES)
    return _public(record), data


def forget_device(device_id: str) -> None:
    """Remove every file sent to ``device_id`` (on unpair)."""

    for record in _records(device_id):
        _drop(device_id, str(record.get("id")))
    with contextlib.suppress(OSError):
        _device_dir(device_id).rmdir()


def paired_phone() -> dict[str, Any] | None:
    """The one active paired phone (not a chat-app bot), or None when there is none or several."""

    phones = paired_phones()
    return phones[0] if len(phones) == 1 else None


def paired_phones() -> list[dict[str, Any]]:
    """Every active paired phone (chat-app bots are not phones)."""

    try:
        from core.mobile_companion.listener import load_settings
        from core.web.api.mobile_companion_api import mobile_companion_service

        if not load_settings().get("enabled"):
            return []
        phones = [
            device for device in mobile_companion_service().store.devices().values()
            if device.get("status") == "active" and device.get("kind") != "bot" and device.get("sealed_protocol")
        ]
    except Exception:
        return []
    return phones
