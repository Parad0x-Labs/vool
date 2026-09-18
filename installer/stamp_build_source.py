"""CLI: stamp the current git SHA into config/build-source.json for a build.

Run at build/stage time so the packaged install can report its exact commit at /api/runtime/version
and in the /chat footer (a bundle has no .git). Example:

    python installer/stamp_build_source.py --root . --out G:/vool-build/bundle/app/config/build-source.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.build_provenance import write_build_source


def main() -> int:
    parser = argparse.ArgumentParser(description="Write config/build-source.json with the source git SHA.")
    parser.add_argument("--root", default=".", help="repo root to read git provenance from")
    parser.add_argument("--out", default=None, help="output path (default: <root>/config/build-source.json)")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    out = Path(args.out).resolve() if args.out else None
    path = write_build_source(root, out)
    print(f"wrote build-source stamp -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
