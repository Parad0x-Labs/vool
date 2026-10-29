"""Mac guard for the tool-seating mission: no run may reach the owner's real apps.

Python loads this file at start-up when its folder is first on PYTHONPATH. The harness puts it there for every daemon
it starts on macOS, and TASK.md puts it there for every pytest run on the Mac. It refuses, inside that process only:

- the programs that drive or read Mac apps and dialogs (osascript for Notes and consent dialogs, open, shortcuts,
  screencapture, pbpaste, security, Spotlight, Swift helpers): they fail as "not found", so VOOL answers with its
  own "not present" refusal;
- the PyObjC bindings that reach Calendar, Contacts, Touch ID, location, camera, photos and notifications: they
  fail to import, as they do on Linux.

Every refusal is appended to $VOOL_HOME/macguard.log (or $TOOL_SEATING_MACGUARD_LOG) so a run shows what it would
have touched. It changes nothing on disk outside that log and nothing in the repo.
"""

from __future__ import annotations

import importlib.abc
import os
import subprocess
import sys
import time

_PROGRAMS = frozenset({
    "osascript", "osacompile", "open", "shortcuts", "automator", "screencapture", "say", "afplay", "pbcopy",
    "pbpaste", "security", "launchctl", "swift", "swiftc", "xcrun", "codesign", "tccutil", "defaults", "mdfind",
    "mdls", "mdutil",
})
_MODULES = frozenset({
    "EventKit", "Contacts", "LocalAuthentication", "ScriptingBridge", "CoreLocation", "AVFoundation", "Photos",
    "UserNotifications",
})


def _note(kind: str, what: str) -> None:
    path = os.environ.get("TOOL_SEATING_MACGUARD_LOG") or os.path.join(os.environ.get("VOOL_HOME") or ".", "macguard.log")
    try:
        with open(path, "a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {os.getpid()} {kind} {what}\n")
    except OSError:
        pass


def _program(args, executable) -> str:
    first = executable
    if first is None:
        if isinstance(args, (str, bytes, os.PathLike)):
            first = os.fsdecode(args).strip().split(" ")[0] if isinstance(args, (str, bytes)) else os.fspath(args)
        elif args:
            first = args[0]
    return os.fsdecode(first) if first is not None else ""


_popen_init = subprocess.Popen.__init__


def _guarded_popen_init(self, args, *rest, **kwargs):
    executable = kwargs.get("executable")
    program = _program(args, executable)
    if os.path.basename(program) in _PROGRAMS:
        _note("exec", program)
        raise FileNotFoundError(2, "refused by the tool-seating Mac guard", program)
    return _popen_init(self, args, *rest, **kwargs)


subprocess.Popen.__init__ = _guarded_popen_init
_os_system = os.system


def _guarded_system(command):
    program = _program(command, None)
    if os.path.basename(program) in _PROGRAMS:
        _note("exec", program)
        return 127 << 8
    return _os_system(command)


os.system = _guarded_system


class _RefuseModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _MODULES:
            _note("import", fullname)
            raise ImportError(f"{fullname} is refused by the tool-seating Mac guard", name=fullname)
        return None


sys.meta_path.insert(0, _RefuseModules())
