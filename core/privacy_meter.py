"""How much of the owner's text left this computer for online AI models today.

Counted at the one seam every model call crosses (`MemoryFirstRouter._invoke_manifest`, immediately
before the adapter is entered, retries included): a call to a manifest that does not run on this
machine adds the bytes of what was sent (system prompt, messages and prompt, UTF-8). A local model
adds nothing, because nothing leaves. The count is per local calendar day and is kept in the
runtime home, so it survives a restart.

What this is not: web searches, page fetches and other online lanes are their own egress and are
not counted here. The meter names exactly what it measures ("sent to online AI models").
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_FILE = "privacy-meter.json"
_KEEP_DAYS = 31


def _path() -> Path | None:
    try:
        from core.runtime_paths import active_vool_home

        return Path(active_vool_home()) / _FILE
    except Exception:
        return None


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def request_bytes(request: Any) -> int:
    """UTF-8 bytes of what a model request carries to the provider."""
    parts = [str(getattr(request, "system_prompt", "") or ""), str(getattr(request, "prompt", "") or "")]
    for message in list(getattr(request, "messages", None) or []):
        if isinstance(message, dict):
            parts.append(str(message.get("content") or ""))
    return sum(len(part.encode("utf-8")) for part in parts)


def note_model_call(manifest: Any, request: Any) -> None:
    """Called once per provider attempt. Never raises; a local manifest is not recorded."""
    try:
        from core.local_model_policy import manifest_is_local

        if manifest_is_local(manifest):
            return
        size = request_bytes(request)
        provider = str(getattr(manifest, "provider_id", "") or "unknown")
        path = _path()
        if path is None:
            return
        day = _today()
        with _LOCK:
            data = _load(path)
            row = data.setdefault(day, {"bytes": 0, "calls": 0, "providers": {}})
            row["bytes"] = int(row.get("bytes") or 0) + size
            row["calls"] = int(row.get("calls") or 0) + 1
            providers = row.setdefault("providers", {})
            providers[provider] = int(providers.get(provider) or 0) + size
            for old in sorted(data)[:-_KEEP_DAYS]:
                data.pop(old, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
    except Exception:
        return


def today() -> dict[str, Any]:
    """{"day", "bytes", "calls", "providers"} for today; zeros when nothing left."""
    path = _path()
    row = (_load(path) if path is not None else {}).get(_today()) or {}
    return {
        "day": _today(),
        "bytes": int(row.get("bytes") or 0),
        "calls": int(row.get("calls") or 0),
        "providers": dict(row.get("providers") or {}),
    }


def plain_line(state: dict[str, Any] | None = None) -> str:
    """ "Today nothing you wrote was sent to online AI models." or the size and where it went."""
    state = state or today()
    if not state["bytes"]:
        return "Today nothing you wrote was sent to online AI models."
    size = state["bytes"]
    shown = f"{size} bytes" if size < 1024 else (f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1048576:.1f} MB")
    where = ", ".join(sorted(state["providers"])) or "an online model"
    calls = state["calls"]
    return f"Today {shown} of your text went to online AI models ({where}), in {calls} call{'s' if calls != 1 else ''}."


__all__ = ["note_model_call", "plain_line", "request_bytes", "today"]
