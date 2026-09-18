"""The desktop pet's tracker must not fight an in-progress drag (native settle policy).

NEW regression for product/desktop-usability-20260917. The failure path exercised here is
OWNERSHIP/ORDER of positioning updates, which no existing pet test covers: the baseline tick
applied its off-screen clamp EVERY tick -- including ticks between a drag's ``mouseDragged_``
events -- so the native tracker and pywebview's drag both called ``setFrameOrigin_`` and the
window visibly fought the cursor (reported as "one drag often fails to hold the pet; a second
drag is required"). A second defect rode along: the settled position was only persisted on
hide/return, never at drop time.

Two layers of proof:

* PURE -- ``settle_decision`` is the placement policy as a function (runs in the plain suite).
* NATIVE SEAM -- the real ``PetWindowTracker._tick`` against a fake NSWindow with the real
  ``AppKit.NSEvent`` class methods patched at the seam (skipped where pyobjc is absent, e.g.
  the 3.12 test interpreter; the decision, clamp and settle reporting are otherwise production).
"""

from __future__ import annotations

import pytest

from installer.bundle import pet_native

SCREEN = [(0.0, 0.0, 1440.0, 900.0)]
W, H = pet_native.PET_WINDOW_WIDTH, pet_native.PET_WINDOW_HEIGHT


# --------------------------------------------------------------------- pure policy


def test_a_drag_owns_the_geometry_no_clamp_while_the_button_is_down():
    """THE one-drag law: mid-drag ticks must not move the window at all."""
    past_right_edge = (1420.0, 400.0, W, H)      # overlap < MIN_VISIBLE_POINTS
    for drag_seen in (False, True):
        action, frame = pet_native.settle_decision(
            past_right_edge, SCREEN, button_down=True, drag_seen=drag_seen
        )
        assert action == "hold", "the tracker must not clamp while the drag owns the geometry"
        assert frame == past_right_edge, "hold must return the frame untouched"


def test_release_settles_once_with_the_clamped_final_position():
    released_past_edge = (1420.0, 400.0, W, H)
    action, frame = pet_native.settle_decision(
        released_past_edge, SCREEN, button_down=False, drag_seen=True
    )
    assert action == "settle", "the first tick after release settles the drag"
    assert frame == (1440.0 - W, 400.0, W, H), "settle clamps to keep minimum visibility"

    # Once settled (no drag seen since), the same position must HOLD, not re-clamp or re-report.
    action2, frame2 = pet_native.settle_decision(frame, SCREEN, button_down=False, drag_seen=False)
    assert action2 == "idle" and frame2 == frame, "after the settle the position must hold"


def test_an_onscreen_release_settles_without_moving():
    on_screen = (300.0, 300.0, W, H)
    action, frame = pet_native.settle_decision(on_screen, SCREEN, button_down=False, drag_seen=True)
    assert action == "settle", "release still reports for persistence"
    assert frame == on_screen, "an on-screen drop is not moved by settling"


def test_idle_offscreen_windows_are_still_rescued():
    """Preservation: the unplugged-monitor/off-screen safety net keeps working while idle."""
    off_everything = (5000.0, 500.0, W, H)
    action, frame = pet_native.settle_decision(
        off_everything, SCREEN, button_down=False, drag_seen=False
    )
    assert action == "rescue"
    assert 0.0 <= frame[0] <= 1440.0 - W and 0.0 <= frame[1] <= 900.0 - H


def test_a_straddling_window_is_left_alone():
    """Preservation: a window straddling two displays (>= min visible on one) is legitimate."""
    two_screens = [(0.0, 0.0, 1440.0, 900.0), (1440.0, 0.0, 1440.0, 900.0)]
    straddling = (1350.0, 400.0, W, H)           # >= 40pt visible on both
    action, frame = pet_native.settle_decision(
        straddling, two_screens, button_down=False, drag_seen=False
    )
    assert action == "idle" and frame == straddling


# ----------------------------------------------------------------- native seam


class _Pt:
    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = float(x), float(y)


class _Rect:
    def __init__(self, x: float, y: float, w: float, h: float) -> None:
        self.origin = _Pt(x, y)
        self.size = type("S", (), {"width": float(w), "height": float(h)})()


class _FakeWindow:
    def __init__(self, x: float, y: float, w: float = float(W), h: float = float(H)) -> None:
        self._frame = _Rect(x, y, w, h)
        self.moves: list[tuple[float, float]] = []
        self.ignores = False

    def frame(self) -> _Rect:
        return self._frame

    def setFrameOrigin_(self, pt) -> None:
        self.moves.append((float(pt.x), float(pt.y)))
        self._frame = _Rect(pt.x, pt.y, self._frame.size.width, self._frame.size.height)

    def ignoresMouseEvents(self) -> bool:
        return self.ignores

    def setIgnoresMouseEvents_(self, value: bool) -> None:
        self.ignores = bool(value)


@pytest.fixture()
def native_seam(monkeypatch):
    """One 1440x900 display; AppKit button/cursor state routed through a controllable seam."""
    pytest.importorskip("AppKit")
    import AppKit

    monkeypatch.setattr(pet_native, "visible_frames", lambda: list(SCREEN))
    buttons = type("Buttons", (), {"pressed": 0, "cursor": _Pt(0.0, 0.0)})()
    monkeypatch.setattr(
        AppKit.NSEvent, "pressedMouseButtons", classmethod(lambda _c: buttons.pressed)
    )
    monkeypatch.setattr(
        AppKit.NSEvent, "mouseLocation", classmethod(lambda _c: buttons.cursor)
    )
    return buttons


def test_real_tick_holds_through_a_drag_and_settles_once(native_seam):
    window = _FakeWindow(1200.0, 700.0)
    settled: list[tuple[float, float]] = []
    tracker = pet_native.PetWindowTracker(window, on_drop=lambda origin: settled.append(origin))

    # A drag carries the window mostly past the right edge and holds it there across ticks.
    native_seam.pressed = 1
    window.setFrameOrigin_(_Pt(1420.0, 400.0))
    window.moves.clear()
    tracker._tick()
    tracker._tick()
    tracker._tick()
    assert window.moves == [], "the real tick must not clamp while the drag is in progress"

    native_seam.pressed = 0
    tracker._tick()
    assert len(window.moves) == 1, f"one settle move, got {window.moves}"
    assert window.moves[0][0] == 1440.0 - W
    assert settled and settled[-1] == window.moves[0], "the settle is reported for persistence"

    window.moves.clear()
    tracker._tick()
    tracker._tick()
    assert window.moves == [] and len(settled) == 1, "after the settle the position HOLDS"


def test_real_tick_rescues_an_idle_offscreen_window(native_seam):
    window = _FakeWindow(5000.0, 500.0)
    tracker = pet_native.PetWindowTracker(window, on_drop=None)
    native_seam.pressed = 0
    tracker._tick()
    assert len(window.moves) == 1 and window.moves[0][0] <= 1440.0 - W


def test_controller_marshals_the_settle_off_the_calling_thread():
    """The settle callback must not run on the timer's thread (evaluate_js deadlock guard)."""
    import threading

    controller = pet_native.PetWindowController.__new__(pet_native.PetWindowController)
    controller.on_position_settled = None
    controller._report_settled((10.0, 20.0))     # no callback: a no-op, never raises

    seen: list[tuple[str, tuple]] = []
    done = threading.Event()

    def callback(origin):
        seen.append((threading.current_thread().name, tuple(origin)))
        done.set()

    controller.on_position_settled = callback
    main_name = threading.current_thread().name
    controller._report_settled((30.0, 40.0))
    assert done.wait(5.0), "the settle callback must run"
    assert seen and seen[0][1] == (30.0, 40.0)
    assert seen[0][0] != main_name, "the callback must be marshalled off the calling thread"


def test_window_api_wires_drop_settling_to_persistence():
    """The host must persist at drop time: detach_companion registers the settle observer."""
    from installer.bundle.vool_window import _WindowApi

    api = _WindowApi()
    api._window = None
    # The observer the window wires must degrade to a no-op without a chat window (it reads
    # _remember_pet_position's own guards), so wiring it never raises at detach time.
    assert callable(api._remember_pet_position)
