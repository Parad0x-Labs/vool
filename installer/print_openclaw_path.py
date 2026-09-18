"""Print one resolved OpenClaw path for Windows batch installers."""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.openclaw_locator import discover_openclaw_paths


def main() -> int:
    parser = argparse.ArgumentParser(prog="print_openclaw_path")
    parser.add_argument("field", choices=["config_path", "compat_bridge_dir"])
    args = parser.parse_args()

    paths = discover_openclaw_paths(create_default=True)
    print(getattr(paths, args.field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
