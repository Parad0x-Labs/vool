"""
core/self_update_state.py
=========================
Durable persistence for the self-update check state + the latest availability.

Lives under VOOL_HOME/data (NOT inside the code dir), so it survives a code swap
the same way the wallet and DB do. Two small JSON files:

  * update_check_state.json  — the 24h-check bookkeeping (last check time, last
    offered version, the version the user declined). Round-trips
    :class:`core.self_update_check.UpdateCheckState`.
  * update_available.json     — the cached result of the most recent successful
    network check, so a chat turn can render the "update now?" offer WITHOUT doing
    any network I/O on the turn.

All reads fail safe to "nothing available / never checked": a missing or corrupt
file must never crash a chat turn or wrongly claim an update.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir
from core.self_update_check import UpdateAvailability, UpdateCheckState

_CHECK_STATE_FILENAME = "update_check_state.json"
_AVAILABLE_FILENAME = "update_available.json"


def _data_dir() -> Path:
    return active_data_dir()


def check_state_path() -> Path:
    return (_data_dir() / _CHECK_STATE_FILENAME).resolve()


def available_path() -> Path:
    return (_data_dir() / _AVAILABLE_FILENAME).resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_check_state() -> UpdateCheckState:
    path = check_state_path()
    if not path.exists():
        return UpdateCheckState()
    try:
        return UpdateCheckState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return UpdateCheckState()  # fail safe: treat as never-checked


def save_check_state(state: UpdateCheckState) -> None:
    # Persistence is best-effort; a write failure must not break the caller.
    with contextlib.suppress(Exception):
        _write_json(check_state_path(), state.to_dict())


def _availability_to_dict(av: UpdateAvailability) -> dict[str, Any]:
    return {
        "installed_version": av.installed_version,
        "available": bool(av.available),
        "target_version": av.target_version,
        "changelog": list(av.changelog or []),
        "asset_url": av.asset_url,
        "sha256_url": av.sha256_url,
        "release_url": av.release_url,
        "dismissed": bool(av.dismissed),
        "reason": av.reason,
    }


def _availability_from_dict(data: dict[str, Any]) -> UpdateAvailability:
    return UpdateAvailability(
        installed_version=str(data.get("installed_version") or ""),
        available=bool(data.get("available")),
        target_version=str(data.get("target_version") or ""),
        changelog=[str(x) for x in (data.get("changelog") or [])],
        asset_url=str(data.get("asset_url") or ""),
        sha256_url=str(data.get("sha256_url") or ""),
        release_url=str(data.get("release_url") or ""),
        dismissed=bool(data.get("dismissed")),
        reason=str(data.get("reason") or ""),
    )


def save_available(availability: UpdateAvailability) -> None:
    with contextlib.suppress(Exception):
        _write_json(available_path(), _availability_to_dict(availability))


def load_available() -> UpdateAvailability | None:
    path = available_path()
    if not path.exists():
        return None
    try:
        return _availability_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None  # fail safe: no cached availability


def clear_available() -> None:
    try:
        p = available_path()
        if p.exists():
            p.unlink()
    except Exception:
        pass


__all__ = [
    "available_path",
    "check_state_path",
    "clear_available",
    "load_available",
    "load_check_state",
    "save_available",
    "save_check_state",
]
