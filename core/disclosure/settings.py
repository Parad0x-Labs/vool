from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()


def load_settings() -> dict:
    from core.runtime_paths import active_config_home_dir

    path = active_config_home_dir() / "disclosure.json"
    with _LOCK:
        try:
            saved = json.loads(path.read_text())
        except FileNotFoundError:
            saved = {}
        if not isinstance(saved, dict) or type(saved.get("preview_enabled", True)) is not bool:
            raise ValueError("disclosure settings could not be read")
        return {"preview_enabled": saved.get("preview_enabled", True), "keep_copies": False,
                "retention_days": 30, "copies_available": False}


def save_settings(*, preview_enabled: bool) -> dict:
    from core.runtime_paths import active_config_home_dir

    if type(preview_enabled) is not bool:
        raise ValueError("choose whether to show previews")
    path = active_config_home_dir() / "disclosure.json"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps({"preview_enabled": preview_enabled}))
        temporary.replace(path)
        path.chmod(0o600)
    return load_settings()
