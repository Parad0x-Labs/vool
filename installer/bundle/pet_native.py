"""Narrow AppKit corrections for the detached pixel pet, and the geometry they depend on.

Kept in its own module on purpose. ``vool_window.py`` is shared with the wallet, startup and
authentication work, so the pet's native behaviour lives here and that file only calls in. Upgrading
or removing the pet touches this file and four lines over there.

Two halves:

* **Pure geometry** (no AppKit import): hit regions, screen unions, clamping, saved-position
  recovery. Importable and testable anywhere, which is what lets the awkward cases -- a display at a
  negative origin, a saved position on a monitor that has been unplugged, a window flung above the
  top edge -- be asserted without a display attached.
* **A thin native layer** (AppKit, lazily imported): window level, Spaces policy, click-through and
  the on-screen safety net, all marshalled onto the Cocoa main thread.

Everything here is presentation. Nothing in this module reads or writes user data, runs a task, or
carries any authority.
"""
from __future__ import annotations

import contextlib
import math
import threading
import time

# --------------------------------------------------------------------------- constants

# The pet's window is 176x176. The character canvas is 132x132 centred inside it; the rest is
# deliberately empty margin that must NOT intercept mouse input.
PET_WINDOW_WIDTH = 176
PET_WINDOW_HEIGHT = 176
PET_CANVAS_POINTS = 132
PET_CONTROL_POINTS = 24
PET_CONTROL_INSET = 6

# AppKit window levels, restated as plain numbers so the policy is assertable without a GUI.
# NSFloatingWindowLevel: above ordinary application windows, BELOW anything the operator has to
# answer. NSStatusWindowLevel (25) -- what pywebview's ``on_top=True`` sets -- sits above modal
# panels, which is why the pet is not allowed to use it.
PET_WINDOW_LEVEL = 3           # NSFloatingWindowLevel
MODAL_PANEL_LEVEL = 8          # NSModalPanelWindowLevel
STATUS_WINDOW_LEVEL = 25       # NSStatusWindowLevel
SCREEN_SAVER_LEVEL = 1000      # NSScreenSaverWindowLevel

# How much of the pet must stay inside some display. A saved position pointing at a monitor that is
# no longer attached, or a drag that flings the window past an edge, is pulled back to this much.
MIN_VISIBLE_POINTS = 40

# Cursor poll interval for click-through. Bounded and measured (see ``PetWindowTracker.stats``); the
# timer is stopped whenever the pet is hidden, so a hidden pet costs nothing.
TRACK_INTERVAL_SECONDS = 0.06


Rect = tuple[float, float, float, float]


# --------------------------------------------------------------------------- pure geometry


def pet_hit_rects(width: float = PET_WINDOW_WIDTH, height: float = PET_WINDOW_HEIGHT) -> list[Rect]:
    """Window-local rects (AppKit origin: bottom-left) that are allowed to intercept the mouse.

    Deliberately a LIST, not one bounding box: the character canvas and the corner control are
    separate islands, and unioning them into a single rectangle would hand the empty corners back
    the click-blocking behaviour this exists to remove.

    Granularity is region-level, not per-pixel-alpha: the canvas is the pet's whole drawing surface
    and several states paint out to its edges (confetti, the desk and monitor, the question mark),
    so the canvas box is the honest boundary. It is not a claim that every transparent pixel inside
    the canvas passes clicks through.
    """
    canvas = float(min(PET_CANVAS_POINTS, width, height))
    x = (width - canvas) / 2.0
    y = (height - canvas) / 2.0
    control = float(PET_CONTROL_POINTS)
    inset = float(PET_CONTROL_INSET)
    return [
        (x, y, canvas, canvas),
        (width - inset - control, height - inset - control, control, control),
    ]


def _point_in_rect(px: float, py: float, rect: Rect) -> bool:
    x, y, w, h = rect
    return x <= px <= x + w and y <= py <= y + h


def point_hits_pet(screen_x: float, screen_y: float, frame: Rect, avoid_rect: Rect | None = None) -> bool:
    """Would a click at this SCREEN point land on the pet rather than on its transparent margin?

    ``avoid_rect`` (AppKit bottom-left coordinates, the same space ``frame`` lives in) is the
    OPERATOR-ANSWER region: while a permission bar or mode banner the operator must answer is
    on screen, a click inside that region belongs to the surface UNDER the pet, so the pet
    yields the pointer there instead of intercepting it (2026-09-18: the pet parked over the
    footer made Revoke unreachable by ordinary click). Everywhere else the pet's own hit rects
    decide exactly as before, so dragging stays functional.
    """
    if avoid_rect is not None:
        ax, ay, aw, ah = avoid_rect
        if ax <= screen_x <= ax + aw and ay <= screen_y <= ay + ah:
            return False
    fx, fy, fw, fh = frame
    local_x = screen_x - fx
    local_y = screen_y - fy
    return any(_point_in_rect(local_x, local_y, rect) for rect in pet_hit_rects(fw, fh))


def _intersection(a: Rect, b: Rect) -> tuple[float, float]:
    """Overlap of two rects as (width, height); zero when they do not meet."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return (max(0.0, w), max(0.0, h))


def visible_overlap(frame: Rect, visible_frames: list[Rect]) -> tuple[float, float]:
    """Best (width, height) overlap between the window and any single display."""
    best = (0.0, 0.0)
    for screen in visible_frames:
        w, h = _intersection(frame, screen)
        if w * h > best[0] * best[1]:
            best = (w, h)
    return best


def _nearest_screen(frame: Rect, visible_frames: list[Rect]) -> Rect:
    fx, fy, fw, fh = frame
    cx, cy = fx + fw / 2.0, fy + fh / 2.0
    best = visible_frames[0]
    best_distance = None
    for screen in visible_frames:
        sx, sy, sw, sh = screen
        dx = cx - (sx + sw / 2.0)
        dy = cy - (sy + sh / 2.0)
        distance = dx * dx + dy * dy
        if best_distance is None or distance < best_distance:
            best, best_distance = screen, distance
    return best


def clamp_frame_to_screens(
    frame: Rect, visible_frames: list[Rect], min_visible: float = MIN_VISIBLE_POINTS
) -> Rect:
    """Pull a window back onto a display, without forbidding a straddle across two of them.

    A window that keeps ``min_visible`` points of overlap with SOME display is left exactly where it
    is -- straddling a monitor boundary is a legitimate place for a pet to sit. Anything else (a
    stale saved position on an unplugged display, a drag flung past the top edge, coordinates from a
    different Dock or menu-bar layout) is moved into the nearest display and clamped there.

    This also stands in for pywebview's own clamp, which adds the window height where it must
    subtract it and so pushes a window flung upward FURTHER off-screen.
    """
    if not visible_frames:
        return frame
    fx, fy, fw, fh = frame
    overlap_w, overlap_h = visible_overlap(frame, visible_frames)
    if overlap_w >= min(min_visible, fw) and overlap_h >= min(min_visible, fh):
        return frame
    sx, sy, sw, sh = _nearest_screen(frame, visible_frames)
    x = min(max(fx, sx), sx + max(0.0, sw - fw))
    y = min(max(fy, sy), sy + max(0.0, sh - fh))
    return (x, y, fw, fh)


def restore_saved_position(
    saved: object, visible_frames: list[Rect], size: tuple[float, float] | None = None
) -> Rect | None:
    """Turn a persisted {x, y} into a frame that is actually on a display today.

    Returns ``None`` when nothing usable was saved, so the caller can centre a first-run pet rather
    than inventing a coordinate.
    """
    if not isinstance(saved, dict):
        return None
    try:
        x = float(saved["x"])
        y = float(saved["y"])
    except (KeyError, TypeError, ValueError):
        return None
    width, height = size or (float(PET_WINDOW_WIDTH), float(PET_WINDOW_HEIGHT))
    return clamp_frame_to_screens((x, y, width, height), visible_frames)


def default_pet_frame(visible_frames: list[Rect]) -> Rect:
    """Where a pet with no saved position goes: lower-right of the primary display, inset."""
    width, height = float(PET_WINDOW_WIDTH), float(PET_WINDOW_HEIGHT)
    if not visible_frames:
        return (0.0, 0.0, width, height)
    sx, sy, sw, _sh = visible_frames[0]
    return (sx + sw - width - 48.0, sy + 96.0, width, height)


# --------------------------------------------------------------------------- native layer


def on_main_thread(fn, timeout: float = 5.0):
    """Run ``fn`` on the Cocoa main thread and return its result.

    Not optional. macOS 26 aborts the process (SIGTRAP, no Python traceback) when NSWindow geometry
    is mutated from a background thread, and pywebview's JS-API bridge -- which is what calls into
    the pet -- runs on a worker thread.
    """
    import threading as _threading

    from PyObjCTools import AppHelper

    if _threading.current_thread() is _threading.main_thread():
        return fn()

    box: dict = {}
    done = threading.Event()

    def _run():
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc
        finally:
            done.set()

    AppHelper.callAfter(_run)
    if not done.wait(timeout):
        raise TimeoutError("pet window main-thread call did not complete")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def visible_frames() -> list[Rect]:
    """Every attached display's visible frame (menu bar and Dock already excluded)."""
    import AppKit

    frames = []
    for screen in AppKit.NSScreen.screens():
        rect = screen.visibleFrame()
        frames.append((
            float(rect.origin.x), float(rect.origin.y),
            float(rect.size.width), float(rect.size.height),
        ))
    return frames


def configure_pet_window(ns_window) -> dict:
    """Apply the pet's window policy. Must already be on the main thread."""
    import AppKit

    ns_window.setLevel_(PET_WINDOW_LEVEL)
    # Follow the operator across Spaces and be allowed over a full-screen app, while never being
    # promoted into a window that takes part in full-screen itself.
    ns_window.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        | AppKit.NSWindowCollectionBehaviorStationary
    )
    ns_window.setOpaque_(False)
    ns_window.setHasShadow_(False)
    ns_window.setIgnoresMouseEvents_(False)
    return {
        "level": int(ns_window.level()),
        "collection_behavior": int(ns_window.collectionBehavior()),
        "is_opaque": bool(ns_window.isOpaque()),
        "has_shadow": bool(ns_window.hasShadow()),
    }


def order_front_without_focus(ns_window) -> None:
    """Show the pet without activating the app.

    pywebview's ``Window.show()`` calls ``makeKeyAndOrderFront:`` AND
    ``activateIgnoringOtherApps:``, which yanks the whole VOOL app in front of whatever the operator
    is typing into. A pet must appear without taking the keyboard.
    """
    ns_window.orderFrontRegardless()


def settle_decision(
    frame: Rect,
    screens: list[Rect],
    *,
    button_down: bool,
    drag_seen: bool,
) -> tuple[str, Rect]:
    """Pure placement policy for one tracker tick. No AppKit, no clock, no I/O.

    Returns ``(action, frame)`` where action is one of:

    * ``"hold"`` -- a drag owns the geometry: the tracker must NOT move the window. This is
      the ownership rule that stops the timer fighting pywebview's own ``mouseDragged_``
      (both call ``setFrameOrigin_``; letting both write is the reported one-drag failure).
    * ``"settle"`` -- the button just came up: clamp exactly ONCE and report the final
      position for persistence. After this the position must hold.
    * ``"rescue"`` -- no drag is involved and the window is off every display: pull it back
      (the unplugged-monitor safety net, preserved from the original tracker).
    * ``"idle"`` -- nothing to do.
    """
    if button_down:
        return ("hold", frame)
    if drag_seen:
        corrected = clamp_frame_to_screens(frame, screens)
        return ("settle", corrected)
    corrected = clamp_frame_to_screens(frame, screens)
    if corrected[:2] != frame[:2]:
        return ("rescue", corrected)
    return ("idle", frame)


class PetWindowTracker:
    """Cursor-driven click-through plus an on-screen safety net, on one bounded main-thread timer.

    Why a poll and not a tracking area: a window that is ignoring mouse events receives no mouse
    events, so it cannot learn that the cursor came back. Reading ``NSEvent.mouseLocation`` needs no
    Accessibility or Screen Recording permission, and it is the standard way this is done.

    The same tick keeps the window on a display, which is what neutralises pywebview's inverted
    top-edge clamp and rescues a pet whose monitor has been unplugged -- but NEVER while the
    operator is dragging (product/desktop-usability-20260917). pywebview's ``mouseDragged_`` and
    this timer both call ``setFrameOrigin_``; letting the timer clamp mid-drag made the two fight,
    and the window visibly jumped away from the cursor. The clamp runs exactly once, on the first
    tick after the button comes up, and the settled position is reported through ``on_drop`` so
    the caller can persist it.
    """

    def __init__(self, ns_window, interval: float = TRACK_INTERVAL_SECONDS, on_drop=None) -> None:
        self._window = ns_window
        self._interval = float(interval)
        self._timer = None
        self._ticks = 0
        self._tick_seconds = 0.0
        self._clamps = 0
        self._running = False
        self._dragging = False
        self._on_drop = on_drop
        # The operator-answer region the pet currently yields to (AppKit coords), or None.
        # Written from the window-api bridge thread, read on the main-thread tick: assignment of
        # an immutable tuple is atomic enough for a presentation-only pointer-routing hint.
        self._yield_rect: Rect | None = None

    def set_yield_rect(self, rect: Rect | None) -> None:
        """Register/clear the operator-answer region. None restores ordinary hit-testing."""
        self._yield_rect = tuple(rect) if rect else None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        import AppKit

        def _start():
            self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                self._interval, True, lambda _timer: self._tick()
            )
            AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
                self._timer, AppKit.NSRunLoopCommonModes
            )
            self._running = True

        on_main_thread(_start)

    def stop(self) -> None:
        """Stop polling. Called whenever the pet is hidden or returned to the app, so a hidden pet
        does no work at all."""
        if not self._running:
            return

        def _stop():
            if self._timer is not None:
                self._timer.invalidate()
            self._timer = None
            self._running = False
            # Leave the window accepting input, so a re-show is never stuck click-through.
            self._window.setIgnoresMouseEvents_(False)

        on_main_thread(_stop)

    # -- the tick ------------------------------------------------------------

    def _tick(self) -> None:
        import AppKit

        started = time.perf_counter()
        try:
            frame_rect = self._window.frame()
            frame = (
                float(frame_rect.origin.x), float(frame_rect.origin.y),
                float(frame_rect.size.width), float(frame_rect.size.height),
            )
            dragging = bool(AppKit.NSEvent.pressedMouseButtons() & 1)
            action, corrected = settle_decision(
                frame, visible_frames(), button_down=dragging, drag_seen=self._dragging
            )
            self._dragging = dragging
            if action == "hold":
                pass  # the drag owns the geometry until the button comes up
            elif action in ("settle", "rescue"):
                if corrected[:2] != frame[:2]:
                    self._window.setFrameOrigin_(AppKit.NSMakePoint(corrected[0], corrected[1]))
                    self._clamps += 1
                if action == "settle" and self._on_drop is not None:
                    # Report the FINAL settled position for persistence. The controller
                    # marshals this off the main thread (see _report_settled).
                    self._on_drop((corrected[0], corrected[1]))
            elif action == "idle":
                # Only flip click-through while the button is up: flipping mid-drag would
                # drop the drag the instant the cursor crossed into a margin.
                point = AppKit.NSEvent.mouseLocation()
                self.apply_for_cursor(float(point.x), float(point.y), frame)
        except Exception:
            # A tracker fault must never take the app down; the pet simply keeps its last state.
            pass
        finally:
            self._ticks += 1
            self._tick_seconds += time.perf_counter() - started

    def apply_for_cursor(self, cursor_x: float, cursor_y: float, frame: Rect | None = None) -> bool:
        """Set the window's click-through state for a cursor at this screen point.

        Split out of the tick so the native proof harness can exercise the REAL policy against the
        REAL window server without warping the operator's pointer around the screen. The only thing
        the harness substitutes is the cursor coordinate; the decision, the window and the OS's
        routing of a click are all the production ones.
        """
        if frame is None:
            rect = self._window.frame()
            frame = (
                float(rect.origin.x), float(rect.origin.y),
                float(rect.size.width), float(rect.size.height),
            )
        inside = point_hits_pet(cursor_x, cursor_y, frame, avoid_rect=self._yield_rect)
        if bool(self._window.ignoresMouseEvents()) == inside:
            self._window.setIgnoresMouseEvents_(not inside)
        return inside

    # -- measurement ---------------------------------------------------------

    def stats(self) -> dict:
        """Observed cost. Reported rather than asserted to be free."""
        return {
            "running": self._running,
            "interval_seconds": self._interval,
            "ticks": self._ticks,
            "total_tick_seconds": round(self._tick_seconds, 6),
            "mean_tick_ms": round((self._tick_seconds / self._ticks) * 1000.0, 4) if self._ticks else 0.0,
            "onscreen_corrections": self._clamps,
        }


def ns_window_for(pywebview_window, timeout: float = 0.0) -> object | None:
    """The real NSWindow behind a pywebview window handle, or None if the backend is not Cocoa.

    ``timeout`` matters. For any window that is not pywebview's master, the Cocoa backend registers
    the BrowserView with ``AppHelper.callAfter`` -- asynchronously, on the main thread -- so
    ``create_window`` returns a handle whose NSWindow does not exist yet. The pet is created from
    the JS-API bridge's worker thread, which is exactly that case: the first live launch of the
    repaired app logged "no_native_window" and fell back to a window with none of the pet's policy
    applied. Wait for the registration instead of sampling it once.
    """
    try:
        from webview.platforms import cocoa
    except Exception:
        return None
    uid = getattr(pywebview_window, "uid", "")
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        instance = cocoa.BrowserView.instances.get(uid)
        window = getattr(instance, "window", None) if instance is not None else None
        if window is not None or time.monotonic() >= deadline:
            return window
        time.sleep(0.02)


class PetWindowController:
    """Owns the detached pet's native behaviour for one pywebview window.

    ``vool_window.py`` is shared with the wallet, startup and authentication lanes, so everything
    the pet needs natively is behind this one object: the integrator merges four call sites there
    instead of a window subsystem.

    Presentation only. It moves, shows, hides and measures a window. It reads no user data, runs no
    task and holds no authority.
    """

    def __init__(self, pywebview_window) -> None:
        self._pywebview_window = pywebview_window
        self._ns_window = None
        self._tracker: PetWindowTracker | None = None
        # Called with (x, y) window points whenever a drag settles. The host uses it to
        # persist the pet's final position at drop time, not only on hide/return.
        self.on_position_settled = None

    # -- resolution ----------------------------------------------------------

    # How long to wait for Cocoa to register the window it was asked to create, on the main thread.
    ADOPT_TIMEOUT_SECONDS = 5.0

    def _window(self, timeout: float = 0.0):
        if self._ns_window is None:
            self._ns_window = ns_window_for(self._pywebview_window, timeout)
        return self._ns_window

    @property
    def available(self) -> bool:
        return self._window() is not None

    # -- lifecycle -----------------------------------------------------------

    def adopt(self, saved_position: object = None) -> dict:
        """Apply the pet's window policy, place it, and start click-through tracking."""
        window = self._window(self.ADOPT_TIMEOUT_SECONDS)
        if window is None:
            return {"ok": False, "error": "no_native_window"}

        def _apply():
            import AppKit

            facts = configure_pet_window(window)
            frames = visible_frames()
            frame = restore_saved_position(saved_position, frames) or default_pet_frame(frames)
            window.setFrame_display_(AppKit.NSMakeRect(*frame), True)
            order_front_without_focus(window)
            facts["frame"] = list(frame)
            facts["restored_saved_position"] = restore_saved_position(saved_position, frames) is not None
            return facts

        facts = on_main_thread(_apply)
        if self._tracker is None:
            self._tracker = PetWindowTracker(window, on_drop=self._report_settled)
        self._tracker.start()
        facts["ok"] = True
        return facts

    def _report_settled(self, origin: tuple[float, float]) -> None:
        """A drag just settled: hand the final position to the host's persistence seam.

        Runs INSIDE the native timer tick (main thread), and the host's persistence reads the
        position and calls ``evaluate_js`` on the chat window -- pywebview blocks that call on a
        semaphore the main run loop must drain. Persisting synchronously here would deadlock the
        loop, so the host callback is marshalled to a short-lived worker thread; the position
        itself is already settled and stable when it runs.
        """
        if self.on_position_settled is None:
            return
        callback, payload = self.on_position_settled, tuple(origin)

        def _persist():
            # Persistence is best-effort; it must never take the native timer down.
            with contextlib.suppress(Exception):
                callback(payload)

        threading.Thread(target=_persist, name="vool-pet-drop-settle", daemon=True).start()

    def set_yield_rect(self, payload: object) -> dict:
        """Register the OPERATOR-ANSWER region the pet yields the pointer over, or clear it.

        The page reports the footer's bounds while a permission bar or mode banner is visible,
        in page screen coordinates (origin at the top-left of the primary display, y growing
        down). AppKit places windows bottom-left with y growing up, so the rect is flipped
        against the primary display's frame -- the one display whose AppKit origin is (0, 0).
        Presentation-only pointer routing: nothing but hit-testing reads it, and the pet stays
        draggable everywhere outside the rect.
        """
        if self._tracker is None:
            return {"ok": False, "error": "pet_not_active"}
        try:
            if payload is None:
                self._tracker.set_yield_rect(None)
                return {"ok": True, "cleared": True}
            data = dict(payload) if isinstance(payload, dict) else {}
            x = float(data.get("x"))
            y = float(data.get("y"))
            width = float(data.get("width"))
            height = float(data.get("height"))
            if not all(math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0:
                return {"ok": False, "error": "invalid_rect"}
            if width > 20000 or height > 20000:
                return {"ok": False, "error": "rect_too_large"}
            primary_height = 0.0
            for frame in visible_frames():
                fx, fy, fw, fh = frame
                if fx <= 0.0 <= fx + fw and fy <= 0.0 <= fy + fh:
                    primary_height = fh
                    break
            if primary_height <= 0.0:
                return {"ok": False, "error": "no_primary_display"}
            appkit_y = primary_height - (y + height)
            self._tracker.set_yield_rect((x, appkit_y, width, height))
            return {"ok": True, "rect": [x, appkit_y, width, height]}
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_rect"}

    def show(self) -> None:
        window = self._window()
        if window is None:
            return
        on_main_thread(lambda: order_front_without_focus(window))
        if self._tracker is not None:
            self._tracker.start()

    def hide(self) -> None:
        """Order the pet out and stop all polling. A hidden pet must cost nothing."""
        if self._tracker is not None:
            self._tracker.stop()
        window = self._window()
        if window is not None:
            on_main_thread(lambda: window.orderOut_(None))

    # -- position ------------------------------------------------------------

    def position(self) -> dict | None:
        window = self._window()
        if window is None:
            return None

        def _read():
            frame = window.frame()
            return {"x": float(frame.origin.x), "y": float(frame.origin.y)}

        try:
            return on_main_thread(_read)
        except Exception:
            return None

    def reset_position(self) -> dict | None:
        """Put the pet back at the default spot on the primary display."""
        window = self._window()
        if window is None:
            return None

        def _reset():
            import AppKit

            frame = default_pet_frame(visible_frames())
            window.setFrame_display_(AppKit.NSMakeRect(*frame), True)
            order_front_without_focus(window)
            return {"x": frame[0], "y": frame[1]}

        return on_main_thread(_reset)

    def apply_for_cursor(self, cursor_x: float, cursor_y: float) -> bool:
        """Click-through state for a cursor at this screen point. See PetWindowTracker."""
        if self._tracker is None:
            return False
        return on_main_thread(lambda: self._tracker.apply_for_cursor(cursor_x, cursor_y))

    def stats(self) -> dict:
        return self._tracker.stats() if self._tracker is not None else {"running": False, "ticks": 0}
