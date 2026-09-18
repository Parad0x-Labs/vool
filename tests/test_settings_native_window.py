"""The native Settings window: one window, reused, deep-linkable, and Cmd+, actually bound.

These exercise installer/bundle/vool_window.py directly. They deliberately do NOT run its
main(): that starts the native runtime supervisor, which with the shipped
VOOL_NATIVE_REQUIRE_OWNED_RUNTIME=1 STOPS a pre-existing runtime -- including the operator's own
daemon. The window-management logic and the Cocoa menu binding are both reachable without it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_window_module():
    """Import the bundle window module by path; it is not part of an importable package."""
    spec = importlib.util.spec_from_file_location(
        "vool_window_under_test", ROOT / "installer" / "bundle" / "vool_window.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeWindow:
    def __init__(self, title: str, url: str) -> None:
        self.title = title
        self.url = url
        self.shown = 0
        self.restored = 0
        self.destroyed = False
        self.loaded: list[str] = []
        self.events = type("E", (), {})()
        self.events.closed = _FakeEvent()

    def show(self) -> None:
        self.shown += 1

    def restore(self) -> None:
        self.restored += 1

    def destroy(self) -> None:
        self.destroyed = True

    def load_url(self, url: str) -> None:
        self.loaded.append(url)
        self.url = url


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self) -> None:
        for fn in list(self.handlers):
            fn()


class _FakeWebview:
    def __init__(self) -> None:
        self.created: list[_FakeWindow] = []

    def create_window(self, title, url=None, **kw):
        win = _FakeWindow(title, url)
        self.created.append(win)
        return win


@pytest.fixture()
def api():
    module = _load_window_module()
    bridge = module._WindowApi()
    fake = _FakeWebview()
    bridge.set_window(_FakeWindow("VOOL", module.URL), fake)
    return module, bridge, fake


def test_one_settings_window_is_created_and_then_reused(api) -> None:
    module, bridge, fake = api
    first = bridge.open_settings()
    assert first["ok"] and first["mode"] == "created"
    assert len(fake.created) == 1
    settings = fake.created[0]
    assert settings.title == "VOOL Settings"
    assert settings.url == module.SETTINGS_URL
    assert settings.url.endswith("/settings")

    # Reopening focuses what exists; it must never stack a second window.
    again = bridge.open_settings()
    assert again["ok"] and again["mode"] == "focused"
    assert len(fake.created) == 1, "a second Settings window was created"
    assert settings.shown >= 1


def test_a_closed_window_is_forgotten_so_the_next_open_rebuilds(api) -> None:
    _module, bridge, fake = api
    bridge.open_settings()
    settings = fake.created[0]
    settings.events.closed.fire()          # the user closed the window
    assert bridge._settings_window is None
    bridge.open_settings()
    assert len(fake.created) == 2, "a closed Settings window was not rebuilt"


def test_closing_settings_leaves_the_chat_window_alone(api) -> None:
    _module, bridge, fake = api
    chat = bridge._window
    bridge.open_settings()
    settings = fake.created[0]
    out = bridge.close_settings()
    assert out["ok"] is True
    assert settings.destroyed is True
    assert bridge._settings_window is None
    # The chat window object is untouched: not destroyed, not reloaded, not hidden.
    assert getattr(chat, "destroyed", False) is False
    assert chat.loaded == []
    assert bridge._window is chat


def test_a_section_deep_link_reaches_an_open_window(api) -> None:
    module, bridge, fake = api
    bridge.open_settings("memory")
    assert fake.created[0].url == module.SETTINGS_URL + "#memory"
    # An open window keeps its old fragment, so it has to be re-pointed rather than just raised.
    bridge.open_settings("models")
    assert fake.created[0].loaded[-1] == module.SETTINGS_URL + "#models"
    assert len(fake.created) == 1


def test_a_hostile_section_cannot_steer_the_window(api) -> None:
    """The section crosses from page JS, so it is allowlisted to a bare identifier. Anything else
    falls back to the plain Settings URL rather than navigating somewhere chosen by the caller."""
    module, bridge, fake = api
    for hostile in (
        "../../etc/passwd",
        "memory' onload='x",
        "http://evil.example/",
        "a b",
        "//evil.example",
        "memory?x=1",
    ):
        bridge._settings_window = None
        fake.created.clear()
        bridge.open_settings(hostile)
        assert fake.created[0].url == module.SETTINGS_URL, f"{hostile!r} steered the window"
    # A legitimate identifier still works.
    bridge._settings_window = None
    fake.created.clear()
    bridge.open_settings("agent-network_1")
    assert fake.created[0].url == module.SETTINGS_URL + "#agent-network_1"


def test_settings_is_unavailable_rather_than_broken_without_pywebview(api) -> None:
    _module, bridge, _fake = api
    bridge.set_window(bridge._window, None)
    out = bridge.open_settings()
    assert out == {"ok": False, "error": "native_window_unavailable"}


def test_the_menu_degrades_to_nothing_when_the_api_is_missing(monkeypatch) -> None:
    """The bundle installs pywebview unpinned, so the menu API can be absent. Losing the menu item
    is acceptable; failing to open the window is not."""
    module = _load_window_module()
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def deny(name, *a, **kw):
        if name == "webview.menu":
            raise ImportError("no menu API in this pywebview")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", deny)
    assert module._settings_menu(object()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa menu binding is macOS-only")
def test_cmd_comma_is_bound_on_a_real_cocoa_menu() -> None:
    """Build the application menu the way pywebview builds it, then run the real binder against
    it. This is the half that can be checked without opening a window."""
    AppKit = pytest.importorskip("AppKit")
    module = _load_window_module()

    app = AppKit.NSApplication.sharedApplication()
    main_menu = AppKit.NSMenu.alloc().init()
    app_item = AppKit.NSMenuItem.alloc().init()
    main_menu.insertItem_atIndex_(app_item, 0)
    app_menu = AppKit.NSMenu.alloc().init()
    app_item.setSubmenu_(app_menu)
    app_menu.addItemWithTitle_action_keyEquivalent_("About VOOL", "orderFrontStandardAboutPanel:", "")
    # pywebview creates every custom MenuAction with an EMPTY key equivalent; that is the gap.
    settings_item = app_menu.addItemWithTitle_action_keyEquivalent_("Settings…", "handleMenuAction:", "")
    assert str(settings_item.keyEquivalent()) == ""

    previous = app.mainMenu()
    app.setMainMenu_(main_menu)
    try:
        module._install_settings_shortcut()
        assert str(settings_item.keyEquivalent()) == ","
        assert int(settings_item.keyEquivalentModifierMask()) & int(AppKit.NSCommandKeyMask)
    finally:
        if previous is not None:
            app.setMainMenu_(previous)


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa menu binding is macOS-only")
def test_the_shortcut_binder_survives_a_menu_that_has_no_settings_item() -> None:
    AppKit = pytest.importorskip("AppKit")
    module = _load_window_module()
    app = AppKit.NSApplication.sharedApplication()
    empty = AppKit.NSMenu.alloc().init()
    item = AppKit.NSMenuItem.alloc().init()
    empty.insertItem_atIndex_(item, 0)
    item.setSubmenu_(AppKit.NSMenu.alloc().init())
    previous = app.mainMenu()
    app.setMainMenu_(empty)
    try:
        module._install_settings_shortcut()   # must not raise
    finally:
        if previous is not None:
            app.setMainMenu_(previous)


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa menu binding is macOS-only")
def test_the_shortcut_binder_waits_for_a_menu_that_arrives_after_the_post_start_hook(monkeypatch) -> None:
    """Measured in the built bundle: pywebview attaches the application menu AFTER the post-start
    hook runs, so a binder that looks once finds nothing and the shipped item has no shortcut. The
    binder must re-check on the main run loop until the item exists."""
    AppKit = pytest.importorskip("AppKit")
    from PyObjCTools import AppHelper

    module = _load_window_module()
    app = AppKit.NSApplication.sharedApplication()
    previous = app.mainMenu()
    empty = AppKit.NSMenu.alloc().init()
    app.setMainMenu_(empty)                       # nothing there yet, as at post-start time
    scheduled: list = []
    monkeypatch.setattr(AppHelper, "callLater", lambda delay, fn, *args: scheduled.append((delay, fn, args)))
    try:
        module._install_settings_shortcut(attempts=5, delay_s=0.01)
        assert scheduled, "a missing item must schedule a re-check on the run loop"
        # The menu arrives, the way pywebview builds it.
        main_menu = AppKit.NSMenu.alloc().init()
        app_item = AppKit.NSMenuItem.alloc().init()
        main_menu.insertItem_atIndex_(app_item, 0)
        app_menu = AppKit.NSMenu.alloc().init()
        app_item.setSubmenu_(app_menu)
        settings_item = app_menu.addItemWithTitle_action_keyEquivalent_("Settings…", "handleMenuAction:", "")
        app.setMainMenu_(main_menu)
        delay, fn, args = scheduled.pop(0)
        fn(*args)                                  # the scheduled re-check fires
        assert str(settings_item.keyEquivalent()) == ","
        assert int(settings_item.keyEquivalentModifierMask()) & int(AppKit.NSCommandKeyMask)
        assert not scheduled, "once bound, nothing more is scheduled"
    finally:
        if previous is not None:
            app.setMainMenu_(previous)


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa menu binding is macOS-only")
def test_the_shortcut_binder_gives_up_after_its_bounded_attempts(monkeypatch) -> None:
    AppKit = pytest.importorskip("AppKit")
    from PyObjCTools import AppHelper

    module = _load_window_module()
    app = AppKit.NSApplication.sharedApplication()
    previous = app.mainMenu()
    app.setMainMenu_(AppKit.NSMenu.alloc().init())
    scheduled: list = []
    monkeypatch.setattr(AppHelper, "callLater", lambda delay, fn, *args: scheduled.append((delay, fn, args)))
    try:
        module._install_settings_shortcut(attempts=3, delay_s=0.01)
        fired = 0
        while scheduled and fired < 10:
            _, fn, args = scheduled.pop(0)
            fn(*args)
            fired += 1
        assert fired == 3, fired                   # the first call plus (attempts - 1) re-checks, then it stops
    finally:
        if previous is not None:
            app.setMainMenu_(previous)
