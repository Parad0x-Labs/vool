"""VOOL's own Linux window, run on the system python3.

The distribution builds its GTK and WebKitGTK bindings (python3-gi, gir1.2-webkit2-4.1) for its own
interpreter only, so launch.py runs this file on that python3, isolated (-I: no PYTHON* variables, no
user site-packages, not the current directory) and without writing bytecode (-B). pywebview and its
pure-Python dependencies come from window-lib/ beside this file and VOOL's code from app/; both go
ahead of the system's own folders, so a distribution's pywebview or typing_extensions is never the one
loaded. The server the window starts keeps the package's own Python (VOOL_RUNTIME_PYTHON).
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path[0:0] = [str(root / "window-lib"), str(root / "app")]
    from installer.bundle import vool_window

    vool_window.configure_linux_package(root)
    code = vool_window.main()
    if vool_window.update_handoff_requested():
        # VOOL's updater replaced this version while the window was open: open the new one.
        return vool_window.relaunch_current_version()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
