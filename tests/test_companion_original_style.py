"""Acceptance tests for the original-style companion work (pet-original-style-20260930).

These cover the conditions the mission states as hard acceptance, and they are written against
the real shipped renderer rather than a description of it:

* the four approved originals are preserved, proved by a colour-aware per-cell comparison
  against the protected prototypes' OWN drawing code run headlessly, excluding only the
  owner-requested removal of external cast shadows;
* ten distinct companions, none an alias or recolour of another;
* the display enlargement is an integer multiple of the 48px art grid and does not touch art;
* every authoritative state and every activity substate has an authored pose that actually
  changes the drawing;
* the status bubble is above the pet, inert, bounded and safely escaped;
* activity identity comes from the typed category, never from a clock or free text.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

CANDIDATE = pathlib.Path(__file__).resolve().parents[1]
TOOLS = CANDIDATE / "tests" / "fixtures" / "companion"
REFERENCE_FRAMES = TOOLS / "approved-originals.json.gz"

sys.path.insert(0, str(CANDIDATE))

from core.companion_art_fragment import (
    COMPANION_ART_JS,
    COMPANION_ORIGINALS,
    COMPANION_ROSTER,
)
from core.companion_presentation_fragment import render_companion_fragment
from core.companion_world_fragment import (
    COMPANION_WORLD_JS,
    normalise_companion_payload,
    render_desktop_companion_html,
)

ORIGINALS = ["beetle", "raven", "golem", "tide"]
SAVED_IDS = ["spark", "rascal", "prime", "prism", "veil", "ember"]
STATES = [
    "idle", "starting", "thinking", "tool", "waiting",
    "approval", "retry", "success", "failure", "cancelled", "unknown",
]
ACTIVITIES = ["read", "search", "dig", "code", "exec", "test", "watch"]
NATIVE = shutil_which = __import__("shutil").which("node")

requires_node = pytest.mark.skipif(not NATIVE, reason="node is required to drive the renderer")


def _write_js(tmp_path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _render(tmp_path, states=None, activity=None, elapsed=1400, chars=None):
    """Render pets headlessly through the REAL shared renderer and return colour grids."""
    js = _write_js(tmp_path, "world.js", COMPANION_WORLD_JS)
    states = states or ["idle"]
    chars = chars or list(COMPANION_ROSTER)
    out = str(tmp_path / "frames.json")
    result = subprocess.run(
        [NATIVE, str(TOOLS / "render_pets.js"), js, ",".join(states), out,
         str(elapsed), ",".join(chars), activity or ""],
        capture_output=True, text=True, check=True,
    )
    return json.loads(pathlib.Path(out).read_text()), result.stdout


# --------------------------------------------------------------------------- roster

def test_roster_is_ten_with_four_stable_originals():
    assert list(COMPANION_ROSTER) == ORIGINALS + SAVED_IDS
    assert list(COMPANION_ORIGINALS) == ORIGINALS
    for saved in SAVED_IDS:
        assert saved in COMPANION_ROSTER, f"saved id {saved} must survive the change"


@requires_node
def test_ten_pets_each_draw_a_distinct_picture(tmp_path):
    grids, _ = _render(tmp_path, states=["idle"])
    seen = {}
    for pet in COMPANION_ROSTER:
        g = grids[f"{pet}/idle"]
        sig = tuple(tuple(row) for row in g)
        assert sig not in seen, f"{pet} is pixel-identical to {seen.get(sig)}"
        seen[sig] = pet
    assert len(seen) == 10


@requires_node
def test_no_pet_fills_the_grid_and_silhouettes_differ(tmp_path):
    """Enlargement must not become canvas padding, and the family must stay varied."""
    grids, _ = _render(tmp_path, states=["idle"])
    boxes = {}
    for pet in COMPANION_ROSTER:
        pts = [(x, y) for y in range(48) for x in range(48) if grids[f"{pet}/idle"][y][x]]
        assert pts, f"{pet} drew nothing"
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        fill = len(pts) / (48 * 48)
        assert fill < 0.80, f"{pet} fills {fill:.0%} of the grid -- that is padding, not art"
        boxes[pet] = (w, h)
    widths = [w for w, _ in boxes.values()]
    heights = [h for _, h in boxes.values()]
    # The family is not uniform: something is wide and low, something is tall and narrow.
    assert max(widths) - min(widths) >= 8
    assert max(heights) - min(heights) >= 4


# ------------------------------------------------- preservation of the approved art

@requires_node
def test_original_creature_pixels_match_the_approved_prototype(tmp_path):
    """Keep every creature pixel; omit only the prototype's external two-row cast shadow."""
    import gzip

    ref = json.loads(gzip.decompress(REFERENCE_FRAMES.read_bytes()))

    # Frozen prototype colors and rows, independent of the candidate palette. Do not mask
    # whole bottom rows: feet and action props can occupy them and must remain pixel-identical.
    shadow_colors = {"beetle": "rgba(23,38,43,.18)", "raven": "rgba(17,29,38,.2)",
                     "golem": "rgba(32,43,43,.2)", "tide": "rgba(18,44,67,.2)"}

    # The product's five prototype states, each sampled at the same documented millisecond.
    # Several product states share the prototype's single 'working' state, so the reference is
    # keyed by (prototype state, instant) -- comparing on state alone would compare the wrong
    # frame and quietly prove nothing.
    times = {"idle": 0, "starting": 600, "thinking": 1400, "tool": 600,
             "approval": 1000, "retry": 800, "success": 400, "failure": 200}
    pairs = [(s, "working" if s in ("starting", "thinking", "tool", "retry") else s)
             for s in times]
    for pet in ORIGINALS:
        for pstate in pairs:
            state, rstate = pstate
            js = _write_js(tmp_path, "w.js", COMPANION_WORLD_JS)
            out = str(tmp_path / f"{pet}_{state}.json")
            subprocess.run(
                [NATIVE, str(TOOLS / "render_pets.js"), js, state, out, str(times[state]), pet, ""],
                capture_output=True, text=True, check=True,
            )
            got = json.loads(pathlib.Path(out).read_text())[f"{pet}/{state}"]
            want = [row.copy() for row in ref[f"{pet}/{rstate}@{times[state]}"]]
            for y in (44, 45):
                for x in range(48):
                    if want[y][x] == shadow_colors[pet]:
                        want[y][x] = None
            diff = [
                (x, y) for y in range(48) for x in range(48) if got[y][x] != want[y][x]
            ]
            assert not diff, (
                f"{pet}/{state} differs from the approved prototype in {len(diff)} cells, "
                f"first at {diff[:3]}"
            )


@requires_node
def test_the_art_grid_is_still_48_and_the_palette_is_preserved(tmp_path):
    """The originals' exact prototype palette values must still be the ones in use."""
    probe = (
        "const out={};['beetle','raven','golem','tide'].forEach(function(id){"
        "out[id]=A_PAL[id];});console.log(JSON.stringify(out));"
    )
    res = subprocess.run([NATIVE, "-e", COMPANION_ART_JS + probe],
                         capture_output=True, text=True, check=True)
    pal = json.loads(res.stdout)
    # Values copied from references/collection.html's four primary palettes.
    assert pal["beetle"]["glow"] == "#f4b860"
    assert pal["beetle"]["body"] == "#8fa3a8"
    assert pal["raven"]["body"] == "#304957"
    assert pal["raven"]["outline"] == "#111d26"
    assert pal["golem"]["body"] == "#687575"
    assert pal["tide"]["outline"] == "#122c43"
    # The golem's palette genuinely has no metalLight; the approved art depends on that.
    assert "metalLight" not in pal["golem"]


# ------------------------------------------------------------ enlargement and cadence

def test_inline_display_is_an_integer_multiple_of_the_art_grid():
    """Enlargement is nearest-neighbour scaling of a 48px grid, not a redraw or a padding."""
    css = render_companion_fragment()
    rule = css.split("#companionLayer .vool-ninja canvas {", 1)[1].split("}", 1)[0]
    m = re.search(r"width:\s*(\d+)px", rule)
    assert m, "the inline canvas must have an explicit basis"
    width = int(m.group(1))
    assert width % 48 == 0, f"{width}px is not an integer multiple of the 48px art grid"
    assert width >= 96, "the inline pet must actually be larger than before"
    assert "image-rendering: pixelated" in rule, "scaling must stay nearest-neighbour"
    # The canvas element itself is still the 48px logical grid.
    assert "vnCanvas.width = 48" in css and "vnCanvas.height = 48" in css


def test_desktop_window_and_canvas_are_integer_multiples_of_the_grid():
    from installer.bundle import pet_native

    assert pet_native.PET_CANVAS_POINTS % 48 == 0
    assert pet_native.PET_CANVAS_POINTS >= 96
    html = render_desktop_companion_html()
    assert "image-rendering:pixelated" in html
    assert "width='48' height='48'" in html


def test_the_host_cadence_law_is_unchanged():
    """Enlargement and the new renderer must not have redefined the host's frame cadence."""
    assert "if(reduced)return 0;" in COMPANION_WORLD_JS
    assert "ms%6000>=5850" in COMPANION_WORLD_JS, "idle's one short blink must survive"
    assert "['success','failure','cancelled'].includes(state)" in COMPANION_WORLD_JS


# --------------------------------------------------------------------- state reactions

@requires_node
def test_every_state_draws_something_distinct(tmp_path):
    """No state may be a generic bounce, and the collapsed set must be exactly the deliberate one.

    The approved art has ONE working pose. The product has four non-terminal working states
    (starting, thinking, tool, retry), so those four are *supposed* to share a picture -- that is
    preservation, not laziness, and changing it would break the prototype comparison above. What
    separates them is the bubble, which carries the resolved activity, and for ``tool`` with a
    typed activity it is a genuinely different pose (asserted separately below).

    The law this test actually enforces: the sharing is confined to that one group, and every
    other state is its own picture.
    """
    shared = {"starting", "thinking", "tool", "retry"}
    for pet in COMPANION_ROSTER:
        grids, _ = _render(tmp_path, states=STATES, chars=[pet])
        groups = {}
        for st in STATES:
            sig = tuple(tuple(row) for row in grids[f"{pet}/{st}"])
            groups.setdefault(sig, []).append(st)
        collisions = [
            members for members in groups.values()
            if len(members) > 1 and not set(members) <= shared
        ]
        assert not collisions, f"{pet}: unexpected shared pictures {collisions}"
        # The working group really is one picture, so the claim above is not vacuous.
        working = [m for m in groups.values() if set(m) & shared]
        assert len(working) == 1 and set(working[0]) == shared, (
            f"{pet}: expected exactly the working group to share a pose, got {working}"
        )
        # ...and it is a different picture from every other state.
        others = [m for m in groups.values() if not set(m) & shared]
        assert len(others) == len(STATES) - len(shared)


@requires_node
def test_every_activity_substate_has_its_own_pose(tmp_path):
    """Reading must not look like testing. Six activities, six pictures."""
    for pet in ("beetle", "golem", "ember"):
        sigs = {}
        for act in ACTIVITIES:
            js = _write_js(tmp_path, "w.js", COMPANION_WORLD_JS)
            out = str(tmp_path / f"{pet}_{act}.json")
            subprocess.run(
                [NATIVE, str(TOOLS / "render_pets.js"), js, "tool", out, "600", pet, act],
                capture_output=True, text=True, check=True,
            )
            env = {"__ACT__": act}
            grid = json.loads(pathlib.Path(out).read_text())[f"{pet}/tool"]
            sigs.setdefault(tuple(tuple(r) for r in grid), act)
        assert len(sigs) == len(ACTIVITIES), (
            f"{pet} drew {len(sigs)} distinct pictures for {len(ACTIVITIES)} activities"
        )


def test_activity_is_derived_from_typed_categories_not_text():
    """Category -> activity mapping is a fixed table; no natural-language inference exists."""
    assert "A_ACT_CATEGORY" in COMPANION_ART_JS
    for category in ("READING_FILES", "WEB_RESEARCH", "UNDERSTANDING", "APPLYING_CHANGES",
                     "TESTING_CI", "RUNNING_TOOLS", "GIT_INSPECTION"):
        assert category in COMPANION_ART_JS
    # The mapping is a lookup on a known category -- there is no regex over prose anywhere.
    assert "function aActivityFor(category)" in COMPANION_ART_JS
    assert "toLowerCase().includes" not in COMPANION_ART_JS


def test_reduced_motion_keeps_a_readable_pose_and_the_state():
    """Reduced motion freezes the pose; it must not blank the state or the bubble."""
    assert "reduced?10000" in COMPANION_ART_JS
    frag = render_companion_fragment()
    assert "vnPrefersReducedMotion()" in frag


# ------------------------------------------------------------------------- the bubble

def test_bubble_is_above_the_pet_in_both_hosts():
    frag = render_companion_fragment()
    rule = frag.split("#companionLayer .vn-bubble {", 1)[1].split("}", 1)[0]
    assert "bottom: calc(100% - 18px)" in rule, "the inline bubble must sit above the pet"
    assert "position: absolute" in rule, "the bubble must be out of flow so text cannot move the pet"
    assert "pointer-events: none" in rule
    html = render_desktop_companion_html()
    drule = html.split("#bubble{", 1)[1].split("}", 1)[0]
    from core.companion_layout import BUBBLE_GAP, CANVAS_BOTTOM, CANVAS_SIZE
    assert f"bottom:{CANVAS_BOTTOM + CANVAS_SIZE + BUBBLE_GAP}px" in drule
    assert "pointer-events:none" in drule


def test_bubble_has_two_lines_title_then_activity():
    frag = render_companion_fragment()
    assert 'vnBubble.className = "vn-bubble"' in frag
    assert "vn-bubble-title" in frag and "vn-bubble-activity" in frag
    # Title is resolved from the bound chat, the page's own title owner.
    assert "chatTitleFor" in frag


def test_bubble_text_is_escaped_and_bounded():
    frag = render_companion_fragment()
    # textContent only -- never innerHTML for either line.
    assert "vnBubbleTitle.textContent =" in frag
    assert "vnBubbleActivity.textContent =" in frag
    assert "vnBubbleTitle.innerHTML" not in frag
    assert "vnBubbleActivity.innerHTML" not in frag
    assert "function vnSafeText" in frag
    assert "VN_TITLE_MAX" in frag and "VN_ACTIVITY_MAX" in frag
    # Non-Latin titles must not be cut into a lone surrogate.
    assert "Array.from(s)" in frag


def test_bubble_clamps_and_flips_without_moving_the_character():
    frag = render_companion_fragment()
    assert "function vnClampBubble()" in frag
    assert "--vn-bubble-dx" in frag, "the bubble offsets itself, not the pet"
    assert "vn-below" in frag, "the bubble must flip below when there is no room above"


def test_idle_does_not_keep_advertising_stale_work():
    frag = render_companion_fragment()
    assert 'nextState === "idle"' in frag
    assert 'line2 = "";' in frag, "idle must blank the activity line, not repeat the last phrase"


def test_no_raw_commands_or_paths_reach_the_bubble():
    """The bubble may only ever carry a reducer phrase or a typed state name."""
    frag = render_companion_fragment()
    assert "pres.phraseText" in frag
    # No model prose, no user message, no command/argv/path assembly anywhere in the path.
    for forbidden in ("args", "argv", "command_text", "userMessage", "stdout"):
        assert forbidden not in frag, f"{forbidden} must not reach the status surface"


# ------------------------------------------------------------------- payload and safety

def test_payload_carries_title_and_activity_and_rejects_junk():
    ok = normalise_companion_payload({
        "state": "tool", "character": "golem", "activity": "exec",
        "caption": "Running command", "chatTitle": "Fix <b>parser</b>",
    })
    assert ok["activity"] == "exec"
    assert ok["chatTitle"] == "Fix <b>parser</b>"
    # An activity outside the renderer's vocabulary clears the previous pose.
    assert normalise_companion_payload({"activity": "rm -rf /"})["activity"] == ""
    assert normalise_companion_payload({"activity": "<img src=x>"})["activity"] == ""
    # Every roster member is accepted; anything else falls back to a real member.
    for pet in COMPANION_ROSTER:
        assert normalise_companion_payload({"character": pet})["character"] == pet
    assert normalise_companion_payload({"character": "../etc/passwd"})["character"] == "spark"
    # An older payload with none of the new fields still renders.
    legacy = normalise_companion_payload({"state": "thinking", "character": "spark"})
    assert legacy["state"] == "thinking" and legacy["character"] == "spark"
    assert legacy["activity"] == "" and legacy["chatTitle"] == ""


def test_desktop_payload_html_cannot_be_escaped():
    html = render_desktop_companion_html({"chatTitle": "</script><img src=x onerror=1>"})
    assert "</script><img" not in html
    assert r"\u003c" in html


def test_bubble_hit_rect_is_part_of_the_pet_surface_but_corners_pass_through():
    from installer.bundle import pet_native

    rects = pet_native.pet_hit_rects()
    assert len(rects) == 3, "canvas + bubble + controls"
    canvas = rects[0]
    bubble = rects[1]
    assert bubble[1] > canvas[1] + canvas[3] - 1, "the bubble rect sits above the canvas"
    frame = (0.0, 0.0, float(pet_native.PET_WINDOW_WIDTH), float(pet_native.PET_WINDOW_HEIGHT))
    # The bubble's own visible area belongs to the pet.
    bx = frame[0] + bubble[0] + bubble[2] / 2.0
    by = frame[1] + bubble[1] + bubble[3] / 2.0
    assert pet_native.point_hits_pet(bx, by, frame) is True
    # The window's top corners stay transparent.
    assert pet_native.point_hits_pet(2.0, frame[3] - 2.0, frame) is False
    assert pet_native.point_hits_pet(frame[2] - 2.0, frame[3] - 2.0, frame) is False


def test_the_detached_payload_and_the_inline_bubble_say_the_same_thing():
    """One owner for the two lines: the detached pet is not a second, coarser source."""
    frag = render_companion_fragment()
    assert "payload.chatTitle" in frag and "payload.activity" in frag
    html = render_desktop_companion_html()
    assert "vcwState.chatTitle" in html and "vcwState.caption" in html
