"""Owner-local Drop controller. No content enters a chat, planner or request ledger."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote

from core.drop import WARNING, DropError, require_enabled, settings
from core.drop.service import MAX_FILE, DropHome, LocalRelay


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
    }.get(action)
    if fields is None or set(body) != fields | {"action"}:
        return 400, {"ok": False, "error": "INVALID_INPUT"}
    try:
        if action == "status":
            return 200, {"ok": True, "warning": WARNING, **settings.load()}
        if action == "settings":
            cfg = settings.save(**{k: body[k] for k in fields})
            return 200, {"ok": True, **cfg}
        require_enabled()
        if action in {"invite", "join"} and body["confirmed"] is not True:
            raise DropError("FINGERPRINT_CONFIRMATION_REQUIRED")
        # Relay creation is explicit and local, after owner opt-in. It never starts a service.
        LocalRelay(_configured()["relay"], create=True)
        with _home() as home:
            sid = body.get("sid")
            if action == "identity":
                result = {"fingerprint": home.fingerprint}
            elif action == "invite":
                result = {"link": home.invite(body["fingerprint"])}
            elif action == "join":
                result = {"sid": home.join(body["link"], body["fingerprint"])}
            elif action == "poll":
                result = {"outcomes": home.poll(), "drops": home.list()}
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
        if not name or len(name) > 128 or any(c in name for c in "/\\\0") or len(note.encode()) > 4096:
            raise DropError("INVALID_INPUT")
        with _home() as home:
            # The only readable input is the uploaded file, never an arbitrary local path.
            # A private temporary directory preserves its display name and is removed on failure too.
            with tempfile.TemporaryDirectory(prefix="upload-", dir=home.root) as directory:
                selected = Path(directory) / name
                fd = os.open(selected, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(raw_body)
                manifest = home.send_packet(lowered.get("x-drop-session", ""), note, selected)
            return 200, {"ok": True, "manifest": manifest}
    except DropError as exc:
        return 409, {"ok": False, "error": exc.code}
    except (OSError, ValueError, TypeError):
        return 400, {"ok": False, "error": "LOCAL_INPUT_OR_STATE_FAILURE"}
