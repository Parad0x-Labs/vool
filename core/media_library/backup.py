"""One-click, incremental, encrypted backup of the media library to a USB drive, and restore from it.

On the drive VOOL writes one folder, ``VOOL media backup``::

    vault.json        how to open the backup: the password's salt and settings, and a check value. No key.
    manifest.vmb      the sealed list of what the backup holds (content hash, size, name, date, section).
    objects/<oid>.vmo each item, sealed under the backup key (:mod:`.sealed`), split into 1 GiB segments
                      (<oid>.vmo.1, .2 ...) so a stick formatted FAT32 can hold a long video.

The backup key comes from a backup password (scrypt, at least 12 characters), so the backup can be opened on
another computer when this one is lost. The derived key is remembered in this computer's secret store, so later
backups are one click. Object names are a keyed hash of the content: the drive shows nothing about what is on it.

How it stays right:

* **Incremental.** Only items whose content is not yet in the manifest are copied. Copying the same library twice
  copies nothing the second time.
* **Each object is checked before it counts.** It is written as ``.part``, flushed, read back whole and its hash
  compared with the library's; only then is it renamed into place and listed.
* **Unplugged mid-copy.** The run stops with ``drive_gone``. What was listed is complete; a ``.part`` is removed
  next time, and a complete object that was renamed but not yet listed is checked and taken over. The next run
  carries on with what is missing.
* **A full drive** stops the run cleanly (``drive_full``) before an object that would not fit, or on the disk's
  own "no space" error, leaving the backup consistent.
* **Tampering** fails the check: every piece of every object and the manifest are authenticated. A changed byte is
  reported by name as damaged; it is never restored as if it were genuine.
* **Removals are mirrored only when asked.** By default the backup keeps everything it ever held. With mirroring
  on, items removed from the library are removed from the drive too, and only after the person confirmed that
  exact number.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import hmac
import json
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from core.media_library import sealed
from core.media_library.store import Library, LibraryError

FOLDER = "VOOL media backup"
KIND = "vool-media-backup"
VERSION = 1
SEGMENT = 1024 ** 3
MIN_PASSWORD = 12
MARGIN = 4 * 1024 * 1024
_SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}
_REMEMBER = "media_backup_key_"
_SAVE_EVERY_OBJECTS = 20
_SAVE_EVERY_SECONDS = 10.0


class BackupStop(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _derive(password: str, salt: bytes, params: dict[str, int]) -> bytes:
    return Scrypt(salt=salt, length=32, n=int(params["n"]), r=int(params["r"]), p=int(params["p"])).derive(password.encode("utf-8"))


def _oid(key: bytes, sha256: str) -> str:
    return hmac.new(key, b"object\x00" + sha256.encode("ascii"), hashlib.sha256).hexdigest()[:40]


def _is_drive_gone(error: OSError) -> bool:
    return error.errno in {errno.EIO, errno.ENOENT, errno.ENODEV, errno.ENXIO, errno.ESTALE, errno.EROFS, errno.EACCES, errno.EPERM}


class Vault:
    """One backup folder on one drive, opened with its key."""

    def __init__(self, folder: Path, key: bytes, vault_id: str):
        self.folder, self.key, self.vault_id = folder, key, vault_id
        self.objects = folder / "objects"

    # --- opening ---------------------------------------------------------------------------------------------

    @staticmethod
    def describe(folder: Path) -> dict[str, Any] | None:
        try:
            info = json.loads((folder / "vault.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict) or info.get("kind") != KIND or info.get("version") != VERSION:
            return None
        return info

    @classmethod
    def create(cls, folder: Path, password: str) -> Vault:
        if len(password or "") < MIN_PASSWORD:
            raise BackupStop("password_short")
        if cls.describe(folder):
            raise BackupStop("backup_exists")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "objects").mkdir(exist_ok=True)
        salt = secrets.token_bytes(16)
        vault_id = secrets.token_hex(8)
        key = _derive(password, salt, _SCRYPT)
        info = {
            "kind": KIND, "version": VERSION, "vault_id": vault_id,
            "kdf": {"name": "scrypt", **_SCRYPT, "salt": base64.b64encode(salt).decode("ascii")},
            "check": base64.b64encode(sealed.seal_small(KIND.encode(), wrap_key=key, ident="check:" + vault_id)).decode("ascii"),
            "note": "A VOOL media backup. It can only be opened with its backup password, in VOOL.",
        }
        sealed.write_file(folder / "vault.json", json.dumps(info, indent=1).encode("utf-8"), mode=0o644)
        vault = cls(folder, key, vault_id)
        vault.save_manifest({"vault_id": vault_id, "objects": {}, "updated_at": time.time()})
        return vault

    @classmethod
    def open(cls, folder: Path, *, password: str = "", key: bytes | None = None) -> Vault:
        info = cls.describe(folder)
        if info is None:
            raise BackupStop("no_backup")
        vault_id = str(info.get("vault_id") or "")
        if key is None:
            if not password:
                raise BackupStop("password_needed")
            kdf = info.get("kdf") or {}
            try:
                key = _derive(password, base64.b64decode(kdf["salt"]), {"n": kdf["n"], "r": kdf["r"], "p": kdf["p"]})
            except (KeyError, ValueError, TypeError) as error:
                raise BackupStop("backup_damaged") from error
        try:
            if sealed.open_small(base64.b64decode(info.get("check") or ""), wrap_key=key, ident="check:" + vault_id) != KIND.encode():
                raise BackupStop("wrong_password")
        except (sealed.SealedError, ValueError) as error:
            raise BackupStop("wrong_password") from error
        (folder / "objects").mkdir(exist_ok=True)
        return cls(folder, key, vault_id)

    # --- manifest ----------------------------------------------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        path = self.folder / "manifest.vmb"
        if not path.exists():
            return {"vault_id": self.vault_id, "objects": {}, "updated_at": 0}
        try:
            data = json.loads(sealed.open_small(path.read_bytes(), wrap_key=self.key, ident="manifest:" + self.vault_id))
        except (sealed.SealedError, ValueError) as error:
            raise BackupStop("backup_damaged") from error
        except OSError as error:
            raise BackupStop("drive_gone") from error
        if not isinstance(data, dict) or not isinstance(data.get("objects"), dict):
            raise BackupStop("backup_damaged")
        return data

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = time.time()
        blob = sealed.seal_small(json.dumps(manifest, separators=(",", ":")).encode("utf-8"), wrap_key=self.key, ident="manifest:" + self.vault_id)
        sealed.write_file(self.folder / "manifest.vmb", blob, mode=0o644)

    def object_path(self, oid: str) -> Path:
        return self.objects / f"{oid}.vmo"

    def oid(self, sha256: str) -> str:
        return _oid(self.key, sha256)


# --- drives -----------------------------------------------------------------------------------------------------

def drives() -> list[dict[str, Any]]:
    """Removable and external drives (never the computer's own disk), each with whether it holds a media backup."""
    from core.wallet import usb_backup

    listed = []
    for mount, name, kind in usb_backup._drives_now():
        info = Vault.describe(mount / FOLDER)
        listed.append({"drive_id": usb_backup._drive_id(mount), "name": name, "kind": kind,
                       "free_bytes": usb_backup._free_bytes(mount), "has_backup": bool(info),
                       "remembered": bool(info and remembered_key(str(info.get("vault_id") or "")))})
    return listed


def drive_folder(drive_id: str) -> Path:
    from core.wallet import usb_backup

    for mount, _name, _kind in usb_backup._drives_now():
        if usb_backup._drive_id(mount) == str(drive_id or ""):
            return mount / FOLDER
    raise BackupStop("drive_not_found")


def remembered_key(vault_id: str) -> bytes | None:
    if not vault_id:
        return None
    from core import credential_store

    with contextlib.suppress(Exception):
        value = credential_store.get_credential(_REMEMBER + vault_id)
        if value:
            key = base64.b64decode(value)
            if len(key) == 32:
                return key
    return None


def remember_key(vault: Vault) -> None:
    from core import credential_store

    with contextlib.suppress(Exception):
        credential_store.store_credential(_REMEMBER + vault.vault_id, base64.b64encode(vault.key).decode("ascii"), label="VOOL media backup")


def open_vault(folder: Path, *, password: str = "", create: bool = False) -> Vault:
    info = Vault.describe(folder)
    if info is None:
        if not create:
            raise BackupStop("no_backup")
        vault = Vault.create(folder, password)
        remember_key(vault)
        return vault
    if not password:
        key = remembered_key(str(info.get("vault_id") or ""))
        if key is None:
            raise BackupStop("password_needed")
        return Vault.open(folder, key=key)
    vault = Vault.open(folder, password=password)
    remember_key(vault)
    return vault


# --- planning, backing up, checking, restoring ----------------------------------------------------------------------

def _free(folder: Path) -> int:
    """Free bytes on the drive. A drive that is no longer there raises (it is not "full")."""
    if not folder.is_dir():
        raise OSError(errno.ENOENT, "the drive is gone")
    return shutil.disk_usage(folder).free


def _free_or_zero(folder: Path) -> int:
    try:
        return _free(folder)
    except OSError:
        return 0


def _adopt_leftovers(vault: Vault, manifest: dict[str, Any], library: Library) -> int:
    """After a run that was cut short: ``.part`` files go; a complete object that was never listed is checked and
    listed if it is right."""
    adopted = 0
    known = manifest["objects"]
    by_oid = {vault.oid(meta["sha256"]): meta for meta in library.items() if meta.get("sha256")}
    for path in list(vault.objects.glob("*")):
        name = path.name
        if ".part" in name:
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        if not name.endswith(".vmo"):
            continue
        oid = name[:-4]
        if oid in known:
            continue
        meta = by_oid.get(oid)
        try:
            if meta is None:
                raise sealed.SealedError("unknown")
            sealed.verify(path, wrap_key=vault.key, ident="obj:" + oid, sha256=meta["sha256"], size=meta["size"])
            known[oid] = _entry(meta, path)
            adopted += 1
        except (sealed.SealedError, OSError):
            sealed.remove(path)
    return adopted


def _entry(meta: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "sha256": meta["sha256"], "size": int(meta["size"]), "name": meta.get("name", ""), "kind": meta.get("kind", ""),
        "section": meta.get("section", ""), "media_type": meta.get("media_type", ""), "taken_at": meta.get("taken_at", 0),
        "added_at": meta.get("added_at", 0), "sealed_size": sum(p.stat().st_size for p in sealed.segment_paths(path)),
    }


def plan(library: Library, vault: Vault) -> dict[str, Any]:
    manifest = vault.manifest()
    held = {entry.get("sha256") for entry in manifest["objects"].values()}
    here = {meta["sha256"] for meta in library.items() if meta.get("sha256") and not meta.get("damaged")}
    to_copy = [meta for meta in library.items(sort="date", descending=False) if meta.get("sha256") in here and meta["sha256"] not in held]
    removed_here = [oid for oid, entry in manifest["objects"].items() if entry.get("sha256") not in here]
    return {
        "to_copy": len(to_copy), "bytes": sum(sealed.sealed_size(int(m["size"])) for m in to_copy),
        "on_drive": len(manifest["objects"]), "removed_here": len(removed_here), "free_bytes": _free_or_zero(vault.folder),
    }


def backup(library: Library, vault: Vault, *, mirror_removals: bool = False, confirm_removals: int | None = None,
           progress: Callable[[dict[str, Any]], None] | None = None, stop: threading.Event | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"copied": 0, "bytes": 0, "already": 0, "removed": 0, "adopted": 0, "stopped": "", "left": 0}
    try:
        manifest = vault.manifest()
        report["adopted"] = _adopt_leftovers(vault, manifest, library)
    except OSError as error:
        report["stopped"] = "drive_gone" if _is_drive_gone(error) else "drive_error"
        return report
    known = manifest["objects"]
    held = {entry.get("sha256") for entry in known.values()}
    items = [m for m in library.items(sort="date", descending=False) if m.get("sha256") and not m.get("damaged")]
    here = {m["sha256"] for m in items}
    removed_here = [oid for oid, entry in known.items() if entry.get("sha256") not in here]
    if mirror_removals and removed_here and confirm_removals != len(removed_here):
        report["stopped"] = "confirm_removals"
        report["removed_here"] = len(removed_here)
        return report
    todo = [m for m in items if m["sha256"] not in held]
    report["already"] = len(items) - len(todo)
    last_save, unsaved = time.monotonic(), report["adopted"]
    try:
        for number, meta in enumerate(todo):
            if stop is not None and stop.is_set():
                report["stopped"] = "cancelled"
                break
            if progress:
                progress({"done": number, "total": len(todo), "name": meta.get("name", "")})
            oid = vault.oid(meta["sha256"])
            final = vault.object_path(oid)
            if _free(vault.folder) < sealed.sealed_size(int(meta["size"])) + MARGIN:
                report["stopped"] = "drive_full"
                break
            part = final.with_name(final.name + ".part")
            try:
                opened = library.open(meta["id"])
                sealed.seal(opened.chunks(), part, wrap_key=vault.key, ident="obj:" + oid, segment_limit=SEGMENT, mode=0o644)
                sealed.verify(part, wrap_key=vault.key, ident="obj:" + oid, sha256=meta["sha256"], size=meta["size"])
                for index, segment in enumerate(sealed.segment_paths(part)):
                    os.replace(segment, final if index == 0 else final.with_name(f"{final.name}.{index}"))
            except OSError as error:
                with contextlib.suppress(Exception):
                    sealed.remove(part)
                if error.errno == errno.ENOSPC:
                    report["stopped"] = "drive_full"
                    break
                raise
            except sealed.SealedError:
                with contextlib.suppress(Exception):
                    sealed.remove(part)
                report["stopped"] = "copy_check_failed"
                break
            known[oid] = _entry(meta, final)
            report["copied"] += 1
            report["bytes"] += int(meta["size"])
            unsaved += 1
            if unsaved >= _SAVE_EVERY_OBJECTS or time.monotonic() - last_save > _SAVE_EVERY_SECONDS:
                vault.save_manifest(manifest)
                last_save, unsaved = time.monotonic(), 0
        if mirror_removals and not report["stopped"]:
            for oid in removed_here:
                sealed.remove(vault.object_path(oid))
                known.pop(oid, None)
                report["removed"] += 1
        vault.save_manifest(manifest)
    except OSError as error:
        report["stopped"] = "drive_gone" if _is_drive_gone(error) else ("drive_full" if error.errno == errno.ENOSPC else "drive_error")
        with contextlib.suppress(Exception):
            vault.save_manifest(manifest)
    report["left"] = len([m for m in items if vault.oid(m["sha256"]) not in known])
    report["on_drive"] = len(known)
    if progress:
        progress({"done": report["copied"], "total": len(todo), "name": ""})
    return report


def check(vault: Vault, *, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Reads every object on the drive back whole. Lists the damaged or missing ones by name."""
    manifest = vault.manifest()
    damaged: list[str] = []
    ok = 0
    entries = list(manifest["objects"].items())
    for number, (oid, entry) in enumerate(entries):
        if progress:
            progress({"done": number, "total": len(entries), "name": entry.get("name", "")})
        try:
            sealed.verify(vault.object_path(oid), wrap_key=vault.key, ident="obj:" + oid, sha256=entry["sha256"], size=entry["size"])
            ok += 1
        except (sealed.SealedError, OSError, KeyError):
            damaged.append(str(entry.get("name") or oid))
    return {"ok": ok, "damaged": damaged, "total": len(entries)}


def restore(library: Library, vault: Vault, *, progress: Callable[[dict[str, Any]], None] | None = None,
            stop: threading.Event | None = None) -> dict[str, Any]:
    """Brings back every item on the drive that the library does not hold. A damaged object is skipped and named."""
    manifest = vault.manifest()
    have = {meta.get("sha256") for meta in library.items()}
    todo = [(oid, entry) for oid, entry in manifest["objects"].items() if entry.get("sha256") not in have]
    report: dict[str, Any] = {"restored": 0, "already": len(manifest["objects"]) - len(todo), "damaged": [], "stopped": ""}
    for number, (oid, entry) in enumerate(todo):
        if stop is not None and stop.is_set():
            report["stopped"] = "cancelled"
            break
        if progress:
            progress({"done": number, "total": len(todo), "name": entry.get("name", "")})
        try:
            opened = sealed.Opened(vault.object_path(oid), wrap_key=vault.key, ident="obj:" + oid)
            library.add(opened.chunks(), section=entry.get("section") or "local", name=entry.get("name") or "",
                        media_type=entry.get("media_type") or "", taken_at=float(entry.get("taken_at") or 0),
                        expect_sha256=entry["sha256"])
            report["restored"] += 1
        except (sealed.SealedError, LibraryError, KeyError):
            report["damaged"].append(str(entry.get("name") or oid))
        except OSError as error:
            report["stopped"] = "drive_gone" if _is_drive_gone(error) else "drive_error"
            break
    return report
