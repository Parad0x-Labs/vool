"""Dragging the in-app pet hard against the window edge hands it to its desktop window.

Live report (v0.7.0-beta build, 2026-10-13): the pet could not be dragged out of the VOOL window.
The pointer pipeline clamps the sprite VN_EDGE_INSET (24px) inside every edge, and the hand-off
was judged on that clamped position, which can never come within 2px of an edge -- so the
edge hand-off never fired. These drive the REAL pointer pipeline (pointerdown -> pointermove ->
pointerup through the page's captured listeners) with a native bridge present and absent.
"""

from __future__ import annotations

import json

from tests.test_companion_one_drag_finality import run_driver

# The harness window is 1200x800 (tests/chat_page_js_harness.py); VN_SIZE is 168.
DRIVER_TEMPLATE = r"""
const res = { errors: [], calls: [] };
try {
  const VC = window.VoolCompanion;
  if (__NATIVE__) {
    window.pywebview = { api: {
      detach_companion: function (payload) { res.calls.push(payload || null); return new Promise(function () {}); },
      sync_companion: function () { return Promise.resolve({ ok: true }); },
    } };
  }
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");
  const down = sprite.__on && sprite.__on.pointerdown;
  if (!down) throw new Error("sprite has no pointerdown handler");
  const startX = parseFloat(sprite.style.left) || 0, startY = parseFloat(sprite.style.top) || 0;
  const target = __TARGET__;
  const grab = 56;
  down({ pointerId: 1, clientX: startX + grab, clientY: startY + grab });
  const move = __winH.pointermove[__winH.pointermove.length - 1];
  const relX = target[0] + grab, relY = target[1] + grab;
  move({ pointerId: 1, clientX: relX, clientY: relY });
  const up = __winH.pointerup[__winH.pointerup.length - 1];
  up({ pointerId: 1, clientX: relX, clientY: relY });
  res.mode = VC.pos().mode;
  res.pos = [VC.pos().x, VC.pos().y];
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def drag_to(target: tuple[float, float], *, native: bool) -> dict:
    driver = DRIVER_TEMPLATE.replace("__NATIVE__", json.dumps(native)).replace("__TARGET__", json.dumps(list(target)))
    out = run_driver(driver)
    assert not out["errors"], out["errors"]
    return out


def test_drag_past_the_right_edge_hands_the_pet_to_the_desktop() -> None:
    # The sprite's left edge released at 1150: its 168px box runs past the 1200px window edge.
    out = drag_to((1150, 300), native=True)
    assert len(out["calls"]) == 1, f"edge release must open the desktop pet once: {out}"


def test_drag_past_the_top_left_corner_hands_the_pet_to_the_desktop() -> None:
    out = drag_to((-60, -40), native=True)
    assert len(out["calls"]) == 1, f"edge release must open the desktop pet once: {out}"


def test_drop_inside_the_window_stays_in_the_app() -> None:
    out = drag_to((500, 400), native=True)
    assert out["calls"] == [], "an ordinary drop must not leave the app"
    assert out["mode"] == "free" and out["pos"] == [500, 400]


def test_drop_inside_the_inset_but_short_of_the_edge_stays_in_the_app() -> None:
    # Released 10px from the left edge: clamped to the 24px inset, not a hand-off.
    out = drag_to((10, 300), native=True)
    assert out["calls"] == [], "a drop short of the edge must not leave the app"
    assert out["mode"] == "free" and out["pos"] == [24, 300]


def test_edge_release_without_the_native_window_stays_clamped_inside() -> None:
    # A browser tab has no desktop window: the pet stays in the page, clamped to the inset.
    out = drag_to((1150, 300), native=False)
    assert out["calls"] == []
    assert out["mode"] == "free" and out["pos"] == [1200 - 168 - 24, 300]
