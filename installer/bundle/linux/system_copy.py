"""Bring a person's own VOOL up to the system copy the .deb installed, when that copy is newer.

The .deb keeps one copy of this package in /opt/vool/package and sets it up for each person on first open,
in ~/.local/share/vool, where VOOL then keeps itself up to date. After an apt upgrade the system copy can be
newer than a person's own copy; the next time they open VOOL it is set up for them first, exactly as the
first open did. It is never set up when:
  * it is older or the same (an in-app update is never undone),
  * the copy being opened is not this person's own copy in ~/.local/share/vool,
  * an in-app update has not ended yet (its own recovery finishes it on the copy it started from),
  * VOOL is already running, or for a headless start (the in-app updater restarts VOOL that way).

Run with the package's own Python: ``system_copy.py <the copy being opened> [launch arguments]``.
Exit 0: the system copy was set up and is now current, so open that one. Exit 1: open the copy as it is.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

SYSTEM_PACKAGE = Path("/opt/vool/package")
DEFAULT_PORT = 11435
ENDED = {"finalized", "rolled_back", "aborted"}
# The version order and the profile choice are the app's own, from the package this file ships in.
sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))


def _version(package: Path):
    from core.updater.versions import parse_semver

    return parse_semver((package / "VERSION").read_text(encoding="utf-8").strip())


def own_root() -> Path:
    return Path.home() / ".local/share/vool"


def profile() -> Path:
    """The profile VOOL opens with, chosen the way launch.py chooses it."""
    chosen = (os.environ.get("VOOL_HOME") or "").strip() or (os.environ.get("NULLA_HOME") or "").strip()
    if chosen:
        return Path(chosen).expanduser()
    from core.runtime_paths import user_runtime_default

    return user_runtime_default()


def update_unfinished(profile_dir: Path) -> bool:
    """Whether an in-app update in this profile has not reached its end (finalized, rolled back or aborted)."""
    for journal in (profile_dir / "data/update_v2/transactions").glob("*/journal.jsonl"):
        last = None
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a torn last line: the last complete step counts, as in the updater
            last = entry.get("step", last)
        if last is not None and last not in ENDED:
            return True
    return False


def _port(arguments: list[str]) -> int:
    for index, argument in enumerate(arguments):
        if argument.startswith("--port="):
            value = argument.split("=", 1)[1]
        elif argument == "--port" and index + 1 < len(arguments):
            value = arguments[index + 1]
        else:
            continue
        return int(value) if value.isdigit() else DEFAULT_PORT
    return DEFAULT_PORT


def _serving(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def should_set_up(own: Path, arguments: list[str], *, system: Path = SYSTEM_PACKAGE) -> bool:
    if "--headless" in arguments or not (system / "install.sh").is_file():
        return False
    own = own.resolve()
    if own == system.resolve() or own.parent != (own_root() / "versions").resolve():
        return False
    if _version(system) <= _version(own):
        return False
    if update_unfinished(profile()):
        return False
    return not _serving(_port(arguments))


def main(argv: list[str]) -> int:
    own = Path(argv[1])
    arguments = argv[2:]
    try:
        if not should_set_up(own, arguments):
            return 1
    except Exception:
        return 1  # whatever went wrong here, opening the copy as it is still works
    result = subprocess.run(["bash", str(SYSTEM_PACKAGE / "install.sh")], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Opening the person's own copy still works; say why the newer one was not set up.
        print(f"The newer VOOL from your system could not be set up: {(result.stderr or result.stdout).strip()[-300:]}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
