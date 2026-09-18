"""The working card's indicator is VOOL's own hand-drawn mark, animated with CSS -- not a spinner.

The card beside a running answer used to carry a 56x56 `<canvas class="tc-snake">` driven by the
`VoolCore` per-pixel glass shader (a metaball field -> fresnel/refraction pipeline with its own
requestAnimationFrame loop, adaptive-resolution ladder and visibilitychange handler). That whole
runtime is gone: the indicator is now an inline SVG trace of the official VOOL mark -- the bold V,
the two O eyes, the L-shaped smile -- brought to life purely with CSS against one shared 3.4s
clock, staged so the sequence reads V -> oo -> L -> complete mark -> hold -> implode -> restart.

The resting markup IS the complete static mark: strokes carry no dash attributes (the draw-in
dashes live only inside keyframes) and the particle/burst circles sit at opacity:0. So
`animation: none` -- reduced motion, or a terminal card -- is the finished logo, instantly, and
removing the card removes the animation with it (no timers, no rAF handles, nothing to leak).

RED evidence at base 56672d58: every assertion that names tc-mark/tc-v/tc-ol/tc-or/tc-l failed
there, while `<canvas class="tc-snake"` and `function VoolCore(` were present in the emitted page
(proof the old spinner was the thing being replaced, and that these pins are load-bearing).
"""

from __future__ import annotations

import re

from tests.chat_page_js_harness import DOM, HTML, run_node, script

CYCLE_SECONDS = 3.4


def _css() -> str:
    styles = re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.DOTALL)
    assert styles, "no <style> block found in the chat page"
    return "\n".join(styles)


def _keyframes(css: str, name: str) -> list[float]:
    """The numeric waypoints of one @keyframes block, in source order (brace-counted, since the
    page writes some keyframes on a single line)."""
    m = re.search(r"@keyframes " + re.escape(name) + r"\s*\{", css)
    assert m, f"@keyframes {name} not found in the page CSS"
    depth, i = 1, m.end()
    while depth and i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    body = css[m.end():i - 1]
    return [float(p) for p in re.findall(r"(\d+(?:\.\d+)?)%", body)]


def _mark_svg() -> str:
    m = re.search(r"const TC_MARK_SVG = (.*?)\n\n", script(), re.DOTALL)
    assert m, "TC_MARK_SVG not found in the page script"
    return m.group(1)


# ---------------------------------------------------------------- 1. the old spinner is gone


def test_the_old_canvas_spinner_and_its_runtime_are_completely_removed() -> None:
    for gone in ("tc-snake", "<canvas", "VoolCore", "CORE_SRC", "coreRender", "coreParams",
                 "corePhaseAt", "CORE_BASE", "guardCost", "run.snake"):
        assert gone not in HTML, f"the removed canvas animation left '{gone}' behind"
    # The whole page has no canvas element left at all -- the working indicator is the only
    # raster-free replacement and nothing else re-introduced one.
    assert "<canvas" not in HTML


# ------------------------------------------------- 2. the mark: inline SVG, four traced parts


def test_the_mark_is_an_inline_svg_aria_hidden_at_the_same_56px_footprint() -> None:
    svg = _mark_svg()
    assert svg.startswith("'<svg class=\"tc-mark\""), "the mark must be one inline SVG, not an image"
    assert 'viewBox="0 0 80 100"' in svg and 'width="56" height="56"' in svg, (
        "the 56x56 footprint is part of the card layout contract -- no layout shift"
    )
    assert 'aria-hidden="true"' in svg and 'focusable="false"' in svg, (
        "the adjacent 'VOOL is working' text is the status signal; the SVG must be decoration"
    )
    assert "http" not in svg.lower() and "import" not in svg, "no external assets, no libraries"
    css = _css()
    assert ".tc-mark { flex:0 0 auto; width:56px; height:56px;" in css, (
        "the mark keeps the exact box the old canvas occupied"
    )


def test_the_four_hand_drawn_parts_are_present_and_stroke_only() -> None:
    svg = _mark_svg()
    for part, label in (("tc-v", "the V"), ("tc-ol", "the left O"), ("tc-or", "the right O"),
                        ("tc-l", "the L-shaped smile")):
        assert f'class="{part}"' in svg, f"{label} is missing from the traced mark"
        assert f'class="{part}" pathLength="100"' in svg, (
            f"{label} must carry pathLength=100 so the CSS draw-in uses one dash recipe"
        )
    # Strokes, not filled shapes: the hand-drawn character lives in the drawn line.
    assert svg.count("<path") == 4
    assert 'fill="none"' not in svg  # fill:none comes from the .tc-mark path CSS rule
    css = _css()
    assert ".tc-mark path { fill:none; stroke:currentColor;" in css, (
        "monochrome: every stroke inherits the theme's accent colour"
    )
    # Sketch dust converging on each part (both O's share one dust stage -- they form together),
    # plus the implode burst.
    assert svg.count('tc-p tc-pv"') == 5 and svg.count('tc-p tc-pl"') == 5
    assert svg.count('tc-p tc-po"') == 10
    assert svg.count('class="tc-b"') == 5, "expected the compact implode burst"


def test_buildCard_wires_the_mark_and_no_per_run_animation_object() -> None:
    src = script()
    assert "+ TC_MARK_SVG" in src, "buildCard must embed the shared mark markup"
    assert "mark: q('.tc-mark')" in src
    assert "new VoolCore" not in src and "run.snake" not in src, (
        "no per-run animation handle may be created: the animation lives in CSS on the card's DOM"
    )


# ------------------------------------------------- 3. the staged sequence: V -> oo -> L -> hold


def test_the_sequence_is_staged_v_then_both_os_then_l_then_a_hold_before_implosion() -> None:
    css = _css()
    # Each draw-in block has the shape [0, hidden_until, drawn_by, 100]: hidden, resolve, hold.
    v, ol, orr, l = (_keyframes(css, n) for n in ("tcDrawV", "tcDrawOl", "tcDrawOr", "tcDrawL"))
    for block in (v, ol, orr, l):
        assert len(block) == 4 and block[0] == 0 and block[3] == 100, f"unexpected shape {block}"
    life = sorted(set(_keyframes(css, "tcLife")))          # [0, hold_ends, gone_by, 100]
    implode_at, gone_by = life[1], life[2]

    assert v[2] <= ol[1], f"the V must resolve ({v[2]}%) before the O's even start ({ol[1]}%)"
    assert ol[2] <= l[1], f"both O's must resolve ({ol[2]}%) before the L starts ({l[1]}%)"
    assert l[2] < implode_at, (
        f"the complete mark must hold ({l[2]}% -> {implode_at}%) before the implosion"
    )
    assert implode_at < gone_by <= 100, "the implosion must finish fading before the cycle wraps"
    # The left and right O form together, as one stage of the sequence.
    assert orr[1:] == ol[1:]


def test_every_layer_shares_one_clock_inside_the_required_cycle_window() -> None:
    css = _css()
    durations = set(re.findall(r"animation: tc\w+\s+([\d.]+s) linear infinite", css))
    assert durations == {f"{CYCLE_SECONDS}s"}, f"one shared clock expected, saw {durations}"
    assert 2.8 <= CYCLE_SECONDS <= 4.0, "the full cycle must sit in the 2.8-4s window"
    # Dust converges BEFORE its part finishes resolving: particles sketch, the stroke completes.
    dust_v = max(p for p in _keyframes(css, "tcDustV") if p < 15)
    dust_o = max(p for p in _keyframes(css, "tcDustO") if p < 40)
    dust_l = max(p for p in _keyframes(css, "tcDustL") if p < 60)
    assert dust_v <= min(p for p in _keyframes(css, "tcDrawV") if p > 2)
    assert dust_o <= max(p for p in _keyframes(css, "tcDrawOl") if p < 44)
    assert dust_l <= max(p for p in _keyframes(css, "tcDrawL") if p < 70)


# ------------------------------------- 4. reduced motion / terminal: the complete static mark


def test_reduced_motion_shows_the_complete_static_mark_immediately() -> None:
    css = _css()
    # (The page carries several reduced-motion blocks for other components; this one is ours.)
    assert "@media (prefers-reduced-motion: reduce) { .tc-mark * { animation: none !important; } }" in css, (
        "reduced motion must disable the mark's animations, falling back to the resting markup"
    )
    # Which IS the finished logo: no dash attributes in the markup, particles dark at rest.
    svg = _mark_svg()
    assert "stroke-dasharray" not in svg and "stroke-dashoffset" not in svg
    assert ".tc-mark .tc-p, .tc-mark .tc-b { fill:currentColor; opacity:0; }" in css


def test_terminal_and_waiting_cards_settle_the_mark_without_touching_their_text() -> None:
    css = _css()
    terminal = ".task-card.done .tc-mark *, .task-card.failed .tc-mark *, .task-card.cancelled .tc-mark * { animation: none !important; }"
    assert terminal in css, "every terminal kind must settle the mark into the static logo"
    # Waiting for approval is NOT terminal: the mark freezes mid-sketch rather than finishing.
    assert ".task-card.perm .tc-mark * { animation-play-state: paused !important; }" in css
    assert ".tc-hold .tc-mark * { animation-play-state: paused !important; }" in css
    src = script()
    # The terminal/writing text path is byte-identical to what shipped before the mark.
    assert "kind === 'completed' ? 'done' : kind" in src
    assert "'Waiting for your approval'" in src and "'Failed safely'" in src
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
  window.__card = run.card;
});
drive('permission holds the mark, the next tool resumes it', () => {
  const run = window.__run = cardFor();
  applyTaskEvent(run, { type: 'permission.required', summary: 'Approve', approval: { approval_id: 'a1', scope_options: ['once'] } });
  if (!run.card.classList.contains('tc-hold')) throw new Error('permission did not hold the mark');
  applyTaskEvent(run, { type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading' });
  if (run.card.classList.contains('tc-hold')) throw new Error('tool.started did not resume the mark');
});
drive('terminal cards take their kind class and drop the hold', () => {
  for (const kind of ['completed', 'failed', 'cancelled']) {
    const run = cardFor();
    run.card.classList.add('tc-hold');
    finishRun(run, kind, 'end', new Date().toISOString());
    if (!run.card.classList.contains(kind === 'completed' ? 'done' : kind)) {
      throw new Error(kind + ' did not paint its terminal class');
    }
  }
  const run = cardFor();
  finishRun(run, 'awaiting_approval', 'needs you', new Date().toISOString());
  if (!run.card.classList.contains('perm')) throw new Error('awaiting_approval did not paint perm');
});
drive('repeated sends rebuild one mark per card with no shared handle', () => {
  const cards = [];
  for (let i = 0; i < 3; i++) { const run = cardFor(); cards.push(run.card); run.refs = null; }
  if (new Set(cards).size !== 3) throw new Error('cards were reused across sends');
});

_st(() => {
  const cerr = globalThis.__consoleErrors || [];
  for (const e of cerr) errors.push('console.error: ' + e);
  __report(); process.exit(0);
}, 400);
"""


def test_the_card_lifecycle_drives_clean_under_the_node_dom() -> None:
    data = run_node(DOM + script() + DRIVE)
    assert data["errors"] == [], "the mark's card lifecycle threw under node:\n" + "\n".join(data["errors"])
    assert len(data["drove"]) >= 4, f"only drove {data['drove']}"
