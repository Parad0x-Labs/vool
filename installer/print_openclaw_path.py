"""Retired OpenClaw path helper.

The OpenClaw integration is removed from VOOL product paths; VOOL never reads,
writes, or discovers third-party OpenClaw state anymore. This entrypoint remains
as an honest side-effect-free refusal so old callers fail loudly instead of
silently succeeding.
"""

from __future__ import annotations

import argparse
import sys

RETIREMENT_NOTICE = (
    "OpenClaw integration is retired from VOOL. VOOL installs and starts natively "
    "(Start_VOOL / Talk_To_VOOL / Open_Web0) and does not read or write OpenClaw "
    "state. OpenClaw-specific skills live separately: "
    "https://github.com/Parad0x-Labs/openclaw-skills"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="print_openclaw_path")
    parser.add_argument("field", choices=["config_path", "compat_bridge_dir"])
    parser.parse_args(argv)
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
