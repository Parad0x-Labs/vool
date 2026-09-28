"""Retired OpenClaw Web0 navigation-pill injector.

The OpenClaw integration is removed from VOOL product paths. VOOL must not mutate
a third-party application's installed files (the compiled OpenClaw Control UI),
so this injector is now an honest, side-effect-free refusal: ``apply`` never
reads or writes any file and always reports that no injection was performed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RETIREMENT_NOTICE = (
    "OpenClaw integration is retired from VOOL. VOOL no longer modifies the "
    "OpenClaw Control UI (or any third-party installed files). The native web UI "
    "is served by VOOL itself at http://127.0.0.1:11435/web0 (Open_Web0 launcher). "
    "OpenClaw-specific skills live separately: "
    "https://github.com/Parad0x-Labs/openclaw-skills"
)


def apply(_index_path: Path, *, remove: bool = False) -> str:
    """Refuse UI injection without side effects.

    Historic callers passed the OpenClaw dist's index.html path; it is ignored,
    nothing is read or written, and a truthful not-applied status is returned.
    """
    del remove
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return "refused: OpenClaw integration is retired from VOOL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inject_openclaw_web0_pill")
    parser.add_argument("index_path", nargs="?", type=Path, default=None)
    parser.add_argument("--remove", action="store_true")
    parser.parse_args(argv)
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
