"""Native desktop-companion boundary tests: real window flags, reuse, sync, and return."""

from __future__ import annotations

from core.companion_world_fragment import render_desktop_companion_html
from installer.bundle.vool_window import _WindowApi


class _FakeWindow:
    def __init__(self) -> None:
        self.shown = 0
        self.hidden = 0
        self.scripts: list[str] = []

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


class _FakeWebview:
    def __init__(self, result: object | None = None) -> None:
        self.window = result if result is not None else _FakeWindow()
        self.calls: list[tuple[tuple, dict]] = []

    def create_window(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.window


def test_desktop_document_is_the_same_48px_bighead_family() -> None:
    html = render_desktop_companion_html()
    assert "width='48' height='48'" in html
    assert "SPARK · Tiny Engineer" in html
    assert "RASCAL · Little Menace" in html
    assert "PRIME · Premium Voxel" in html
    assert "image-rendering:pixelated" in html
    assert "window.VoolDesktopCompanion" in html


def test_initial_state_is_embedded_safely_before_the_first_native_frame() -> None:
    html = render_desktop_companion_html(
        {"state": "tool", "character": "rascal", "pack": "ninja", "caption": "</script><b>bad</b>"}
    )
    assert '"state":"tool"' in html
    assert '"character":"rascal"' in html
    assert '"pack":"ninja"' in html
    assert "</script><b>bad</b>" not in html
    assert r"\u003c/script\u003e\u003cb\u003ebad\u003c/b\u003e" in html


def test_payload_is_allowlisted_and_bounded() -> None:
    clean = _WindowApi._companion_payload(
        {"state": "made-up", "character": "foreign", "pack": "bad", "caption": "x" * 200,
         "secret": "must-not-cross"}
    )
    assert clean == {"state": "unknown", "character": "spark", "pack": "default", "caption": "x" * 64}


def test_detach_creates_one_real_desktop_window_and_reuses_it() -> None:
    main = _FakeWindow()
    webview = _FakeWebview()
    api = _WindowApi()
    api.set_window(main, webview)

    result = api.detach_companion(
        {"state": "tool", "character": "rascal", "pack": "ninja", "caption": "Running tests…"}
    )
    assert result == {"ok": True, "mode": "desktop"}
    assert len(webview.calls) == 1
    args, kwargs = webview.calls[0]
    assert args == ("VOOL Companion",)
    assert kwargs["frameless"] is True
    assert kwargs["transparent"] is True
    # NOT on_top: pywebview maps that to NSStatusWindowLevel (25), which floats above modal and
    # security panels. The pet is placed at NSFloatingWindowLevel by installer/bundle/pet_native.py
    # instead -- above ordinary windows, below anything the operator has to answer.
    assert kwargs["on_top"] is False
    assert kwargs["easy_drag"] is True
    assert kwargs["resizable"] is False
    assert kwargs["focus"] is False
    assert kwargs["js_api"] is api
    assert "<canvas id='pet' width='48' height='48'>" in kwargs["html"]
    assert '"state":"tool"' in kwargs["html"], "the native window's first frame must not race idle"
    assert '"character":"rascal"' in kwargs["html"]
    companion = webview.window
    assert any('"state":"tool"' in script and '"character":"rascal"' in script
               for script in companion.scripts)

    again = api.detach_companion({"state": "success", "caption": "PASS"})
    assert again["ok"] is True
    assert len(webview.calls) == 1, "detaching twice must reuse, not stack native windows"
    assert companion.shown == 1
    assert any('"state":"success"' in script for script in companion.scripts)


def test_attach_hides_desktop_window_and_notifies_main_page() -> None:
    main = _FakeWindow()
    webview = _FakeWebview()
    api = _WindowApi()
    api.set_window(main, webview)
    assert api.detach_companion({"state": "idle"})["ok"] is True

    result = api.attach_companion()
    assert result == {"ok": True, "mode": "app"}
    assert webview.window.hidden == 1
    assert main.scripts == [
        "window.VoolCompanion&&window.VoolCompanion.desktopReturned&&window.VoolCompanion.desktopReturned()"
    ]


def test_non_native_surface_refuses_desktop_claim() -> None:
    api = _WindowApi()
    assert api.detach_companion({"state": "idle"}) == {
        "ok": False,
        "error": "native_window_unavailable",
    }


def test_settings_appearance_opens_only_the_owning_chat_controls() -> None:
    api = _WindowApi()
    assert api.open_companion_appearance() == {"ok": False, "error": "chat_unavailable"}

    class AppearanceWindow(_FakeWindow):
        def evaluate_js(self, script):
            self.scripts.append(script)
            return True

    main = AppearanceWindow()
    api.set_window(main, _FakeWebview())
    assert api.open_companion_appearance() == {"ok": True}
    assert main.scripts == ["Boolean(window.VoolCompanionDrawer && window.VoolCompanionDrawer.open())"]
    assert main.shown == 1
    unavailable = _FakeWindow()
    api.set_window(unavailable, _FakeWebview())
    assert api.open_companion_appearance() == {"ok": False, "error": "appearance_unavailable"}
    assert unavailable.shown == 0
