"""Opt-in daemon polling; Settings is a controller, not the delivery engine."""
import atexit
import logging
import threading
import time

from core.drop import enabled as drop_enabled
from core.runtime_paths import active_vool_home

from . import settings

_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_thread = None
_booted = False
_state = {'state':'off', 'last_checked':None, 'error':None}


def _loop(home):
    while not _stop.is_set():
        try:
            root = home/'lend'
            if root.exists():
                from .worker import clean_orphans

                clean_orphans(root)
            if active_vool_home().resolve() == home and settings.enabled() and drop_enabled():
                from core.web.api.drop_api import _home

                from .service import LendHome

                with _home() as drop, LendHome(drop) as owner:
                    _state.update(state='checking', error=None)
                    owner.poll()
                    _state.update(state='ready', last_checked=int(time.time()*1000), error=None)
            else:
                _state.update(state='off', error=None)
        except Exception as exc:
            _state.update(state='unavailable', error=type(exc).__name__)
            logging.getLogger(__name__).exception('Lending delivery is unavailable; it will be checked again')
        _wake.wait(2)
        _wake.clear()


def wake():
    global _thread
    if not _booted:
        return
    with _lock:
        needs_cleanup = (active_vool_home()/'lend/supervision').exists()
        if (((settings.enabled() and drop_enabled()) or needs_cleanup)
                and (_thread is None or not _thread.is_alive())):
            _thread = threading.Thread(target=_loop, args=(active_vool_home().resolve(),),
                                       name='lend-delivery', daemon=True)
            _thread.start()
        _wake.set()


def boot():
    global _booted
    _booted = True
    wake()


def status():
    return {'configured':_booted, 'running':bool(_thread and _thread.is_alive()), **_state}


def shutdown():
    _stop.set()
    _wake.set()
    if _thread is not None and _thread is not threading.current_thread():
        _thread.join(125)


atexit.register(shutdown)
