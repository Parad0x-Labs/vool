"""Checks the shared folder for Drops while Drop is on, so nobody has to press a button.

One daemon thread per server, started at boot and woken when Settings saves. It only does
what the "Check for Drops" button does (``DropHome.poll``). Off means off: with Drop disabled
the thread does nothing and opens nothing.
"""
import logging
import threading
import time

from core.drop import enabled
from core.runtime_paths import active_vool_home

INTERVAL = 15
_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_thread = None
_booted = False
_state = {"state": "off", "last_checked": None, "error": None}


def _loop(home):
    while not _stop.is_set():
        try:
            if active_vool_home().resolve() == home and enabled():
                from core.web.api.drop_api import _home

                with _home() as drop:
                    drop.poll()
                _state.update(state="ready", last_checked=int(time.time() * 1000), error=None)
            else:
                _state.update(state="off", error=None)
        except Exception as exc:
            # A missing folder, a busy store, a locked keychain: said in Settings, tried again.
            _state.update(state="unavailable", error=getattr(exc, "code", type(exc).__name__))
        _wake.wait(INTERVAL)
        _wake.clear()


def wake():
    global _thread
    if not _booted:
        return
    with _lock:
        if enabled() and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_loop, args=(active_vool_home().resolve(),),
                                       name="drop-check", daemon=True)
            _thread.start()
        _wake.set()


def boot():
    global _booted
    _booted = True
    try:
        wake()
    except Exception:
        logging.getLogger(__name__).exception("Drop checking did not start; Settings can still check by hand")


def status():
    return {"running": bool(_thread and _thread.is_alive()), **_state}
