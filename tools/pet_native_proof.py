"""Native proof harness for the desktop pet: real NSWindows, real pixels, real hit tests.

This instrument exists because every cheap way of "proving" a transparent desktop pet is a lie:
CSS saying ``background:transparent`` proves nothing about the WKWebView backing, a fake bridge
returning ``{"ok": True}`` proves nothing about a window existing, and moving a DOM element proves
nothing about moving an OS window. So this harness only ever asserts on things the window server
itself decides:

* **Transparency** is measured from a screen capture of a *known* backdrop this harness owns. A
  margin pixel that still shows the backdrop colour is transparent; anything else is not.
* **Hit behaviour** is measured with ``+[NSWindow windowNumberAtPoint:belowWindowWithWindowNumber:]``,
  which is the OS answering "which window would get a click here". No synthetic events are posted,
  so no Accessibility permission is requested.
* **Window position** is read back from ``-[NSWindow frame]`` after a move, in screen points.
* **Window level** is read back from ``-[NSWindow level]`` and compared against the AppKit
  constants, so "does not sit above modal/security panels" is a number, not a promise.

Capture safety: the only region ever captured is the rectangle of a backdrop window this harness
created and raised itself, so no unrelated window is photographed.

Usage (from the worktree, repo on PYTHONPATH, the repo interpreter):

    python tools/pet_native_proof.py --out <dir> [--screen all|0|1] [--label baseline]

    python tools/pet_native_proof.py --backdrop-server --rect x,y,w,h --title T   # internal

The second form is spawned by the first: it is a genuinely separate process holding a plain AppKit
window, so "the pet is transparent over another application's window" is proven across a process
boundary and not merely against another window of the same app.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The pet window's own geometry. Kept here so the harness measures the same box the runtime opens.
PET_W = 176
PET_H = 176
BACKDROP_W = 560
BACKDROP_H = 440

BACKDROPS = {
    "light": "#f2f4f8",
    "dark": "#0b0d12",
}
BACKDROP_RGB = {
    "light": (242, 244, 248),
    "dark": (11, 13, 18),
}
# The colour of the procedural renderer's ground bar (``vcwR(g,0,42,48,6,'#20232b')``). A run of
# this colour spanning the canvas width is the rectangular slab the operator reported.
GROUND_BAR_RGB = (32, 35, 43)
# The caption card's fill (``#16191f``), drawn at ~91% alpha over whatever is behind it.
CAPTION_CARD_RGB = (22, 25, 31)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _near(a: tuple[int, int, int], b: tuple[int, int, int], tol: int = 10) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


# --------------------------------------------------------------------------- backdrop subprocess


def _run_backdrop_server(rect: str, title: str) -> int:
    """Hold one opaque AppKit window at ``rect`` until killed. Runs as its own process."""
    import AppKit
    from PyObjCTools import AppHelper

    x, y, w, h = (float(v) for v in rect.split(","))
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(x, y, w, h),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    window.setBackgroundColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.42, 0.78, 1.0))
    window.setLevel_(2)  # above ordinary windows, below the pet's floating level (3)
    window.setTitle_(title)

    view = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(18, h - 130, w - 36, 100))
    view.setStringValue_("ANOTHER APPLICATION\nvisible content behind the pet\n" + title)
    view.setBezeled_(False)
    view.setDrawsBackground_(False)
    view.setEditable_(False)
    view.setTextColor_(AppKit.NSColor.whiteColor())
    view.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(15, AppKit.NSFontWeightBold))
    window.contentView().addSubview_(view)
    window.orderFrontRegardless()
    print(json.dumps({"backdrop_window_number": int(window.windowNumber()), "pid": os.getpid()}), flush=True)
    AppHelper.runEventLoop()
    return 0


# --------------------------------------------------------------------------- capture + geometry


def _primary_height() -> float:
    import AppKit

    for screen in AppKit.NSScreen.screens():
        frame = screen.frame()
        if frame.origin.x == 0 and frame.origin.y == 0:
            return float(frame.size.height)
    return float(AppKit.NSScreen.screens()[0].frame().size.height)


def _appkit_rect_to_quartz(x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    """AppKit (bottom-left origin, y up) -> screencapture -R (top-left origin, y down)."""
    return int(round(x)), int(round(_primary_height() - (y + h))), int(round(w)), int(round(h))


def _capture(rect_appkit: tuple[float, float, float, float], path: Path) -> bool:
    x, y, w, h = _appkit_rect_to_quartz(*rect_appkit)
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["/usr/sbin/screencapture", "-x", "-o", f"-R{x},{y},{w},{h}", str(path)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and path.exists()


def _backdrop_owns_capture(
    png: Path,
    backdrop_rect: tuple[float, float, float, float],
    pet_rect: tuple[float, float, float, float],
    tolerance: int = 12,
) -> tuple[bool, list]:
    """Is every border probe of this capture the backdrop, i.e. did the backdrop really cover it?

    Capture safety gate. ``screencapture`` photographs whatever the window server shows, so if the
    backdrop sank behind another application the PNG would hold the operator's private windows.
    This samples points along the capture border -- all far outside the pet's own rect -- and the
    caller DELETES the file when they disagree. Fail closed: an unverifiable capture is destroyed,
    never analysed and never kept as evidence.
    """
    from PIL import Image

    image = Image.open(png).convert("RGB")
    bx, by, bw, bh = backdrop_rect
    scale = image.width / float(bw)
    inset = max(2, int(round(5 * scale)))
    probes = []
    for fx in (0.02, 0.25, 0.5, 0.75, 0.98):
        probes.append((min(image.width - 1, max(0, int(fx * image.width))), inset))
        probes.append((min(image.width - 1, max(0, int(fx * image.width))), image.height - 1 - inset))
    for fy in (0.25, 0.5, 0.75):
        probes.append((inset, int(fy * image.height)))
        probes.append((image.width - 1 - inset, int(fy * image.height)))
    samples = [image.getpixel(pt) for pt in probes]
    reference = samples[0]
    agree = all(_near(s, reference, tolerance) for s in samples)
    return agree, [list(s) for s in samples[:6]]


def _analyse(
    png: Path,
    backdrop_rect: tuple[float, float, float, float],
    pet_rect: tuple[float, float, float, float],
    backdrop_rgb: tuple[int, int, int],
) -> dict:
    """Measure, in the captured pixels, what actually reached the screen inside the pet's rect."""
    from PIL import Image

    image = Image.open(png).convert("RGB")
    bx, by, bw, bh = backdrop_rect
    scale = image.width / float(bw)
    px, py, pw, ph = pet_rect
    # The backdrop colour is READ from a point that is inside the capture but outside the pet's
    # window, so colour-space conversion between what was asked for and what the display actually
    # emitted can never be mistaken for the pet painting an opaque rectangle.
    probe = image.getpixel((max(2, int(6 * scale)), image.height // 2))
    measured_backdrop = (int(probe[0]), int(probe[1]), int(probe[2]))
    requested_backdrop = tuple(backdrop_rgb)
    backdrop_rgb = measured_backdrop

    # AppKit y grows up; image y grows down.
    left = int(round((px - bx) * scale))
    top = int(round(((by + bh) - (py + ph)) * scale))
    width = int(round(pw * scale))
    height = int(round(ph * scale))
    crop = image.crop((left, top, left + width, top + height))
    pixels = crop.load()

    total = width * height
    backdrop_hits = 0
    ground_hits = 0
    caption_hits = 0
    for yy in range(height):
        for xx in range(width):
            rgb = pixels[xx, yy]
            if _near(rgb, backdrop_rgb, 8):
                backdrop_hits += 1
            if _near(rgb, GROUND_BAR_RGB, 6):
                ground_hits += 1
            if _near(rgb, CAPTION_CARD_RGB, 6):
                caption_hits += 1

    # A rectangular slab is a horizontal run of one non-backdrop colour spanning most of the box.
    widest_ground_run = 0
    widest_caption_run = 0
    for yy in range(height):
        run_g = run_c = 0
        for xx in range(width):
            rgb = pixels[xx, yy]
            run_g = run_g + 1 if _near(rgb, GROUND_BAR_RGB, 6) else 0
            run_c = run_c + 1 if _near(rgb, CAPTION_CARD_RGB, 6) else 0
            widest_ground_run = max(widest_ground_run, run_g)
            widest_caption_run = max(widest_caption_run, run_c)

    # Corner probes: 6 points into each corner of the pet window. These are pure margin.
    inset = max(3, int(round(6 * scale)))
    corners = {
        "top_left": pixels[inset, inset],
        "top_right": pixels[width - 1 - inset, inset],
        "bottom_left": pixels[inset, height - 1 - inset],
        "bottom_right": pixels[width - 1 - inset, height - 1 - inset],
    }
    return {
        "capture": str(png),
        "backdrop_rgb_measured": list(measured_backdrop),
        "backdrop_rgb_requested": list(requested_backdrop),
        "capture_size_px": [image.width, image.height],
        "backing_scale": round(scale, 3),
        "pet_box_px": [left, top, width, height],
        "backdrop_visible_fraction": round(backdrop_hits / float(total), 4),
        "ground_bar_pixel_fraction": round(ground_hits / float(total), 4),
        "caption_card_pixel_fraction": round(caption_hits / float(total), 4),
        "widest_ground_bar_run_px": widest_ground_run,
        "widest_caption_card_run_px": widest_caption_run,
        "pet_box_width_px": width,
        "corner_pixels": {k: list(v) for k, v in corners.items()},
        "corners_show_backdrop": {k: _near(v, backdrop_rgb, 8) for k, v in corners.items()},
    }


# --------------------------------------------------------------------------- native window access


def _ns_window(pywebview_window) -> object | None:
    """The real NSWindow behind a pywebview window handle."""
    from webview.platforms import cocoa

    instance = cocoa.BrowserView.instances.get(pywebview_window.uid)
    return getattr(instance, "window", None) if instance is not None else None


def _window_facts(ns_window) -> dict:
    import AppKit

    level = int(ns_window.level())
    frame = ns_window.frame()
    background = ns_window.backgroundColor()
    try:
        alpha = float(background.alphaComponent())
    except Exception:
        alpha = -1.0
    return {
        "level": level,
        "level_name": {
            int(AppKit.NSNormalWindowLevel): "NSNormalWindowLevel",
            int(AppKit.NSFloatingWindowLevel): "NSFloatingWindowLevel",
            int(AppKit.NSModalPanelWindowLevel): "NSModalPanelWindowLevel",
            int(AppKit.NSStatusWindowLevel): "NSStatusWindowLevel",
            int(AppKit.NSPopUpMenuWindowLevel): "NSPopUpMenuWindowLevel",
            int(AppKit.NSScreenSaverWindowLevel): "NSScreenSaverWindowLevel",
        }.get(level, f"level_{level}"),
        "above_modal_panel": level > int(AppKit.NSModalPanelWindowLevel),
        "is_opaque": bool(ns_window.isOpaque()),
        "has_shadow": bool(ns_window.hasShadow()),
        "background_alpha": round(alpha, 4),
        "ignores_mouse_events": bool(ns_window.ignoresMouseEvents()),
        "window_number": int(ns_window.windowNumber()),
        "frame": [float(frame.origin.x), float(frame.origin.y), float(frame.size.width), float(frame.size.height)],
    }


def _hit_test(point_x: float, point_y: float) -> int:
    """Which window number would receive a click at this screen point? The OS answers."""
    import AppKit

    return int(AppKit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_(
        AppKit.NSMakePoint(point_x, point_y), 0
    ))


def _screens() -> list[dict]:
    import AppKit

    out = []
    for index, screen in enumerate(AppKit.NSScreen.screens()):
        frame = screen.frame()
        visible = screen.visibleFrame()
        out.append({
            "index": index,
            "frame": [float(frame.origin.x), float(frame.origin.y), float(frame.size.width), float(frame.size.height)],
            "visible_frame": [float(visible.origin.x), float(visible.origin.y), float(visible.size.width), float(visible.size.height)],
            "backing_scale": float(screen.backingScaleFactor()),
        })
    return out


# --------------------------------------------------------------------------- the drive


def on_main(fn, timeout: float = 10.0):
    """Run ``fn`` on the Cocoa main thread and return its result.

    macOS aborts the process (SIGTRAP) when NSWindow geometry is mutated from a background thread,
    so every AppKit call in this harness -- and in the runtime fix it proves -- is marshalled here.
    Discovered the hard way: the first harness run died at ``setFrame:display:`` with no traceback.
    """
    from PyObjCTools import AppHelper

    box: dict = {}
    done = threading.Event()

    def _run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            box["error"] = exc
        finally:
            done.set()

    AppHelper.callAfter(_run)
    if not done.wait(timeout):
        raise TimeoutError("main-thread call did not complete")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _step(name: str) -> None:
    """Progress marker. A native trap kills the process before ``finally`` can write the report, so
    the last line printed is the only way to know where the harness died."""
    print(f"[pet-proof] {name}", flush=True)


def _drive(state: dict) -> None:
    """Runs on a worker thread once the Cocoa GUI is up."""
    import AppKit
    import webview

    from installer.bundle import pet_native

    _step("drive:start")

    report = state["report"]
    out_dir = state["out_dir"]
    try:
        time.sleep(1.6)  # first paint of both WKWebViews
        report["screens"] = on_main(_screens)
        _step("screens")

        pet_window = state["pet_window"]
        ns_pet = _ns_window(pet_window)
        if ns_pet is None:
            report["fatal"] = "pet NSWindow not reachable"
            return
        report["pet_window_facts_before_adopt"] = on_main(lambda: _window_facts(ns_pet))
        controller = pet_native.PetWindowController(pet_window)
        report["adopted"] = controller.adopt(None)
        report["pet_window_facts_at_creation"] = on_main(lambda: _window_facts(ns_pet))
        state["controller"] = controller
        _step("window_facts")

        for screen_info in report["screens"]:
            if state["screen_filter"] not in ("all", str(screen_info["index"])):
                continue
            sx, sy, sw, sh = screen_info["visible_frame"]
            bx = sx + (sw - BACKDROP_W) / 2.0
            by = sy + (sh - BACKDROP_H) / 2.0
            backdrop_rect = (bx, by, float(BACKDROP_W), float(BACKDROP_H))
            pet_rect = (
                bx + (BACKDROP_W - PET_W) / 2.0,
                by + (BACKDROP_H - PET_H) / 2.0,
                float(PET_W),
                float(PET_H),
            )
            scenarios = []

            for name, css_colour in BACKDROPS.items():
                backdrop = state["backdrop_window"]
                ns_backdrop = _ns_window(backdrop)
                backdrop.evaluate_js(f"document.body.style.background={css_colour!r}")
                def _place():
                    # One level under the pet, but above every ordinary application window, so the
                    # captured region is owned by this harness and not by the operator's desktop.
                    ns_backdrop.setLevel_(pet_native.PET_WINDOW_LEVEL - 1)
                    ns_backdrop.setFrame_display_(AppKit.NSMakeRect(*backdrop_rect), True)
                    ns_backdrop.orderFrontRegardless()
                    ns_pet.setFrame_display_(AppKit.NSMakeRect(*pet_rect), True)
                    ns_pet.orderFrontRegardless()

                on_main(_place)
                time.sleep(1.3)
                _step(f"positioned:{name}")

                png = out_dir / f"screen{screen_info['index']}_{name}.png"
                captured = _capture(backdrop_rect, png)
                owned, probe_samples = (False, [])
                if captured:
                    owned, probe_samples = _backdrop_owns_capture(png, backdrop_rect, pet_rect)
                    if not owned:
                        png.unlink(missing_ok=True)
                        captured = False
                _step(f"captured:{name}:{captured}")
                entry = {
                    "backdrop": name,
                    "backdrop_kind": "same-process window",
                    "backdrop_rect_appkit": list(backdrop_rect),
                    "pet_rect_appkit": list(pet_rect),
                    "captured": captured,
                    "backdrop_owns_capture": owned,
                    "border_probe_samples": probe_samples,
                }
                if captured:
                    entry.update(_analyse(png, backdrop_rect, pet_rect, BACKDROP_RGB[name]))
                else:
                    entry["discarded"] = "capture border was not uniformly the backdrop; file deleted unread"
                scenarios.append(entry)

            # --- the same measurement over a genuinely separate process's window
            other_app_error = ""
            proc = subprocess.Popen(
                [
                    sys.executable, str(Path(__file__).resolve()), "--backdrop-server",
                    # "=" form: a rect on a left-hand monitor starts with "-" and argparse would
                    # otherwise read it as an option flag.
                    "--rect=" + ",".join(str(v) for v in backdrop_rect),
                    f"--title=screen{screen_info['index']}",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            other_app_number = -1
            _step("spawn_backdrop_app")
            try:
                line = proc.stdout.readline()
                if not line.strip():
                    raise RuntimeError(
                        "backdrop app produced no handshake; stderr="
                        + (proc.stderr.read(2000) if proc.stderr else "")
                    )
                other_app_number = int(json.loads(line)["backdrop_window_number"])
                time.sleep(0.8)
                def _repin_pet():
                    # Re-place AND re-raise the pet for this scenario. Relying on it still sitting
                    # where an earlier scenario left it produced a run where the pet was simply not
                    # over the capture at all, which reads as a perfect pass on every pixel metric.
                    _ns_window(state["backdrop_window"]).orderOut_(None)
                    ns_pet.setFrame_display_(AppKit.NSMakeRect(*pet_rect), True)
                    ns_pet.orderFrontRegardless()
                    frame = ns_pet.frame()
                    return {
                        "visible": bool(ns_pet.isVisible()),
                        "level": int(ns_pet.level()),
                        "frame": [float(frame.origin.x), float(frame.origin.y),
                                  float(frame.size.width), float(frame.size.height)],
                    }

                pet_presence = on_main(_repin_pet)
                time.sleep(1.1)
                png = out_dir / f"screen{screen_info['index']}_otherapp.png"
                captured = _capture(backdrop_rect, png)
                owned, probe_samples = (False, [])
                if captured:
                    owned, probe_samples = _backdrop_owns_capture(png, backdrop_rect, pet_rect)
                    if not owned:
                        png.unlink(missing_ok=True)
                        captured = False
                entry = {
                    "backdrop": "other_app",
                    "backdrop_kind": f"separate process pid={proc.pid}",
                    "backdrop_rect_appkit": list(backdrop_rect),
                    "pet_rect_appkit": list(pet_rect),
                    "captured": captured,
                    "backdrop_owns_capture": owned,
                    "border_probe_samples": probe_samples,
                    "pet_presence_at_capture": pet_presence,
                }
                if captured:
                    entry.update(_analyse(png, backdrop_rect, pet_rect, (46, 107, 199)))
                else:
                    entry["discarded"] = "capture border was not uniformly the backdrop; file deleted unread"

                # --- hit test: margin must reach the OTHER APP, pet body must reach the pet
                margin_point = (pet_rect[0] + 6.0, pet_rect[1] + pet_rect[3] - 6.0)  # top-left margin
                body_point = (pet_rect[0] + PET_W / 2.0, pet_rect[1] + PET_H / 2.0 + 14.0)
                _step("hit_test")
                controller = state["controller"]
                # Pause the live-cursor tick first. It re-decides every 60ms from where the
                # operator's pointer actually is, and would overwrite the state under test between
                # setting it and asking the OS -- which is exactly how the first repaired run
                # produced a "body does not reach the pet" that was measuring the wrong state.
                controller._tracker.stop()
                # Ask the production policy what it does for a cursor in the margin, then let the
                # OS say which window a click there would reach. Same for a cursor on the character.
                margin_inside = controller.apply_for_cursor(*margin_point)
                time.sleep(0.15)
                margin_hit = on_main(lambda: _hit_test(*margin_point))
                body_inside = controller.apply_for_cursor(*body_point)
                time.sleep(0.15)
                body_hit = on_main(lambda: _hit_test(*body_point))
                entry["hit_test"] = {
                    "pet_window_number": on_main(lambda: int(ns_pet.windowNumber())),
                    "other_app_window_number": other_app_number,
                    "margin_point": list(margin_point),
                    "policy_says_margin_is_pet": margin_inside,
                    "margin_hit_window": margin_hit,
                    "body_point": list(body_point),
                    "policy_says_body_is_pet": body_inside,
                    "body_hit_window": body_hit,
                }
                entry["hit_test"]["margin_reaches_other_app"] = (
                    entry["hit_test"]["margin_hit_window"] == other_app_number
                )
                controller._tracker.start()
                entry["hit_test"]["note"] = (
                    "the live-cursor tick was paused for the measurement so it could not overwrite "
                    "the state under test; the policy, the window and the OS routing are production"
                )
                entry["hit_test"]["body_reaches_pet"] = (
                    entry["hit_test"]["body_hit_window"] == entry["hit_test"]["pet_window_number"]
                )
                scenarios.append(entry)
            except Exception as exc:
                other_app_error = f"{type(exc).__name__}: {exc}"
                scenarios.append({"backdrop": "other_app", "error": other_app_error})
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except Exception:
                    proc.kill()
                on_main(lambda: _ns_window(state["backdrop_window"]).orderFrontRegardless())

            report.setdefault("per_screen", []).append(
                {"screen": screen_info, "scenarios": scenarios}
            )

        # --- the REAL production path: the JS-API bridge's worker thread calls detach_companion,
        # which calls create_window and then adopts the NSWindow. Cocoa registers that window
        # asynchronously, so this is where the live app logged "no_native_window" and fell back to a
        # window carrying none of the pet's policy. Reproduced here without booting the daemon.
        _step("bridge_detach")
        try:
            from installer.bundle.vool_window import _WindowApi

            api = _WindowApi()
            api.set_window(state["backdrop_window"], webview)
            detach = api.detach_companion({"state": "tool", "caption": "BRIDGE DETACH"})
            controller = api._pet
            facts = None
            if controller is not None and controller.available:
                bridge_ns = controller._window()
                facts = on_main(lambda: _window_facts(bridge_ns))
            report["bridge_detach"] = {
                "detach_result": detach,
                "controller_available": bool(controller is not None and controller.available),
                "window_facts": facts,
                "level_is_floating": bool(facts and facts["level"] == pet_native.PET_WINDOW_LEVEL),
                "not_above_modal": bool(facts and not facts["above_modal_panel"]),
                "overhead": api.companion_overhead(),
            }
            api.hide_companion()
        except Exception as exc:
            report["bridge_detach"] = {"error": f"{type(exc).__name__}: {exc}"}

        # --- physical cross-monitor movement, read back from the OS
        moves = []
        _step("native_moves")
        for screen_info in report["screens"]:
            sx, sy, sw, sh = screen_info["visible_frame"]
            for label, target in (
                ("top_left", (sx + 8.0, sy + sh - PET_H - 8.0)),
                ("bottom_left", (sx + 8.0, sy + 8.0)),
                ("centre", (sx + (sw - PET_W) / 2.0, sy + (sh - PET_H) / 2.0)),
                ("top_right", (sx + sw - PET_W - 8.0, sy + sh - PET_H - 8.0)),
            ):
                on_main(lambda t=target: ns_pet.setFrameOrigin_(AppKit.NSMakePoint(*t)))
                time.sleep(0.18)
                frame = on_main(ns_pet.frame)
                landed = (float(frame.origin.x), float(frame.origin.y))
                moves.append({
                    "screen_index": screen_info["index"],
                    "screen_backing_scale": screen_info["backing_scale"],
                    "corner": label,
                    "requested": list(target),
                    "native_frame_after": landed,
                    "exact": abs(landed[0] - target[0]) < 1.0 and abs(landed[1] - target[1]) < 1.0,
                })
        report["native_moves"] = moves
        report["physical_monitor_count"] = len(report["screens"])
        report["cross_monitor"] = (
            "PHYSICAL — moved and read back on %d attached displays" % len(report["screens"])
            if len(report["screens"]) > 1
            else "NOT TESTED — only one display attached"
        )
        report["pet_window_facts_final"] = on_main(lambda: _window_facts(ns_pet))
        # Checkpoint B asks for a bounded observation of the pet's cost, not a claim that it is
        # free. These are the real counters from the tracker that ran for this whole drive.
        report["tracker_overhead"] = state["controller"].stats()
    except Exception as exc:  # a harness crash must be visible, never a silent green
        report["fatal"] = f"{type(exc).__name__}: {exc}"
        import traceback

        report["traceback"] = traceback.format_exc()
    finally:
        (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        time.sleep(0.2)
        import webview as _wv

        for window in list(_wv.windows):
            try:
                window.destroy()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    parser.add_argument("--label", default="run")
    parser.add_argument("--screen", default="all", help="all | 0 | 1 ...")
    parser.add_argument("--backdrop-server", action="store_true")
    parser.add_argument("--rect", default="")
    parser.add_argument("--title", default="")
    args = parser.parse_args()

    if args.backdrop_server:
        return _run_backdrop_server(args.rect, args.title)

    import webview

    from core.companion_world_fragment import render_desktop_companion_html

    out_dir = Path(args.out or (REPO_ROOT / "evidence" / "pet" / args.label)).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    pet_html = render_desktop_companion_html({"state": "idle", "caption": "IDLE"})
    (out_dir / "pet_document.html").write_text(pet_html, encoding="utf-8")

    report: dict = {
        "label": args.label,
        "started": _now(),
        "python": sys.version.split()[0],
        "pet_document_sha256": __import__("hashlib").sha256(pet_html.encode()).hexdigest(),
    }

    backdrop_window = webview.create_window(
        "pet-proof backdrop",
        html="<style>html,body{margin:0;height:100%;background:#f2f4f8;font:14px -apple-system}"
             "</style><div style='padding:14px'>backdrop</div>",
        width=BACKDROP_W,
        height=BACKDROP_H,
        frameless=True,
        easy_drag=False,
        on_top=False,
    )
    # The pet window is created with the SAME flags the runtime uses, read from the runtime module
    # so this harness cannot drift from production.
    from installer.bundle import pet_native
    from installer.bundle.vool_window import COMPANION_WINDOW_FLAGS

    report["companion_window_flags"] = {k: v for k, v in COMPANION_WINDOW_FLAGS.items()}
    try:
        pet_window = webview.create_window("VOOL Companion", html=pet_html, **COMPANION_WINDOW_FLAGS)
        report["production_flags_accepted"] = True
    except Exception as exc:
        # The production flags are rejected by the real dependency. That IS the headline defect, so
        # it is recorded verbatim; the visual measurement then continues under a MINIMAL correction
        # (background_color only) so the document's own rectangles can still be photographed. The
        # report says plainly that the pixels came from a corrected window, never from production.
        report["production_flags_accepted"] = False
        report["production_flags_error"] = f"{type(exc).__name__}: {exc}"
        fallback = dict(COMPANION_WINDOW_FLAGS)
        fallback["background_color"] = "#000000"
        report["visual_baseline_correction"] = {"background_color": "#00000000 -> #000000"}
        pet_window = webview.create_window("VOOL Companion", html=pet_html, **fallback)

    state = {
        "report": report,
        "out_dir": out_dir,
        "pet_window": pet_window,
        "backdrop_window": backdrop_window,
        "screen_filter": args.screen,
    }
    webview.start(_drive, (state,), gui="cocoa")
    report["finished"] = _now()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_screen"}, indent=2)[:2000])
    return 0 if not report.get("fatal") else 1


if __name__ == "__main__":
    raise SystemExit(main())
