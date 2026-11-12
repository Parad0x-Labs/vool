"""Local Drop opt-in. Only the owner-local HTTP controller writes these values."""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

from core.runtime_paths import active_config_home_dir, active_vool_home


def _private_home():
    from core.drop import DropError

    home = active_vool_home()
    # Only the app's owned profile may be made private, never the user's HOME or a root.
    if home == Path.home().resolve() or home.parent == home:
        raise DropError("UNSAFE_HOME")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise DropError("DIRECTORY_PERMISSIONS")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def load() -> dict:
    path = active_config_home_dir() / "drop.json"
    if not path.exists():
        return {"enabled": os.environ.get("VOOL_DROP") == "1", "relay": "", "allow_digest": False}
    try:
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or type(value.get("enabled")) is not bool
                or not isinstance(value.get("relay"), str) or type(value.get("allow_digest")) is not bool):
            raise ValueError
        return value
    except (OSError, ValueError):
        return {"enabled": False, "relay": "", "allow_digest": False}


def save(*, enabled: bool, relay: str, allow_digest: bool) -> dict:
    from core.drop import DropError

    if type(enabled) is not bool or type(allow_digest) is not bool or not isinstance(relay, str):
        raise DropError("INVALID_SETTINGS")
    if enabled and (not relay or not Path(relay).is_absolute()):
        raise DropError("RELAY_REQUIRED")
    if enabled:
        _private_home()
    value = {"enabled": enabled, "relay": relay, "allow_digest": allow_digest}
    path = active_config_home_dir() / "drop.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return load()
