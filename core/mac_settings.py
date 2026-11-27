"""Open one privacy pane of the Mac's own System Settings, for a permission VOOL needs.

Owner's iMac, 2026-11-27: the help line said "Privacy & Security > Calendars" and the owner looked for
it in VOOL. Every macOS permission step now names the Mac's System Settings and carries a button that
opens the right pane there. Only the panes below can be opened: the caller names a pane by key, never
a URL, so nothing else reaches `open`.
"""
from __future__ import annotations

import subprocess
import sys

#: key -> the anchor System Settings (and System Preferences before macOS 13) opens on.
PANES: dict[str, str] = {
    "calendars": "Privacy_Calendars",
    "contacts": "Privacy_Contacts",
    "reminders": "Privacy_Reminders",
    "automation": "Privacy_Automation",
    "files": "Privacy_FilesAndFolders",
    "full_disk": "Privacy_AllFiles",
    "microphone": "Privacy_Microphone",
    "speech": "Privacy_SpeechRecognition",
}

_OPEN = "/usr/bin/open"


def pane_url(pane: str) -> str:
    anchor = PANES.get(str(pane or "").strip())
    return f"x-apple.systempreferences:com.apple.preference.security?{anchor}" if anchor else ""


def open_pane(pane: str, *, run=subprocess.run, platform: str = sys.platform) -> dict:
    url = pane_url(pane)
    if not url:
        return {"ok": False, "error": "unknown_pane"}
    if platform != "darwin":
        return {"ok": False, "error": "not_a_mac"}
    try:
        done = run([_OPEN, url], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "error": "open_failed"}
    return {"ok": done.returncode == 0, "pane": pane} if done.returncode == 0 else {"ok": False, "error": "open_failed"}
