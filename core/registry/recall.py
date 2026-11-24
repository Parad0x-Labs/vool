"""The daily check for add-ons the Skill Codex has removed. On unless the owner turns it off.

When the VOOL team removes a skill from the Skill Codex, a copy already installed learns of it only
when VOOL next reads the signed list, and until now that happened only when the owner pressed
Browse Skill Codex. With this switch on, VOOL reads the same public list once a day by itself, so a
removed add-on stops being offered within a day.

The requests are the ones Browse makes: one GET of the public signed list and one of the public
badge document, so a withdrawn badge leaves within a day as well. Nothing about the person, their
chats or what they installed is sent. It runs only while the owner has at least one add-on
from the Skill Codex, and never after they turned the switch off. It is on from the start because
its whole use is the day nobody expects: a skill found harmful after people installed it.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from core.registry.codec import RegistryError
from core.registry.native import home

# Once a day carries a removal soon enough; asking more often would only add requests.
INTERVAL = 24 * 3600
# A check that could not finish (offline, the service down, a list that does not verify) is tried again in an hour.
RETRY = 3600
# The app starts first; the check is never part of start-up.
FIRST_DELAY = 120
_NAME = "registry_recall.json"
_LOCK = threading.Lock()
_RUNNING = threading.Lock()


def _path() -> Path:
    return home() / "config" / _NAME


def _state() -> dict:
    """The saved choice and the last check. A file that cannot be read counts as off: a damaged choice never turns into a request."""
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"enabled": False}
    return value if isinstance(value, dict) else {"enabled": False}


def _save(state: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _on(state: dict) -> bool:
    """No saved choice means on; only a saved True or nothing at all counts."""
    return state.get("enabled", True) is True


def registry_installs() -> int:
    """How many installed add-ons came from the Skill Codex. Read from this device only."""
    try:
        from core import plugin_lifecycle

        return sum(1 for record in plugin_lifecycle.records()
                   if str(record.source).startswith("registry:") and record.stage != plugin_lifecycle.STAGE_UNINSTALLED)
    except Exception:
        return 0


def _time(value) -> int | None:
    return value if type(value) is int and value > 0 else None


def status() -> dict:
    """What the Add-ons page shows beside the switch."""
    state = _state()
    return {"status": "ok", "enabled": _on(state), "installs": registry_installs(),
            "last_checked": _time(state.get("last_checked")), "last_failed": _time(state.get("last_failed"))}


def set_enabled(enabled: bool) -> dict:
    """Save the owner's choice. Turning it on asks for a check at the next pass, not at once."""
    with _LOCK:
        state = _state()
        state["enabled"] = enabled is True
        _save(state)
    return status()


def due(now: int) -> bool:
    state = _state()
    if not _on(state):
        return False
    checked, failed = _time(state.get("last_checked")) or 0, _time(state.get("last_failed")) or 0
    # A clock set back must not silence the check: a time in the future is treated as never.
    checked, failed = (checked if checked <= now else 0), (failed if failed <= now else 0)
    return now - checked >= INTERVAL and now - failed >= RETRY


def _record(key: str, now: int) -> None:
    with _LOCK:
        state = _state()
        state[key] = now
        if key == "last_checked":
            state.pop("last_failed", None)
        _save(state)


def check(*, now: int | None = None, client=None) -> dict:
    """One pass. Reads the signed list unless the owner turned it off, when they have a Skill Codex add-on and a day has gone by.

    Never raises: a check that cannot finish is recorded and tried again later, and what was installed
    keeps working from the last list VOOL verified.
    """
    now = int(time.time()) if now is None else now
    if not _RUNNING.acquire(blocking=False):
        return {"status": "busy"}  # one check at a time; the switch itself never waits on the network
    try:
        if not _on(_state()):
            return {"status": "off"}
        if not registry_installs():
            return {"status": "nothing_installed"}
        if not due(now):
            return {"status": "not_due"}
        owned = client is None
        try:
            if owned:
                from core.registry.client import configured_client

                client = configured_client()
            try:
                result = client.recall(now=now)
            finally:
                if owned:
                    client.close()
        except (RegistryError, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            _record("last_failed", now)
            return {"status": "failed"}
        _record("last_checked", now)
    finally:
        _RUNNING.release()
    with contextlib.suppress(Exception):
        from core import plugin_catalog

        plugin_catalog.invalidate_storage_listing()  # a removed add-on leaves the lists at once
    return {"status": "ok", "removed_listed": result["removed_listed"]}


def start(stop: threading.Event | None = None) -> threading.Thread:
    """Run the check in the background for as long as the app runs. Wakes hourly; most wakes read one small file."""
    stop = stop or threading.Event()

    def _loop() -> None:
        if stop.wait(FIRST_DELAY):
            return
        while True:
            with contextlib.suppress(Exception):  # this thread outlives any surprise; the next hour tries again
                check()
            if stop.wait(RETRY):
                return

    thread = threading.Thread(target=_loop, name="vool-registry-recall", daemon=True)
    thread.start()
    return thread
