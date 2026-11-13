"""List the files VOOL has generated, across all chats, for the console's Files panel.

The panel is the answer to "where did that render / doc go?": one place to scroll (newest first),
sort, and search everything VOOL produced. This reads REAL on-disk locations — it never invents
entries. Files can be revealed in Finder via the owner-local endpoint, and only files that live under
a known root can be opened (so the endpoint can't be used to reach arbitrary paths). Add extra roots
with VOOL_FILES_DIRS (os.pathsep-separated).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.runtime_paths import active_vool_home, runtime_auxiliary_path

# Sidecar index: which chat (session) + prompt produced each generated file, so the Files panel can
# group/sort "by chat". Written at save time; a file with no index entry simply has no chat attached.
_INDEX_LOCK = threading.Lock()


def _index_path() -> Path:
    return active_vool_home() / "data" / "generated_files_index.json"


def _load_index() -> dict[str, dict[str, Any]]:
    path = _index_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def record_generated_file(path: str, *, session_id: str = "", prompt: str = "") -> None:
    """Tag a just-saved generated file with the chat + prompt that made it (best-effort, fail-soft)."""
    real = os.path.realpath(os.path.expanduser(str(path or "")))
    if not real:
        return
    entry = {"session_id": str(session_id or "").strip(), "prompt": str(prompt or "").strip()[:200]}
    with _INDEX_LOCK:
        index = _load_index()
        index[real] = entry
        # Keep the index from growing without bound.
        if len(index) > 5000:
            index = dict(list(index.items())[-4000:])
        out = _index_path()
        with contextlib.suppress(OSError):
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(json.dumps(index), encoding="utf-8")
            os.replace(tmp, out)

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".heic"}
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".gif"}
_DOC_EXT = {".md", ".txt", ".pdf", ".json", ".csv", ".html", ".srt"}
_MAX_FILES = 1000


def _default_roots() -> list[Path]:
    """Where VOOL writes generated media/docs. Renders land in ~/Pictures/VOOL-Renders today."""
    roots = [runtime_auxiliary_path("generated", legacy=Path.home() / "Pictures" / "VOOL-Renders")]
    extra = str(os.environ.get("VOOL_FILES_DIRS") or "").strip()
    if extra:
        for part in extra.split(os.pathsep):
            cleaned = part.strip()
            if cleaned:
                roots.append(Path(os.path.expanduser(cleaned)))
    # De-dupe by realpath while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = os.path.realpath(str(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def files_roots() -> list[str]:
    return [str(r) for r in _default_roots()]


def _file_type(suffix: str) -> str:
    s = str(suffix or "").lower()
    if s in _IMAGE_EXT:
        return "image"
    if s in _VIDEO_EXT:
        return "video"
    if s in _DOC_EXT:
        return "doc"
    return "file"


def list_generated_files(*, limit: int = 500) -> dict[str, Any]:
    """Every generated file under the known roots, newest first, tagged with the chat/prompt that made
    it (when known). {files, count, shown, roots}."""
    index = _load_index()
    items: list[dict[str, Any]] = []
    for root in _default_roots():
        if not root.is_dir():
            continue
        for entry in root.rglob("*"):
            if len(items) >= _MAX_FILES:
                break
            try:
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                st = entry.stat()
            except OSError:
                continue
            tag = index.get(os.path.realpath(str(entry))) or {}
            items.append({
                "name": entry.name,
                "path": str(entry),
                "type": _file_type(entry.suffix),
                "size": int(st.st_size),
                "mtime": float(st.st_mtime),   # epoch seconds; the UI formats it
                "root": str(root),
                "session_id": str(tag.get("session_id") or ""),
                "prompt": str(tag.get("prompt") or ""),
            })
    items.sort(key=lambda item: item["mtime"], reverse=True)
    shown = items[: max(1, int(limit))]
    return {"files": shown, "count": len(items), "shown": len(shown), "roots": files_roots()}


#: The picture formats ``save_image_bytes`` keeps, by their first bytes (never by a server's word).
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)
MAX_SAVED_IMAGE_BYTES = 25 * 1024 * 1024


def image_suffix_for(data: bytes) -> str:
    """The file suffix of a PNG, JPEG, GIF or WebP picture by its own bytes, or "" for anything else."""
    head = bytes(data[:16])
    for magic, suffix in _IMAGE_MAGIC:
        if head.startswith(magic):
            return suffix
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return ""


def save_image_bytes(data: bytes, *, stem: str = "image", session_id: str = "", prompt: str = "") -> str:
    """Keep a picture VOOL made elsewhere (a cloud render) as a file here, and return its path, or "".

    The chat page draws pictures only from VOOL itself, so a cloud render is saved under the first
    Files root and shown through ``/api/files/raw`` like a local render. Only a real PNG, JPEG, GIF
    or WebP up to ``MAX_SAVED_IMAGE_BYTES`` is kept; the name comes from the bytes' own digest.
    """
    blob = bytes(data or b"")
    suffix = image_suffix_for(blob)
    if not suffix or len(blob) > MAX_SAVED_IMAGE_BYTES:
        return ""
    safe_stem = re.sub(r"[^a-z0-9-]+", "-", str(stem or "image").lower()).strip("-")[:24] or "image"
    root = _default_roots()[0]
    target = root / f"{safe_stem}-{hashlib.sha256(blob).hexdigest()[:16]}{suffix}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(blob)
            os.replace(tmp, target)
    except OSError:
        return ""
    with contextlib.suppress(Exception):
        record_generated_file(str(target), session_id=session_id, prompt=prompt)
    return str(target)


def path_is_allowed(path: str) -> bool:
    """True only when ``path`` resolves to a file under one of the known roots (never an arbitrary path)."""
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        target = os.path.realpath(os.path.expanduser(raw))
    except OSError:
        return False
    for root in _default_roots():
        r = os.path.realpath(str(root))
        if target == r or target.startswith(r + os.sep):
            return True
    return False


def open_file(path: str, *, reveal: bool = True) -> bool:
    """Open a generated file. ``reveal`` shows it in Finder (``open -R``); otherwise it opens in the
    default app (``open``, e.g. Preview for an image). Refuses anything outside the known roots."""
    if not path_is_allowed(path):
        return False
    import subprocess

    real = os.path.realpath(os.path.expanduser(path))
    args = ["open", "-R", real] if reveal else ["open", real]
    try:
        subprocess.Popen(args)
        return True
    except Exception:
        return False


_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".bmp": "image/bmp", ".tiff": "image/tiff", ".heic": "image/heic",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".m4v": "video/x-m4v",
    ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".json": "application/json", ".csv": "text/csv", ".html": "text/html; charset=utf-8",
}
_MAX_RAW_BYTES = 64 * 1024 * 1024


def content_type_for(path: str) -> str:
    return _MIME.get(os.path.splitext(str(path or ""))[1].lower(), "application/octet-stream")


def read_file_bytes(path: str) -> bytes | None:
    """Bytes of a generated file for inline display, or None. Confined to the known roots + size-capped."""
    if not path_is_allowed(path):
        return None
    real = os.path.realpath(os.path.expanduser(str(path)))
    try:
        if os.path.getsize(real) > _MAX_RAW_BYTES:
            return None
        with open(real, "rb") as handle:
            return handle.read()
    except OSError:
        return None


__all__ = [
    "MAX_SAVED_IMAGE_BYTES",
    "content_type_for",
    "files_roots",
    "image_suffix_for",
    "list_generated_files",
    "open_file",
    "path_is_allowed",
    "read_file_bytes",
    "save_image_bytes",
]
