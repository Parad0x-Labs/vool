"""What the paired phone asked for and how this computer answered, one line each, for finding a fault together
with the owner: the action, the answer's code, how long it took. Never what was asked or answered (no text, no
file bytes, no names, no keys). Kept at most two files of 4 MB in the VOOL home's logs folder."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_LIMIT = 4 * 1024 * 1024
_SAFE = ("offset", "length", "section", "kind", "sort", "order", "how", "index", "count", "bytes")


def _path() -> Path:
    from core.runtime_paths import data_path

    return data_path("logs") / "phone-trace.log"


def _plain(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)[:40]


def record(action: str, *, status: int, code: str = "", ms: float = 0.0, device_id: str = "", params: dict | None = None) -> None:
    """One phone request. Only the few plain fields in _SAFE are kept from its parameters; an item id only as its
    first eight characters, enough to tell two items apart in one session."""
    line = {"t": round(time.time(), 3), "action": str(action)[:48], "status": int(status), "ms": round(float(ms), 1),
            "phone": str(device_id)[:8]}
    if code:
        line["code"] = str(code)[:48]
    for key in _SAFE:
        if params and key in params:
            line[key] = _plain(params[key])
    if params and isinstance(params.get("id"), str):
        line["item"] = params["id"][:8]
    if params and isinstance(params.get("lines"), list):
        line["phone_lines"] = len(params["lines"])
    write(line)


def write(line: dict) -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _LOCK:
            if path.exists() and path.stat().st_size > _LIMIT:
                path.replace(path.with_suffix(".log.1"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)
    except OSError:
        pass
