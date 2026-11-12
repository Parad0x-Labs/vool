"""VOOL's own window on Linux: which python3 can show it, and what to tell a person when it cannot.

Standard library only. installer/bundle/linux/launch.py uses this on the package's own Python to pick
the interpreter for the window, and installer/bundle/vool_window.py uses it on that system python3
(installer/bundle/linux/window_host.py), where the server's dependencies are not installed.

Why a system python3 at all: the window is GTK + WebKitGTK through PyGObject, and the distribution
builds those bindings (python3-gi) for its own interpreter only. The package's self-contained Python
cannot load them, so the window runs on the system python3 while the server keeps the package's
Python and dependencies.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

# The distribution packages the window needs; the Ubuntu/Debian package lists them in Depends.
# pywebview and its pure-Python dependencies ship inside the package (<version>/window-lib).
WINDOW_APT_PACKAGES = ("python3-gi", "gir1.2-gtk-3.0", "gir1.2-webkit2-4.1")
# The oldest system python3 the window runs on: Ubuntu 22.04's.
MIN_WINDOW_PYTHON = (3, 10)
_SYSTEM_BIN = Path("/usr/bin")

# What each candidate interpreter runs. The same GTK/WebKit2/Soup pairs pywebview's GTK backend asks
# for (webview/platforms/gtk.py). require_version only checks that the typelibs are installed: nothing
# is loaded and no display is opened, so the probe is quick and safe without a desktop.
_PROBE = f"""
import sys
if sys.version_info[:2] < {MIN_WINDOW_PYTHON!r}:
    raise SystemExit(4)
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
    gi.require_version("Soup", "3.0")
except ValueError:
    gi.require_version("WebKit2", "4.0")
    gi.require_version("Soup", "2.4")
"""


def has_display(env: Mapping[str, str] | None = None) -> bool:
    """True when this session has a desktop to show a window on (X11 or Wayland)."""
    env_map = os.environ if env is None else env
    return bool(str(env_map.get("DISPLAY") or "").strip() or str(env_map.get("WAYLAND_DISPLAY") or "").strip())


def window_python_candidates(bin_dir: Path = _SYSTEM_BIN) -> list[str]:
    """The distribution's interpreters, in the order they are tried: python3, then each python3.N,
    newest first. On Ubuntu python3 is the one python3-gi is built for; a system where python3 points
    elsewhere (a newer interpreter installed beside the distribution's) still finds the right one."""
    found = []
    default = bin_dir / "python3"
    if default.exists():
        found.append(str(default))
    versioned = []
    with contextlib.suppress(OSError):
        for path in bin_dir.iterdir():
            match = re.fullmatch(r"python3\.(\d+)", path.name)
            if match:
                versioned.append((int(match.group(1)), str(path)))
    found.extend(path for _minor, path in sorted(versioned, reverse=True))
    return found


def find_window_python(candidates: list[str] | None = None, *, timeout: float = 15.0) -> str | None:
    """The first system interpreter that can show VOOL's window, or None.

    Each candidate runs the probe isolated (-I: no PYTHON* variables, no user site-packages, not the
    current directory) and without writing bytecode. The same interpreter reached by two names is
    tried once.
    """
    seen: set[str] = set()
    for candidate in window_python_candidates() if candidates is None else candidates:
        real = os.path.realpath(candidate)
        if real in seen or not os.access(real, os.X_OK):
            continue
        seen.add(real)
        try:
            done = subprocess.run([candidate, "-I", "-B", "-c", _PROBE], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return candidate
    return None


def window_guidance(env: Mapping[str, str] | None = None) -> str:
    """What a person can do about a window that cannot open. Two failures, two remedies: a session
    without a display can never show a window; a desktop without the GTK and WebKitGTK bindings needs
    the packages named here."""
    if not has_display(env):
        return ("No desktop display was found, so VOOL cannot open its window here.\n"
                "Open VOOL from your desktop, or run 'vool --headless' and open "
                "http://127.0.0.1:11435/chat in a browser.")
    return ("VOOL's window needs the GTK and WebKitGTK system packages. Install them, then open VOOL again:\n"
            f"  sudo apt install {' '.join(WINDOW_APT_PACKAGES)}")


def tell_user(title: str, body: str, *, icon: str | Path | None = None) -> None:
    """Say it where a person sees it. An app-menu launch has no terminal, so standard error alone is
    not enough: a desktop notification too (notify-send, from libnotify-bin), when one can be shown.
    Best effort; never raises."""
    with contextlib.suppress(Exception):
        print(f"{title}\n{body}", file=sys.stderr, flush=True)
    if not has_display():
        return
    exe = shutil.which("notify-send")
    if exe is None:
        return
    command = [exe, "--app-name=VOOL"]
    if icon is not None and Path(icon).is_file():
        command.append(f"--icon={icon}")
    with contextlib.suppress(Exception):
        subprocess.Popen([*command, title, body], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
