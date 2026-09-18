"""The native Setup window mirrors the native Settings window: one window, reused, deep-linkable by step,
closable from inside, and it never touches the chat window or an open Settings window.

Exercises installer/bundle/vool_window.py directly, never its main() (which would supervise a runtime).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_window_module():
    spec = importlib.util.spec_from_file_location("vool_window_under_test_setup", ROOT / "installer" / "bundle" / "vool_window.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self) -> None:
        for fn in list(self.handlers):
            fn()


class _FakeWindow:
    def __init__(self, title: str, url: str) -> None:
        self.title, self.url = title, url
        self.shown = self.restored = 0
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


def test_one_setup_window_is_created_and_then_reused(api) -> None:
    module, bridge, fake = api
    first = bridge.open_setup()
    assert first["ok"] and first["mode"] == "created"
    assert len(fake.created) == 1
    win = fake.created[0]
    assert win.title == "VOOL Setup" and win.url == module.SETUP_URL and win.url.endswith("/setup")
    again = bridge.open_setup()
    assert again["ok"] and again["mode"] == "focused"
    assert len(fake.created) == 1, "a second Setup window was created"
    assert win.shown >= 1


def test_a_step_deep_link_reaches_a_new_and_an_open_window(api) -> None:
    module, bridge, fake = api
    bridge.open_setup("folder")
    assert fake.created[0].url == module.SETUP_URL + "#step=folder"
    bridge.open_setup("permissions")
    assert fake.created[0].loaded[-1] == module.SETUP_URL + "#step=permissions"
    assert len(fake.created) == 1


def test_a_hostile_step_cannot_steer_the_window(api) -> None:
    module, _bridge, _fake = api
    for hostile in ("../../etc/passwd", "folder' onload='x", "http://evil.example/", "a b", "//evil.example", "folder?x=1"):
        bridge2 = module._WindowApi()
        fake2 = _FakeWebview()
        bridge2.set_window(_FakeWindow("VOOL", module.URL), fake2)
        bridge2.open_setup(hostile)
        assert fake2.created[0].url == module.SETUP_URL, hostile


def test_a_closed_setup_window_is_forgotten_so_the_next_open_rebuilds(api) -> None:
    _module, bridge, fake = api
    bridge.open_setup()
    fake.created[0].events.closed.fire()
    assert bridge._setup_window is None
    bridge.open_setup()
    assert len(fake.created) == 2


def test_closing_setup_leaves_the_chat_and_an_open_settings_window_alone(api) -> None:
    _module, bridge, fake = api
    chat = bridge._window
    bridge.open_settings()
    settings = fake.created[0]
    bridge.open_setup()
    setup = fake.created[1]
    out = bridge.close_setup()
    assert out["ok"] is True and setup.destroyed is True and bridge._setup_window is None
    assert settings.destroyed is False and bridge._settings_window is settings
    assert getattr(chat, "destroyed", False) is False and chat.loaded == [] and bridge._window is chat
    assert bridge.close_setup() == {"ok": False, "error": "not_open"}


def test_no_native_backend_reports_itself_instead_of_raising(api) -> None:
    module, _bridge, _fake = api
    bare = module._WindowApi()
    assert bare.open_setup() == {"ok": False, "error": "native_window_unavailable"}
