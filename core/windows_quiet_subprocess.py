"""Suppress child-process console windows on Windows.

The VOOL server runs windowless (pythonw.exe), but any console child it spawns -- git, the
hardware/GPU probes, tool commands -- still gets its OWN console window unless CREATE_NO_WINDOW is
set. With dozens of subprocess call sites, a normal desktop user sees a "swarm" of console windows
flicker on every turn, which reads as malware. Rather than touch each call site, patch the Popen
default once: every subprocess in this process (and any future call site) becomes windowless.
No-op off Windows.
"""
from __future__ import annotations

import subprocess
import sys

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


def _creationflags_hidden(flags: int) -> int:
    """Add CREATE_NO_WINDOW unless the caller deliberately wants a new console."""
    if flags & _CREATE_NEW_CONSOLE:
        return flags
    return flags | _CREATE_NO_WINDOW


def enable_quiet_subprocess() -> None:
    """Make every subprocess spawned by this process windowless on Windows (idempotent, no-op
    elsewhere). Call as early as possible so import-time and per-turn child processes are hidden."""
    if sys.platform != "win32":
        return
    if getattr(subprocess.Popen, "_vool_quiet_patched", False):
        return
    _orig_init = subprocess.Popen.__init__

    def _quiet_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Console child processes never flash a window; output redirection is unaffected.
        kwargs["creationflags"] = _creationflags_hidden(kwargs.get("creationflags", 0))
        _orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _quiet_init  # type: ignore[assignment]
    subprocess.Popen._vool_quiet_patched = True  # type: ignore[attr-defined]
