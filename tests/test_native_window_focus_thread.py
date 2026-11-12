"""Window focus must cross the Cocoa main-thread boundary from bridge workers."""
import importlib.util
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


def load_host():
    path = Path(__file__).resolve().parents[1] / 'installer/bundle/vool_window.py'
    spec = importlib.util.spec_from_file_location('focus_host', path)
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    return host


def test_bridge_focus_dispatches_restore_and_show_to_main_thread(monkeypatch):
    host = load_host()
    monkeypatch.setattr(host.sys, 'platform', 'darwin')
    pending = queue.Queue()
    monkeypatch.setitem(sys.modules, 'PyObjCTools', SimpleNamespace(
        AppHelper=SimpleNamespace(callAfter=lambda fn: pending.put(fn))))
    calls = []
    window = SimpleNamespace(
        restore=lambda: calls.append(('restore', threading.current_thread())),
        show=lambda: calls.append(('show', threading.current_thread())))
    worker = threading.Thread(target=host._focus_window, args=(window,))
    worker.start()
    try:
        callback = pending.get(timeout=1)
    except queue.Empty:
        worker.join(1)
        raise AssertionError(f'Cocoa focus bypassed main-thread dispatch: {calls}')
    assert calls == []
    callback()
    worker.join(2)
    assert not worker.is_alive()
    assert calls == [('restore', threading.main_thread()), ('show', threading.main_thread())]


def test_focus_on_main_thread_remains_immediate(monkeypatch):
    host = load_host()
    monkeypatch.setattr(host.sys, 'platform', 'darwin')
    monkeypatch.setitem(sys.modules, 'PyObjCTools', SimpleNamespace(
        AppHelper=SimpleNamespace(callAfter=lambda fn: (_ for _ in ()).throw(
            AssertionError('main thread must not enqueue and wait on itself')))))
    calls = []
    host._focus_window(SimpleNamespace(restore=lambda: calls.append('restore'),
                                      show=lambda: calls.append('show')))
    assert calls == ['restore', 'show']


def test_other_backends_keep_restore_and_show_without_cocoa(monkeypatch):
    host = load_host()
    monkeypatch.setattr(host.sys, 'platform', 'linux')
    calls = []
    host._focus_window(SimpleNamespace(restore=lambda: calls.append('restore'),
                                      show=lambda: calls.append('show')))
    assert calls == ['restore', 'show']
