"""TEST LAUNCHER for the served daemon rig (tests/_served_daemon.py). Not a runtime entry point, and never shipped.

It runs the real daemon entry point (``apps.vool_api_server``) unchanged in this process, with this
checkout first on the import path. Nothing is substituted.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    sys.argv = ["apps.vool_api_server", *sys.argv[1:]]
    runpy.run_module("apps.vool_api_server", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
