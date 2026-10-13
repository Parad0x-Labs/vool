"""Native pet host policy; construct real AppKit objects without showing windows."""
import sys
from types import SimpleNamespace

import pytest


def test_only_the_pet_is_a_nonactivating_panel(monkeypatch):
    if sys.platform != 'darwin':
        pytest.skip('AppKit policy requires macOS')
    AppKit = pytest.importorskip('AppKit')
    pytest.importorskip('webview')
    from webview.platforms import cocoa

    from installer.bundle import pet_native

    AppKit.NSApplication.sharedApplication()
    seen = []
    def capture_host(view, window):
        seen.append(cocoa.BrowserView.WindowHost)
    monkeypatch.setattr(cocoa.BrowserView, '__init__', capture_host)
    # Process-global native classes/selectors live for this process; this test installs once.
    pet_native.install_nonactivating_pet_host()
    original_host = cocoa.BrowserView.WindowHost
    ordinary = SimpleNamespace(title='VOOL', focus=True, frameless=False, transparent=False, easy_drag=False)
    pet = SimpleNamespace(title='VOOL Companion', focus=False, frameless=True, transparent=True, easy_drag=True)
    cocoa.BrowserView.__init__(object(), ordinary)
    cocoa.BrowserView.__init__(object(), pet)
    cocoa.BrowserView.__init__(object(), ordinary)
    assert seen[0] is seen[2] is original_host
    assert issubclass(seen[1], AppKit.NSPanel)
    panel = seen[1].alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0,0,176,176), AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False)
    panel.setReleasedWhenClosed_(False)
    try:
        facts = pet_native.configure_pet_window(panel)
        assert facts['nonactivating_panel'] is True
        assert panel.canBecomeKeyWindow() is False
        assert panel.canBecomeMainWindow() is False
        assert panel.hidesOnDeactivate() is False
        assert panel.level() < AppKit.NSModalPanelWindowLevel
    finally:
        panel.close()
