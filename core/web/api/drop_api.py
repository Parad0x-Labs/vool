"""Owner-local Drop controller. No content enters a chat, planner or request ledger."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

from core.drop import WARNING, DropError, require_enabled, settings
from core.drop.service import MAX_FILE, DropHome, LocalRelay, check_relay

#: The folder inside a synced place that one click in Settings sets up.
FOLDER_NAME = "VOOL Drop"


def _guard(headers, client_host, *, raw=False):
    from core.web.api.service import _credential_page_refusal

    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if raw:
        if lowered.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
            return 415, {"ok": False, "error": "CONTENT_TYPE"}
        lowered["content-type"] = "application/json"
    why = _credential_page_refusal(lowered, client_host)
    if why:
        return (415 if why == "content_type" else 403), {"ok": False, "error": "OWNER_REQUIRED"}
    return None


def _configured():
    cfg = settings.load()
    if not cfg["relay"] or not Path(cfg["relay"]).is_absolute():
        raise DropError("RELAY_REQUIRED")
    return cfg


def _home():
    require_enabled()
    cfg = _configured()
    return DropHome(relay=cfg["relay"], allowed=("read_files",) if cfg["allow_digest"] else ())


def synced_places() -> list[dict]:
    """Folders on this computer that a sync service keeps, so Settings can offer them by
    name instead of asking for a typed path. Only places that exist are listed."""
    home = Path.home()
    found = []

    def add(label, path):
        try:
            if path.is_dir() and os.access(path, os.W_OK) and all(str(path.resolve()) != item["root"] for item in found):
                found.append({"label": label, "root": str(path.resolve()),
                              "path": str(path.resolve() / FOLDER_NAME)})
        except OSError:
            pass

    add("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
    cloud = home / "Library" / "CloudStorage"
    try:
        for entry in sorted(cloud.iterdir()) if cloud.is_dir() else ():
            add(entry.name.split("-", 1)[0].replace("GoogleDrive", "Google Drive"), entry)
    except OSError:
        pass
    for label in ("Dropbox", "OneDrive", "Google Drive", "Nextcloud", "Sync", "pCloudDrive", "MEGA"):
        add(label, home / label)
    return found[:8]


def _relay_for_save(body) -> str:
    """The folder to save: it must exist. The one exception is the ready-made folder inside
    a synced place the owner picked by name, which is made here (one level, never a path)."""
    relay = body["relay"]
    if body.get("create") is True:
        wanted = Path(relay)
        if wanted.name != FOLDER_NAME or str(wanted) not in {item["path"] for item in synced_places()}:
            raise DropError("MISSING_RELAY")
        try:
            wanted.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise DropError("RELAY_READ_ONLY") from exc
    return str(check_relay(relay))


def _save_folder() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def _reveal(path: Path) -> bool:
    """Show a saved file in the file manager. The file is never opened or run from here."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def handle(body, *, headers, client_host):
    refused = _guard(headers, client_host)
    if refused:
        return refused
    if not isinstance(body, dict) or len(json.dumps(body).encode()) > 16384:
        return 400, {"ok": False, "error": "INVALID_INPUT"}
    action = body.get("action")
    if not isinstance(action, str):
        return 400, {"ok": False, "error": "INVALID_INPUT"}
    fields = {
        "status": set(), "settings": {"enabled", "relay", "allow_digest"}, "identity": set(),
        "invite": {"fingerprint", "confirmed"}, "join": {"link", "fingerprint", "confirmed"},
        "poll": set(), "list": set(), "show": {"sid"}, "sweep": set(),
        "task": {"sid"}, "approve": {"sid", "task_id"}, "decline": {"sid", "task_id"},
        "execute": {"sid"}, "delete": {"sid"},
        "me": set(), "name": {"name"}, "card": {"card"}, "contacts": set(), "inbox": set(),
        "contact_add": {"card", "name", "verified"}, "contact_rename": {"fingerprint", "name"},
        "contact_verify": {"fingerprint", "verified"}, "contact_remove": {"fingerprint"},
        "save": {"sid"}, "reveal": {"sid"}, "places": set(),
    }.get(action)
    optional = {"create"} if action == "settings" else set()
    if fields is None or not fields | {"action"} <= set(body) <= fields | optional | {"action"}:
        return 400, {"ok": False, "error": "INVALID_INPUT"}
    try:
        if action == "status":
            from core.drop import runtime

            return 200, {"ok": True, "warning": WARNING, **settings.load(), "checking": runtime.status()}
        if action == "places":
            return 200, {"ok": True, "places": synced_places()}
        if action == "settings":
            values = {k: body[k] for k in fields}
            if values["enabled"] is True and isinstance(values["relay"], str):
                # Refused here, before "Saved": a folder that is missing, is not a folder, is
                # read-only or belongs to someone else would fail every later step.
                values["relay"] = _relay_for_save(body)
            cfg = settings.save(**values)
            from core.drop import runtime

            runtime.wake()
            return 200, {"ok": True, **cfg}
        require_enabled()
        if action in {"invite", "join"} and body["confirmed"] is not True:
            raise DropError("FINGERPRINT_CONFIRMATION_REQUIRED")
        for name in ("fingerprint", "link", "card"):
            if isinstance(body.get(name), str):
                body[name] = body[name].strip()  # pasted text often carries a space or newline
        # The folder itself is never made here: a path that is not there is a typing mistake.
        LocalRelay(str(check_relay(_configured()["relay"])), create=True)
        with _home() as home:
            sid = body.get("sid")
            if action == "identity":
                result = {"fingerprint": home.fingerprint}
            elif action == "me":
                result = home.me()
            elif action == "name":
                result = home.set_name(body["name"])
            elif action == "card":
                result = home.card_preview(body["card"])
            elif action == "contacts":
                result = {"contacts": home.contacts()}
            elif action == "inbox":
                result = {"drops": home.inbox()}
            elif action == "contact_add":
                result = {"contact": home.contact_add(body["card"], body["name"], verified=body["verified"])}
            elif action == "contact_rename":
                result = {"contact": home.contact_rename(body["fingerprint"], body["name"])}
            elif action == "contact_verify":
                result = {"contact": home.contact_verify(body["fingerprint"], body["verified"])}
            elif action == "contact_remove":
                home.contact_remove(body["fingerprint"])
                result = {"state": "removed"}
            elif action == "save":
                saved = home.save_file(sid, _save_folder())
                result = {"saved": saved.name, "folder": saved.parent.name, "shown": _reveal(saved)}
            elif action == "reveal":
                name = home.show(sid)["file"].get("saved")
                target = _save_folder() / name if name else None
                if target is None or not target.is_file():
                    raise DropError("SAVED_FILE_MOVED")
                result = {"shown": _reveal(target)}
            elif action == "invite":
                result = {"link": home.invite(body["fingerprint"])}
            elif action == "join":
                result = {"sid": home.join(body["link"], body["fingerprint"])}
            elif action == "poll":
                result = {"outcomes": home.poll(), "drops": home.list(), "inbox": home.inbox()}
            elif action == "list":
                result = {"drops": home.list()}
            elif action == "show":
                result = home.show(sid)
            elif action == "task":
                result = {"task_id": home.send_task(sid, "sha256-selected-file")}
            elif action == "approve":
                home.approve(sid, body["task_id"])
                result = {"state": "approved"}
            elif action == "decline":
                home.decline(sid, body["task_id"])
                result = {"state": "declined"}
            elif action == "execute":
                result = {"result": home.execute_approved(sid)}
            elif action == "delete":
                home.delete(sid)
                result = {"state": "deleted"}
            else:
                result = {"states": home.sweep()}
            return 200, {"ok": True, **result}
    except DropError as exc:
        return 409, {"ok": False, "error": exc.code}
    except (OSError, ValueError, KeyError, TypeError):
        return 400, {"ok": False, "error": "LOCAL_INPUT_OR_STATE_FAILURE"}


def upload(raw_body, *, headers, client_host):
    refused = _guard(headers, client_host, raw=True)
    if refused:
        return refused
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    try:
        require_enabled()
        if len(raw_body) > MAX_FILE:
            return 413, {"ok": False, "error": "INPUT_LIMIT"}
        name = unquote(lowered.get("x-drop-name", ""))
        note = unquote(lowered.get("x-drop-note", ""))
        if not name or len(name) > 128 or any(c in name for c in "/\\\0"):
            raise DropError("INVALID_FILENAME")
        if len(note.encode()) > 4096:
            raise DropError("NOTE_LIMIT")
        contact = lowered.get("x-drop-contact", "").strip()
        with _home() as home:
            # The only readable input is the uploaded file, never an arbitrary local path, and
            # its bytes go straight into the Drop: no unsealed copy is written on this side.
            if contact:
                sid, manifest = home.send_to(contact, note, name, bytes(raw_body))
            else:
                sid = lowered.get("x-drop-session", "")
                manifest = home.send_bytes(sid, note, name, bytes(raw_body))
            return 200, {"ok": True, "manifest": manifest, "sid": sid}
    except DropError as exc:
        return 409, {"ok": False, "error": exc.code}
    except (OSError, ValueError, TypeError):
        return 400, {"ok": False, "error": "LOCAL_INPUT_OR_STATE_FAILURE"}
