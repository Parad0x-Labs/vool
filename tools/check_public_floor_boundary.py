"""Refuse local-only Floor files in the public VOOL repository."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import PurePosixPath

PRIVATE_DIRECTORIES = {"wallet-lab", "wallet_lab", "rh-migrated-targets", "floor-tape-bank"}
PRIVATE_FILES = (
    "core/wallet_lab.py",
    "core/wallet_lab_*.py",
    "docs/guides/wallet-lab*",
    "tests/test_wallet_lab*.py",
    "tests/wallet_lab_*.py",
    "installer/bundle/lab_drag.py",
    "installer/bundle/desktop_blocks.py",
    "core/web_assets/floor-*",
    "tests/test_floor_chat_bridge.py",
)


def is_private_floor_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(part in PRIVATE_DIRECTORIES for part in parts[:-1]) or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in PRIVATE_FILES
    )


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    blocked = sorted(path for path in tracked if path and is_private_floor_path(path))
    if blocked:
        print("Local-only Floor paths must not be tracked publicly:", file=sys.stderr)
        print("\n".join(blocked), file=sys.stderr)
        return 1
    print("No local-only Floor paths are tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
