"""The working card's indicator is VOOL's mark on a tiny black LED panel, lit bulb by bulb, in CSS.

History: a 56x56 `<canvas class="tc-snake">` shader spinner, then a 56px hand-traced SVG sketch of
the mark. On 2026-10-30 the owner asked for the mark 30-40% smaller and redrawn as "black LED
square tiny bulbs" where the VOOL image lights up bulb after bulb. So the indicator is now a 36px
inline SVG (core/working_mark.py): a dark panel, a 12x13 grid of dim square bulbs, and a lit layer
holding the bulbs of the mark -- the V, the two O eyes, the L-shaped smile -- that switch on in
drawing order on one shared clock, hold while the eyes blink, go dark, and restart.

The resting markup IS the complete lit mark: lit bulbs are fully on with no animation. So
`animation: none` -- reduced motion, or a terminal card -- is the finished logo, instantly, and
removing the card removes the animation with it (no timers, no rAF handles, nothing to leak).
"""

from __future__ import annotations

import re

from core import working_mark as wm
from tests.chat_page_js_harness import DOM, HTML, run_node, script


def _css() -> str:
    styles = re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.DOTALL)
    assert styles, "no <style> block found in the chat page"
    return "\n".join(styles)


def _keyframes(css: str, name: str) -> dict[float, str]:
    """One @keyframes block as {waypoint%: declarations} (a "0%,4%" selector maps both)."""
    m = re.search(r"@keyframes " + re.escape(name) + r" \{ (.*?) \}\n", css + "\n")
    assert m, f"@keyframes {name} not found in the page CSS"
    out: dict[float, str] = {}
    for sel, body in re.findall(r"([\d.,%]+) \{ ([^}]*) \}", m.group(1)):
        for pct in sel.split(","):
            out[float(pct.rstrip("%"))] = body
    return out


def _switch_on(css: str, i: int) -> float:
    frames = _keyframes(css, f"tcBulb{i}")
    return max(p for p, body in frames.items() if "opacity:0" in body and p < wm.HOLD_TO)


def _mark_svg() -> str:
    m = re.search(r"const TC_MARK_SVG = (.*?);\n", script())
    assert m, "TC_MARK_SVG not found in the page script"
    return m.group(1)


# ---------------------------------------------------------------- 1. the old spinners are gone


def test_the_old_canvas_spinner_and_the_sketch_are_completely_removed() -> None:
    for gone in ("tc-snake", "VoolCore", "CORE_SRC", "coreRender", "run.snake",
                 "tcDrawV", "tcDustV", "tc-life", "tc-ring"):
        assert gone not in HTML, f"a removed animation left '{gone}' behind"
    canvases = re.findall(r"<canvas\b[^>]*>", HTML)
    others = [tag for tag in canvases if not tag.startswith('<canvas id="vcdPreview" ')]
    assert others == [], f"a canvas came back: {others}"


# ------------------------------------------------- 2. the panel: inline SVG at a fixed 36px


def test_the_mark_is_an_inline_svg_aria_hidden_at_a_fixed_36px_footprint() -> None:
    svg = _mark_svg()
    assert svg.startswith("'<svg class=\"tc-mark\""), "the mark must be one inline SVG, not an image"
    assert 'width="36" height="36"' in svg, "36px: 30-40% smaller than the old 56px"
    assert 'aria-hidden="true"' in svg and 'focusable="false"' in svg, (
        "the title text beside it is the status signal; the SVG is decoration"
    )
    assert "http" not in svg.lower(), "no external assets"
    assert "'" not in wm.mark_svg() and "\\" not in wm.mark_svg(), "the page embeds it in a '...' JS string"
    assert ".tc-mark { flex:0 0 auto; width:36px; height:36px;" in _css()


def test_the_panel_holds_every_dim_bulb_and_the_lit_bulbs_spell_the_mark() -> None:
    svg = _mark_svg()
    assert svg.count('class="tc-panel"') == 1
    assert svg.count('class="tc-off"') == wm.COLS * wm.ROWS, "every grid cell is a dim bulb"
    order = wm.lit_order()
    assert svg.count("tc-on tc-") == len(order)
    parts = [part for _cell, part in order]
    assert parts == sorted(parts, key=["v", "eye", "smile"].index), "V, then the eyes, then the smile"
    cells = [cell for cell, _part in order]
    assert len(set(cells)) == len(cells), "a bulb lights once per cycle"
    assert all(0 <= r < wm.ROWS and 0 <= c < wm.COLS for r, c in cells)
    # The V is one continuous stroke: each bulb touches the one before it.
    for (r0, c0), (r1, c1) in zip(wm.V, wm.V[1:]):
        assert max(abs(r1 - r0), abs(c1 - c0)) == 1


# ------------------------------------------- 3. the sequence: bulb after bulb, hold, go dark


def test_bulbs_switch_on_one_after_another_and_the_whole_mark_holds_before_going_dark() -> None:
    css = _css()
    order = wm.lit_order()
    ons = [_switch_on(css, i) for i in range(len(order))]
    assert ons == sorted(ons) and len(set(ons)) == len(ons), "strictly one bulb after another"
    assert ons[-1] + 3.0 < wm.HOLD_TO, "the last bulb is lit before the hold ends"
    for i in range(len(order)):
        frames = _keyframes(css, f"tcBulb{i}")
        assert "opacity:1" in frames[wm.HOLD_TO], f"bulb {i} must hold lit"
        assert "opacity:0" in frames[wm.DARK_BY] and "opacity:0" in frames[100.0], f"bulb {i} must go dark"
        assert frames[0.0] == "opacity:0;", f"bulb {i} starts dark each cycle"


def test_only_the_eyes_blink_during_the_hold() -> None:
    css = _css()
    order = wm.lit_order()
    last_on = _switch_on(css, len(order) - 1)
    for i, (_cell, part) in enumerate(order):
        dim = [p for p, body in _keyframes(css, f"tcBulb{i}").items() if "opacity:.12" in body]
        if part == "eye":
            assert len(dim) == len(wm.BLINKS), f"eye bulb {i} must blink {len(wm.BLINKS)} times"
            assert all(last_on < p < wm.HOLD_TO for p in dim), "blinks happen while the whole mark holds"
        else:
            assert dim == [], f"a {part} bulb blinked"


def test_every_lit_bulb_runs_on_the_one_shared_clock() -> None:
    css = _css()
    assert ".tc-mark .tc-on { fill:currentColor; animation-duration:4s; animation-timing-function:linear; animation-iteration-count:infinite; }" in css
    assert wm.CYCLE_SECONDS == 4.0


# ------------------------------------- 4. reduced motion / terminal: the complete static mark


def test_reduced_motion_and_finished_cards_show_the_complete_lit_mark() -> None:
    css = _css()
    assert "@media (prefers-reduced-motion: reduce) { .tc-mark * { animation: none !important; } }" in css
    assert ".task-card.done .tc-mark *, .task-card.failed .tc-mark *, .task-card.cancelled .tc-mark * { animation: none !important; }" in css
    # Which IS the finished logo: the lit bulbs carry no resting opacity of their own.
    assert ".tc-mark .tc-on { fill:currentColor;" in css
    assert "opacity" not in css.split(".tc-mark .tc-on {")[1].split("}")[0]


def test_waiting_cards_freeze_the_panel_mid_light_up() -> None:
    css = _css()
    assert ".task-card.perm .tc-mark * { animation-play-state: paused !important; }" in css
    assert ".tc-hold .tc-mark * { animation-play-state: paused !important; }" in css
    src = script()
    assert "kind === 'completed' ? 'done' : kind" in src
    assert ">VOOL is working</span>" in src


# ------------------------------------------------------- 5. lifecycle drive under the node DOM


DRIVE = """
globalThis.__consoleErrors = [];
const __realError = console.error.bind(console);
console.error = (...a) => { globalThis.__consoleErrors.push(a.map(String).join(' ')); __realError(...a); };
const drove = globalThis.__drove;
function drive(name, fn) { try { fn(); drove.push(name); } catch (e) { errors.push(name + ': ' + ((e && e.message) || e)); } }

function cardFor() {
  const run = adoptRun(displayedChat, newRun(displayedChat));
  buildCard(run, null);
  return run;
}
drive('fresh card carries the mark ref', () => {
  const run = cardFor();
  if (!run.refs.mark) throw new Error('no .tc-mark in the built card');
});
drive('permission holds the mark, the next tool resumes it', () => {
  const run = cardFor();
  applyTaskEvent(run, { type: 'permission.required', summary: 'Approve', approval: { approval_id: 'a1', scope_options: ['once'] } });
  if (!run.card.classList.contains('tc-hold')) throw new Error('permission did not hold the mark');
  applyTaskEvent(run, { type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading' });
  if (run.card.classList.contains('tc-hold')) throw new Error('tool.started did not resume the mark');
});
drive('terminal cards take their kind class', () => {
  for (const kind of ['completed', 'failed', 'cancelled']) {
    const run = cardFor();
    finishRun(run, kind, 'end', new Date().toISOString());
    if (!run.card.classList.contains(kind === 'completed' ? 'done' : kind)) throw new Error(kind + ' did not paint its terminal class');
  }
});
_st(() => {
  for (const e of globalThis.__consoleErrors || []) errors.push('console.error: ' + e);
  __report(); process.exit(0);
}, 400);
"""


def test_the_card_lifecycle_drives_clean_under_the_node_dom() -> None:
    data = run_node(DOM + script() + DRIVE)
    assert data["errors"] == [], "\n".join(data["errors"])
    assert len(data["drove"]) == 3, f"only drove {data['drove']}"
