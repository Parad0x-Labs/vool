"""Retired OpenClaw dashboard session-retry patcher.

The OpenClaw integration is removed from VOOL product paths. VOOL must not patch
a third-party application's installed JavaScript (OpenClaw's compiled dashboard
bundle), so this patcher is now an honest, side-effect-free refusal: ``apply``
never reads or writes any file and always reports that no patch was applied.
VOOL's own reply/session paths are native and unaffected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RETIREMENT_NOTICE = (
    "OpenClaw integration is retired from VOOL. VOOL no longer patches the "
    "OpenClaw dashboard (or any third-party installed files). VOOL's own chat and "
    "session handling run natively through Start_VOOL / Talk_To_VOOL. "
    "OpenClaw-specific skills live separately: "
    "https://github.com/Parad0x-Labs/openclaw-skills"
)


def apply(_js_path: Path, *, remove: bool = False) -> str:
    """Refuse third-party code patching without side effects.

    Historic callers passed the OpenClaw dist bundle path; it is ignored, nothing
    is read or written, and a truthful not-applied status is returned.
    """
    del remove
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return "refused: OpenClaw integration is retired from VOOL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patch_openclaw_session_retry")
    parser.add_argument("js_path", nargs="?", type=Path, default=None)
    parser.add_argument("--remove", action="store_true")
    parser.parse_args(argv)
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
