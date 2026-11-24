"""VOOL Doctor: is each part VOOL depends on working right now, and if not, what fixes it.

Every check reads the authority that owns its part, fresh, on this computer:

- ``local_model``  the local model server (core.local_model_presence) and the models it has;
- ``cloud``        the online model's last known connection state (core.cloud_connection_state), cache only;
- ``folders``      whether this computer lets VOOL read the person's Desktop, Documents and Downloads;
- ``phone``        paired phones and whether the phone door is open (core.mobile_companion);
- ``storage``      the data folder can be written, the database answers an integrity check, disk space;
- ``updates``      the signed updater's own status (core.updater).

A check says one of ``ok``, ``problem``, ``off`` (not set up, which is a choice, not a fault) or
``unknown`` (it could not be checked). ``unknown`` is never shown as working: a part that could not
be read is not reported healthy. Nothing here goes online: the online model is read from its cached
state, and the update check reads the updater's stored status.

Each answer is typed (a reason code and its values), never prose: the page words it in the person's
language, and a problem names its repair (the Settings section that fixes it, or the step to take).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

OK, PROBLEM, OFF, UNKNOWN = "ok", "problem", "off", "unknown"
#: Below this much free disk, saving chats, memories and updates can start to fail.
LOW_DISK_BYTES = 1024 ** 3
_FOLDER_WAIT_S = 2.0
_DB_CHECK_WAIT_S = 3.0


def _check(check_id: str, state: str, reason: str, *, repair: str = "", **values: Any) -> dict[str, Any]:
    return {"id": check_id, "state": state, "reason": reason, "repair": repair,
            "values": {k: v for k, v in values.items() if v not in (None, "")}}


def _bounded(label: str, fn: Callable[[], Any], wait_s: float) -> tuple[bool, Any]:
    """Run a read that the computer may hold (a privacy prompt, a busy disk) without holding the Doctor."""
    from core.off_turn import run_bounded_shared

    sentinel = object()
    finished, value = run_bounded_shared("doctor:" + label, fn, wait_s=wait_s, fallback=sentinel)
    return bool(finished) and value is not sentinel, value


# --- the checks ---------------------------------------------------------------------------------

def check_local_model() -> dict[str, Any]:
    from core.local_model_presence import local_model_presence

    presence = local_model_presence(refresh=True)
    if presence.reason == "local_models_disabled":
        return _check("local_model", OFF, "local.off", repair="models")
    cloud_ready = _cloud_ready()
    if not presence.running:
        # The only way to answer: a fault. With an online model ready, a choice the person can leave as is.
        return _check("local_model", OFF if cloud_ready else PROBLEM, "local.not_running", repair="models")
    models = _local_model_names(presence.endpoint)
    if models is None:
        return _check("local_model", UNKNOWN, "local.list_failed", repair="models")
    if not models:
        return _check("local_model", OFF if cloud_ready else PROBLEM, "local.no_models", repair="models")
    return _check("local_model", OK, "local.ok", count=len(models), model=models[0])


def _local_model_names(endpoint: str) -> list[str] | None:
    """The chat models the local endpoint lists, read through the same listing reader that tells whether a local model
    runs (core.local_model_presence), so the Doctor opens no connection of its own; None when the listing is
    unreadable."""
    base = str(endpoint or "").rstrip("/")
    if not base:
        return None
    from core.local_model_presence import _get_json, _listed_chat_models

    data = _get_json(base + "/api/tags")
    if data is None:
        return None
    return _listed_chat_models(data)


def _cloud_ready() -> bool:
    try:
        from core.cloud_connection_state import connection_status

        return str(connection_status(provider=None, probe=False).get("state") or "") == "ok"
    except Exception:
        return False


def check_cloud() -> dict[str, Any]:
    from core.cloud_connection_state import connection_status

    status = connection_status(provider=None, probe=False)
    state = str(status.get("state") or "")
    provider = str(status.get("label") or status.get("provider") or "")
    if state == "no_key":
        return _check("cloud", OFF, "cloud.none", repair="keys")
    if state == "ok":
        return _check("cloud", OK, "cloud.ok", repair="keys", provider=provider)
    if state == "failed":
        return _check("cloud", PROBLEM, "cloud.failed", repair="keys", provider=provider,
                      detail=str(status.get("detail") or status.get("message") or "")[:200])
    # A key that was never tried: VOOL does not go online to find out on its own.
    return _check("cloud", UNKNOWN, "cloud.untested", repair="keys", provider=provider)


def check_folders() -> dict[str, Any]:
    from core.runtime_execution_tools import _machine_home, _safe_machine_roots

    home = _machine_home()
    roots = [Path(root) for root in _safe_machine_roots() if Path(root).is_dir()]
    if not roots:
        return _check("folders", OFF, "folders.none", home=str(home.name))
    kept: list[str] = []
    unread: list[str] = []
    def _readable(root: Path) -> bool:
        try:
            os.listdir(root)
            return True
        except OSError:  # macOS answers a folder it keeps private with "Operation not permitted"
            return False

    for root in roots:
        finished, value = _bounded("folder:" + str(root), lambda root=root: _readable(root), _FOLDER_WAIT_S)
        if not finished:
            unread.append(root.name)  # the computer held the read (a privacy prompt waiting, a slow disk)
        elif value is not True:
            kept.append(root.name)
    if kept:
        return _check("folders", PROBLEM, "folders.private", repair="privacy_files", names=", ".join(kept))
    if unread:
        return _check("folders", UNKNOWN, "folders.slow", repair="privacy_files", names=", ".join(unread))
    return _check("folders", OK, "folders.ok", names=", ".join(root.name for root in roots))


def check_phone(runtime: Any = None) -> dict[str, Any]:
    from contextlib import nullcontext

    from core.command_registry.legacy import forward
    from core.mobile_companion.runtime_context import runtime_scope

    # The phone door belongs to the running VOOL: its settings are read through that runtime, the same
    # way the Settings page reads them.
    with runtime_scope(runtime) if runtime is not None else nullcontext():
        status, payload = forward("mobile.settings.read", {}, client_host="127.0.0.1")
    if int(status or 0) >= 400 or not isinstance(payload, dict):
        return _check("phone", UNKNOWN, "phone.unreadable", repair="phone")
    devices = [d for d in payload.get("devices") or [] if isinstance(d, dict) and not d.get("revoked")]
    if not devices:
        return _check("phone", OFF, "phone.none", repair="phone")
    if not payload.get("enabled"):
        return _check("phone", PROBLEM, "phone.door_off", repair="phone", count=len(devices))
    if not (payload.get("running") or payload.get("relay_connected")):
        return _check("phone", PROBLEM, "phone.not_listening", repair="phone", count=len(devices))
    return _check("phone", OK, "phone.ok", repair="phone", count=len(devices))


def check_storage() -> dict[str, Any]:
    from core.runtime_paths import active_data_dir
    from storage.db import active_default_db_path

    data = Path(active_data_dir())
    try:
        data.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data, prefix=".doctor-", delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError:
        return _check("storage", PROBLEM, "storage.read_only", folder=str(data.name))
    try:
        free = shutil.disk_usage(data).free
    except OSError:
        free = None
    db_path = str(active_default_db_path())

    def _quick_check() -> str:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
            try:
                row = conn.execute("PRAGMA quick_check(1)").fetchone()
                return str(row[0] if row else "")
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return "busy"  # locked or held by another writer: not read, so not known
        except sqlite3.DatabaseError:
            return "damaged"  # "file is not a database", a malformed page

    if Path(db_path).exists():
        finished, verdict = _bounded("db", _quick_check, _DB_CHECK_WAIT_S)
        if not finished or verdict == "busy":
            return _check("storage", UNKNOWN, "storage.db_slow")
        if verdict != "ok":
            return _check("storage", PROBLEM, "storage.damaged", repair="backup")
    if free is not None and free < LOW_DISK_BYTES:
        return _check("storage", PROBLEM, "storage.low_space", free_mb=int(free // (1024 * 1024)))
    return _check("storage", OK, "storage.ok", free_gb=round(free / 1024 ** 3, 1) if free is not None else None)


def check_updates() -> dict[str, Any]:
    from core.updater.runtime import get_update_subsystem

    subsystem = get_update_subsystem()
    if subsystem is None:
        return _check("updates", UNKNOWN, "updates.not_loaded", repair="updates")
    status = subsystem.status_payload()
    version = str(status.get("installed_version") or "")
    if not status.get("configured"):
        return _check("updates", UNKNOWN, "updates.unavailable", repair="updates", version=version,
                      detail=str(status.get("unavailable_plain") or "")[:200])
    phase = str(status.get("phase") or "")
    if phase in {"failed", "rolled_back"} or status.get("fault_code"):
        return _check("updates", PROBLEM, "updates.failed", repair="updates", version=version,
                      detail=str(status.get("message") or "")[:200])
    if status.get("restart_pending") or phase == "ready_to_restart":
        return _check("updates", PROBLEM, "updates.restart", repair="updates", target=str(status.get("target_version") or ""))
    if status.get("target_version") and phase in {"ready", "idle", "up_to_date"} and str(status.get("target_version")) != version:
        return _check("updates", PROBLEM, "updates.available", repair="updates", version=version,
                      target=str(status.get("target_version")))
    if not status.get("online_access"):
        return _check("updates", OFF, "updates.offline", repair="updates", version=version)
    if phase == "up_to_date":
        return _check("updates", OK, "updates.ok", repair="updates", version=version)
    # Configured and allowed, but no check has answered yet: not known to be current.
    return _check("updates", UNKNOWN, "updates.not_checked", repair="updates", version=version)


CHECKS: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
    ("local_model", check_local_model),
    ("cloud", check_cloud),
    ("folders", check_folders),
    ("phone", check_phone),
    ("storage", check_storage),
    ("updates", check_updates),
)


def run_checks(runtime: Any = None) -> dict[str, Any]:
    """Every check, each on its own: one that fails to run is ``unknown`` with its fault named, and the rest
    still run. ``summary`` is ``ok`` only when every part is working or deliberately off."""
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    for check_id, fn in CHECKS:
        try:
            rows.append(fn(runtime) if check_id == "phone" else fn())
        except Exception as exc:  # reported, never hidden, never breaking the page
            rows.append(_check(check_id, UNKNOWN, "check_failed", fault=type(exc).__name__))
    states = [row["state"] for row in rows]
    summary = PROBLEM if PROBLEM in states else UNKNOWN if UNKNOWN in states else OK
    return {"ok": True, "summary": summary, "checks": rows,
            "problems": states.count(PROBLEM), "unknown": states.count(UNKNOWN),
            "took_ms": int((time.monotonic() - started) * 1000)}


__all__ = ["CHECKS", "OFF", "OK", "PROBLEM", "UNKNOWN", "run_checks"]
