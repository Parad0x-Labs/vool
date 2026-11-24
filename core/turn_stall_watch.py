"""Name the frame a silent turn is waiting in, without a signal.

On the Mac (0.8 install on the owner's own profile, 2026-11-21) the first chat after launch,
"Calculate 23 * 19", sat 95 s between ``task_received`` and its route with no event, no model call
and nothing in the logs; the same chat on a fresh home and on a copy of that profile answered in
3-5 s. The SIGUSR1 dump that was meant to catch such a stall crashed a diagnostic copy of the
server (faulthandler writing every thread's stack while one was inside a filesystem call), so it
cannot be used on the person's running app.

This watch is plain Python: a timer thread reads the turn thread's current frame with
``sys._current_frames`` and logs its stack (file, line, function and code line only, never local
values) when the turn has run past the threshold, and again at each later step, a few times, then
every two minutes for up to an hour. A turn that ends earlier logs nothing.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

_LOG = logging.getLogger(__name__)

_DEFAULT_FIRST_S = 20.0
_DEFAULT_EVERY_S = 20.0
_MAX_DUMPS = 4
#: After the first few dumps, a turn still running is named again at this slower step, this many times at most.
#: Intel smoke on 3727b4e72: the last dump came 80 s into a turn that then ran past 15 minutes, and what it waited
#: on for the other 13 had to be read off the model server's own log.
_LATE_EVERY_S = 120.0
_MAX_LATE_DUMPS = 30


def _seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else 0.0


def stack_of(thread_id: int) -> str:
    """The thread's current Python stack, oldest frame first, or "" when it has ended."""
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return ""
    return "".join(traceback.format_stack(frame))


@contextmanager
def watch_turn(label: str = "turn") -> Iterator[None]:
    """Log this thread's stack if the block is still running after the threshold.

    ``VOOL_TURN_STALL_DUMP_SECONDS`` sets the first threshold (0 turns the watch off),
    ``VOOL_TURN_STALL_DUMP_EVERY_SECONDS`` the gap between later dumps."""
    first = _seconds("VOOL_TURN_STALL_DUMP_SECONDS", _DEFAULT_FIRST_S)
    if not first:
        yield
        return
    every = _seconds("VOOL_TURN_STALL_DUMP_EVERY_SECONDS", _DEFAULT_EVERY_S) or _DEFAULT_EVERY_S
    thread_id = threading.get_ident()
    done = threading.Event()
    started = time.monotonic()

    def watch() -> None:
        wait = first
        for count in range(_MAX_DUMPS + _MAX_LATE_DUMPS):
            if count >= _MAX_DUMPS:
                wait = max(wait, _LATE_EVERY_S)
            if done.wait(wait):
                return
            stack = stack_of(thread_id)
            if not stack or done.is_set():
                return
            _LOG.warning(
                "%s still running after %.1f s; it is here:\n%s", label, time.monotonic() - started, stack
            )
            wait = every

    watcher = threading.Thread(target=watch, name="turn-stall-watch", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        done.set()
