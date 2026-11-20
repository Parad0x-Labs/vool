"""The protected media library on this computer.

Photos, videos and sound are kept in VOOL's own data folder, in two sections: **Mobile** (what the phone's private
camera sends) and **Local** (files the person adds on this computer). Every item is sealed (:mod:`.sealed`) under
its own file key; the file keys are sealed by the library key, which lives in the system's secret store (the macOS
Keychain, or VOOL's sealed credential file where there is no keychain). The name, date and size of each item are
sealed too, so the folder shows nothing but random names.

The folder is kept out of the system's search and photo apps: its name ends in ``.noindex`` and it holds the
markers Spotlight, Android-style media scanners and GNOME's tracker honour. Its contents could not be read by them
anyway.

Adding is durable before it is answered: the sealed file is flushed to the disk, read back whole and its hash
checked against the original's, and only then is the item listed.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from core.media_library import sealed

SECTIONS = ("mobile", "local")
KINDS = ("photo", "video", "audio")
KEY_NAME = "media_library_key"
FOLDER = "media_library.noindex"
_ID_RE = re.compile(r"[0-9a-f]{32}")
_MARKERS = (".metadata_never_index", ".nomedia", ".trackerignore", ".noindex")
#: media type -> (kind, file ending)
TYPES: dict[str, tuple[str, str]] = {
    "image/jpeg": ("photo", ".jpg"), "image/heic": ("photo", ".heic"), "image/heif": ("photo", ".heif"),
    "image/png": ("photo", ".png"), "image/webp": ("photo", ".webp"), "image/gif": ("photo", ".gif"),
    "video/mp4": ("video", ".mp4"), "video/quicktime": ("video", ".mov"), "video/webm": ("video", ".webm"),
    "audio/mp4": ("audio", ".m4a"), "audio/mpeg": ("audio", ".mp3"), "audio/ogg": ("audio", ".ogg"),
    "audio/wav": ("audio", ".wav"), "audio/aac": ("audio", ".aac"), "audio/flac": ("audio", ".flac"),
}
_BY_ENDING = {ending: media for media, (_kind, ending) in TYPES.items()}
_BY_ENDING.update({".jpeg": "image/jpeg", ".m4v": "video/mp4", ".oga": "audio/ogg", ".opus": "audio/ogg"})
THUMB_EDGE = 360
_THUMB_SOURCE_LIMIT = 96 * 1024 * 1024


class LibraryError(Exception):
    """A typed refusal: ``code`` is a stable word the page turns into a sentence."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def media_type_for(name: str, given: str = "") -> str:
    given = str(given or "").split(";", 1)[0].strip().lower()
    if given in TYPES:
        return given
    return _BY_ENDING.get(Path(str(name or "")).suffix.lower(), "")


def _default_root() -> Path:
    from core.runtime_paths import data_path

    return data_path(FOLDER)


def _keystore_key(create: bool) -> bytes | None:
    """The library key from the system's secret store. A read whose outcome is unknown (a locked keychain, a
    timeout) raises, so it is never taken for "no key yet" and replaced."""
    from core import credential_store

    try:
        with credential_store.strict_reads():
            value = credential_store.get_credential(KEY_NAME)
    except credential_store.CredentialReadError as error:
        raise LibraryError("key_unavailable") from error
    if value:
        try:
            key = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise LibraryError("key_damaged") from error
        if len(key) != 32:
            raise LibraryError("key_damaged")
        return key
    if not create:
        return None
    key = secrets.token_bytes(32)
    credential_store.store_credential(KEY_NAME, base64.b64encode(key).decode("ascii"), label="VOOL media library")
    with credential_store.strict_reads():
        back = credential_store.get_credential(KEY_NAME)
    if back != base64.b64encode(key).decode("ascii"):
        raise LibraryError("key_unavailable")
    return key


class Library:
    def __init__(self, root: Path | None = None, *, key: bytes | None = None):
        self.root = Path(root) if root is not None else _default_root()
        self._key = key
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] | None = None

    # --- folders and key ---------------------------------------------------------------------------------------

    @property
    def items_dir(self) -> Path:
        return self.root / "items"

    @property
    def uploads_dir(self) -> Path:
        return self.root / "uploads"

    def _prepare(self) -> None:
        for folder in (self.root, self.items_dir, self.uploads_dir):
            folder.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(folder, 0o700)
        for marker in _MARKERS:
            path = self.root / marker
            if not path.exists():
                with contextlib.suppress(OSError):
                    path.write_bytes(b"")

    def key(self, *, create: bool = False) -> bytes:
        if self._key is not None:
            return self._key
        has_items = self.items_dir.is_dir() and any(self.items_dir.glob("*.meta"))
        key = _keystore_key(create=create and not has_items)
        if key is None:
            raise LibraryError("key_missing" if has_items else "empty")
        self._key = key
        return key

    # --- index ---------------------------------------------------------------------------------------------------

    def _meta_path(self, item_id: str) -> Path:
        return self.items_dir / f"{item_id}.meta"

    def _data_path(self, item_id: str) -> Path:
        return self.items_dir / f"{item_id}.vml"

    def _thumb_path(self, item_id: str) -> Path:
        return self.items_dir / f"{item_id}.thumb"

    def _load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._index is not None:
                return self._index
            index: dict[str, dict[str, Any]] = {}
            if self.items_dir.is_dir() and any(self.items_dir.glob("*.meta")):
                key = self.key()
                for meta_path in self.items_dir.glob("*.meta"):
                    item_id = meta_path.stem
                    if not _ID_RE.fullmatch(item_id) or not self._data_path(item_id).exists():
                        continue
                    try:
                        meta = json.loads(sealed.open_small(meta_path.read_bytes(), wrap_key=key, ident=item_id))
                    except (sealed.SealedError, ValueError, OSError):
                        meta = {"id": item_id, "damaged": True, "section": "local", "kind": "photo", "name": "",
                                "size": 0, "taken_at": 0, "added_at": 0}
                    index[item_id] = meta
            self._index = index
            return index

    def sweep(self) -> int:
        """Removes leftovers of adds that were cut short (a sealed file never listed, an upload never finished).
        Returns how many were removed."""
        removed = 0
        now = time.time()
        with self._lock:
            if not self.items_dir.is_dir():
                return 0
            for path in self.items_dir.iterdir():
                name = path.name
                base = name.split(".", 1)[0]
                stale = now - path.stat().st_mtime > 3600
                if name.endswith(".part") or (name.startswith(".") and name.endswith(".tmp")):
                    if stale:
                        sealed.remove(path) if ".vml" in name else path.unlink(missing_ok=True)
                        removed += 1
                elif name.endswith(".vml") and not self._meta_path(base).exists() and stale:
                    sealed.remove(path)
                    removed += 1
            if self.uploads_dir.is_dir():
                for upload in self.uploads_dir.iterdir():
                    if now - upload.stat().st_mtime > 7 * 24 * 3600:
                        for piece in upload.glob("*") if upload.is_dir() else []:
                            piece.unlink(missing_ok=True)
                        with contextlib.suppress(OSError):
                            upload.rmdir() if upload.is_dir() else upload.unlink()
                        removed += 1
        return removed

    # --- reading -------------------------------------------------------------------------------------------------

    def items(self, *, section: str = "", kind: str = "", sort: str = "date", descending: bool = True) -> list[dict[str, Any]]:
        listed = [dict(meta) for meta in self._load().values()
                  if (not section or meta.get("section") == section) and (not kind or meta.get("kind") == kind)]
        field = "size" if sort == "size" else "taken_at"
        listed.sort(key=lambda m: (m.get(field) or 0, m.get("added_at") or 0, m.get("id")), reverse=descending)
        return listed

    def get(self, item_id: str) -> dict[str, Any]:
        meta = self._load().get(str(item_id or ""))
        if meta is None:
            raise LibraryError("not_found")
        return dict(meta)

    def find_sha(self, sha256: str, *, section: str = "") -> dict[str, Any] | None:
        for meta in self._load().values():
            if meta.get("sha256") == sha256 and (not section or meta.get("section") == section) and not meta.get("damaged"):
                return dict(meta)
        return None

    def open(self, item_id: str) -> sealed.Opened:
        self.get(item_id)
        return sealed.Opened(self._data_path(item_id), wrap_key=self.key(), ident=item_id)

    def thumb(self, item_id: str) -> bytes | None:
        self.get(item_id)
        path = self._thumb_path(item_id)
        if not path.exists():
            return None
        try:
            return sealed.open_small(path.read_bytes(), wrap_key=self.key(), ident=item_id + ":thumb")
        except sealed.SealedError:
            return None

    def check(self, item_id: str) -> bool:
        """Reads an item back whole; True when every piece opens and the hash matches."""
        meta = self.get(item_id)
        try:
            sealed.verify(self._data_path(item_id), wrap_key=self.key(), ident=item_id, sha256=meta.get("sha256"), size=meta.get("size"))
            return True
        except (sealed.SealedError, OSError):
            return False

    def summary(self) -> dict[str, Any]:
        counts = {section: {kind: 0 for kind in KINDS} for section in SECTIONS}
        total = 0
        for meta in self._load().values():
            if meta.get("section") in counts and meta.get("kind") in KINDS:
                counts[meta["section"]][meta["kind"]] += 1
            total += int(meta.get("size") or 0)
        return {"counts": counts, "bytes": total, "items": len(self._load())}

    # --- adding --------------------------------------------------------------------------------------------------

    def add(self, source: Iterable[bytes], *, section: str, name: str, media_type: str, taken_at: float,
            expect_sha256: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Seals ``source`` into the library and returns the new item. When the same content (by hash) is already
        in that section, the existing item is returned and nothing is added twice."""
        if section not in SECTIONS:
            raise LibraryError("bad_section")
        media_type = media_type_for(name, media_type)
        if media_type not in TYPES:
            raise LibraryError("unsupported")
        if expect_sha256:
            existing = self.find_sha(expect_sha256, section=section)
            if existing:
                return existing
        with self._lock:
            self._prepare()
            key = self.key(create=True)
            index = self._load()
        item_id = secrets.token_hex(16)
        kind = TYPES[media_type][0]
        keep_for_thumb = io.BytesIO() if kind == "photo" else None
        thumb_state = {"too_big": False}

        def feed() -> Iterator[bytes]:
            for chunk in source:
                if keep_for_thumb is not None and not thumb_state["too_big"]:
                    if keep_for_thumb.tell() + len(chunk) <= _THUMB_SOURCE_LIMIT:
                        keep_for_thumb.write(chunk)
                    else:
                        thumb_state["too_big"] = True
                        keep_for_thumb.seek(0)
                        keep_for_thumb.truncate(0)
                yield chunk

        part = self.items_dir / f"{item_id}.vml.part"
        size, sha, _paths = sealed.seal(feed(), part, wrap_key=key, ident=item_id)
        try:
            if expect_sha256 and sha != expect_sha256:
                raise LibraryError("different_content")
            sealed.verify(part, wrap_key=key, ident=item_id, sha256=sha, size=size)
            existing = self.find_sha(sha, section=section)
            if existing:
                sealed.remove(part)
                return existing
            os.replace(part, self._data_path(item_id))
        except BaseException:
            sealed.remove(part)
            raise
        now = time.time()
        meta = {
            "id": item_id, "section": section, "kind": kind, "name": Path(str(name or "")).name[:200] or f"{kind}{TYPES[media_type][1]}",
            "media_type": media_type, "size": size, "sha256": sha, "taken_at": float(taken_at or now), "added_at": now,
        }
        if extra:
            meta.update({k: v for k, v in extra.items() if k not in meta})
        thumb = _thumbnail(keep_for_thumb.getvalue()) if keep_for_thumb is not None and not thumb_state["too_big"] and keep_for_thumb.tell() else None
        if thumb:
            sealed.write_file(self._thumb_path(item_id), sealed.seal_small(thumb, wrap_key=key, ident=item_id + ":thumb"))
        try:
            sealed.write_file(self._meta_path(item_id), sealed.seal_small(json.dumps(meta).encode("utf-8"), wrap_key=key, ident=item_id))
        except BaseException:
            sealed.remove(self._data_path(item_id))
            self._thumb_path(item_id).unlink(missing_ok=True)
            raise
        with self._lock:
            index[item_id] = meta
        return dict(meta)

    def add_file(self, path: Path, *, section: str, name: str = "", media_type: str = "", taken_at: float | None = None,
                 expect_sha256: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        path = Path(path)
        stamp = taken_at if taken_at is not None else path.stat().st_mtime
        return self.add(sealed.file_chunks(path), section=section, name=name or path.name, media_type=media_type,
                        taken_at=stamp, expect_sha256=expect_sha256, extra=extra)

    # --- removing ------------------------------------------------------------------------------------------------

    def remove(self, item_id: str) -> dict[str, Any]:
        meta = self.get(item_id)
        with self._lock:
            sealed.remove(self._data_path(item_id))
            self._thumb_path(item_id).unlink(missing_ok=True)
            self._meta_path(item_id).unlink(missing_ok=True)
            self._load().pop(item_id, None)
        return meta

    # --- adding from this computer in slices (the page sends a picked file in pieces) -------------------------

    def upload_begin(self, *, name: str, media_type: str, size: int, taken_at: float) -> str:
        media_type = media_type_for(name, media_type)
        if media_type not in TYPES:
            raise LibraryError("unsupported")
        if size <= 0 or size > 64 * 1024 ** 3:
            raise LibraryError("too_large")
        self._prepare()
        key = self.key(create=True)
        upload_id = secrets.token_hex(16)
        folder = self.uploads_dir / upload_id
        folder.mkdir(mode=0o700)
        info = {"name": Path(str(name or "")).name[:200], "media_type": media_type, "size": int(size), "taken_at": float(taken_at or time.time())}
        sealed.write_file(folder / "info", sealed.seal_small(json.dumps(info).encode("utf-8"), wrap_key=key, ident=upload_id))
        return upload_id

    def _upload(self, upload_id: str) -> tuple[Path, dict[str, Any], int]:
        if not _ID_RE.fullmatch(str(upload_id or "")):
            raise LibraryError("not_found")
        folder = self.uploads_dir / upload_id
        if not (folder / "info").exists():
            raise LibraryError("not_found")
        info = json.loads(sealed.open_small((folder / "info").read_bytes(), wrap_key=self.key(), ident=upload_id))
        have = 0
        for piece in sorted(folder.glob("p*")):
            have += int(piece.name.split("-", 1)[1])
        return folder, info, have

    def upload_piece(self, upload_id: str, offset: int, data: bytes) -> int:
        """Stores one slice, sealed, at exactly the offset already held. Returns the new offset."""
        with self._lock:
            folder, info, have = self._upload(upload_id)
            if offset != have:
                raise LibraryError("wrong_offset")
            if not data or have + len(data) > info["size"]:
                raise LibraryError("too_large")
            count = len(list(folder.glob("p*")))
            ident = f"{upload_id}:{count}"
            sealed.write_file(folder / f"p{count:06d}-{len(data)}", sealed.seal_small(data, wrap_key=self.key(), ident=ident))
            return have + len(data)

    def upload_finish(self, upload_id: str) -> dict[str, Any]:
        folder, info, have = self._upload(upload_id)
        if have != info["size"]:
            raise LibraryError("incomplete")
        key = self.key()
        pieces = sorted(folder.glob("p*"))

        def plain() -> Iterator[bytes]:
            for number, piece in enumerate(pieces):
                yield sealed.open_small(piece.read_bytes(), wrap_key=key, ident=f"{upload_id}:{number}")

        item = self.add(plain(), section="local", name=info["name"], media_type=info["media_type"], taken_at=info["taken_at"])
        self.upload_cancel(upload_id)
        return item

    def upload_cancel(self, upload_id: str) -> None:
        if not _ID_RE.fullmatch(str(upload_id or "")):
            return
        folder = self.uploads_dir / upload_id
        for piece in folder.glob("*"):
            piece.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            folder.rmdir()


def _thumbnail(data: bytes) -> bytes | None:
    """A small JPEG of a photo, kept sealed next to it, so the page can show a grid without opening every photo."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None
    with contextlib.suppress(Exception):
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
    try:
        Image.MAX_IMAGE_PIXELS = 200_000_000
        with Image.open(io.BytesIO(data)) as image:
            image.draft("RGB", (THUMB_EDGE * 2, THUMB_EDGE * 2))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((THUMB_EDGE, THUMB_EDGE))
            out = io.BytesIO()
            image.convert("RGB").save(out, "JPEG", quality=78)
            return out.getvalue()
    except Exception:
        return None


_LIBRARY: Library | None = None
_LIBRARY_LOCK = threading.Lock()


def library() -> Library:
    """The library of this VOOL home."""
    global _LIBRARY
    with _LIBRARY_LOCK:
        root = _default_root()
        if _LIBRARY is None or _LIBRARY.root != root:
            _LIBRARY = Library(root)
        return _LIBRARY


def digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    for chunk in sealed.file_chunks(path):
        digest.update(chunk)
    return digest.hexdigest()
