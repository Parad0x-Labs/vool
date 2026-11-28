"""Whether one of Apple's developer tools is really installed on this Mac, read from disk only.

On macOS, /usr/bin/git, /usr/bin/python3, /usr/bin/swiftc, /usr/bin/clang and their kin are not the
tools themselves: they are small launchers that hand the call to the active developer folder (the
Command Line Tools or Xcode). On a Mac that has neither, the launcher exists and is executable, so
``os.access(path, X_OK)`` and ``shutil.which`` both say yes, but starting it opens Apple's "install
the command line developer tools" dialog and the call fails. A normal person's Mac has no developer
tools, so every such spawn put that dialog in front of them (the PDF reader ran its helper under
/usr/bin/python3; the image, speech, notification and wallet helpers compile with /usr/bin/swiftc;
the build stamp asks /usr/bin/git).

``installed(name)`` answers without starting anything: it finds the developer folder the launcher
would use, in the launcher's own order, and checks that the real tool is in it. Off macOS every
tool on the PATH is the real one, so the answer there is whether it is on the PATH at all.
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

# The developer folders the launcher falls back to when none is chosen (xcode-select's defaults).
_XCODE_DEVELOPER = Path("/Applications/Xcode.app/Contents/Developer")
_COMMAND_LINE_TOOLS = Path("/Library/Developer/CommandLineTools")
# Where `xcode-select --switch` records the chosen one.
_SELECTED_LINK = Path("/var/db/xcode_select_link")
# Inside a developer folder: its own tools, and the Swift/clang toolchain Xcode keeps apart.
_TOOL_DIRS = ("usr/bin", "Toolchains/XcodeDefault.xctoolchain/usr/bin")
# Every launcher in /usr/bin that only stands in for a developer tool. Anything else in /usr/bin
# (sandbox-exec, codesign, osascript, ...) is the real program and needs no developer folder.
_LAUNCHERS = frozenset({
    "git", "python3", "pip3", "swift", "swiftc", "clang", "clang++", "cc", "c++", "gcc", "g++",
    "make", "ld", "lldb", "xcrun", "xcodebuild", "svn", "nm", "strip", "otool", "lipo", "as",
})


def _on_macos() -> bool:
    return platform.system() == "Darwin"


def developer_folder() -> Path | None:
    """The developer folder a /usr/bin launcher would hand its call to, or None when there is none."""
    chosen = os.environ.get("DEVELOPER_DIR", "").strip()
    candidates: list[Path] = [Path(chosen)] if chosen else []
    try:
        if _SELECTED_LINK.is_symlink() or _SELECTED_LINK.exists():
            candidates.append(Path(os.path.realpath(_SELECTED_LINK)))
    except OSError:
        pass
    candidates += [_XCODE_DEVELOPER, _COMMAND_LINE_TOOLS]
    for folder in candidates:
        try:
            if folder.is_dir():
                return folder
        except OSError:
            continue
    return None


def installed(name: str) -> bool:
    """Whether running ``name`` (a bare name or a /usr/bin path) starts the real tool, not a dialog."""
    program = Path(str(name or "").strip())
    if not program.name:
        return False
    if not _on_macos():
        return bool(shutil.which(str(program))) if not program.is_absolute() else os.access(program, os.X_OK)
    resolved = Path(shutil.which(str(program)) or program) if not program.is_absolute() else program
    if resolved.parent != Path("/usr/bin") or resolved.name not in _LAUNCHERS:
        return resolved.is_absolute() and os.access(resolved, os.X_OK)
    folder = developer_folder()
    if folder is None:
        return False
    return any(os.access(folder / sub / resolved.name, os.X_OK) for sub in _TOOL_DIRS)
