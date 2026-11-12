"""Web0 product switch — one persisted, off-by-default flag owning every Web0 start.

Web0 (the `.null` name tools, the mesh poll thread, capability announcements, the
`/api/web0/resolve` lookup) is OFF until the operator turns it on in Settings
(Advanced). Off must mean *starts nothing*: no background thread, no network
attempt, no name lookup. This module is the single owner of that truth.

State lives at `<active_vool_home>/data/web0/settings.json` next to the other
daemon-owned state, written atomically with 0600 permissions. Reading never
creates anything: a missing or unreadable file IS the off state, so a fresh
install — which has no file — is off with zero side effects, and a corrupted
file fails closed the same way.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()
_SETTINGS_VERSION = 1

WEB0_OFF_MESSAGE = "Special web addresses are off on this computer. Turn them on in Settings, under Advanced."


def _settings_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "web0" / "settings.json"


def web0_enabled() -> bool:
    """True only when the persisted flag says so. No file (or an unreadable
    one) means off — the fresh-install default, with no side effects on read."""
    try:
        raw = _settings_path().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict) or data.get("version") != _SETTINGS_VERSION:
        return False
    return data.get("enabled") is True


class Web0OffError(RuntimeError):
    """Raised at a Web0 network door (a `.null` lookup, a Web0 tool) while the
    switch is off. Callers that already treat a lookup failure as "no record"
    fail closed on it; callers that speak to a person say Web0 is off."""

    def __init__(self, message: str = WEB0_OFF_MESSAGE) -> None:
        super().__init__(message)


def require_web0_enabled() -> None:
    """Gate for a Web0 network door: raise Web0OffError unless the switch is on."""
    if not web0_enabled():
        raise Web0OffError()


def set_web0_enabled(enabled: bool) -> dict[str, Any]:
    """Persist the flag and return the new status. Atomic replace, 0600."""
    from core.runtime_paths import active_data_dir

    target = active_data_dir() / "web0" / "settings.json"
    payload = {"version": _SETTINGS_VERSION, "enabled": bool(enabled)}
    with _WRITE_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    return web0_settings_status()


def web0_settings_status() -> dict[str, Any]:
    """What the Settings row and the API read."""
    return {"enabled": web0_enabled()}


__all__ = [
    "WEB0_OFF_MESSAGE",
    "Web0OffError",
    "require_web0_enabled",
    "set_web0_enabled",
    "web0_enabled",
    "web0_settings_status",
]
