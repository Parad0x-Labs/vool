"""A wallet's backup key, sealed with a backup password, written to a USB stick the person keeps offline.

The same sealed copy as the Keychain backup (:mod:`core.wallet.keychain_backup`): the backup key under AES-256-GCM,
keyed by scrypt (N=2^17, r=8, p=1) from a backup password of at least 12 characters that is not the PIN, bound to
its network and address. Only the place differs: a file on a removable drive, so a copy survives a lost or wiped
computer and lives in a drawer, not online.

How it stays safe:

* VOOL writes only to a drive this computer reports as removable (USB stick, SD card), never to an internal disk,
  a disk image or a network share. The page names a drive by an opaque id from :func:`drives`; it can never hand
  VOOL a path, so no door here writes or reads anywhere else.
* The file holds the sealed copy plus the wallet's name, network and public address (needed to open it and to
  show it in the list). It holds no key, PIN or password. Whoever finds the stick learns the address, never the key.
* A write is a temporary file, flushed to the stick, renamed into place, then read back and opened with the
  password before VOOL reports it saved. A copy that does not open is removed and the save refused.
* Opening a copy goes through the wallet recovery throttle, so this computer is not a fast guessing oracle.
* The money rule is untouched: restoring re-seals the same key under a new PIN or password; it signs and sends
  nothing, and a wallet brought back is never Main.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.wallet import chains, custody
from core.wallet import keychain_backup as kb
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.usb_backup"
FOLDER = "VOOL wallet backups"
SUFFIX = ".voolbackup"
FILE_KIND = "vool-wallet-backup"
FILE_VERSION = 1
MAX_FILE_BYTES = 64 * 1024
MAX_FILES_PER_DRIVE = 200
_DISKUTIL_TIMEOUT = 5.0


def _fault(reason: str, *, code: str = "wallet_setup_state_invalid", source_context: dict[str, Any] | None = None, **context: Any) -> Exception:
    return wallet_fault(code, authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


# --- which drives count as removable -------------------------------------------------------------------------

def _mac_volume_is_removable(mount: Path) -> bool:
    """diskutil's own answer: an external, ejectable or removable physical volume that is writable. A disk image,
    a network share, the boot disk and its siblings are never one."""
    try:
        out = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(mount)], capture_output=True, timeout=_DISKUTIL_TIMEOUT, check=False)
    except Exception:
        return False
    if out.returncode != 0 or not out.stdout:
        return False
    try:
        info = plistlib.loads(out.stdout)
    except Exception:
        return False
    if str(info.get("MountPoint") or "") != str(mount):
        return False
    if info.get("Internal") is True or str(info.get("VirtualOrPhysical") or "") == "Virtual" or str(info.get("BusProtocol") or "") == "Disk Image":
        return False
    if info.get("WritableVolume") is False:
        return False
    return bool(info.get("Ejectable") or info.get("Removable") or info.get("RemovableMedia") or info.get("RemovableMediaOrExternalDevice")
                or info.get("Internal") is False)


def _mac_mounts() -> list[tuple[Path, str]]:
    root = Path("/Volumes")
    found: list[tuple[Path, str]] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir() or os.path.ismount(entry) is False:
                continue  # the boot disk shows here as a link to "/"
        except OSError:
            continue
        if _mac_volume_is_removable(entry):
            found.append((entry, entry.name))
    return found


def _linux_block_is_removable(device: str) -> bool:
    """A /dev/sdX1 style device whose disk says it is removable or hangs off a USB bus."""
    name = os.path.basename(os.path.realpath(device))
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return False
    block = Path("/sys/class/block") / name
    if not block.exists():
        return False
    disk = block if not (block / "partition").exists() else block.resolve().parent
    with contextlib.suppress(OSError):
        if (disk / "removable").read_text().strip() == "1":
            return True
    with contextlib.suppress(OSError):
        return "/usb" in str(disk.resolve())
    return False


def _unescape_mount(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def _linux_mounts() -> list[tuple[Path, str]]:
    try:
        lines = Path("/proc/self/mounts").read_text().splitlines()
    except OSError:
        return []
    found: list[tuple[Path, str]] = []
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        device, mount, options = parts[0], Path(_unescape_mount(parts[1])), parts[3].split(",")
        if "ro" in options or not device.startswith("/dev/"):
            continue
        if not (str(mount).startswith("/media/") or str(mount).startswith("/run/media/")):
            continue
        if _linux_block_is_removable(device):
            found.append((mount, mount.name))
    return found


def _mounts() -> list[tuple[Path, str]]:
    """(mount point, display name) of every removable drive this computer has mounted and writable now."""
    if sys.platform == "darwin":
        return _mac_mounts()
    if sys.platform.startswith("linux"):
        return _linux_mounts()
    return []


def _drive_id(mount: Path) -> str:
    return "usb-" + hashlib.sha256(str(mount).encode("utf-8")).hexdigest()[:16]


def _free_bytes(mount: Path) -> int:
    try:
        stat = os.statvfs(mount)
        return int(stat.f_bavail * stat.f_frsize)
    except OSError:
        return 0


def drives() -> list[dict[str, Any]]:
    """The removable drives VOOL may write a backup to, by opaque id and name. Never a path."""
    return [{"drive_id": _drive_id(mount), "name": name, "free_bytes": _free_bytes(mount)} for mount, name in _mounts()]


def _mount_for(drive_id: str, *, source_context: dict[str, Any] | None) -> tuple[Path, str]:
    for mount, name in _mounts():
        if _drive_id(mount) == str(drive_id or ""):
            return mount, name
    raise _fault("usb_drive_not_found", source_context=source_context)


# --- the file ---------------------------------------------------------------------------------------------------

def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", str(label or "")).strip()[:24]
    return cleaned or "Wallet"


def _file_name(label: str, address: str) -> str:
    return f"VOOL {_safe_label(label)} {str(address)[:8]}{SUFFIX}"


def _document(envelope: str, *, label: str, network: str, address: str, backup_format: str) -> str:
    return json.dumps({"kind": FILE_KIND, "v": FILE_VERSION, "label": str(label or "")[:40], "network": network, "address": address,
                       "format": backup_format, "saved_at": utcnow(), "sealed": json.loads(envelope),
                       "note": "A VOOL wallet backup, locked with a backup password. Open it in VOOL: Settings, Crypto."}, indent=1, sort_keys=True)


def _read_document(path: Path) -> dict[str, Any] | None:
    """A backup file on the stick, or None when it is not one (or too big, or unreadable). Never raises."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("kind") != FILE_KIND or int(data.get("v") or 0) != FILE_VERSION or not isinstance(data.get("sealed"), dict):
        return None
    try:
        network = chains.resolve_network(str(data.get("network") or "")).network
    except Exception:
        return None
    address = str(data.get("address") or "")
    if not address or str(data["sealed"].get("network") or "") != network or str(data["sealed"].get("address") or "") != address:
        return None
    return {**data, "network": network, "address": address, "envelope": json.dumps(data["sealed"], sort_keys=True)}


def _file_id(drive_id: str, name: str) -> str:
    return f"{drive_id}:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def _backup_files(mount: Path) -> list[Path]:
    folder = mount / FOLDER
    try:
        if folder.is_symlink() or not folder.is_dir():
            return []
        listed = sorted(p for p in folder.iterdir() if p.name.endswith(SUFFIX) and not p.name.startswith("."))
    except OSError:
        return []
    return listed[:MAX_FILES_PER_DRIVE]


def _write_atomically(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(exist_ok=True)
    if folder.is_symlink() or not folder.is_dir():
        raise OSError("backup folder is not a plain folder")
    final = folder / name
    temp = folder / f".{name}.{os.getpid()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, final)
    except Exception:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    with contextlib.suppress(OSError):  # some stick file systems cannot sync a folder; the file itself is synced
        dir_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    return final


# --- the person's doors -------------------------------------------------------------------------------------------

def save(wallet_id: str, *, drive_id: str, credential: str = "", password: str, confirmation: str,
         source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seal this wallet's backup key with ``password`` and write it to the chosen USB stick. Proven with the
    wallet's own PIN or password. Saving again to the same stick replaces that wallet's earlier file there."""
    from core.secret_redaction import register_exact_secret
    from core.wallet import pilot_custody

    custody.require_enabled(source_context=source_context)
    row = kb._owner_pilot_row(wallet_id, source_context=source_context)
    kb._check_password(str(password or ""), str(confirmation or ""), credential=str(credential or ""), source_context=source_context)
    mount, drive_name = _mount_for(drive_id, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    secret, _generation = pilot_custody._verify(row, str(credential or ""), source_context=source_context)
    try:
        backup_value, backup_format = pilot_custody._backup_for(spec, secret, row["public_key"])
    finally:
        del secret
    register_exact_secret(backup_value)
    envelope = kb.seal(backup_value, password=str(password), network=spec.network, address=row["public_key"])
    del backup_value
    label = str(row.get("label") or "")
    name = _file_name(label, row["public_key"])
    try:
        written = _write_atomically(mount / FOLDER, name, _document(envelope, label=label, network=spec.network, address=row["public_key"],
                                                                     backup_format=backup_format))
    except OSError:
        raise _fault("usb_write_failed", source_context=source_context) from None
    back = _read_document(written)
    if back is None or kb.unseal(back["envelope"], password=str(password), network=spec.network, address=row["public_key"]) is None:
        with contextlib.suppress(OSError):
            written.unlink()
        raise _fault("usb_readback_failed", source_context=source_context)
    with connection() as conn:
        conn.execute("INSERT INTO wallet_usb_backups (wallet_id, network, address, drive_name, saved_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(wallet_id) DO UPDATE SET drive_name = excluded.drive_name, saved_at = excluded.saved_at",
                     (row["wallet_id"], spec.network, row["public_key"], drive_name[:80], utcnow()))
    from core.wallet import receipts

    receipts.journal_security_event("wallet_usb_backup_saved", {"wallet_id": row["wallet_id"], "network": spec.network}, source_context=source_context)
    return {"wallet_id": row["wallet_id"], "saved": True, "drive": drive_name, "file": name}


def saved_on(wallet_id: str, *, conn: Any = None) -> dict[str, str] | None:
    """The stick this wallet was last backed up to from here, and when (what VOOL wrote; the stick may be gone)."""
    def read(c: Any) -> dict[str, str] | None:
        found = c.execute("SELECT drive_name, saved_at FROM wallet_usb_backups WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
        return {"drive": str(found[0]), "saved_at": str(found[1])} if found else None

    if conn is not None:
        return read(conn)
    with connection() as c:
        return read(c)


def found() -> list[dict[str, Any]]:
    """Every VOOL backup on the sticks plugged in now, each said as: this wallet is here (open it to set a new PIN),
    it was deleted here (bring it back), or it is not on this computer (bring it back)."""
    present: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in custody.list_wallets():
        if not entry.get("public_key") or str(entry.get("agent_id") or ""):
            continue
        with contextlib.suppress(Exception):
            present[(chains.resolve_network(entry["network"]).network, entry["public_key"])] = entry
    shown: list[dict[str, Any]] = []
    for mount, drive_name in _mounts():
        drive_id = _drive_id(mount)
        for path in _backup_files(mount):
            data = _read_document(path)
            if data is None:
                continue
            here = present.get((data["network"], data["address"]))
            here_ready = bool(here) and str(here.get("mode") or "") == custody.MODE_POCKET_SEALED and (str(here.get("setup_state") or "") or "ready") in (
                "ready", "backup_revealed", "awaiting_backup")
            removed = kb._removed_row(data["network"], data["address"]) if not here else None
            if here_ready:
                state = "on_this_computer"
            elif removed is not None or chains.resolve_network(data["network"]).is_svm:
                state = "can_bring_back"
            else:
                state = "use_backup_key"
            shown.append({"file_id": _file_id(drive_id, path.name), "drive": drive_name, "label": str(data.get("label") or "")[:40],
                          "network": data["network"], "address": data["address"], "saved_at": str(data.get("saved_at") or ""), "state": state,
                          "wallet_id": str(here["wallet_id"]) if here_ready else ""})
    return shown


def _document_for(file_id: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    drive_id = str(file_id or "").split(":", 1)[0]
    mount, _name = _mount_for(drive_id, source_context=source_context)
    for path in _backup_files(mount):
        if _file_id(drive_id, path.name) == str(file_id):
            data = _read_document(path)
            if data is not None:
                return data
    raise _fault("usb_backup_not_found", source_context=source_context)


def open_backup(file_id: str, *, password: str, method: str, credential: str = "", credential_confirmation: str = "", label: str = "",
                source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open a backup on the stick with its backup password. When its wallet is on this computer, the same key is
    sealed under a new PIN or password (forgot PIN); otherwise the wallet is brought back, never as Main."""
    from core.wallet import pilot_custody

    custody.require_enabled(source_context=source_context)
    data = _document_for(file_id, source_context=source_context)
    spec = chains.resolve_network(data["network"])
    here = next((e for e in custody.list_wallets() if e.get("public_key") == data["address"] and not str(e.get("agent_id") or "")
                 and chains.resolve_network(e["network"]).network == spec.network and str(e.get("mode") or "") == custody.MODE_POCKET_SEALED), None)
    if here is not None and (str(here.get("setup_state") or "") or "ready") in ("ready", "backup_revealed", "awaiting_backup"):
        row = kb._owner_pilot_row(str(here["wallet_id"]), source_context=source_context)
        scopes = pilot_custody._recovery_scopes(row["wallet_id"])
        pilot_custody._reserve_attempt(row["wallet_id"], source_context=source_context, scopes=scopes)
        backup_value = kb.unseal(data["envelope"], password=str(password or ""), network=spec.network, address=row["public_key"])
        if backup_value is None:
            pilot_custody._settle_attempt(row["wallet_id"], "failure", scopes=scopes)
            raise _fault("backup_password_wrong", code="wallet_recovery_refused", source_context=source_context)
        pilot_custody._settle_attempt(row["wallet_id"], "refund", scopes=scopes)
        try:
            view = pilot_custody.recover_with_backup(row["wallet_id"], backup_value=backup_value, method=method, credential=credential,
                                                     credential_confirmation=credential_confirmation, source_context=source_context)
        finally:
            del backup_value
        return {**view, "opened": "new_pin"}
    removed = kb._removed_row(spec.network, data["address"])
    if not spec.is_svm and removed is None:
        raise _fault("restore_not_supported_on_this_network", source_context=source_context)
    view = kb.restore_from_envelope(data["envelope"], spec, data["address"], removed=removed, password=password, method=method, credential=credential,
                                    credential_confirmation=credential_confirmation, label=label or str(data.get("label") or ""), scope_prefix="usb",
                                    creation_key=f"usb-restore:{data['address']}", source_context=source_context)
    return {**view, "opened": "brought_back"}


def overview() -> dict[str, Any]:
    """What the Crypto screen shows: the sticks plugged in now and the backups found on them. Never a path."""
    try:
        listed = drives()
    except Exception:
        listed = []
    try:
        backups = found()
    except Exception:
        backups = []
    return {"supported": sys.platform == "darwin" or sys.platform.startswith("linux"), "drives": listed, "backups": backups,
            "min_password": kb.MIN_PASSWORD}


__all__ = ["FOLDER", "SUFFIX", "drives", "found", "open_backup", "overview", "save", "saved_on"]
