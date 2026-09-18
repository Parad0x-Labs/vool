from __future__ import annotations

import subprocess
import sys

from core import windows_quiet_subprocess as q


def test_creationflags_hidden_adds_no_window_but_leaves_new_console() -> None:
    # The core logic, tested cross-platform: hide the window by default, but never override a
    # caller that explicitly wants its own console.
    assert q._creationflags_hidden(0) & q._CREATE_NO_WINDOW
    assert q._creationflags_hidden(q._CREATE_NEW_CONSOLE) == q._CREATE_NEW_CONSOLE
    # idempotent on already-hidden flags
    assert q._creationflags_hidden(q._CREATE_NO_WINDOW) == q._CREATE_NO_WINDOW


def test_enable_is_idempotent_and_subprocess_still_works() -> None:
    q.enable_quiet_subprocess()
    q.enable_quiet_subprocess()  # must not double-patch or raise
    if sys.platform == "win32":
        assert getattr(subprocess.Popen, "_vool_quiet_patched", False) is True
    # the patch must not break subprocess execution or output capture
    out = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout.strip() == "ok"
